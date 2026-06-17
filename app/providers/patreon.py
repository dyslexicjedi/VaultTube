import requests
import os
import re
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from flask import current_app
import datetime
import json
import subprocess
import tempfile

from providers.base import set_status, update_status, del_status
from database import check_db_video, check_db_channel, save_channel, get_connection
from QueueObject import QueueObject
from queue_utils import enqueue


def provider_domains():
    return ['patreon.com']

def _normalize_url(url):
    # yt-dlp's PatreonIE expects /posts/{slug} not /{creator}/posts/{slug}
    return re.sub(r'patreon\.com/[^/]+/(posts/)', r'patreon.com/\1', url)

def _api_get(url, logger):
    """GET a Patreon API URL using the configured cookies + browser impersonation."""
    ydl = yt_dlp.YoutubeDL({
        'cookiefile': os.environ['VAULTTUBE_PATREONCOOKIE'],
        'impersonate': ImpersonateTarget.from_str('chrome'),
        'quiet': True,
    })
    resp = ydl.urlopen(yt_dlp.networking.Request(url, headers={'Content-Type': 'application/vnd.api+json'}))
    return json.loads(resp.read())

def ensure_channel(campaign_id, logger):
    """Create a channels row for a Patreon campaign so it shows up in the UI
    and can be subscribed to. No-op if the row already exists."""
    try:
        if 'VAULTTUBE_PATREONCOOKIE' not in os.environ:
            return
        if check_db_channel(campaign_id, logger):
            return
        # url is the creator's public page, used for the source link in the UI
        data = _api_get('https://www.patreon.com/api/campaigns/%s?fields[campaign]=name,url&json-api-version=1.0' % campaign_id, logger)
        name = data['data']['attributes']['name']
        save_channel(campaign_id, name, data, logger)
        logger.info("Created channel entry for Patreon campaign %s (%s)" % (campaign_id, name))
    except Exception as e:
        logger.error("ensure_channel failed for Patreon campaign %s: %s" % (campaign_id, e))

def _inline_video_media_ids(content_json_string):
    """Media IDs of video blocks in a block-editor post body. Newer Patreon
    posts embed video as a content block (post_type stays 'text_only' and
    post_file stays null), so this is the only place the video shows up."""
    ids = []
    try:
        doc = json.loads(content_json_string or '{}')
        def walk(node):
            if isinstance(node, dict):
                if node.get('type') == 'video' and node.get('attrs', {}).get('media_id'):
                    ids.append(str(node['attrs']['media_id']))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(doc)
    except Exception:
        pass
    return ids

def _post_has_video(attrs):
    """A post is downloadable if it's a classic video post OR a block-editor
    post with an inline video block."""
    if 'video' in (attrs.get('post_type') or ''):
        return True
    return bool(_inline_video_media_ids(attrs.get('content_json_string')))

def scan_campaign(campaign_id, logger):
    """Enqueue any new viewable video posts from a subscribed Patreon campaign.
    Posts without video (announcements, images, polls) are skipped."""
    if 'VAULTTUBE_PATREONCOOKIE' not in os.environ:
        logger.error("VAULTTUBE_PATREONCOOKIE not set, skipping Patreon campaign %s" % campaign_id)
        return
    try:
        url = ('https://www.patreon.com/api/posts'
               '?filter[campaign_id]=%s'
               '&fields[post]=title,post_type,content_json_string,current_user_can_view'
               '&sort=-published_at&page[count]=50&json-api-version=1.0' % campaign_id)
        data = _api_get(url, logger)
        for post in data.get('data', []):
            attrs = post.get('attributes', {})
            post_id = post['id']
            if not _post_has_video(attrs):
                continue
            if not attrs.get('current_user_can_view'):
                logger.info("Skipping locked Patreon post: %s (%s)" % (post_id, attrs.get('title')))
                continue
            if check_db_video(post_id, logger):
                logger.info("Already found: %s" % post_id)
                continue
            logger.info("Processing Patreon post: %s (%s)" % (post_id, attrs.get('title')))
            qo = QueueObject("https://www.patreon.com/posts/%s" % post_id, "", "patreon", 0, "")
            enqueue(qo, current_app.config['queue'], logger)
    except Exception as e:
        logger.error("Scanning Patreon campaign %s failed: %s" % (campaign_id, e))

