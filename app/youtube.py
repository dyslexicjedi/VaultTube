from database import check_db_video, check_pl2vid_info, insert_pl2vid_info, insert_not_found
from backend import get_video
import time, os, requests, json, traceback
from flask import current_app
from io import StringIO
import yt_dlp
from QueueObject import QueueObject

# Use a dict instead of simple globals
# (In production, thread/process-safety might be necessary)
dl_status_map = {}  # videoID -> dict(progress, title, channel, type, etc.)

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
        
def single_download(url, logger):
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
                'js_runtimes': {'deno': {'path': '/root/.deno/bin/deno'}, 'node': {'path': '/root/.nvm/versions/node/v24.13.0/bin/node'}},
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
            return "True"
        else:
            insert_not_found(vid, logger)
            logger.error("Unable to download: %s, content was not found." % vid)
            return "False"
    except Exception as e:
        logger.error("YT Single Download Failed: %s" % e)

def get_channel_video_list(channelid, logger):
    try:
        curl = "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails&id=%s&key=%s" % (channelid[0], os.environ['VAULTTUBE_YTKEY'])
        r = requests.get(curl)
        retj = r.json()
        r.close()
        pid = retj['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        curl = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId=%s&key=%s" % (pid, os.environ['VAULTTUBE_YTKEY'])
        r = requests.get(curl)
        retj = r.json()
        r.close()
        logger.debug(json.dumps(retj, indent=4))
        for vid in retj['items']:
            id = vid['contentDetails']['videoId']
            if check_db_video(id, logger):
                logger.info("Already found: %s" % id)
            else:
                logger.info("Processing: %s" % id)
                url = "https://www.youtube.com/watch?v=%s" % id
                i = QueueObject(url, "", "youtube", 0, "")
                current_app.config['queue'].put(i) 
    except Exception as e:
        logger.error("Scanning Channel Failed on ChannelID: %s" % channelid[0])

def get_dl_status():
    global videoID
    try:
        if 'videoID' not in globals():
            return 0
        status = dl_status_map.get(videoID, {})
        return status.get('progress', 0)
    except Exception:
        return 0

def get_cur_videoID():
    try:
        if 'videoID' in globals():
            return videoID
        return ""
    except Exception:
        return ""

def get_cur_videoTitle():
    try:
        if 'videoID' in globals():
            return dl_status_map.get(videoID, {}).get('title', "")
        return ""
    except Exception:
        return ""

def get_playlist_info(playlistid, logger):
    try:
        curl = "https://www.googleapis.com/youtube/v3/playlists?part=snippet,contentDetails&id=%s&key=%s" % (playlistid, os.environ['VAULTTUBE_YTKEY'])
        r = requests.get(curl)
        retj = r.json()
        r.close()
        return retj
    except Exception as e:
        logger.error("Failed to get playlist info: %s" % e)

def get_playlist_video_list(playlistid, logger, pageToken='0'):
    try:
        if pageToken == '0':
            curl = "https://youtube.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId=%s&key=%s" % (playlistid[0], os.environ['VAULTTUBE_YTKEY'])
        else:
            curl = "https://youtube.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId=%s&key=%s&pageToken=%s" % (playlistid[0], os.environ['VAULTTUBE_YTKEY'], pageToken)
        r = requests.get(curl)
        retj = r.json()
        r.close()
        logger.debug(json.dumps(retj, indent=4))
        for vid in retj['items']:
            id = vid['contentDetails']['videoId']
            if check_db_video(id, logger):
                logger.info("Already found: %s" % id)
                if check_pl2vid_info(playlistid[0], id, logger):
                    logger.info("Found pl2vid info, nothing to do here.")
                else:
                    insert_pl2vid_info(playlistid[0], id, logger)
            else:
                logger.info("Processing: %s" % id)
                url = "https://www.youtube.com/watch?v=%s" % id
                i = QueueObject(url, "", "youtube", 0, "")
                current_app.config['queue'].put(i)
                insert_pl2vid_info(playlistid[0], id, logger)
        if "nextPageToken" in retj:
            logger.info("Processing Next Page for %s" % playlistid)
            get_playlist_video_list(playlistid, logger, retj['nextPageToken'])
    except Exception as e:
        logger.error("get_playlist_video_list failed: %s" % e)
