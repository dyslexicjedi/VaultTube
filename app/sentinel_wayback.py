"""Best-effort Wayback recovery for explicit Sentinel archaeology records."""

import datetime
import html
import json
import logging
import os
import re
import tempfile
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend import save_uploaded_video_metadata
from database import get_connection
from transcoder import get_codec_info


logger = logging.getLogger('sentinel_wayback')
CDX_URL = 'https://web.archive.org/cdx/search/cdx'
TIMEMAP_CDX_URL = 'https://web.archive.org/web/timemap/cdx'
REPLAY_ROOT = 'https://web.archive.org/web/'
MEDIA_TEMPLATE = (
    'https://web.archive.org/web/2oe_/'
    'http://wayback-fakeurl.archive.org/yt/{video_id}'
)
USER_AGENT = 'VaultTube-Sentinel/1.0 (+https://github.com/dyslexicjedi/VaultTube)'
MAX_MEDIA_BYTES = int(os.environ.get(
    'VAULTTUBE_WAYBACK_MAX_BYTES', str(20 * 1024 * 1024 * 1024),
))
SEARCH_TIMEOUT = (
    float(os.environ.get('VAULTTUBE_WAYBACK_CONNECT_TIMEOUT', '10')),
    float(os.environ.get('VAULTTUBE_WAYBACK_READ_TIMEOUT', '90')),
)
MEDIA_TIMEOUT = (
    SEARCH_TIMEOUT[0],
    float(os.environ.get('VAULTTUBE_WAYBACK_MEDIA_TIMEOUT', '300')),
)


def _session(session=None):
    if session is not None:
        session.headers.update({'User-Agent': USER_AGENT})
        return session
    session = requests.Session()
    retries = Retry(
        total=1, connect=1, read=0, status=0, backoff_factor=1,
        allowed_methods=frozenset(('GET',)),
    )
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.headers.update({'User-Agent': USER_AGENT})
    return session


