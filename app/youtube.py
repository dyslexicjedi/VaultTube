from database import check_db_video, check_pl2vid_info, insert_pl2vid_info, insert_not_found
from backend import get_video
import time, os, requests, json, traceback
from flask import current_app
from io import StringIO
import yt_dlp
from QueueObject import QueueObject


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
