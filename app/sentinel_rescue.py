"""Deterministic rescue previews and explicitly approved rescue sessions."""

import datetime
import json
import math
import os
import statistics
import uuid
from urllib.parse import parse_qs, urlparse

from database import get_connection
from QueueObject import QueueObject


DEFAULT_MAX_VIDEOS = 100
MAX_PREVIEW_VIDEOS = 1000
DEFAULT_DURATION_SECONDS = 20 * 60
DEFAULT_BITRATE_LOW_BYTES = 125000   # 1 Mbps
DEFAULT_BITRATE_HIGH_BYTES = 625000  # 5 Mbps
VALID_ORDERS = {'newest', 'oldest', 'inventory'}
RESCUE_QUEUE_PRIORITY = 100
MAX_RESCUE_DELAY_SECONDS = 3600
MAX_FAILURE_THRESHOLD = 20


def _iso(value):
    return value.isoformat() if value is not None else None


def _duration_seconds(value):
    if value is None:
        return 0
    text = str(value).strip()
    if not text or text == '0':
        return 0
    days = 0
    if 'day' in text:
        day_part, text = text.split(',', 1)
        try:
            days = int(day_part.split()[0])
        except (TypeError, ValueError, IndexError):
            return 0
        text = text.strip()
    try:
        parts = [float(part) for part in text.split(':')]
    except (TypeError, ValueError):
        return 0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return max(0, int(round(seconds + days * 86400)))


def _percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    index = int(round((len(values) - 1) * fraction))
    return values[max(0, min(len(values) - 1, index))]


def _video_id_from_url(url):
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.hostname == 'youtu.be':
        return parsed.path.lstrip('/').split('/')[0] or None
    if parsed.hostname in {'youtube.com', 'www.youtube.com'}:
        if parsed.path == '/watch':
            return parse_qs(parsed.query).get('v', [None])[0]
        if parsed.path.startswith('/shorts/'):
            return parsed.path.split('/shorts/', 1)[1].split('/')[0]
    return None


def _normalized_request(options):
    options = options or {}
    try:
        max_videos = int(options.get('max_videos', DEFAULT_MAX_VIDEOS))
    except (TypeError, ValueError):
        raise ValueError('max_videos must be an integer')
    if max_videos < 1 or max_videos > MAX_PREVIEW_VIDEOS:
        raise ValueError('max_videos must be between 1 and %s' % MAX_PREVIEW_VIDEOS)
    max_bytes = options.get('max_bytes')
    if max_bytes in (None, ''):
        max_bytes = None
    else:
        try:
            max_bytes = int(max_bytes)
        except (TypeError, ValueError):
            raise ValueError('max_bytes must be an integer')
        if max_bytes < 1:
            raise ValueError('max_bytes must be positive')
    order = str(options.get('order', 'newest')).lower()
    if order not in VALID_ORDERS:
        raise ValueError('order must be newest, oldest, or inventory')
    return {
        'max_videos': max_videos, 'max_bytes': max_bytes, 'order': order,
    }


def _estimator(cur, source_id):
    def samples(rows):
        durations = []
        bitrates = []
        for length, filesize in rows:
            seconds = _duration_seconds(length)
            if seconds > 0:
                durations.append(seconds)
                bitrates.append(int(filesize) / seconds)
        return durations, bitrates

    cur.execute(
        "SELECT `length`, filesize FROM videos WHERE channelId=%s "
        "AND filesize IS NOT NULL AND filesize>0",
        (source_id,),
    )
    source_rows = cur.fetchall()
    durations, bitrates = samples(source_rows)
    scope = 'source_archive'
    if len(durations) < 3:
        cur.execute(
            "SELECT `length`, filesize FROM videos "
            "WHERE filesize IS NOT NULL AND filesize>0"
        )
        durations, bitrates = samples(cur.fetchall())
        scope = 'vault_archive'
    typical_duration = int(statistics.median(durations)) \
        if durations else DEFAULT_DURATION_SECONDS
    bitrate_low = _percentile(bitrates, 0.25) or DEFAULT_BITRATE_LOW_BYTES
    bitrate_high = _percentile(bitrates, 0.75) or DEFAULT_BITRATE_HIGH_BYTES
    bitrate_low = max(1, int(round(bitrate_low)))
    bitrate_high = max(bitrate_low, int(round(bitrate_high)))
    return {
        'sample_scope': scope if durations else 'conservative_defaults',
        'sample_count': len(durations),
        'typical_duration_seconds': typical_duration,
        'bitrate_low_bytes_per_second': bitrate_low,
        'bitrate_high_bytes_per_second': bitrate_high,
        'confidence': 'medium' if len(durations) >= 10 else 'low',
    }


