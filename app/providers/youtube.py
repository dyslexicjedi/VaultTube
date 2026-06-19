import requests
import os
from io import StringIO
from urllib.parse import urlparse, parse_qs
import yt_dlp
from yt_dlp.utils import DownloadError
from flask import current_app

from database import check_db_video, check_pl2vid_info, insert_pl2vid_info, insert_not_found
from backend import save_video_from_ytdlp
from providers.base import set_status, update_status, del_status
from QueueObject import QueueObject
from queue_utils import enqueue


def dl_progress_hook(d):
    try:
        video_id = d.get('info_dict', {}).get('id', '') or ''
        if d["status"] == "downloading":
            update_status(video_id, {
                'progress': d['_percent_str'],
                'title': d.get('info_dict', {}).get('title', ""),
                'provider': 'youtube',
            })
        elif d["status"] == "finished":
            update_status(video_id, {'progress': '100%'})
    except Exception as e:
        current_app.logger.error("dl_progress_hook Failed: %s" % e)

def provider_domains():
    return ['youtube.com','youtu.be']

def parse_youtube_url(url):
    if not url:
        return (None, None)
    url = url.strip()
    parsed = urlparse(url)
    if parsed.hostname in ('youtu.be',):
        return ('video', parsed.path.lstrip('/'))
    if parsed.hostname in ('youtube.com', 'www.youtube.com'):
        path = parsed.path
        query = parse_qs(parsed.query)
        if path.startswith('/shorts/'):
            vid = path.split('/shorts/')[1].split('/')[0]
            return ('shorts', vid)
        if path == '/watch' and query.get('v'):
            return ('video', query['v'][0])
        if path == '/playlist':
            return ('playlist', query.get('list', [None])[0])
        if path.startswith('/channel/'):
            cid = path.split('/channel/')[1].split('/')[0]
            return ('channel', cid)
        if path.startswith('/c/') or path.startswith('/user/'):
            return ('custom', path.split('/')[2])
    if len(url) == 11 and not url.startswith('http'):
        return ('video', url)
    if not url.startswith('http'):
        if url.startswith('UC'):
            return ('channel', url)
        if url.startswith('PL') or url.startswith('LL') or url.startswith('UL'):
            return ('playlist', url)
    return (None, None)

def _download_attempt(url, ydl_opts, cookies_contents, logger, label=''):
    """Run a single yt-dlp download attempt. Returns True on success.
    Cleans up progress status and any temporary cookie StringIO it creates."""
    opts = dict(ydl_opts)
    if cookies_contents is not None:
        opts['cookiefile'] = StringIO(cookies_contents)
    videoID = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=False)
            channel_id = data['channel_id']
            videoID = data['id']
            videoTitle = data['title']
            set_status(videoID, {'progress': '0%', 'title': videoTitle, 'provider': 'youtube'})
            if label:
                logger.info("YouTube %s attempt: downloading %s" % (label, url))
            ydl.download(url)
        fpath = os.environ['VAULTTUBE_VAULTDIR'] + "/" + channel_id + "/" + videoID + ".mp4"
        save_video_from_ytdlp(videoID, data, fpath, current_app.logger)
        return True
    finally:
        if videoID is not None:
            del_status(videoID)
        cookiefile = opts.get('cookiefile')
        if cookiefile is not None and hasattr(cookiefile, 'close'):
            cookiefile.close()


def download_video(url, logger, cookies=None):
    vid = url.split('/watch?v=')[1] if '/watch?v=' in url else url.split('/shorts/')[1].split('/')[0] if '/shorts/' in url else None
    if not vid:
        if len(url) == 11 and not url.startswith('http'):
            vid = url
            url = "https://www.youtube.com/watch?v=%s" % vid
        else:
            raise ValueError("Could not extract video ID from URL: %s" % url)
    logger.debug("Starting Download: %s" % url)

    if cookies is None:
        cookie_path = os.environ.get('VAULTTUBE_YTCOOKIE')
        if not cookie_path:
            raise RuntimeError("VAULTTUBE_YTCOOKIE not configured; cannot download from YouTube")
        with open(cookie_path) as f:
            cookies_contents = f.read()
    elif isinstance(cookies, str):
        cookies_contents = cookies
    else:
        # file-like object passed by caller; read contents but don't consume it
        cookies_contents = cookies.read()
        if hasattr(cookies, 'seek'):
            cookies.seek(0)

    base_opts = {
        'outtmpl': os.environ['VAULTTUBE_VAULTDIR'] + "/%(channel_id)s/%(id)s.mp4",
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        "progress_hooks": [dl_progress_hook],
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'retry_sleep_functions': {'http': lambda n: 5 * n},
        'http_chunk_size': 10485760,
    }
    deno_path = os.environ.get('VAULTTUBE_DENOPATH')
    if deno_path:
        base_opts['js_runtimes'] = {'deno': {'path': deno_path}}

    proxy_url = os.environ.get('VAULTTUBE_PROXY')
    try:
        _download_attempt(url, base_opts, cookies_contents, logger, label='')
    except DownloadError as e:
        err_msg = str(e)
        if proxy_url and 'country' in err_msg.lower() and 'blocked' in err_msg.lower():
            logger.info("YouTube country block detected; retrying through proxy %s" % proxy_url)
            proxy_opts = dict(base_opts)
            proxy_opts['proxy'] = proxy_url
            _download_attempt(url, proxy_opts, cookies_contents, logger, label='proxy')
        elif any(p in err_msg.lower() for p in ('video unavailable', 'this video does not exist',
                                                  'has been removed', 'private video', 'not available')):
            insert_not_found(vid, logger)
            logger.error("Video not available, added to ignore list: %s" % vid)
            return False
        else:
            raise
    return True

