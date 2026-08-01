import os
import logging
import re
from io import StringIO
from urllib.parse import urlparse, parse_qs
import requests
import yt_dlp
from yt_dlp.utils import DownloadError
from flask import current_app

from database import check_db_video, check_pl2vid_info, insert_pl2vid_info, insert_not_found
from backend import save_video_from_ytdlp
from providers.base import set_status, update_status, del_status, raise_alert
from QueueObject import QueueObject
from queue_utils import enqueue

logger = logging.getLogger('youtube')

# yt-dlp emits this exact phrase (case-insensitive) when the supplied
# YouTube cookies have been rotated/expired by Google's security measures.
_COOKIE_INVALID_MARKER = 'cookies are no longer valid'
_PLAYLIST_ITEMS_ENDPOINT = 'https://www.googleapis.com/youtube/v3/playlistItems'
_CHANNELS_ENDPOINT = 'https://www.googleapis.com/youtube/v3/channels'
_DEFAULT_PLAYLIST_MAX_PAGES = 100


class YouTubeQuotaExceeded(RuntimeError):
    """The YouTube Data API refused a request because its quota is exhausted."""

    page_token = None
    pages_fetched = 0
    items_seen = 0
    requested_page_tokens = ()


class YouTubeScanBudgetExceeded(RuntimeError):
    """A local scanner pass used all of its allowed YouTube API requests."""

    page_token = None
    requests_made = 0
    pages_fetched = 0
    items_seen = 0
    requested_page_tokens = ()


class YouTubePlaylistTruncated(RuntimeError):
    """Playlist enumeration stopped before a trustworthy completion signal."""


class YouTubeSourceCheckFailed(RuntimeError):
    """A source-level check failed without proving source unavailability."""

    failure_class = 'incomplete'


class YouTubeRequestBudget:
    """Simple shared request counter for one subscription-scan pass."""

    def __init__(self, limit):
        self.limit = max(0, int(limit))
        self.used = 0

    @property
    def remaining(self):
        return max(0, self.limit - self.used)

    def consume(self):
        if self.used >= self.limit:
            raise YouTubeScanBudgetExceeded(
                "YouTube scan request budget exhausted (%d requests)" % self.limit
            )
        self.used += 1


def _is_cookie_invalid_error(err_msg):
    return _COOKIE_INVALID_MARKER in str(err_msg).lower()


class _CookieWarningCapture(logging.Handler):
    """yt-dlp logger that captures the "cookies are no longer valid" warning.

    yt-dlp emits this as a WARNING (not an exception): the download often
    succeeds anyway via a fallback extractor (android vr player), so an
    error-based check would miss it. When yt-dlp's ``params['logger']`` is
    set, every ``report_warning()`` call routes through this handler's
    ``handle()`` so we can inspect the message and raise the alert.
    """

    def __init__(self):
        super().__init__()
        self.cookie_invalid = False

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if _COOKIE_INVALID_MARKER in msg.lower():
            self.cookie_invalid = True


def _check_cookie_warnings(capture):
    """Raise the youtube_cookies_invalid alert if the capture saw the warning."""
    if capture is not None and getattr(capture, 'cookie_invalid', False):
        raise_alert(
            'youtube_cookies_invalid',
            'YouTube cookies are invalid',
            'yt-dlp reports the YouTube account cookies in VAULTTUBE_YTCOOKIE '
            'are no longer valid. Re-export a fresh cookies.txt from a logged-in '
            'browser session and restart VaultTube.',
        )


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
        logger.error("dl_progress_hook Failed: %s" % e)

def provider_domains():
    return ['youtube.com','youtu.be']


def _youtube_api_error_values(payload):
    error = payload.get('error', {}) if isinstance(payload, dict) else {}
    values = []
    if isinstance(payload, dict):
        for key in ('reason', 'status'):
            if payload.get(key):
                values.append(payload[key])
    if isinstance(error, dict):
        for key in ('reason', 'status'):
            if error.get(key):
                values.append(error[key])
        for item in error.get('errors', []) or []:
            if not isinstance(item, dict):
                continue
            for key in ('reason', 'status'):
                if item.get(key):
                    values.append(item[key])
        for detail in error.get('details', []) or []:
            if not isinstance(detail, dict):
                continue
            for key in ('reason', 'status'):
                if detail.get(key):
                    values.append(detail[key])
            error_info = detail.get('errorInfo', {})
            if isinstance(error_info, dict):
                for key in ('reason', 'status'):
                    if error_info.get(key):
                        values.append(error_info[key])
    return values