def _meta(html_text, key):
    patterns = [
        r'<meta[^>]+(?:property|name|itemprop)=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(key),
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name|itemprop)=["\']%s["\']' % re.escape(key),
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return None


def _extract_metadata(html_text):
    title = _meta(html_text, 'og:title') or _meta(html_text, 'title')
    if not title:
        match = re.search(r'<title[^>]*>(.*?)</title>', html_text, re.I | re.S)
        title = html.unescape(match.group(1)).strip() if match else None
    return {
        'title': title,
        'description': _meta(html_text, 'og:description') or _meta(html_text, 'description'),
        'thumbnail_url': _meta(html_text, 'og:image') or _meta(html_text, 'thumbnailUrl'),
        'published_at': _meta(html_text, 'datePublished') or _meta(html_text, 'uploadDate'),
    }


def _primary_cdx(original, session):
    response = session.get(CDX_URL, params={
        'url': original, 'output': 'json',
        'fl': 'timestamp,original,statuscode,mimetype',
        'filter': ['statuscode:200', 'mimetype:text/html'],
        'collapse': 'digest', 'limit': '50',
    }, timeout=SEARCH_TIMEOUT)
    response.raise_for_status()
    rows = response.json()
    if not rows:
        return []
    headings = rows[0]
    return [dict(zip(headings, row)) for row in rows[1:]]


def _timemap_cdx(original, session):
    response = session.get(TIMEMAP_CDX_URL, params={
        'url': original,
        'fl': 'timestamp,original,statuscode,mimetype',
        'filter': ['statuscode:200', 'mimetype:text/html'],
        'collapse': 'digest', 'limit': '50',
    }, timeout=SEARCH_TIMEOUT)
    response.raise_for_status()
    captures = []
    for line in response.text.splitlines():
        fields = line.split(None, 3)
        if len(fields) == 4:
            captures.append(dict(zip(
                ('timestamp', 'original', 'statuscode', 'mimetype'), fields,
            )))
    return captures


def _capture_index(original, video_id, scheme, session):
    try:
        return _primary_cdx(original, session), None
    except (requests.RequestException, ValueError) as primary_exc:
        primary_error = str(primary_exc)
        logger.warning(
            'Wayback legacy CDX lookup failed for %s/%s; trying Timemap: %s',
            video_id, scheme, primary_error,
        )
    try:
        captures = _timemap_cdx(original, session)
        logger.info(
            'Wayback Timemap fallback succeeded for %s/%s with %d captures',
            video_id, scheme, len(captures),
        )
        return captures, None
    except (requests.RequestException, ValueError) as fallback_exc:
        logger.warning(
            'Wayback Timemap lookup also failed for %s/%s: %s',
            video_id, scheme, fallback_exc,
        )
        return [], '%s URL index: legacy CDX failed (%s); Timemap failed (%s)' % (
            scheme.upper(), primary_error, fallback_exc,
        )


def _captures(video_id, session):
    captures = []
    successful_queries = 0
    errors = []
    for scheme in ('https', 'http'):
        original = '%s://www.youtube.com/watch?v=%s' % (scheme, video_id)
        rows, error = _capture_index(original, video_id, scheme, session)
        if error is None:
            successful_queries += 1
            captures.extend(rows)
        else:
            errors.append(error)
    # Earliest captures are most likely to predate a deletion/private-state
    # page; later captures often contain only YouTube's generic error metadata.
    return (
        sorted(captures, key=lambda item: item['timestamp']),
        successful_queries,
        errors,
    )


def search_wayback(channel_id, video_id, session=None):
    _candidate(channel_id, video_id)
    session = _session(session)
    captures, index_successes, warnings = _captures(video_id, session)
    best = None
    replay_successes = 0
    for capture in captures:
        replay = REPLAY_ROOT + capture['timestamp'] + 'id_/' + capture['original']
        try:
            response = session.get(replay, timeout=SEARCH_TIMEOUT)
            response.raise_for_status()
            replay_successes += 1
            metadata = _extract_metadata(response.text)
            if any(metadata.values()):
                best = {'capture_url': replay, 'capture_timestamp': capture['timestamp'], 'metadata': metadata}
                break
        except requests.RequestException as exc:
            warnings.append('Capture replay: %s' % exc)
            continue

    media_url = MEDIA_TEMPLATE.format(video_id=video_id)
    media_found = False
    try:
        probe = session.get(
            media_url, headers={'Range': 'bytes=0-0'}, stream=True,
            timeout=SEARCH_TIMEOUT,
        )
        content_type = (probe.headers.get('Content-Type') or '').lower()
        media_found = probe.status_code in (200, 206) and (
            content_type.startswith('video/') or content_type == 'application/octet-stream'
        )
        probe.close()
    except requests.RequestException as exc:
        warnings.append('Media probe: %s' % exc)

    if not best and not media_found:
        no_usable_index = index_successes == 0
        all_replays_failed = bool(captures) and replay_successes == 0
        media_failed = any(item.startswith('Media probe:') for item in warnings)
        if no_usable_index or all_replays_failed or media_failed:
            raise RuntimeError(
                'Wayback did not complete the lookup after retries. Please try again. '
                + (warnings[-1] if warnings else '')
            )

    status = 'media' if media_found else 'metadata' if best else 'not_found'
    result = {
        'status': status,
        'capture_url': best['capture_url'] if best else None,
        'capture_timestamp': best['capture_timestamp'] if best else None,
        'media_url': media_url if media_found else None,
        'metadata': best['metadata'] if best else {},
        'partial': bool(warnings),
        'warnings': warnings,
    }
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "UPDATE sentinel_archaeology_candidates SET wayback_status=%s, "
            "wayback_checked_at=NOW(), wayback_capture_url=%s, "
            "wayback_capture_timestamp=%s, wayback_media_url=%s, "
            "wayback_metadata_json=%s WHERE provider='youtube' "
            "AND source_type='channel' AND source_id=%s AND entity_id=%s",
            (status, result['capture_url'], result['capture_timestamp'],
             result['media_url'], json.dumps(result['metadata']), channel_id, video_id),
        )
        if cur.rowcount == 0:
            raise ValueError('Historical discovery not found for this channel')
        cur.close()
    finally:
        con.close()
    return result