def download_playlist(qo, logger):
    """Expand a playlist into individual video queue items, then remove the playlist entry."""
    try:
        playlist_url = qo.url if hasattr(qo, 'url') else qo
        if not playlist_url:
            logger.error("Playlist URL is empty")
            return False

        playlist_id = playlist_url
        if playlist_url.startswith('http'):
            parsed = urlparse(playlist_url)
            playlist_id = parse_qs(parsed.query).get('list', [None])[0]
        if not playlist_id:
            logger.error("Could not extract playlist ID from URL: %s" % playlist_url)
            return False

        key = os.environ['VAULTTUBE_YTKEY']
        all_video_ids = []
        page_token = None

        while True:
            curl = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId=%s&key=%s&maxResults=50" % (playlist_id, key)
            if page_token:
                curl += "&pageToken=%s" % page_token

            r = requests.get(curl, timeout=30)
            retj = r.json()
            r.close()

            if 'items' not in retj:
                logger.error("Invalid playlist response for ID: %s" % playlist_id)
                return False

            for vid in retj['items']:
                content_details = vid.get('contentDetails', {})
                vid_id = content_details.get('videoId')
                if not vid_id:
                    continue
                all_video_ids.append(vid_id)

            if 'nextPageToken' in retj:
                page_token = retj['nextPageToken']
            else:
                break

        logger.info("Playlist %s contains %d videos, adding to queue" % (playlist_id, len(all_video_ids)))

        for vid_id in all_video_ids:
            if check_db_video(vid_id, logger):
                logger.debug("Already exists in DB: %s" % vid_id)
                insert_pl2vid_info(playlist_id, vid_id, logger)
            else:
                logger.info("Queueing video from playlist: %s" % vid_id)
                url = "https://www.youtube.com/watch?v=%s" % vid_id
                qi = QueueObject(url, "", "youtube", 0, "")
                # enqueue() writes the queue table row (restart resumption +
                # duplicate-skip); a bare q.put() would not
                enqueue(qi, current_app.config['queue'], logger)
                insert_pl2vid_info(playlist_id, vid_id, logger)

        return True
    except Exception as e:
        logger.error("download_playlist failed: %s" % e)
        return False

def download_channel(qo, logger):
    try:
        channel_id = qo.url if hasattr(qo, 'url') else qo
        if not channel_id:
            logger.error("Channel ID is empty")
            return False
        if channel_id.startswith('http'):
            from urllib.parse import urlparse
            parsed = urlparse(channel_id)
            if parsed.path.startswith('/channel/'):
                channel_id = parsed.path.split('/channel/')[1].split('/')[0]
            else:
                logger.error("Cannot extract channel ID from URL: %s" % channel_id)
                return False
        if not channel_id.startswith('UC'):
            logger.error("Invalid channel ID format: %s" % channel_id)
            return False
        # The uploads playlist is always the channel ID with UC swapped for UU
        uploads_id = 'UU' + channel_id[2:]
        logger.info("Using uploads playlist %s for channel %s" % (uploads_id, channel_id))
        qo_playlist = QueueObject(uploads_id, "", "youtube", 0, "")
        return download_playlist(qo_playlist, logger)
    except Exception as e:
        logger.error("download_channel failed: %s" % e)
        return False

def download(qo, logger):
    """Accept a QueueObject or a plain URL string."""
    url = qo.url if hasattr(qo, 'url') else qo
    try:
        url_type, vid = parse_youtube_url(url)
        if url_type is None:
            raise ValueError("Could not determine URL type for: %s" % url)
        if url_type in ('video', 'shorts'):
            if url_type == 'shorts':
                url = "https://www.youtube.com/shorts/%s" % vid
            elif 'youtu.be' in url:
                url = "https://www.youtube.com/watch?v=%s" % vid
            return download_video(url, logger)
        elif url_type == 'playlist':
            return download_playlist(qo, logger)
        elif url_type == 'channel':
            return download_channel(qo, logger)
        elif url_type == 'custom':
            logger.error("Custom channel URLs (e.g. /c/ or /user/) require channel ID conversion. URL: %s" % url)
            return False
        else:
            raise ValueError("Unknown URL type: %s" % url_type)
    except Exception as e:
        logger.error("YT Single Download Failed: %s" % e)
        return False