def download(q,logger):
    if 'VAULTTUBE_PATREONCOOKIE' not in os.environ:
        raise RuntimeError("VAULTTUBE_PATREONCOOKIE not configured; cannot download from Patreon")
    try:
        url = _normalize_url(q.url)
        logger.debug("Starting Patreon Download: %s" % url)
        ydl_opts = {
            'cookiefile': os.environ['VAULTTUBE_PATREONCOOKIE'],
            'outtmpl': os.environ['VAULTTUBE_VAULTDIR']+"/%(channel_id)s/%(id)s.mp4",
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            "progress_hooks": [dl_progress_hook],
            # Impersonate a browser TLS fingerprint globally (not just for the
            # generic extractor) or Patreon's Cloudflare returns 403 on API calls
            'impersonate': ImpersonateTarget.from_str('chrome'),
            'socket_timeout': 30,        # seconds before a socket read times out
            'retries': 10,               # retry failed fragment/chunk downloads
            'fragment_retries': 10,      # retry failed fragments specifically
            'retry_sleep_functions': {'http': lambda n: 5 * n},  # back-off: 5s, 10s, 15s...
            'http_chunk_size': 10485760, # 10 MB chunks instead of the default large size
        }
        deno_path = os.environ.get('VAULTTUBE_DENOPATH')
        if deno_path:
            ydl_opts['js_runtimes'] = {'deno': {'path': deno_path}}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                data = ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as e:
                # Block-editor posts keep post_type 'text_only' and have no
                # post_file, so yt-dlp's extractor finds nothing — the video
                # lives in an inline content block instead
                if 'No supported media found' in str(e):
                    return _download_inline_video(url, logger)
                raise
            videoid = data['id']
            channel_id = data['channel_id']
            title = data['title']
            PublishedAt = datetime.datetime.strptime(data['upload_date'], '%Y%m%d')
            ensure_channel(channel_id, logger)
            set_status(videoid, {'progress': '0%', 'title': title, 'provider': 'patreon'})
            try:
                ydl.download(url)
                ps = patreon_screenshot(videoid, channel_id, logger)
                pdb = patreon_db_info(videoid, channel_id, PublishedAt, title, logger)
                if ps and pdb:
                    return True
                else:
                    return False
            finally:
                del_status(videoid)
    except Exception as e:
        logger.error("Patreon download failed: %s" % e)
        raise

def _download_inline_video(url, logger):
    """Download a block-editor post whose video is an inline media block."""
    post_id = re.search(r'(\d+)/?$', url).group(1)
    post = _api_get('https://www.patreon.com/api/posts/%s'
                    '?fields[post]=title,published_at,content_json_string'
                    '&json-api-version=1.0' % post_id, logger)
    attrs = post['data']['attributes']
    media_ids = _inline_video_media_ids(attrs.get('content_json_string'))
    if not media_ids:
        raise Exception("No supported media found in this post (no inline video blocks either)")
    if len(media_ids) > 1:
        logger.info("Post %s has %d inline videos, downloading the first" % (post_id, len(media_ids)))
    channel_id = post['data']['relationships']['campaign']['data']['id']
    title = attrs['title']
    PublishedAt = datetime.datetime.fromisoformat(attrs['published_at']).replace(tzinfo=None)

    media = _api_get('https://www.patreon.com/api/media/%s?json-api-version=1.0' % media_ids[0], logger)
    mattrs = media['data']['attributes']
    # display.url is the signed HLS master (all renditions); download_url is a
    # lower-quality progressive mp4 fallback
    stream_url = (mattrs.get('display') or {}).get('url') or mattrs.get('download_url')
    if not stream_url:
        raise Exception("Inline video media %s has no stream URL" % media_ids[0])

    def hook(d):
        try:
            if d['status'] == 'downloading':
                update_status(post_id, {'progress': d.get('_percent_str', ''), 'title': title, 'provider': 'patreon'})
            elif d['status'] == 'finished':
                update_status(post_id, {'progress': '100%'})
        except Exception:
            pass

    ydl_opts = {
        'cookiefile': os.environ['VAULTTUBE_PATREONCOOKIE'],
        'outtmpl': os.environ['VAULTTUBE_VAULTDIR'] + "/" + channel_id + "/" + post_id + ".mp4",
        'progress_hooks': [hook],
        'impersonate': ImpersonateTarget.from_str('chrome'),
        'http_headers': {'Referer': 'https://www.patreon.com/'},
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'retry_sleep_functions': {'http': lambda n: 5 * n},
    }
    ensure_channel(channel_id, logger)
    set_status(post_id, {'progress': '0%', 'title': title, 'provider': 'patreon'})
    try:
        logger.info("Downloading inline video for post %s (%s)" % (post_id, title))
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([stream_url])
        ps = patreon_screenshot(post_id, channel_id, logger)
        pdb = patreon_db_info(post_id, channel_id, PublishedAt, title, logger)
        return bool(ps and pdb)
    finally:
        del_status(post_id)

