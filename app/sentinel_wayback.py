"""Best-effort Wayback recovery for explicit Sentinel archaeology records."""

import datetime
import html
import json
import logging
import os
import re
import tempfile
import requests

from backend import save_uploaded_video_metadata
from database import get_connection
from transcoder import get_codec_info


logger = logging.getLogger('sentinel_wayback')
CDX_URL = 'https://web.archive.org/cdx/search/cdx'
REPLAY_ROOT = 'https://web.archive.org/web/'
MEDIA_TEMPLATE = (
    'https://web.archive.org/web/2oe_/'
    'http://wayback-fakeurl.archive.org/yt/{video_id}'
)
USER_AGENT = 'VaultTube-Sentinel/1.0 (+https://github.com/dyslexicjedi/VaultTube)'
MAX_MEDIA_BYTES = int(os.environ.get(
    'VAULTTUBE_WAYBACK_MAX_BYTES', str(20 * 1024 * 1024 * 1024),
))


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


def _captures(video_id, session):
    captures = []
    for scheme in ('https', 'http'):
        original = '%s://www.youtube.com/watch?v=%s' % (scheme, video_id)
        response = session.get(CDX_URL, params={
            'url': original, 'output': 'json',
            'fl': 'timestamp,original,statuscode,mimetype',
            'filter': ['statuscode:200', 'mimetype:text/html'],
            'collapse': 'digest', 'limit': '50',
        }, timeout=30)
        response.raise_for_status()
        rows = response.json()
        if rows:
            captures.extend(dict(zip(rows[0], row)) for row in rows[1:])
    # Earliest captures are most likely to predate a deletion/private-state
    # page; later captures often contain only YouTube's generic error metadata.
    return sorted(captures, key=lambda item: item['timestamp'])


def search_wayback(channel_id, video_id, session=None):
    _candidate(channel_id, video_id)
    session = session or requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})
    captures = _captures(video_id, session)
    best = None
    for capture in captures:
        replay = REPLAY_ROOT + capture['timestamp'] + 'id_/' + capture['original']
        try:
            response = session.get(replay, timeout=30)
            response.raise_for_status()
            metadata = _extract_metadata(response.text)
            if any(metadata.values()):
                best = {'capture_url': replay, 'capture_timestamp': capture['timestamp'], 'metadata': metadata}
                break
        except requests.RequestException:
            continue

    media_url = MEDIA_TEMPLATE.format(video_id=video_id)
    media_found = False
    try:
        probe = session.get(media_url, headers={'Range': 'bytes=0-0'}, stream=True, timeout=30)
        content_type = (probe.headers.get('Content-Type') or '').lower()
        media_found = probe.status_code in (200, 206) and (
            content_type.startswith('video/') or content_type == 'application/octet-stream'
        )
        probe.close()
    except requests.RequestException:
        pass

    status = 'media' if media_found else 'metadata' if best else 'not_found'
    result = {
        'status': status,
        'capture_url': best['capture_url'] if best else None,
        'capture_timestamp': best['capture_timestamp'] if best else None,
        'media_url': media_url if media_found else None,
        'metadata': best['metadata'] if best else {},
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
    session = session or requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})
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
                response = session.get(thumbnail_url, timeout=30)
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
        response = session.get(item['media_url'], stream=True, timeout=(30, 120))
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
