"""SponsorBlock skip segments for YouTube videos.

Segments are fetched lazily the first time a video is played (submitters
usually add them days after upload, so fetching at download time would mostly
find nothing) and cached in ``skip_segments``. The cache is kept for good: if
SponsorBlock later loses the data, or the video is gone from YouTube
(``videos.isDeleted``), the stored segments keep working.

Lookups use the hash-prefix endpoint, so the SponsorBlock server only sees
the first 4 hex characters of sha256(video_id), not which video is playing.

``parse_segments`` is a pure function with no Flask/DB dependencies so it can
be unit-tested directly, like ``chapters.parse_chapters``.
"""

import datetime
import hashlib
import json
import logging
import os

import requests

from database import get_connection

logger = logging.getLogger('sponsorblock')

DEFAULT_API_URL = 'https://sponsor.ajay.app'
CATEGORIES = [
    'sponsor', 'selfpromo', 'interaction', 'intro', 'outro', 'preview',
    'hook', 'music_offtopic', 'filler', 'exclusive_access', 'poi_highlight',
    'chapter',
]
ACTION_TYPES = ['skip', 'mute', 'full', 'poi', 'chapter']
FETCH_TIMEOUT_SECONDS = 5
# Re-check recently published videos for new submissions; older videos are
# treated as settled once fetched.
REFRESH_AFTER = datetime.timedelta(days=7)
RECENT_WINDOW = datetime.timedelta(days=90)
# After a failed request, wait before hitting the API again for that video.
ERROR_BACKOFF = datetime.timedelta(hours=1)


def enabled():
    return os.environ.get('VAULTTUBE_SPONSORBLOCK', 'true').lower() not in (
        '0', 'false', 'no', 'off')


def api_url():
    return os.environ.get('VAULTTUBE_SPONSORBLOCK_URL', DEFAULT_API_URL).rstrip('/')


def hash_prefix(video_id):
    return hashlib.sha256(video_id.encode('utf-8')).hexdigest()[:4]


def parse_segments(payload, video_id):
    """Pick ``video_id``'s segments out of a hash-prefix API response.

    The response lists every video sharing the prefix. Returns a list of
    ``{uuid, category, action_type, start, end, votes, description}`` sorted
    by start; malformed entries are dropped.
    """
    if not isinstance(payload, list):
        return []
    segments = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get('videoID') != video_id:
            continue
        for seg in entry.get('segments') or []:
            try:
                start, end = (float(x) for x in seg['segment'])
                uuid = str(seg['UUID'])
                category = str(seg['category'])
                action_type = str(seg.get('actionType') or 'skip')
            except (KeyError, TypeError, ValueError):
                continue
            if end < start or start < 0:
                continue
            segments.append({
                'uuid': uuid,
                'category': category,
                'action_type': action_type,
                'start': start,
                'end': end,
                'votes': int(seg.get('votes') or 0),
                'description': (seg.get('description') or '')[:500],
            })
    segments.sort(key=lambda s: (s['start'], s['end']))
    return segments


def chapters_from_segments(segments):
    """SponsorBlock community chapters in the ``/api/chapters`` shape."""
    return [
        {'start': int(s['start']), 'title': s['description']}
        for s in segments if s['action_type'] == 'chapter'
    ]


def fetch_remote(video_id):
    """Query the API. Returns a segment list ([] when the video has none),
    or None when the request failed."""
    try:
        resp = requests.get(
            '%s/api/skipSegments/%s' % (api_url(), hash_prefix(video_id)),
            params={
                'categories': json.dumps(CATEGORIES),
                'actionTypes': json.dumps(ACTION_TYPES),
            },
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return parse_segments(resp.json(), video_id)
    except Exception as e:
        logger.warning("SponsorBlock fetch failed for %s: %s", video_id, e)
        return None


def _needs_fetch(fetch_row, published_at, is_deleted, now):
    if fetch_row is None:
        return True
    status, fetched_at, attempted_at = fetch_row
    if is_deleted and fetched_at is not None:
        return False
    if status == 'error' and attempted_at and now - attempted_at < ERROR_BACKOFF:
        return False
    if fetched_at is None:
        return True
    recent = published_at is not None and now - published_at < RECENT_WINDOW
    return recent and now - fetched_at > REFRESH_AFTER


def _load_cached(cur, video_id):
    cur.execute(
        "SELECT uuid, category, action_type, start_s, end_s, votes, description "
        "FROM skip_segments WHERE video_id = %s ORDER BY start_s, end_s",
        (video_id,))
    return [
        {'uuid': r[0], 'category': r[1], 'action_type': r[2], 'start': r[3],
         'end': r[4], 'votes': r[5], 'description': r[6] or ''}
        for r in cur.fetchall()
    ]


def cached_segments(video_id):
    """Stored segments only; never touches the network."""
    con = get_connection(logger)
    try:
        cur = con.cursor()
        return _load_cached(cur, video_id)
    finally:
        con.close()


def get_segments(video_id):
    """Segments for a vault video, fetching or refreshing them when due.

    Non-YouTube and unknown videos return []. A failed request falls back to
    whatever is cached.
    """
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT source, PublishedAt, isDeleted FROM videos WHERE id = %s",
            (video_id,))
        video = cur.fetchone()
        if not video or video[0] != 'youtube':
            return []
        cur.execute(
            "SELECT status, fetched_at, attempted_at FROM sponsorblock_fetch "
            "WHERE video_id = %s", (video_id,))
        fetch_row = cur.fetchone()
        now = datetime.datetime.now()
        if not enabled() or not _needs_fetch(fetch_row, video[1], video[2], now):
            return _load_cached(cur, video_id)

        segments = fetch_remote(video_id)
        if segments is None:
            cur.execute(
                "INSERT INTO sponsorblock_fetch (video_id, status, attempted_at) "
                "VALUES (%s, 'error', %s) ON DUPLICATE KEY UPDATE "
                "status = 'error', attempted_at = VALUES(attempted_at)",
                (video_id, now))
            con.commit()
            return _load_cached(cur, video_id)

        # Replace the cache only on a successful fetch. An empty answer for a
        # video that used to have segments keeps the old ones: SponsorBlock
        # dropping data is exactly what the archive should outlive.
        if segments:
            cur.execute("DELETE FROM skip_segments WHERE video_id = %s", (video_id,))
            cur.executemany(
                "INSERT INTO skip_segments (video_id, uuid, category, action_type, "
                "start_s, end_s, votes, description) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(video_id, s['uuid'], s['category'], s['action_type'],
                  s['start'], s['end'], s['votes'], s['description'])
                 for s in segments])
        cur.execute(
            "INSERT INTO sponsorblock_fetch (video_id, status, fetched_at, attempted_at) "
            "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE status = VALUES(status), "
            "fetched_at = VALUES(fetched_at), attempted_at = VALUES(attempted_at)",
            (video_id, 'ok' if segments else 'none', now, now))
        con.commit()
        return _load_cached(cur, video_id)
    finally:
        con.close()
