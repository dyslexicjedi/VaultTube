from pytube import YouTube,Channel
from database import check_db_video
from backend import get_video
import time,os,requests,json
from flask import current_app

complete = False

def processComplete(stream,fpath):
    print("Complete: "+fpath)
    global complete
    complete=True

def processing(stream,chunk,bytes_remaining):
    total_size = stream.filesize
    bytes_downloaded = total_size - bytes_remaining
    pct_completed = bytes_downloaded / total_size * 100
    print(f"Status: {round(pct_completed, 2)} %")

def single_download(url,logger):
    try:
        global complete
        logger.debug("Starting Download: %s"%url)
        yt = YouTube(url,on_complete_callback=processComplete,on_progress_callback=processing)
        if(not os.path.exists(os.environ['VAULTTUBE_VAULTDIR']+"/"+yt.vid_info['videoDetails']['channelId'])):
            os.mkdir(os.environ['VAULTTUBE_VAULTDIR']+"/"+yt.vid_info['videoDetails']['channelId'])
        ys = yt.streams.filter(progressive=True,file_extension='mp4').order_by('resolution').desc().first().download(filename=yt.vid_info['videoDetails']['videoId']+".mp4",output_path=os.environ['VAULTTUBE_VAULTDIR']+"/"+yt.vid_info['videoDetails']['channelId']+"/")
        while not complete:
            time.sleep(5)
        complete = False
        get_video(os.environ['VAULTTUBE_VAULTDIR']+"/"+yt.vid_info['videoDetails']['channelId']+"/"+yt.vid_info['videoDetails']['videoId']+".mp4",logger)
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