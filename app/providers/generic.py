"""Fallback provider for any site yt-dlp has an extractor for (Vimeo, Twitch
VODs, Rumble, Odysee, PeerTube, Dailymotion, ...).

It claims no domains (``provider_domains()`` is empty); ``downloader.py``
asks ``claims(url)`` after the site-specific providers pass on a URL.

IDs are namespaced as ``{source}-{id}`` because ``videos.id`` is one global
key derived from the file name, and numeric channel directories are reserved
for Patreon campaigns. ``source`` is the site part of yt-dlp's extractor name
(``vimeo``, ``twitch``, ...).
"""

import datetime
import hashlib
import json
import logging
import os
import re
from urllib.parse import urlparse

import requests
import yt_dlp
from flask import current_app

from backend import save_uploaded_video_metadata
from database import check_db_video, get_connection
from providers.base import set_status, update_status, del_status
from QueueObject import QueueObject
from queue_utils import enqueue
from vault_paths import vault_relative_path

logger = logging.getLogger('generic')

# Sites with their own provider; never claim these here.
_OWN_PROVIDER_KEYS = ('Youtube', 'Patreon', 'Reddit', 'RedGifs')
_ID_MAX = 50       # videos.id is varchar(50)
_CHANNEL_MAX = 100  # channels.channelid is varchar(100)


def provider_domains():
    return []


def _allow_any():
    return os.environ.get('VAULTTUBE_GENERIC_ALLOW_ANY', 'false').lower() in (
        '1', 'true', 'yes', 'on')


def claims(url):
    """The extractor key that would handle ``url``, or None.

    yt-dlp's catch-all Generic extractor (scrape any page for media) is only
    used when VAULTTUBE_GENERIC_ALLOW_ANY is set.
    """
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        return None
    for ie in yt_dlp.extractor.gen_extractor_classes():
        key = ie.ie_key()
        if key == 'Generic':
            continue
        if ie.suitable(url):
            if key.startswith(_OWN_PROVIDER_KEYS) or not ie.working():
                return None
            return key
    return 'Generic' if _allow_any() else None


def source_name(info):
    """Site name from yt-dlp's extractor name: 'twitch:vod' -> 'twitch', so a
    site's VODs and clips share one source."""
    name = (info.get('extractor') or info.get('extractor_key') or 'generic')
    return re.sub(r'[^a-z0-9]', '', name.split(':')[0].lower()) or 'generic'


def namespaced_id(source, raw_id, max_len=_ID_MAX):
    """``{source}-{id}`` limited to [A-Za-z0-9_-] (no dots, which would break
    the vault scanner's ``basename.split('.')`` ID parsing). Over-long IDs
    keep a readable prefix plus a hash so they stay unique."""
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', str(raw_id)).strip('_') or 'unknown'
    full = '%s-%s' % (source, safe)
    if len(full) <= max_len:
        return full
    digest = hashlib.sha1(str(raw_id).encode('utf-8')).hexdigest()[:12]
    return full[:max_len - 13] + '-' + digest


def channel_ref(info):
    """(namespaced channel id, display name, channel URL) for an info dict."""
    source = source_name(info)
    raw = (info.get('channel_id') or info.get('uploader_id')
           or info.get('channel') or info.get('uploader'))
    if not raw:
        host = urlparse(info.get('webpage_url') or '').hostname or 'unknown'
        raw = host
    name = (info.get('channel') or info.get('uploader') or str(raw))[:100]
    url = info.get('channel_url') or info.get('uploader_url')
    return namespaced_id(source, raw, _CHANNEL_MAX), name, url


def _cookie_file(url):
    """Per-site Netscape cookies: $VAULTTUBE_COOKIES_DIR/<host>.txt, trying
    the full host then its parent domains (www.nebula.tv -> nebula.tv)."""
    base = os.environ.get('VAULTTUBE_COOKIES_DIR')
    host = urlparse(url).hostname or ''
    if not base or not host:
        return None
    parts = host.split('.')
    for i in range(len(parts) - 1):
        path = os.path.join(base, '.'.join(parts[i:]) + '.txt')
        if os.path.isfile(path):
            return path
    return None


