import time
import logging
from flask import current_app

from QueueObject import QueueObject
from database import get_active_subscriptions,get_active_playlist_subs
from database import check_db_video, check_pl2vid_info, insert_pl2vid_info
from database import cleanup_old_errors, cleanup_old_queue_rows
from providers.patreon import scan_campaign
from queue_utils import enqueue

logger = logging.getLogger('scanner')

def start_scanner(app):
    logger.info("*Starting Scanner")
    while True:
        #Process Channel Subs
        data = get_active_subscriptions()
        for id in data:
            with app.app_context():
                # Patreon campaign IDs are numeric; YouTube channel IDs start with UC
                if id[0].isdigit():
                    logger.info("Scanning Patreon Campaign: %s"%id[0])
                    scan_campaign(id[0])
                else:
                    logger.info("Scanning Channel: %s"%id)
                    get_channel_video_list(id)
        #Process Playlist Subs
        data = get_active_playlist_subs()
        for id in data:
            with app.app_context():
                logger.info("Scanning Playlist: %s"%id)
                get_playlist_video_list(id)
        # Retention used to run only at startup; long-lived containers need it here
        cleanup_old_errors(7)
        cleanup_old_queue_rows(7)
        time.sleep(3600)


def uploads_playlist_id(channel_id):
    """A channel's uploads playlist is its channel ID with UC swapped for UU,
    so no API call is needed to resolve it."""
    return 'UU' + channel_id[2:]


def iter_playlist_pages(playlist_id):
    """Yield video-ID lists one playlistItems page (50 items, newest first)
    at a time. Stops on API errors after logging them.

    Thin seam over providers.youtube.iter_playlist_pages so tests can
    monkeypatch the scanner-facing surface without reaching into the
    provider module."""
    from providers.youtube import iter_playlist_pages as _iter
    for page in _iter(playlist_id):
        yield page


def get_channel_video_list(channelid):
    """Enqueue new uploads from a subscribed channel. Pages are newest-first,
    so paging continues only while pages are entirely new: a subscription means
    "everything newer than what I have", not a deep-history backfill. Steady
    state is one API call per channel per scan."""
    try:
        playlist_id = uploads_playlist_id(channelid[0])
        for page in iter_playlist_pages(playlist_id):
            known_hit = False
            for vid in page:
                if check_db_video(vid):
                    known_hit = True
                else:
                    logger.info("Processing: %s" % vid)
                    qo = QueueObject("https://www.youtube.com/watch?v=%s" % vid, "", "youtube", 0, "")
                    enqueue(qo, current_app.config['queue'])
            if known_hit:
                break
    except Exception as e:
        logger.error("Scanning Channel Failed on ChannelID: %s: %s" % (channelid[0], e))


def get_playlist_video_list(playlistid):
    """Enqueue new videos from a subscribed playlist and keep pl2vid mappings
    current. Playlists are bounded and curated, so every page is walked (a
    playlist subscription means "archive this whole list")."""
    try:
        for page in iter_playlist_pages(playlistid[0]):
            for vid in page:
                if check_db_video(vid):
                    if not check_pl2vid_info(playlistid[0], vid):
                        insert_pl2vid_info(playlistid[0], vid)
                else:
                    logger.info("Processing: %s" % vid)
                    qo = QueueObject("https://www.youtube.com/watch?v=%s" % vid, "", "youtube", 0, "")
                    enqueue(qo, current_app.config['queue'])
                    insert_pl2vid_info(playlistid[0], vid)
    except Exception as e:
        logger.error("get_playlist_video_list failed: %s" % e)
