"""Read-only comparison of manually exported historical YouTube ID lists."""

import re

from database import get_connection


MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_IDS = 5000
_VIDEO_ID = r'[A-Za-z0-9_-]{11}'
_BARE_ID_RE = re.compile(r'^%s$' % _VIDEO_ID)
_URL_ID_RE = re.compile(
    r'(?:[?&]v=|youtu\.be/|youtube\.com/(?:shorts|live|embed)/)'
    r'(%s)(?![A-Za-z0-9_-])' % _VIDEO_ID,
    re.IGNORECASE,
)


def parse_historical_export(raw):
    """Extract ordered unique YouTube IDs from a UTF-8 Filmot text export."""
    if not isinstance(raw, bytes):
        raise ValueError('Import must be uploaded as a file')
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError('Import file must be 1 MB or smaller')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('Import file must be UTF-8 text')

    video_ids = []
    seen = set()
    duplicate_count = 0
    invalid_lines = []
    for line_number, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line:
            continue
        matches = _URL_ID_RE.findall(line)
        if not matches and _BARE_ID_RE.fullmatch(line):
            matches = [line]
        if not matches:
            invalid_lines.append({
                'line': line_number,
                'value': line[:200],
            })
            continue
        for video_id in matches:
            if video_id in seen:
                duplicate_count += 1
                continue
            seen.add(video_id)
            video_ids.append(video_id)
            if len(video_ids) > MAX_IMPORT_IDS:
                raise ValueError(
                    'Import contains more than %s unique video IDs'
                    % MAX_IMPORT_IDS
                )
    if not video_ids:
        raise ValueError('Import contains no valid YouTube video IDs')
    return {
        'video_ids': video_ids,
        'duplicates_removed': duplicate_count,
        'invalid_lines': invalid_lines,
    }


def _chunks(values, size=500):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def compare_historical_ids(channel_id, parsed):
    """Classify IDs against VaultTube without modifying any database state."""
    video_ids = parsed['video_ids']
    archived_channels = {}
    ignored = set()
    inventory_channels = {}
    con = get_connection()
    if con is None:
        raise RuntimeError('Unable to get database connection for import comparison')
    try:
        cur = con.cursor()
        for chunk in _chunks(video_ids):
            placeholders = ','.join(['%s'] * len(chunk))
            cur.execute(
                'SELECT id, channelId FROM videos WHERE id IN (%s)'
                % placeholders,
                tuple(chunk),
            )
            archived_channels.update(cur.fetchall())
            cur.execute(
                'SELECT id FROM IgnoreVid WHERE id IN (%s)' % placeholders,
                tuple(chunk),
            )
            ignored.update(row[0] for row in cur.fetchall())
            cur.execute(
                "SELECT i.entity_id, i.source_id FROM sentinel_inventory i "
                "JOIN sentinel_inventory_runs r ON r.id=i.scan_run_id "
                "WHERE i.provider='youtube' AND i.source_type='channel' "
                "AND r.status='complete' AND i.entity_id IN (%s)"
                % placeholders,
                tuple(chunk),
            )
            for video_id, source_id in cur.fetchall():
                inventory_channels.setdefault(video_id, set()).add(source_id)
        cur.close()
    finally:
        con.close()

    result = {
        'archived': [],
        'known_unarchived': [],
        'ignored': [],
        'other_channel': [],
        'missing': [],
    }
    for video_id in video_ids:
        if video_id in archived_channels:
            category = 'archived' if archived_channels[video_id] == channel_id \
                else 'other_channel'
        elif video_id in ignored:
            category = 'ignored'
        elif channel_id in inventory_channels.get(video_id, set()):
            category = 'known_unarchived'
        elif inventory_channels.get(video_id):
            category = 'other_channel'
        else:
            category = 'missing'
        result[category].append(video_id)

    result.update({
        'total': len(video_ids),
        'duplicates_removed': parsed['duplicates_removed'],
        'invalid_lines': parsed['invalid_lines'],
    })
    return result