def _base_opts(url):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
    }
    cookie = _cookie_file(url)
    if cookie:
        opts['cookiefile'] = cookie
    return opts


def _published_at(info):
    ts = info.get('timestamp') or info.get('release_timestamp')
    if ts:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None)
    for key in ('upload_date', 'release_date'):
        if info.get(key):
            try:
                return datetime.datetime.strptime(info[key], '%Y%m%d')
            except ValueError:
                pass
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _thumbnail(info):
    url = info.get('thumbnail')
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.warning("Thumbnail fetch failed for %s: %s", url, e)
        return None


def ensure_channel(channel_id, name, source, url=None):
    """Create the channels row for a generic source (no-op if present)."""
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT IGNORE INTO channels (channelid, channelname, json, source) "
            "VALUES (%s, %s, %s, %s)",
            (channel_id, name[:100], json.dumps({'url': url, 'source': source}), source))
        con.commit()
    finally:
        con.close()


def _expand_playlist(info, qo):
    """Queue each entry of a playlist/channel URL as its own item."""
    queued = 0
    for entry in info.get('entries') or []:
        if not entry:
            continue
        url = entry.get('url') or entry.get('webpage_url')
        if not url:
            continue
        if enqueue(QueueObject(url, qo.channel_id, 'generic', 0, ''),
                   current_app.config['queue']):
            queued += 1
    logger.info("Queued %d entries from %s", queued, qo.url)
    return True


def download(qo):
    url = qo.url if hasattr(qo, 'url') else qo
    opts = _base_opts(url)
    with yt_dlp.YoutubeDL(dict(opts, extract_flat='in_playlist')) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get('_type') in ('playlist', 'multi_video'):
        return _expand_playlist(info, qo)
    if info.get('_type') == 'url':
        # Flat entry pointing at the real page; resolve it fully
        url = info.get('url') or url
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    source = source_name(info)
    video_id = namespaced_id(source, info['id'])
    if check_db_video(video_id):
        logger.info("Already in vault: %s", video_id)
        return True
    channel_id, channel_name, channel_url = channel_ref(info)
    # Subscription scans pass the subscribed channel so its videos land under
    # it even when the site reports the uploader differently per video.
    if getattr(qo, 'channel_id', ''):
        channel_id = qo.channel_id
    else:
        ensure_channel(channel_id, channel_name, source, channel_url)

    target_dir = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id)
    os.makedirs(target_dir, exist_ok=True)
    title = info.get('title') or video_id

    def hook(d):
        try:
            if d['status'] == 'downloading':
                update_status(video_id, {'progress': d.get('_percent_str', ''),
                                         'title': title, 'type': source})
            elif d['status'] == 'finished':
                update_status(video_id, {'progress': '100%'})
        except Exception as e:
            logger.error("progress hook failed: %s", e)

    dl_opts = dict(
        opts,
        outtmpl=os.path.join(target_dir, video_id + '.%(ext)s'),
        format='bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo*+bestaudio/best',
        merge_output_format='mp4',
        progress_hooks=[hook],
    )
    set_status(video_id, {'progress': '0%', 'title': title, 'type': source})
    try:
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            result = ydl.process_ie_result(info, download=True)
        downloads = result.get('requested_downloads') or []
        filepath = downloads[0].get('filepath') if downloads else None
        if not filepath or not os.path.isfile(filepath):
            raise RuntimeError("yt-dlp reported no output file for %s" % url)
        chapters = [
            {'start': int(c.get('start_time') or 0), 'title': c.get('title') or ''}
            for c in (info.get('chapters') or [])
        ]
        save_uploaded_video_metadata(
            video_id, filepath, title, channel_id, _published_at(info),
            vault_relative_path(filepath), source,
            webpage_url=info.get('webpage_url') or url,
            description=info.get('description') or '',
            thumbnail_override=_thumbnail(info),
            extra={'extractor': info.get('extractor_key'), 'chapters': chapters},
        )
    finally:
        del_status(video_id)
    return True


# ---------------- Subscriptions ----------------

SCAN_LIMIT = 50


