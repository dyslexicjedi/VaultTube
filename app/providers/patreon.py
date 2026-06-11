import requests
import os
import re
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from flask import current_app
import datetime
import json
import subprocess
import mariadb

from providers.base import set_status, update_status, del_status


def provider_domains():
    return ['patreon.com']

def _normalize_url(url):
    # yt-dlp's PatreonIE expects /posts/{slug} not /{creator}/posts/{slug}
    return re.sub(r'patreon\.com/[^/]+/(posts/)', r'patreon.com/\1', url)

def download(q,logger):
    try:
        url = _normalize_url(q.url)
        logger.debug("Starting Patreon Download: %s" % url)
        ydl_opts = {
            'cookiefile': os.environ['VAULTTUBE_PATREONCOOKIE'],
            'outtmpl': os.environ['VAULTTUBE_VAULTDIR']+"/%(channel_id)s/%(id)s.mp4",
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            "progress_hooks": [dl_progress_hook],
            'js_runtimes': {'deno': {'path': os.environ['VAULTTUBE_DENOPATH']}},
            # Impersonate a browser TLS fingerprint globally (not just for the
            # generic extractor) or Patreon's Cloudflare returns 403 on API calls
            'impersonate': ImpersonateTarget.from_str('chrome'),
            'socket_timeout': 30,        # seconds before a socket read times out
            'retries': 10,               # retry failed fragment/chunk downloads
            'fragment_retries': 10,      # retry failed fragments specifically
            'retry_sleep_functions': {'http': lambda n: 5 * n},  # back-off: 5s, 10s, 15s...
            'http_chunk_size': 10485760, # 10 MB chunks instead of the default large size
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False)
            videoid = data['id']
            channel_id = data['channel_id']
            title = data['title']
            PublishedAt = datetime.datetime.strptime(data['upload_date'], '%Y%m%d')
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

def patreon_screenshot(videoid,channelid,logger):
    try:
        #print(t)
        input_video = os.environ['VAULTTUBE_VAULTDIR']+"/"+channelid+"/"+str(videoid)+".mp4"
        output_img = videoid+".jpg"
        subprocess.call(['ffmpeg', '-i', input_video, '-ss', '00:00:01.000', '-vframes', '1', output_img])
        img = open(videoid+".jpg",'rb').read()
        con = mariadb.connect(host=os.environ['VAULTTUBE_DBHOST'],user=os.environ['VAULTTUBE_DBUSER'],password=os.environ['VAULTTUBE_DBPASS'],database=os.environ['VAULTTUBE_DBNAME'],autocommit=True,port=int(os.environ['VAULTTUBE_DBPORT']))
        cur = con.cursor()
        sql = "Insert Ignore into images(id,image) values(%s,%s)"
        cur.execute(sql,(videoid,img))
        con.commit()
        con.close()
        logger.debug("Screenshot saved")
        os.remove(videoid+".jpg")
        return True
    except Exception as e:
        return False

def patreon_db_info(videoid,channelid,PublishedAt,title,logger):
    try:
        t = json.loads(open('template','r').read())
        t['items'][0]['snippet']['title'] = title
        t['items'][0]['snippet']['channelId'] = channelid
        t['items'][0]['snippet']['channelTitle'] = ""
        t['items'][0]['snippet']['publishedAt'] = PublishedAt.strftime('%Y-%m-%d %H:%M:%S.%f')
        t['items'][0]['id'] = videoid
        source = "patreon"

        con = mariadb.connect(host=os.environ['VAULTTUBE_DBHOST'],user=os.environ['VAULTTUBE_DBUSER'],password=os.environ['VAULTTUBE_DBPASS'],database=os.environ['VAULTTUBE_DBNAME'],autocommit=True,port=int(os.environ['VAULTTUBE_DBPORT']))
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
        con.close()
        logger.debug("Metadata saved")
        return True
    except Exception as e:
        return False

def dl_progress_hook(d):
    try:
        video_id = d.get('info_dict', {}).get('id', None)
        if not video_id:
            video_id = globals().get('videoID', '')
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