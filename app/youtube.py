from database import check_db_video
from backend import get_video
import time,os,requests,json,traceback
from flask import current_app
from io import StringIO
import yt_dlp

def dl_progress_hook(d):
    try:
        global dl_progress
        global videoTitle
        global videoID
        if d["status"] == "downloading":
            dl_progress = d['_percent_str']
        if d["status"] == "finished":
            dl_progress = 0
            videoTitle = ""
            videoID = ""
    except Exception as e:
        current_app.logger("dl_progress_hook Failed: %s"%e)
        

def single_download(url,logger):
    try:
        global dl_progress
        global videoID
        global videoTitle
        dl_progress = 0
        logger.debug("Starting Download: %s"%url)
        #Set Cookie
        contents = open(os.environ['VAULTTUBE_YTCOOKIE']).read()
        cookies = StringIO(contents)
        ydl_opts = {
            'cookiefile': cookies,
            'outtmpl': os.environ['VAULTTUBE_VAULTDIR']+"/%(channel_id)s/%(id)s.mp4",
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            "progress_hooks": [dl_progress_hook],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url,download=False)
            channel_id = data['channel_id']
            videoID = data['id']
            videoTitle = data['title']
            if(not os.path.exists(os.environ['VAULTTUBE_VAULTDIR']+"/"+data['channel_id'])):
                os.mkdir(os.environ['VAULTTUBE_VAULTDIR']+"/"+data['channel_id'])
            ydl.download(url)
        get_video(os.environ['VAULTTUBE_VAULTDIR']+"/"+channel_id+"/"+videoID+".mp4",logger)
        return "True"
    except Exception as e:
        logger.error("YT Single Download Failed: %s"%e)

def get_channel_video_list(channelid,logger):
    try:
        curl = "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails&id=%s&key=%s"%(channelid[0],os.environ['VAULTTUBE_YTKEY'])
        r = requests.get(curl).json()
        pid = r['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        #logger.info(json.dumps(r, indent=4))

        curl = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId=%s&key=%s"%(pid,os.environ['VAULTTUBE_YTKEY'])
        r = requests.get(curl).json()
        logger.debug(json.dumps(r, indent=4))
        for vid in r['items']:
            id = vid['contentDetails']['videoId']
            if(check_db_video(id,logger)):
                logger.info("Already found: %s"%id)
            else:
                logger.info("Processing: %s"%id)
                current_app.config['queue'].put("https://www.youtube.com/watch?v=%s"%id)
    except Exception as e:
        logger.error("Scanning Channel Failed: %s"%e)

def get_dl_status():
    global dl_progress
    try:
        return dl_progress
    except NameError:
        return 0

def get_cur_videoID():
    global videoID
    try:
        return videoID
    except NameError:
        return ""

def get_cur_videoTitle():
    global videoTitle
    try:
        return videoTitle
    except NameError:
        return ""
