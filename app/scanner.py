import time
import os
import logging
from flask import current_app

from QueueObject import QueueObject
from database import get_active_subscriptions,get_active_playlist_subs
from database import check_db_video, check_pl2vid_info, insert_pl2vid_info
from database import cleanup_old_errors, cleanup_old_queue_rows
from providers.patreon import scan_campaign
from providers.youtube import (
    YouTubePlaylistTruncated,
    YouTubeQuotaExceeded,
    YouTubeRequestBudget,
    YouTubeScanBudgetExceeded,
)
from queue_utils import enqueue

logger = logging.getLogger('scanner')
_DEFAULT_YOUTUBE_SCAN_BUDGET = 250
_YOUTUBE_SCAN_CURSOR_CONFIG = 'VAULTTUBE_YT_SCAN_CURSOR'
_YOUTUBE_SCAN_CONTINUATIONS_CONFIG = 'VAULTTUBE_YT_SCAN_CONTINUATIONS'


def _youtube_scan_budget():
    raw_limit = os.environ.get(
        'VAULTTUBE_YT_SCAN_BUDGET', str(_DEFAULT_YOUTUBE_SCAN_BUDGET)
    )
    try:
        limit = int(raw_limit)
        if limit < 0:
            raise ValueError
    except ValueError:
        logger.warning(
            "Invalid VAULTTUBE_YT_SCAN_BUDGET=%r; using %d",
            raw_limit, _DEFAULT_YOUTUBE_SCAN_BUDGET,
        )
        limit = _DEFAULT_YOUTUBE_SCAN_BUDGET
    return YouTubeRequestBudget(limit)


def scan_once(app):
    """Run one subscription pass with one shared YouTube request budget."""
    request_budget = _youtube_scan_budget()
    youtube_open = True
    breaker_logged = False
    next_cursor = None

    def trip_youtube_breaker(exc):
        nonlocal youtube_open, breaker_logged
        youtube_open = False
        if not breaker_logged:
            logger.warning(
                "Stopping remaining YouTube API work for this scan pass: %s",
                exc,
            )
            breaker_logged = True

    channel_subscriptions = get_active_subscriptions()
    playlist_subscriptions = get_active_playlist_subs()

    # Patreon remains independent of the YouTube circuit breaker and runs
    # every pass regardless of the YouTube starting position or budget.
    for channel_id in channel_subscriptions:
        if not channel_id[0].isdigit():
            continue
        with app.app_context():
            logger.info("Scanning Patreon Campaign: %s" % channel_id[0])
            scan_campaign(channel_id[0])

    youtube_work = [
        ('channel', channel_id)
        for channel_id in channel_subscriptions
        if not channel_id[0].isdigit()
    ]
    youtube_work.extend(
        ('playlist', playlist_id)
        for playlist_id in playlist_subscriptions
    )
    valid_work_keys = {
        '%s:%s' % (work_type, item_id[0])
        for work_type, item_id in youtube_work
    }
    saved_continuations = app.config.get(
        _YOUTUBE_SCAN_CONTINUATIONS_CONFIG, {}
    )
    if not isinstance(saved_continuations, dict):
        saved_continuations = {}
    continuations = {
        key: dict(value)
        for key, value in saved_continuations.items()
        if key in valid_work_keys and isinstance(value, dict)
    }

    if youtube_work:
        start = int(app.config.get(_YOUTUBE_SCAN_CURSOR_CONFIG, 0))
        start %= len(youtube_work)
        ordered_work = list(enumerate(youtube_work))
        ordered_work = ordered_work[start:] + ordered_work[:start]
    else:
        start = 0
        ordered_work = []

    for original_index, (work_type, item_id) in ordered_work:
        if not youtube_open:
            break
        work_key = '%s:%s' % (work_type, item_id[0])
        continuation = continuations.get(work_key)
        with app.app_context():
            try:
                if work_type == 'channel':
                    channel_id = item_id
                    logger.info("Scanning Channel: %s" % (channel_id,))
                    if continuation:
                        work_result = get_channel_video_list(
                            channel_id, request_budget, continuation
                        )
                    else:
                        work_result = get_channel_video_list(
                            channel_id, request_budget
                        )
                else:
                    playlist_id = item_id
                    logger.info("Scanning Playlist: %s" % (playlist_id,))
                    if continuation:
                        work_result = get_playlist_video_list(
                            playlist_id, request_budget, continuation
                        )
                    else:
                        work_result = get_playlist_video_list(
                            playlist_id, request_budget
                        )
                if work_result is not False:
                    continuations.pop(work_key, None)
            except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded) as exc:
                if isinstance(exc, YouTubeScanBudgetExceeded):
                    page_token = getattr(exc, 'page_token', None)
                    requests_made = getattr(exc, 'requests_made', 0)
                    if page_token:
                        continuations[work_key] = {
                            'page_token': page_token,
                            'pages_fetched': getattr(
                                exc, 'pages_fetched', 0
                            ),
                            'items_seen': getattr(exc, 'items_seen', 0),
                            'requested_page_tokens': list(getattr(
                                exc, 'requested_page_tokens', ()
                            )),
                        }
                    if page_token and requests_made > 0:
                        next_cursor = (
                            original_index + 1
                        ) % len(youtube_work)
                    else:
                        next_cursor = original_index
                else:
                    # Quota failures may reflect a provider-wide outage. Keep
                    # the current item first. A mid-pagination quota response
                    # carries a safe retry token; a first-page/synthetic quota
                    # exception leaves any existing continuation untouched.
                    page_token = getattr(exc, 'page_token', None)
                    if page_token:
                        continuations[work_key] = {
                            'page_token': page_token,
                            'pages_fetched': getattr(
                                exc, 'pages_fetched', 0
                            ),
                            'items_seen': getattr(exc, 'items_seen', 0),
                            'requested_page_tokens': list(getattr(
                                exc, 'requested_page_tokens', ()
                            )),
                        }
                    next_cursor = original_index
                trip_youtube_breaker(exc)

    if youtube_work:
        if next_cursor is None:
            next_cursor = (start + 1) % len(youtube_work)
        app.config[_YOUTUBE_SCAN_CURSOR_CONFIG] = next_cursor
    else:
        app.config[_YOUTUBE_SCAN_CURSOR_CONFIG] = 0
    app.config[_YOUTUBE_SCAN_CONTINUATIONS_CONFIG] = continuations

    cleanup_old_errors(7)
    cleanup_old_queue_rows(7)