def _estimate_item(estimator):
    duration = estimator['typical_duration_seconds']
    low_duration = max(1, int(duration * 0.5))
    high_duration = max(low_duration, int(math.ceil(duration * 1.5)))
    return {
        'estimated_duration_seconds': duration,
        'estimated_bytes_low': low_duration * estimator['bitrate_low_bytes_per_second'],
        'estimated_bytes_high': high_duration * estimator['bitrate_high_bytes_per_second'],
        'estimate_basis': estimator['sample_scope'] + '_duration_and_bitrate_range',
    }


def _sort_candidates(candidates, order):
    if order == 'inventory':
        return sorted(candidates, key=lambda item: (item['position'], item['id']))
    dated = [item for item in candidates if item['remote_published_at']]
    undated = [item for item in candidates if not item['remote_published_at']]
    dated.sort(
        key=lambda item: (item['remote_published_at'], item['id']),
        reverse=(order == 'newest'),
    )
    undated.sort(
        key=lambda item: (item['position'], item['id']),
        reverse=(order == 'oldest'),
    )
    return dated + undated


def create_rescue_preview(source_type, source_id, options=None):
    """Persist and return an immutable preview. Never imports queue writers."""
    if source_type != 'channel':
        raise ValueError('Rescue preview currently supports channels')
    request_data = _normalized_request(options)
    con = get_connection()
    try:
        con.begin()
        cur = con.cursor()
        cur.execute(
            "SELECT id, completed_at, requests_made FROM sentinel_inventory_runs "
            "WHERE provider='youtube' AND source_type=%s AND source_id=%s "
            "AND status='complete' ORDER BY id DESC LIMIT 1",
            (source_type, source_id),
        )
        run = cur.fetchone()
        if run is None:
            raise ValueError('A complete inventory is required before previewing rescue')
        run_id, inventory_completed_at, inventory_requests = run
        cur.execute(
            "SELECT availability_state FROM sentinel_sources "
            "WHERE provider='youtube' AND source_type=%s AND source_id=%s",
            (source_type, source_id),
        )
        source_state_row = cur.fetchone()
        source_state = source_state_row[0] if source_state_row else 'unknown'

        cur.execute(
            "SELECT i.entity_id, i.position, i.remote_published_at, v.id, "
            "ignored.id, state.state FROM sentinel_inventory i "
            "LEFT JOIN videos v ON v.id=i.entity_id "
            "LEFT JOIN IgnoreVid ignored ON ignored.id=i.entity_id "
            "LEFT JOIN sentinel_video_state state ON state.video_id=i.entity_id "
            "WHERE i.scan_run_id=%s",
            (run_id,),
        )
        inventory_rows = cur.fetchall()
        cur.execute(
            "SELECT url FROM queue WHERE status IN ('pending','downloading')"
        )
        queued_ids = {
            video_id for (url,) in cur.fetchall()
            for video_id in [_video_id_from_url(url)] if video_id
        }

        exclusions = {
            'archived': 0, 'ignored': 0, 'unavailable': 0, 'queued': 0,
        }
        candidates = []
        for video_id, position, published_at, archived, ignored, state in inventory_rows:
            if archived:
                exclusions['archived'] += 1
            elif ignored:
                exclusions['ignored'] += 1
            elif state == 'unavailable':
                exclusions['unavailable'] += 1
            elif video_id in queued_ids:
                exclusions['queued'] += 1
            else:
                candidates.append({
                    'id': video_id, 'position': int(position or 0),
                    'remote_published_at': published_at,
                })

        blocked_reason = None
        if source_state == 'unavailable':
            blocked_reason = 'source_unavailable'
        elif source_state == 'suspected_unavailable':
            blocked_reason = 'source_awaiting_confirmation'

        estimator = _estimator(cur, source_id)
        ordered = _sort_candidates(candidates, request_data['order'])
        selected = []
        estimated_low = 0
        estimated_high = 0
        if blocked_reason is None:
            for candidate in ordered:
                if len(selected) >= request_data['max_videos']:
                    break
                estimate = _estimate_item(estimator)
                if (request_data['max_bytes'] is not None
                        and estimated_high + estimate['estimated_bytes_high']
                        > request_data['max_bytes']):
                    break
                selected.append({**candidate, **estimate})
                estimated_low += estimate['estimated_bytes_low']
                estimated_high += estimate['estimated_bytes_high']

        published = [
            item['remote_published_at'] for item in selected
            if item['remote_published_at'] is not None
        ]
        preview_id = str(uuid.uuid4())
        summary = {
            'id': preview_id, 'provider': 'youtube',
            'source_type': source_type, 'source_id': source_id,
            'inventory_run_id': int(run_id),
            'inventory_completed_at': _iso(inventory_completed_at),
            'source_availability_state': source_state,
            'blocked_reason': blocked_reason,
            'inventory_count': len(inventory_rows),
            'eligible_count': len(candidates),
            'selected_count': len(selected),
            'excluded': exclusions,
            'limited_count': max(0, len(candidates) - len(selected)),
            'estimated_bytes_low': int(estimated_low),
            'estimated_bytes_high': int(estimated_high),
            'estimate': estimator,
            'date_range': {
                'oldest': _iso(min(published)) if published else None,
                'newest': _iso(max(published)) if published else None,
                'known_for_selected': len(published),
            },
            'expected_provider_work': {
                'inventory_requests_already_used': int(inventory_requests),
                'future_metadata_requests': int(math.ceil(len(selected) / 50.0)),
                'future_download_jobs': len(selected),
            },
            'limits': request_data,
            'observation_only': True,
            'enqueued_count': 0,
        }
        cur.execute(
            "INSERT INTO sentinel_rescue_previews "
            "(id, provider, source_type, source_id, inventory_run_id, "
            "request_json, summary_json) VALUES(%s,'youtube',%s,%s,%s,%s,%s)",
            (
                preview_id, source_type, source_id, run_id,
                json.dumps(request_data, sort_keys=True),
                json.dumps(summary, sort_keys=True),
            ),
        )
        for rank, item in enumerate(selected, 1):
            cur.execute(
                "INSERT INTO sentinel_rescue_preview_items "
                "(preview_id, entity_id, rank_order, remote_published_at, "
                "estimated_duration_seconds, estimated_bytes_low, "
                "estimated_bytes_high, estimate_basis) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    preview_id, item['id'], rank,
                    item['remote_published_at'],
                    item['estimated_duration_seconds'],
                    item['estimated_bytes_low'], item['estimated_bytes_high'],
                    item['estimate_basis'],
                ),
            )
        con.commit()
        cur.close()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return get_rescue_preview(preview_id)


