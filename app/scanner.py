import time
import requests
import os
import json
from flask import current_app

from QueueObject import QueueObject
from database import get_active_subscriptions,get_active_playlist_subs
from database import check_db_video, check_pl2vid_info, insert_pl2vid_info
from providers.patreon import scan_campaign
from queue_utils import enqueue

def start_scanner(logger,app):
    logger.info("*Starting Scanner")
    while True:
        #Process Channel Subs
        data = get_active_subscriptions(logger)
        for id in data:
            with app.app_context():
                # Patreon campaign IDs are numeric; YouTube channel IDs start with UC
                if id[0].isdigit():
                    logger.info("Scanning Patreon Campaign: %s"%id[0])
                    scan_campaign(id[0],logger)
                else:
                    logger.info("Scanning Channel: %s"%id)
                    get_channel_video_list(id,logger)
        #Process Playlist Subs
        data = get_active_playlist_subs(logger)
        for id in data:
            with app.app_context():
                logger.info("Scanning Playlist: %s"%id)
                get_playlist_video_list(id,logger)
        time.sleep(3600)


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
                enqueue(i, current_app.config['queue'], logger)
    except Exception as e:
        logger.error("Scanning Channel Failed on ChannelID: %s: %s" % (channelid[0], e))

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
                enqueue(i, current_app.config['queue'], logger)
                insert_pl2vid_info(playlistid[0], id, logger)
        if "nextPageToken" in retj:
            logger.info("Processing Next Page for %s" % playlistid)
            get_playlist_video_list(playlistid, logger, retj['nextPageToken'])
    except Exception as e:
        logger.error("get_playlist_video_list failed: %s" % e)
