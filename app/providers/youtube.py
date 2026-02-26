import requests
import os
from io import StringIO
import yt_dlp
from flask import current_app

from database import check_db_video, check_pl2vid_info, insert_pl2vid_info, insert_not_found
from backend import get_video
from providers.base import dl_status_map


def dl_progress_hook(d):
    try:
        video_id = d.get('info_dict', {}).get('id', None)
        if not video_id:
            # fallback to global, if needed
            video_id = globals().get('videoID', '')
        status_obj = dl_status_map.setdefault(video_id, {})
        if d["status"] == "downloading":
            status_obj['progress'] = d['_percent_str']
            status_obj['title'] = d.get('info_dict', {}).get('title', "")
            status_obj['type'] = 'youtube'
        elif d["status"] == "finished":
            status_obj['progress'] = "100%"
    except Exception as e:
        current_app.logger.error("dl_progress_hook Failed: %s" % e)

def provider_domains():
    return ['youtube.com','youtu.be']

def download(qo, logger):
    """Accept a QueueObject or a plain URL string."""
    url = qo.url if hasattr(qo, 'url') else qo
    try:
        vid = url.split('=')[1]
        r = requests.get("https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails&id=%s&key=%s" % (vid, os.environ['VAULTTUBE_YTKEY']))
        retj = r.json()
        r.close()
        if retj['pageInfo']['totalResults'] > 0:
            logger.debug("Starting Download: %s" % url)
            # Set Cookie
            f = open(os.environ['VAULTTUBE_YTCOOKIE'])
            contents = f.read()
            f.close()
            cookies = StringIO(contents)
            ydl_opts = {
                'cookiefile': cookies,
                'outtmpl': os.environ['VAULTTUBE_VAULTDIR'] + "/%(channel_id)s/%(id)s.mp4",
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                "progress_hooks": [dl_progress_hook],
                'js_runtimes': {'deno': {'path': '/root/.deno/bin/deno'}, 'node': {'path': '/usr/local/bin/node'}},
                'socket_timeout': 30,        # seconds before a socket read times out
                'retries': 10,               # retry failed fragment/chunk downloads
                'fragment_retries': 10,      # retry failed fragments specifically
                'retry_sleep_functions': {'http': lambda n: 5 * n},  # back-off: 5s, 10s, 15s...
                'http_chunk_size': 10485760, # 10 MB chunks instead of the default large size
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(url, download=False)
                channel_id = data['channel_id']
                videoID = data['id']
                videoTitle = data['title']
                if not os.path.exists(os.environ['VAULTTUBE_VAULTDIR'] + "/" + data['channel_id']):
                    os.mkdir(os.environ['VAULTTUBE_VAULTDIR'] + "/" + data['channel_id'])
                dl_status_map[videoID] = {'progress': '0%', 'title': videoTitle, 'type': 'youtube'}
                ydl.download(url)
            get_video(os.environ['VAULTTUBE_VAULTDIR'] + "/" + channel_id + "/" + videoID + ".mp4", current_app.logger)
            if videoID in dl_status_map:
                del dl_status_map[videoID]
            videoTitle = ""
            videoID = ""
            channel_id = ""
            cookies.close()
            return True
        else:
            insert_not_found(vid, logger)
            logger.error("Unable to download: %s, content was not found." % vid)
            return False
    except Exception as e:
        logger.error("YT Single Download Failed: %s" % e)