def get_rescue_preview(preview_id):
    con = get_connection()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT summary_json, created_at FROM sentinel_rescue_previews "
            "WHERE id=%s",
            (preview_id,),
        )
        row = cur.fetchone()
        if row is None:
            cur.close()
            return None
        summary = json.loads(row[0])
        summary['created_at'] = _iso(row[1])
        cur.execute(
            "SELECT entity_id, rank_order, remote_published_at, "
            "estimated_duration_seconds, estimated_bytes_low, "
            "estimated_bytes_high, estimate_basis "
            "FROM sentinel_rescue_preview_items WHERE preview_id=%s "
            "ORDER BY rank_order",
            (preview_id,),
        )
        summary['items'] = [
            {
                'id': item[0], 'rank': int(item[1]),
                'remote_published_at': _iso(item[2]),
                'estimated_duration_seconds': item[3],
                'estimated_bytes_low': int(item[4]),
                'estimated_bytes_high': int(item[5]),
                'estimate_basis': item[6],
                'url': 'https://www.youtube.com/watch?v=%s' % item[0],
            }
            for item in cur.fetchall()
        ]
        cur.close()
        return summary
    finally:
        con.close()


def _session_options(options):
    options = options or {}
    try:
        delay = int(options.get(
            'download_delay_seconds', os.environ.get('VAULTTUBE_DL_DELAY', 30),
        ))
        threshold = int(options.get('stop_failure_threshold', 3))
    except (TypeError, ValueError):
        raise ValueError('Rescue delay and failure threshold must be integers')
    if delay < 0 or delay > MAX_RESCUE_DELAY_SECONDS:
        raise ValueError('download_delay_seconds must be between 0 and 3600')
    if threshold < 1 or threshold > MAX_FAILURE_THRESHOLD:
        raise ValueError('stop_failure_threshold must be between 1 and 20')
    return {
        'download_delay_seconds': delay,
        'stop_failure_threshold': threshold,
    }