def _candidate(channel_id, video_id):
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT wayback_status,wayback_capture_url,wayback_capture_timestamp,"
            "wayback_media_url,wayback_metadata_json FROM sentinel_archaeology_candidates "
            "WHERE provider='youtube' AND source_type='channel' AND source_id=%s AND entity_id=%s",
            (channel_id, video_id),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            raise ValueError('Historical discovery not found for this channel')
        return {'status': row[0], 'capture_url': row[1], 'timestamp': row[2],
                'media_url': row[3], 'metadata': json.loads(row[4] or '{}')}
    finally:
        con.close()


def import_wayback(channel_id, video_id, include_media=True, session=None):
    item = _candidate(channel_id, video_id)
    if item['status'] not in ('metadata', 'media'):
        raise ValueError('Search Wayback successfully before importing')
    session = _session(session)
    metadata = item['metadata']
    thumbnail = None
    if metadata.get('thumbnail_url'):
        thumbnail_urls = [metadata['thumbnail_url']]
        if item.get('timestamp'):
            thumbnail_urls.append(
                REPLAY_ROOT + item['timestamp'] + 'id_/' + metadata['thumbnail_url']
            )
        for thumbnail_url in thumbnail_urls:
            try:
                response = session.get(thumbnail_url, timeout=SEARCH_TIMEOUT)
                if response.ok and (response.headers.get('Content-Type') or '').startswith('image/'):
                    thumbnail = response.content
                    break
            except requests.RequestException:
                continue

    recovered = False
    if include_media and item['media_url']:
        channel_dir = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id)
        os.makedirs(channel_dir, exist_ok=True)
        final_path = os.path.join(channel_dir, video_id + '.mp4')
        if os.path.exists(final_path):
            raise ValueError('A vault file already exists for this video ID')
        response = session.get(item['media_url'], stream=True, timeout=MEDIA_TIMEOUT)
        response.raise_for_status()
        size = int(response.headers.get('Content-Length') or 0)
        if size > MAX_MEDIA_BYTES:
            response.close()
            raise ValueError('Archived video exceeds the Wayback import size limit')
        written = 0
        fd, temp_path = tempfile.mkstemp(prefix='.%s-wayback-' % video_id, dir=channel_dir)
        moved = False
        try:
            with os.fdopen(fd, 'wb') as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_MEDIA_BYTES:
                        raise ValueError('Archived video exceeds the Wayback import size limit')
                    handle.write(chunk)
            response.close()
            if written == 0:
                raise ValueError('Wayback returned an empty media file')
            codec_info = get_codec_info(temp_path)
            if not codec_info.get('vcodec'):
                raise ValueError('Wayback response is not a valid video file')
            published = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            if metadata.get('published_at'):
                try:
                    published = datetime.datetime.fromisoformat(metadata['published_at'].replace('Z', '+00:00')).replace(tzinfo=None)
                except ValueError:
                    pass
            os.replace(temp_path, final_path)
            moved = True
            save_uploaded_video_metadata(
                video_id, final_path, metadata.get('title') or video_id,
                channel_id, published, os.path.join(channel_id, video_id + '.mp4'),
                'youtube', webpage_url=item['capture_url'],
                description=metadata.get('description') or '', thumbnail_override=thumbnail,
            )
            recovered = True
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            if moved and os.path.exists(final_path):
                os.unlink(final_path)
            raise

    con = get_connection(logger)
    try:
        cur = con.cursor()
        if thumbnail:
            cur.execute("INSERT IGNORE INTO images(id,image) VALUES(%s,%s)", (video_id, thumbnail))
        cur.execute(
            "UPDATE sentinel_archaeology_candidates SET metadata_imported_at=NOW(), "
            "recovered_at=IF(%s,NOW(),recovered_at) WHERE provider='youtube' "
            "AND source_type='channel' AND source_id=%s AND entity_id=%s",
            (recovered, channel_id, video_id),
        )
        cur.close()
    finally:
        con.close()
    return {'metadata_imported': True, 'video_recovered': recovered,
            'title': metadata.get('title'), 'thumbnail_imported': bool(thumbnail)}