def patreon_screenshot(videoid,channelid,logger):
    output_img = os.path.join(tempfile.gettempdir(), "%s.jpg" % videoid)
    con = None
    cur = None
    try:
        input_video = os.environ['VAULTTUBE_VAULTDIR']+"/"+channelid+"/"+str(videoid)+".mp4"
        rc = subprocess.call(['ffmpeg', '-y', '-i', input_video, '-ss', '00:00:01.000', '-vframes', '1', output_img],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc != 0 or not os.path.exists(output_img):
            logger.error("patreon_screenshot: ffmpeg failed for %s (exit %s)" % (videoid, rc))
            return False
        with open(output_img, 'rb') as f:
            img = f.read()
        con = get_connection(logger)
        cur = con.cursor()
        sql = "Insert Ignore into images(id,image) values(%s,%s)"
        cur.execute(sql,(videoid,img))
        con.commit()
        logger.debug("Screenshot saved")
        return True
    except Exception as e:
        logger.error("patreon_screenshot failed for %s: %s" % (videoid, e))
        return False
    finally:
        if cur is not None:
            try: cur.close()
            except Exception: pass
        if con is not None:
            try: con.close()
            except Exception: pass
        if os.path.exists(output_img):
            os.remove(output_img)

def patreon_db_info(videoid,channelid,PublishedAt,title,logger):
    con = None
    cur = None
    try:
        t = {
            'id': videoid,
            'title': title,
            'channelId': channelid,
            'publishedAt': PublishedAt.strftime('%Y-%m-%d %H:%M:%S.%f'),
            'source': 'patreon',
            'webpage_url': 'https://www.patreon.com/posts/%s' % videoid,
        }
        source = "patreon"

        con = get_connection(logger)
        cur = con.cursor()
        sql = "Select * from videos where id = %s"
        cur.execute(sql,(videoid,))
        if cur.fetchone():
            logger.info("Video already exists in database, updating")
            sql = "Update videos set youtuber=%s,channelId=%s,filepath=%s,PublishedAt=%s,json=%s,source=%s,title=%s  where id=%s"
        else:
            sql = "Insert into videos(youtuber,channelId,filepath,PublishedAt,json,source,title,id) values(%s,%s,%s,%s,%s,%s,%s,%s)"
        cur.execute(sql,("",channelid,"/"+channelid+"/"+str(videoid)+".mp4",PublishedAt.strftime('%Y-%m-%d %H:%M:%S.%f'),json.dumps(t),source,title,videoid))
        con.commit()
        logger.debug("Metadata saved")
        return True
    except Exception as e:
        logger.error("patreon_db_info failed for %s: %s" % (videoid, e))
        return False
    finally:
        if cur is not None:
            try: cur.close()
            except Exception: pass
        if con is not None:
            try: con.close()
            except Exception: pass

def dl_progress_hook(d):
    try:
        video_id = d.get('info_dict', {}).get('id', '') or ''
        if d["status"] == "downloading":
            update_status(video_id, {
                'progress': d['_percent_str'],
                'title': d.get('info_dict', {}).get('title', ""),
                'provider': 'patreon',
            })
        elif d["status"] == "finished":
            update_status(video_id, {'progress': '100%'})
    except Exception as e:
        current_app.logger.error("dl_progress_hook Failed: %s" % e)