def start_scanner(app):
    logger.info("*Starting Scanner")
    while True:
        scan_once(app)
        time.sleep(3600)


def uploads_playlist_id(channel_id):
    """A channel's uploads playlist is its channel ID with UC swapped for UU,
    so no API call is needed to resolve it."""
    return 'UU' + channel_id[2:]


def iter_playlist_pages(playlist_id, request_budget=None, max_pages=100,
                        continuation=None):
    """Yield video-ID lists one playlistItems page (50 items, newest first)
    at a time. Stops on API errors after logging them.

    Thin seam over providers.youtube.iter_playlist_pages so tests can
    monkeypatch the scanner-facing surface without reaching into the
    provider module."""
    from providers.youtube import iter_playlist_pages as _iter
    kwargs = {}
    if request_budget is not None:
        kwargs['request_budget'] = request_budget
    if max_pages != 100:
        kwargs['max_pages'] = max_pages
    if continuation:
        kwargs.update({
            'start_page_token': continuation.get('page_token'),
            'pages_already_fetched': continuation.get('pages_fetched', 0),
            'items_already_seen': continuation.get('items_seen', 0),
            'requested_page_tokens': continuation.get(
                'requested_page_tokens', ()
            ),
        })
    for page in _iter(playlist_id, **kwargs):
        yield page


def get_channel_video_list(channelid, request_budget=None, continuation=None):
    """Enqueue new uploads from a subscribed channel. Pages are newest-first,
    so paging continues only while pages are entirely new: a subscription means
    "everything newer than what I have", not a deep-history backfill. Steady
    state is one API call per channel per scan."""
    try:
        playlist_id = uploads_playlist_id(channelid[0])
        if request_budget is None:
            pages = iter_playlist_pages(playlist_id)
        elif continuation:
            pages = iter_playlist_pages(
                playlist_id, request_budget, continuation=continuation
            )
        else:
            pages = iter_playlist_pages(playlist_id, request_budget)
        for page in pages:
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
        return True
    except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded):
        raise
    except YouTubePlaylistTruncated as e:
        logger.error(
            "Scanning Channel truncated on ChannelID %s: %s",
            channelid[0], e,
        )
        return True
    except Exception as e:
        logger.error("Scanning Channel Failed on ChannelID: %s: %s" % (channelid[0], e))
        return False


def get_playlist_video_list(playlistid, request_budget=None,
                            continuation=None):
    """Enqueue new videos from a subscribed playlist and keep pl2vid mappings
    current. Playlists are bounded and curated, so every page is walked (a
    playlist subscription means "archive this whole list")."""
    try:
        if request_budget is None:
            pages = iter_playlist_pages(playlistid[0])
        elif continuation:
            pages = iter_playlist_pages(
                playlistid[0], request_budget, continuation=continuation
            )
        else:
            pages = iter_playlist_pages(playlistid[0], request_budget)
        for page in pages:
            for vid in page:
                if check_db_video(vid):
                    if not check_pl2vid_info(playlistid[0], vid):
                        insert_pl2vid_info(playlistid[0], vid)
                else:
                    logger.info("Processing: %s" % vid)
                    qo = QueueObject("https://www.youtube.com/watch?v=%s" % vid, "", "youtube", 0, "")
                    enqueue(qo, current_app.config['queue'])
                    insert_pl2vid_info(playlistid[0], vid)
        return True
    except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded):
        raise
    except YouTubePlaylistTruncated as e:
        logger.error(
            "Playlist scan truncated on PlaylistID %s: %s",
            playlistid[0], e,
        )
        return True
    except Exception as e:
        logger.error("get_playlist_video_list failed: %s" % e)
        return False