def _entry_source(entry):
    """Source name for a flat playlist entry (entries carry an ie_key such as
    'TwitchVod'; the full extractor name 'twitch:vod' gives 'twitch')."""
    key = entry.get('ie_key')
    if key:
        try:
            return source_name({'extractor': yt_dlp.extractor.get_info_extractor(key).IE_NAME})
        except Exception:
            pass
    return source_name(entry)


def list_entries(url, limit=SCAN_LIMIT):
    """(playlist info, [(namespaced video id, entry url), ...]) for a channel
    or playlist URL, newest first as the site lists them. ``limit`` None
    lists everything."""
    opts = dict(_base_opts(url), extract_flat='in_playlist')
    if limit:
        opts['playlistend'] = limit
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get('_type') not in ('playlist', 'multi_video'):
        raise ValueError("Not a channel or playlist URL: %s" % url)
    entries = []
    for entry in info.get('entries') or []:
        if not entry or not entry.get('id'):
            continue
        entry_url = entry.get('url') or entry.get('webpage_url')
        if entry_url:
            entries.append((namespaced_id(_entry_source(entry), entry['id']), entry_url))
    return info, entries


def subscribe(url, backfill=False):
    """Create (or re-enable) a URL subscription. Without ``backfill`` the
    current listing is recorded as already seen, so only uploads after now
    are downloaded; with it the first scan queues the whole back catalogue.
    Returns (channel id, channel name)."""
    info, entries = list_entries(url)
    source = source_name(info)
    raw = info.get('channel_id') or info.get('uploader_id') or info.get('id')
    channel_id = namespaced_id(source, raw or url, _CHANNEL_MAX)
    name = (info.get('channel') or info.get('uploader') or info.get('title') or channel_id)[:100]
    state = {'seen': [] if backfill else [vid for vid, _ in entries], 'backfill': bool(backfill)}
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO channels (channelid, channelname, json, subscribed, source, source_url, scan_state) "
            "VALUES (%s, %s, %s, 1, %s, %s, %s) ON DUPLICATE KEY UPDATE subscribed = 1, "
            "source = VALUES(source), source_url = VALUES(source_url), scan_state = VALUES(scan_state), "
            "last_scanned_at = NULL",
            (channel_id, name, json.dumps({'url': info.get('webpage_url') or url, 'source': source}),
             source, url, json.dumps(state)))
        con.commit()
    finally:
        con.close()
    return channel_id, name


def _known_ids(ids):
    if not ids:
        return set()
    con = get_connection(logger)
    try:
        cur = con.cursor()
        marks = ','.join(['%s'] * len(ids))
        cur.execute("SELECT id FROM videos WHERE id IN (%s) UNION SELECT id FROM IgnoreVid WHERE id IN (%s)"
                    % (marks, marks), tuple(ids) * 2)
        return {row[0] for row in cur.fetchall()}
    finally:
        con.close()


def scan_subscription(channel_id, url, scan_state, q):
    """List one URL subscription and queue entries not yet in the vault or
    seen on an earlier scan. Returns the number queued."""
    try:
        state = json.loads(scan_state) if scan_state else {}
    except ValueError:
        state = {}
    backfill = bool(state.get('backfill'))
    _, entries = list_entries(url, limit=None if backfill else SCAN_LIMIT)
    seen = set(state.get('seen') or [])
    known = _known_ids([vid for vid, _ in entries])
    queued = 0
    for vid, entry_url in entries:
        if vid in seen or vid in known:
            continue
        if enqueue(QueueObject(entry_url, channel_id, 'generic', 0, ''), q):
            queued += 1
    # Remember the newest listing; older IDs fall out of a 50-item listing
    # anyway, and anything downloaded is caught by the videos lookup.
    new_state = {'seen': [vid for vid, _ in entries[:SCAN_LIMIT]], 'backfill': False}
    con = get_connection(logger)
    try:
        cur = con.cursor()
        # Python's clock, not the DB's NOW(): the scanner compares against
        # datetime.now() and the two containers' time zones can differ
        cur.execute("UPDATE channels SET scan_state = %s, last_scanned_at = %s WHERE channelid = %s",
                    (json.dumps(new_state), datetime.datetime.now(), channel_id))
        con.commit()
    finally:
        con.close()
    logger.info("Scanned %s: %d new of %d listed", channel_id, queued, len(entries))
    return queued