def _current_queue_video_ids(cur):
    cur.execute("SELECT url FROM queue WHERE status IN ('pending','downloading')")
    return {
        video_id for (url,) in cur.fetchall()
        for video_id in [_video_id_from_url(url)] if video_id
    }


def create_rescue_session(preview_id, options, download_queue):
    """Approve one immutable preview and persist its low-priority queue work."""
    session_options = _session_options(options)
    con = get_connection()
    queued_objects = []
    try:
        con.begin()
        cur = con.cursor()
        cur.execute(
            "SELECT provider, source_type, source_id, inventory_run_id, "
            "request_json, summary_json FROM sentinel_rescue_previews "
            "WHERE id=%s FOR UPDATE",
            (preview_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError('Rescue preview not found')
        provider, source_type, source_id, inventory_run_id, request_json, summary_json = row
        request_data = json.loads(request_json)
        summary = json.loads(summary_json)
        if summary.get('blocked_reason'):
            raise ValueError('Blocked rescue previews cannot be approved')
        if int(summary.get('selected_count', 0)) < 1:
            raise ValueError('Rescue preview has no selected items')
        cur.execute(
            "SELECT id FROM sentinel_inventory_runs WHERE provider=%s "
            "AND source_type=%s AND source_id=%s AND status='complete' "
            "ORDER BY id DESC LIMIT 1",
            (provider, source_type, source_id),
        )
        latest_run = cur.fetchone()
        if latest_run is None or int(latest_run[0]) != int(inventory_run_id):
            raise ValueError('Rescue preview is stale; build a new preview')
        cur.execute(
            "SELECT availability_state FROM sentinel_sources WHERE provider=%s "
            "AND source_type=%s AND source_id=%s",
            (provider, source_type, source_id),
        )
        source_row = cur.fetchone()
        if source_row and source_row[0] in {'unavailable', 'suspected_unavailable'}:
            raise ValueError('Source availability must be confirmed before rescue')
        cur.execute(
            "SELECT id FROM rescue_sessions WHERE preview_id=%s",
            (preview_id,),
        )
        existing = cur.fetchone()
        if existing:
            raise ValueError('Rescue preview was already approved as %s' % existing[0])

        session_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO rescue_sessions "
            "(id, preview_id, provider, source_type, source_id, status, "
            "max_videos, max_bytes, selected_count, estimated_bytes_low, "
            "estimated_bytes_high, download_delay_seconds, "
            "stop_failure_threshold) "
            "VALUES(%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,%s)",
            (
                session_id, preview_id, provider, source_type, source_id,
                request_data['max_videos'], request_data.get('max_bytes'),
                summary['selected_count'], summary['estimated_bytes_low'],
                summary['estimated_bytes_high'],
                session_options['download_delay_seconds'],
                session_options['stop_failure_threshold'],
            ),
        )
        cur.execute(
            "SELECT p.entity_id, p.rank_order, p.estimated_bytes_low, "
            "p.estimated_bytes_high, v.id, ignored.id, state.state "
            "FROM sentinel_rescue_preview_items p "
            "LEFT JOIN videos v ON v.id=p.entity_id "
            "LEFT JOIN IgnoreVid ignored ON ignored.id=p.entity_id "
            "LEFT JOIN sentinel_video_state state ON state.video_id=p.entity_id "
            "WHERE p.preview_id=%s ORDER BY p.rank_order",
            (preview_id,),
        )
        items = cur.fetchall()
        queued_ids = _current_queue_video_ids(cur)
        for (entity_id, rank, bytes_low, bytes_high, archived, ignored,
             availability_state) in items:
            skip_reason = None
            if archived:
                skip_reason = 'already_archived'
            elif ignored:
                skip_reason = 'ignored'
            elif availability_state == 'unavailable':
                skip_reason = 'unavailable'
            elif entity_id in queued_ids:
                skip_reason = 'already_queued'
            if skip_reason:
                cur.execute(
                    "INSERT INTO rescue_items "
                    "(session_id,entity_id,rank_order,status,estimated_bytes_low,"
                    "estimated_bytes_high,last_error) "
                    "VALUES(%s,%s,%s,'skipped',%s,%s,%s)",
                    (session_id, entity_id, rank, bytes_low, bytes_high, skip_reason),
                )
                continue
            url = 'https://www.youtube.com/watch?v=%s' % entity_id
            cur.execute(
                "INSERT INTO queue "
                "(url,source,channel_id,unsave,status,attempts,priority,origin,"
                "rescue_session_id,target_item_id) "
                "VALUES(%s,%s,%s,0,'pending',0,%s,'sentinel_rescue',%s,%s)",
                (url, provider, source_id, RESCUE_QUEUE_PRIORITY, session_id, entity_id),
            )
            queue_id = cur.lastrowid
            cur.execute(
                "INSERT INTO rescue_items "
                "(session_id,entity_id,rank_order,queue_id,status,"
                "estimated_bytes_low,estimated_bytes_high) "
                "VALUES(%s,%s,%s,%s,'queued',%s,%s)",
                (session_id, entity_id, rank, queue_id, bytes_low, bytes_high),
            )
            qo = QueueObject(
                url, source_id, provider, priority=RESCUE_QUEUE_PRIORITY,
                origin='sentinel_rescue', rescue_session_id=session_id,
                target_item_id=entity_id,
                download_delay_seconds=session_options['download_delay_seconds'],
            )
            qo.row_id = queue_id
            queued_objects.append(qo)
            queued_ids.add(entity_id)
        if not queued_objects:
            cur.execute(
                "UPDATE rescue_sessions SET status='completed', "
                "completed_at=NOW() WHERE id=%s",
                (session_id,),
            )
        con.commit()
        cur.close()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    for qo in queued_objects:
        download_queue.put(qo)
    return get_rescue_session(session_id)


def _session_counts(cur, session_id):
    cur.execute(
        "SELECT status, COUNT(*) FROM rescue_items WHERE session_id=%s "
        "GROUP BY status",
        (session_id,),
    )
    counts = {
        'queued': 0, 'downloading': 0, 'preserved': 0, 'failed': 0,
        'skipped': 0, 'cancelled': 0,
    }
    for status, count in cur.fetchall():
        counts[status] = int(count)
    return counts


def get_rescue_session(session_id):
    from providers.base import get_status_copy

    live_status = get_status_copy()
    con = get_connection()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id,preview_id,provider,source_type,source_id,status,"
            "max_videos,max_bytes,selected_count,estimated_bytes_low,"
            "estimated_bytes_high,download_delay_seconds,"
            "stop_failure_threshold,consecutive_blocking_failures,created_at,"
            "started_at,updated_at,completed_at FROM rescue_sessions WHERE id=%s",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            cur.close()
            return None
        keys = [
            'id', 'preview_id', 'provider', 'source_type', 'source_id', 'status',
            'max_videos', 'max_bytes', 'selected_count', 'estimated_bytes_low',
            'estimated_bytes_high', 'download_delay_seconds',
            'stop_failure_threshold', 'consecutive_blocking_failures',
            'created_at', 'started_at', 'updated_at', 'completed_at',
        ]
        session = dict(zip(keys, row))
        for key in ('created_at', 'started_at', 'updated_at', 'completed_at'):
            session[key] = _iso(session[key])
        for key in (
            'max_videos', 'selected_count', 'estimated_bytes_low',
            'estimated_bytes_high', 'download_delay_seconds',
            'stop_failure_threshold', 'consecutive_blocking_failures',
        ):
            session[key] = int(session[key])
        session['max_bytes'] = int(session['max_bytes']) \
            if session['max_bytes'] is not None else None
        session['counts'] = _session_counts(cur, session_id)
        cur.execute(
            "SELECT entity_id,rank_order,queue_id,status,estimated_bytes_low,"
            "estimated_bytes_high,last_error,updated_at FROM rescue_items "
            "WHERE session_id=%s ORDER BY rank_order",
            (session_id,),
        )
        session['items'] = []
        for item in cur.fetchall():
            row_data = {
                'id': item[0], 'rank': int(item[1]), 'queue_id': item[2],
                'status': item[3], 'estimated_bytes_low': int(item[4]),
                'estimated_bytes_high': int(item[5]), 'last_error': item[6],
                'updated_at': _iso(item[7]),
                'url': 'https://www.youtube.com/watch?v=%s' % item[0],
            }
            if item[0] in live_status:
                row_data['live_progress'] = dict(live_status[item[0]])
            session['items'].append(row_data)
        cur.close()
        return session
    finally:
        con.close()


def set_rescue_session_status(session_id, action):
    transitions = {
        'pause': ({'active'}, 'paused'),
        'resume': ({'paused'}, 'active'),
        'cancel': ({'active', 'paused'}, 'cancelled'),
    }
    if action not in transitions:
        raise ValueError('Unsupported rescue action')
    allowed, target = transitions[action]
    con = get_connection()
    try:
        con.begin()
        cur = con.cursor()
        cur.execute(
            "SELECT status FROM rescue_sessions WHERE id=%s FOR UPDATE",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError('Rescue session not found')
        if row[0] not in allowed:
            raise ValueError('Cannot %s a %s rescue' % (action, row[0]))
        if target == 'cancelled':
            cur.execute(
                "UPDATE queue SET status='cancelled' WHERE rescue_session_id=%s "
                "AND status='pending'",
                (session_id,),
            )
            cur.execute(
                "UPDATE rescue_items SET status='cancelled' "
                "WHERE session_id=%s AND status='queued'",
                (session_id,),
            )
            cur.execute(
                "UPDATE rescue_sessions SET status='cancelled',completed_at=NOW() "
                "WHERE id=%s",
                (session_id,),
            )
        else:
            cur.execute(
                "UPDATE rescue_sessions SET status=%s WHERE id=%s",
                (target, session_id),
            )
        con.commit()
        cur.close()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return get_rescue_session(session_id)


def rescue_queue_disposition(qo):
    """Return process, defer, or drop for a rescue QueueObject."""
    if getattr(qo, 'origin', None) != 'sentinel_rescue':
        return 'process'
    con = get_connection()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT status FROM rescue_sessions WHERE id=%s",
            (qo.rescue_session_id,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None or row[0] in {'cancelled', 'completed'}:
            return 'drop'
        if row[0] == 'paused':
            return 'defer'
        return 'process'
    finally:
        con.close()