def _is_youtube_quota_error(payload, http_status=None):
    if http_status == 429:
        return True
    quota_values = {
        'quotaexceeded',
        'dailylimitexceeded',
        'ratelimitexceeded',
        'userratelimitexceeded',
        'resourceexhausted',
    }
    normalized = {
        re.sub(r'[^a-z0-9]', '', str(value).lower())
        for value in _youtube_api_error_values(payload)
    }
    return bool(normalized & quota_values)


def check_channel_presence(channel_id, request_budget=None):
    """Return whether channels.list contains the ID after a successful check.

    Provider, auth, network, quota, and malformed responses raise instead of
    being interpreted as a missing channel.
    """
    if request_budget is not None:
        request_budget.consume()
    params = {
        'part': 'id', 'id': channel_id,
        'key': os.environ['VAULTTUBE_YTKEY'],
    }
    try:
        response = requests.get(_CHANNELS_ENDPOINT, params=params, timeout=30)
    except Exception as exc:
        error = YouTubeSourceCheckFailed(
            'YouTube channel check request failed: %s' % exc
        )
        error.failure_class = 'network'
        raise error
    status = getattr(response, 'status_code', None)
    try:
        payload = response.json()
    except Exception as exc:
        error = YouTubeSourceCheckFailed(
            'YouTube channel check returned invalid JSON: %s' % exc
        )
        error.failure_class = 'incomplete'
        raise error
    finally:
        response.close()
    if _is_youtube_quota_error(payload, status):
        raise YouTubeQuotaExceeded(
            'YouTube API quota exceeded while checking channel %s' % channel_id
        )
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        values = {
            re.sub(r'[^a-z0-9]', '', str(value).lower())
            for value in _youtube_api_error_values(payload)
        }
        error = YouTubeSourceCheckFailed(
            'YouTube channel check returned an invalid API response'
        )
        error.failure_class = (
            'auth' if values & {
                'keyinvalid', 'forbidden', 'unauthorized',
                'iprefererblocked', 'accessnotconfigured',
            } else 'incomplete'
        )
        raise error
    return any(
        isinstance(item, dict) and item.get('id') == channel_id
        for item in payload['items']
    )


def _attach_quota_resume_metadata(exc, page_token, requested_tokens,
                                  pages_fetched, items_seen):
    """Attach a safe retry point without marking the failed token successful."""
    exc.page_token = page_token
    exc.pages_fetched = pages_fetched
    exc.items_seen = items_seen
    exc.requested_page_tokens = tuple(
        token for token in requested_tokens
        if token is not None and token != page_token
    )
    return exc


def iter_playlist_pages(playlist_id, request_budget=None,
                        max_pages=_DEFAULT_PLAYLIST_MAX_PAGES,
                        start_page_token=None, pages_already_fetched=0,
                        items_already_seen=0, requested_page_tokens=None,
                        include_page_info=False):
    """Yield video-ID lists one playlistItems page (50 items, newest first)
    at a time via the YouTube Data API.

    Shared by scanner (channel/playlist subscriptions) and download_playlist
    (manual playlist expansion). Callers that only need "everything newer
    than what I have" break early; callers that need the whole list drain it.
    Paging is bounded and rejects token cycles so malformed API
    responses cannot make a scan run forever."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    page_token = start_page_token
    requested_tokens = set(requested_page_tokens or ())
    requests_made = 0
    pages_yielded = 0
    items_seen = max(0, int(items_already_seen))
    pages_already_fetched = max(0, int(pages_already_fetched))
    stop_reason = 'complete'
    abnormal = False
    suspended_at_yield = False

    try:
        while pages_already_fetched + pages_yielded < max_pages:
            if page_token in requested_tokens:
                abnormal = True
                stop_reason = "repeated page token %r" % page_token
                raise YouTubePlaylistTruncated(
                    "Playlist %s returned repeated page token %r" %
                    (playlist_id, page_token)
                )
            if request_budget is not None:
                try:
                    request_budget.consume()
                except YouTubeScanBudgetExceeded as exc:
                    exc.page_token = page_token
                    exc.requests_made = requests_made
                    exc.pages_fetched = (
                        pages_already_fetched + pages_yielded
                    )
                    exc.items_seen = items_seen
                    exc.requested_page_tokens = tuple(
                        token for token in requested_tokens
                        if token is not None
                    )
                    abnormal = True
                    stop_reason = 'local request budget exhausted'
                    raise
            requested_tokens.add(page_token)

            params = {
                'part': 'contentDetails',
                'playlistId': playlist_id,
                'maxResults': 50,
                'key': os.environ['VAULTTUBE_YTKEY'],
            }
            if page_token:
                params['pageToken'] = page_token

            try:
                r = requests.get(
                    _PLAYLIST_ITEMS_ENDPOINT, params=params, timeout=30
                )
                requests_made += 1
            except Exception:
                abnormal = True
                stop_reason = 'request failed'
                raise
            http_status = getattr(r, 'status_code', None)
            if http_status == 429:
                r.close()
                abnormal = True
                stop_reason = 'YouTube API quota exceeded (HTTP 429)'
                exc = YouTubeQuotaExceeded(
                    "YouTube API quota exceeded while scanning playlist %s "
                    "(HTTP 429)" % playlist_id
                )
                raise _attach_quota_resume_metadata(
                    exc, page_token, requested_tokens,
                    pages_already_fetched + pages_yielded, items_seen,
                )
            try:
                retj = r.json()
            except Exception:
                abnormal = True
                stop_reason = 'invalid JSON response'
                raise YouTubePlaylistTruncated(
                    "Playlist %s returned invalid JSON" % playlist_id
                )
            finally:
                r.close()

            if _is_youtube_quota_error(retj, http_status):
                abnormal = True
                stop_reason = 'YouTube API quota exceeded'
                exc = YouTubeQuotaExceeded(
                    "YouTube API quota exceeded while scanning playlist %s" %
                    playlist_id
                )
                raise _attach_quota_resume_metadata(
                    exc, page_token, requested_tokens,
                    pages_already_fetched + pages_yielded, items_seen,
                )
            if (not isinstance(retj, dict)
                    or not isinstance(retj.get('items'), list)):
                abnormal = True
                stop_reason = 'invalid API response'
                logger.error(
                    "Invalid playlistItems response for %s: %s",
                    playlist_id,
                    retj.get('error', retj)
                    if isinstance(retj, dict) else retj,
                )
                exc = YouTubePlaylistTruncated(
                    "Playlist %s returned an invalid API response" % playlist_id
                )
                reasons = {
                    re.sub(r'[^a-z0-9]', '', str(value).lower())
                    for value in _youtube_api_error_values(retj)
                } if isinstance(retj, dict) else set()
                exc.failure_class = (
                    'source' if reasons & {
                        'playlistnotfound', 'channelnotfound', 'notfound',
                    } else 'incomplete'
                )
                raise exc

            raw_items = retj['items']
            video_ids = [
                v['contentDetails']['videoId'] for v in raw_items
                if isinstance(v, dict)
                and v.get('contentDetails', {}).get('videoId')
            ]
            inventory_items = [
                {
                    'video_id': item['contentDetails']['videoId'],
                    'published_at': item['contentDetails'].get(
                        'videoPublishedAt'
                    ),
                }
                for item in raw_items
                if isinstance(item, dict)
                and item.get('contentDetails', {}).get('videoId')
            ]
            items_seen += len(raw_items)

            page_info = retj.get('pageInfo', {})
            total_results = (
                page_info.get('totalResults')
                if isinstance(page_info, dict) else None
            )
            next_token = retj.get('nextPageToken')
            reached_total = (
                isinstance(total_results, int) and items_seen >= total_results
            )
            complete = reached_total or not next_token

            pages_yielded += 1
            suspended_at_yield = True
            if include_page_info:
                yield {
                    'video_ids': video_ids,
                    'items': inventory_items,
                    'requested_page_token': page_token,
                    'next_page_token': None if complete else next_token,
                    'complete': complete,
                    'items_seen': items_seen,
                    'pages_fetched': pages_already_fetched + pages_yielded,
                }
            else:
                yield video_ids
            suspended_at_yield = False

            if reached_total:
                stop_reason = 'pageInfo.totalResults reached'
                return
            if not next_token:
                return
            if next_token in requested_tokens:
                abnormal = True
                stop_reason = "cyclic nextPageToken %r" % next_token
                raise YouTubePlaylistTruncated(
                    "Playlist %s returned cyclic nextPageToken %r" %
                    (playlist_id, next_token)
                )
            page_token = next_token

        abnormal = True
        stop_reason = 'hard page cap reached (%d)' % max_pages
        raise YouTubePlaylistTruncated(
            "Playlist %s reached the hard page cap (%d)" %
            (playlist_id, max_pages)
        )
    finally:
        if suspended_at_yield and not abnormal:
            stop_reason = 'consumer stopped early'
        log = logger.warning if abnormal else logger.info
        log(
            "playlistItems scan %s: requests=%d pages=%d items=%d stop=%s",
            playlist_id, requests_made,
            pages_already_fetched + pages_yielded,
            items_seen, stop_reason,
        )

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

def _download_attempt(url, ydl_opts, cookies_contents, label=''):
    """Run a single yt-dlp download attempt. Returns True on success.
    Cleans up progress status and any temporary cookie StringIO it creates."""
    opts = dict(ydl_opts)
    if cookies_contents is not None:
        opts['cookiefile'] = StringIO(cookies_contents)
    # Install a warning-capture logger so the "cookies are no longer valid"
    # warning is observable even when the download succeeds via a fallback.
    capture = _CookieWarningCapture()
    cookie_logger = logging.getLogger('yt_dlp')
    cookie_logger.addHandler(capture)
    opts['logger'] = cookie_logger
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
        save_video_from_ytdlp(videoID, data, fpath)
        return True
    except DownloadError as e:
        if _is_cookie_invalid_error(e):
            raise_alert(
                'youtube_cookies_invalid',
                'YouTube cookies are invalid',
                'yt-dlp reports the YouTube account cookies in VAULTTUBE_YTCOOKIE '
                'are no longer valid. Re-export a fresh cookies.txt from a logged-in '
                'browser session and restart VaultTube.',
            )
        raise
    except Exception as e:
        if _is_cookie_invalid_error(e):
            raise_alert(
                'youtube_cookies_invalid',
                'YouTube cookies are invalid',
                'yt-dlp reports the YouTube account cookies in VAULTTUBE_YTCOOKIE '
                'are no longer valid. Re-export a fresh cookies.txt from a logged-in '
                'browser session and restart VaultTube.',
            )
        raise
    finally:
        # Check the warning capture in finally so the alert fires even when
        # an exception (e.g. "Sign in to confirm you're not a bot") is raised
        # AFTER yt-dlp already emitted the cookie-invalid warning.
        _check_cookie_warnings(capture)
        if videoID is not None:
            del_status(videoID)
        cookiefile = opts.get('cookiefile')
        if cookiefile is not None and hasattr(cookiefile, 'close'):
            cookiefile.close()
        cookie_logger.removeHandler(capture)


def download_video(url, cookies=None):
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
        _download_attempt(url, base_opts, cookies_contents, label='')
    except DownloadError as e:
        err_msg = str(e)
        if proxy_url and 'country' in err_msg.lower() and 'blocked' in err_msg.lower():
            logger.info("YouTube country block detected; retrying through proxy %s" % proxy_url)
            proxy_opts = dict(base_opts)
            proxy_opts['proxy'] = proxy_url
            _download_attempt(url, proxy_opts, cookies_contents, label='proxy')
        elif any(p in err_msg.lower() for p in ('video unavailable', 'this video does not exist',
                                                  'has been removed', 'private video', 'not available')):
            insert_not_found(vid)
            logger.error("Video not available, added to ignore list: %s" % vid)
            return False
        else:
            raise
    return True

def download_playlist(qo):
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

        all_video_ids = []
        for page in iter_playlist_pages(playlist_id):
            all_video_ids.extend(page)

        logger.info("Playlist %s contains %d videos, adding to queue" % (playlist_id, len(all_video_ids)))

        for vid_id in all_video_ids:
            if check_db_video(vid_id):
                logger.debug("Already exists in DB: %s" % vid_id)
                insert_pl2vid_info(playlist_id, vid_id)
            else:
                logger.info("Queueing video from playlist: %s" % vid_id)
                url = "https://www.youtube.com/watch?v=%s" % vid_id
                qi = QueueObject(url, "", "youtube", 0, "")
                # enqueue() writes the queue table row (restart resumption +
                # duplicate-skip); a bare q.put() would not
                enqueue(qi, current_app.config['queue'])
                insert_pl2vid_info(playlist_id, vid_id)

        return True
    except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded):
        raise
    except YouTubePlaylistTruncated as e:
        logger.error("download_playlist failed: %s" % e)
        return False
    except Exception as e:
        logger.error("download_playlist failed: %s" % e)
        return False

def download_channel(qo):
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
        return download_playlist(qo_playlist)
    except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded):
        raise
    except Exception as e:
        logger.error("download_channel failed: %s" % e)
        return False

def download(qo):
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
            return download_video(url)
        elif url_type == 'playlist':
            return download_playlist(qo)
        elif url_type == 'channel':
            return download_channel(qo)
        elif url_type == 'custom':
            logger.error("Custom channel URLs (e.g. /c/ or /user/) require channel ID conversion. URL: %s" % url)
            return False
        else:
            raise ValueError("Unknown URL type: %s" % url_type)
    except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded):
        raise
    except Exception as e:
        logger.error("YT Single Download Failed: %s | url=%s", e, url, exc_info=True)
        return False
