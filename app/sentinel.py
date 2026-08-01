"""Durable source-availability observations for VaultTube Sentinel.

Phase 1 deliberately covers only videos already present in the vault. Later
phases add remote inventories, risk scoring, and rescue planning on top of this
event ledger.
"""

import json
import logging

from database import get_connection


logger = logging.getLogger('sentinel')

AVAILABLE = 'available'
SUSPECTED_UNAVAILABLE = 'suspected_unavailable'
UNAVAILABLE = 'unavailable'


def start_scan_run(provider='youtube', scan_type='availability',
                   source_type='video_batch', source_id='archived_videos'):
    """Create a durable scan run and return its row ID.

    Callers must not update availability if this fails: a state transition
    without its evidence-quality record would make the event ledger incomplete.
    """
    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel scan')
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO sentinel_scan_runs "
            "(provider, scan_type, source_type, source_id, status) "
            "VALUES(%s, %s, %s, %s, 'running')",
            (provider, scan_type, source_type, source_id),
        )
        scan_id = cur.lastrowid
        con.commit()
        cur.close()
        return scan_id
    finally:
        con.close()


def finish_scan_run(scan_id, status, items_seen=0, requests_made=0,
                    error_message=None):
    """Finish a scan as complete, partial, or failed."""
    if status not in {'complete', 'partial', 'failed'}:
        raise ValueError('Invalid Sentinel scan status: %s' % status)
    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel scan')
    try:
        cur = con.cursor()
        cur.execute(
            "UPDATE sentinel_scan_runs SET status=%s, completed_at=NOW(), "
            "items_seen=%s, requests_made=%s, error_message=%s WHERE id=%s",
            (status, items_seen, requests_made, error_message, scan_id),
        )
        con.commit()
        cur.close()
    finally:
        con.close()


def _event_dedupe_key(event_type, video_id, scan_id):
    return '%s:youtube:video:%s:scan:%s' % (event_type, video_id, scan_id)


def _insert_event(cur, video_id, event_type, from_state, to_state,
                  scan_id, evidence):
    cur.execute(
        "INSERT IGNORE INTO sentinel_events "
        "(provider, entity_type, entity_id, event_type, from_state, to_state, "
        "scan_run_id, evidence_json, dedupe_key) "
        "VALUES('youtube', 'video', %s, %s, %s, %s, %s, %s, %s)",
        (
            video_id,
            event_type,
            from_state,
            to_state,
            scan_id,
            json.dumps(evidence or {}, sort_keys=True),
            _event_dedupe_key(event_type, video_id, scan_id),
        ),
    )


def record_video_observation(video_id, available, scan_id, evidence=None):
    """Apply one successful provider observation.

    Returns the emitted event type, or ``None`` when no confirmed transition
    occurred. Negative results require two different scan IDs. Positive results
    immediately clear suspicion and restore a previously unavailable video.
    """
    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel observation')
    try:
        # get_connection uses autocommit for the rest of VaultTube. An explicit
        # transaction keeps the state row locked until its event and legacy
        # videos flag have been updated atomically.
        con.begin()
        cur = con.cursor()
        cur.execute(
            "SELECT state, consecutive_negative_checks, last_scan_id "
            "FROM sentinel_video_state WHERE video_id=%s FOR UPDATE",
            (video_id,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT isDeleted FROM videos WHERE id=%s", (video_id,))
            video_row = cur.fetchone()
            if video_row is None:
                raise ValueError('Cannot observe unknown video: %s' % video_id)
            state = UNAVAILABLE if video_row[0] else AVAILABLE
            negative_checks = 2 if state == UNAVAILABLE else 0
            last_scan_id = None
            cur.execute(
                "INSERT INTO sentinel_video_state "
                "(video_id, provider, state, consecutive_negative_checks) "
                "VALUES(%s, 'youtube', %s, %s)",
                (video_id, state, negative_checks),
            )
        else:
            state, negative_checks, last_scan_id = row

        previous_scan_complete = True
        if last_scan_id is not None and last_scan_id != scan_id:
            cur.execute(
                "SELECT status FROM sentinel_scan_runs WHERE id=%s",
                (last_scan_id,),
            )
            previous_scan = cur.fetchone()
            previous_scan_complete = bool(
                previous_scan and previous_scan[0] == 'complete'
            )

        # Duplicate observations inside one scan are idempotent and cannot be
        # used to manufacture the second independent negative check.
        if last_scan_id == scan_id:
            cur.close()
            con.commit()
            return None

        event_type = None
        now_state = state
        now_negative_checks = negative_checks

        if available:
            now_state = AVAILABLE
            now_negative_checks = 0
            if state == UNAVAILABLE:
                event_type = 'source_restored'
                _insert_event(
                    cur, video_id, event_type, state, now_state, scan_id,
                    evidence,
                )
            cur.execute(
                "UPDATE videos SET isDeleted=0, lastScanned=NOW() WHERE id=%s",
                (video_id,),
            )
        elif state == UNAVAILABLE:
            now_negative_checks = max(2, negative_checks + 1)
            cur.execute(
                "UPDATE videos SET isDeleted=1, lastScanned=NOW() WHERE id=%s",
                (video_id,),
            )
        elif state == SUSPECTED_UNAVAILABLE and previous_scan_complete:
            now_state = UNAVAILABLE
            now_negative_checks = negative_checks + 1
            event_type = 'source_unavailable'
            _insert_event(
                cur, video_id, event_type, state, now_state, scan_id,
                evidence,
            )
            cur.execute(
                "UPDATE videos SET isDeleted=1, lastScanned=NOW() WHERE id=%s",
                (video_id,),
            )
        else:
            now_state = SUSPECTED_UNAVAILABLE
            now_negative_checks = 1
            # A suspected result is not exposed through the legacy flag, but
            # lastScanned still reflects the successful provider observation.
            cur.execute(
                "UPDATE videos SET lastScanned=NOW() WHERE id=%s",
                (video_id,),
            )

        cur.execute(
            "UPDATE sentinel_video_state SET state=%s, "
            "consecutive_negative_checks=%s, last_scan_id=%s, "
            "last_checked_at=NOW(), "
            "last_positive_at=CASE WHEN %s=1 THEN NOW() ELSE last_positive_at END, "
            "last_negative_at=CASE WHEN %s=0 THEN NOW() ELSE last_negative_at END "
            "WHERE video_id=%s",
            (
                now_state,
                now_negative_checks,
                scan_id,
                1 if available else 0,
                1 if available else 0,
                video_id,
            ),
        )
        con.commit()
        cur.close()
        return event_type
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()


def _iso(value):
    return value.isoformat() if value is not None else None


def _event_row(row):
    evidence = {}
    if row[7]:
        try:
            evidence = json.loads(row[7])
        except (TypeError, ValueError):
            evidence = {'raw': row[7]}
    return {
        'id': int(row[0]),
        'entity_id': row[1],
        'event_type': row[2],
        'from_state': row[3],
        'to_state': row[4],
        'observed_at': _iso(row[5]),
        'scan_run_id': row[6],
        'evidence': evidence,
        'title': row[8],
        'channel_id': row[9],
        'channel_name': row[10] or row[9],
    }


def get_summary():
    """Observatory totals derived strictly from Phase 1 local observations."""
    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel summary')
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(s.state='unavailable'),0), "
            "COALESCE(SUM(s.state='suspected_unavailable'),0) "
            "FROM sentinel_video_state s JOIN videos v ON v.id=s.video_id"
        )
        monitored, unavailable, suspected = cur.fetchone()
        cur.execute(
            "SELECT "
            "COALESCE(SUM(event_type='source_unavailable' AND "
            "observed_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)),0), "
            "COALESCE(SUM(event_type='source_restored' AND "
            "observed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)),0), "
            "COALESCE(SUM(event_type='imported_existing_state'),0), COUNT(*) "
            "FROM sentinel_events"
        )
        unavailable_7d, restored_30d, imported, total_events = cur.fetchone()
        cur.execute(
            "SELECT id, status, started_at, completed_at, items_seen, "
            "requests_made, error_message FROM sentinel_scan_runs "
            "ORDER BY id DESC LIMIT 1"
        )
        scan = cur.fetchone()
        cur.close()
        return {
            'monitored_videos': int(monitored),
            'preserved_unavailable': int(unavailable),
            'suspected_unavailable': int(suspected),
            'newly_unavailable_7d': int(unavailable_7d),
            'restored_30d': int(restored_30d),
            'imported_existing_state': int(imported),
            'total_events': int(total_events),
            'last_scan': None if scan is None else {
                'id': int(scan[0]),
                'status': scan[1],
                'started_at': _iso(scan[2]),
                'completed_at': _iso(scan[3]),
                'items_seen': int(scan[4]),
                'requests_made': int(scan[5]),
                'error_message': scan[6],
            },
        }
    finally:
        con.close()


EVENT_TYPES = {
    'source_unavailable', 'source_restored', 'imported_existing_state',
}


def get_events(limit=50, offset=0, event_type=None, channel_id=None):
    """Return a paged event feed enriched with current video/channel labels."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    conditions = ["e.entity_type='video'"]
    params = []
    if event_type:
        if event_type not in EVENT_TYPES:
            raise ValueError('Unknown Sentinel event type')
        conditions.append('e.event_type=%s')
        params.append(event_type)
    if channel_id:
        conditions.append('v.channelId=%s')
        params.append(channel_id)
    where = ' AND '.join(conditions)

    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel events')
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM sentinel_events e "
            "LEFT JOIN videos v ON v.id=e.entity_id WHERE " + where,
            tuple(params),
        )
        total = int(cur.fetchone()[0])
        cur.execute(
            "SELECT e.id, e.entity_id, e.event_type, e.from_state, e.to_state, "
            "e.observed_at, e.scan_run_id, e.evidence_json, v.title, "
            "v.channelId, c.channelname FROM sentinel_events e "
            "LEFT JOIN videos v ON v.id=e.entity_id "
            "LEFT JOIN channels c ON c.channelid=v.channelId WHERE " + where +
            " ORDER BY e.observed_at DESC, e.id DESC LIMIT %s OFFSET %s",
            tuple(params + [limit, offset]),
        )
        items = [_event_row(row) for row in cur.fetchall()]
        cur.close()
        return {
            'items': items,
            'total': total,
            'limit': limit,
            'offset': offset,
        }
    finally:
        con.close()


def _source_status(unavailable, suspected, newly_unavailable):
    if newly_unavailable or suspected:
        return 'attention'
    if unavailable:
        return 'historical_loss'
    return 'stable'


def get_sources(limit=50, offset=0):
    """Creators ordered by recent confirmed/suspected source changes."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel sources')
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(DISTINCT channelId) FROM videos WHERE channelId IS NOT NULL")
        total = int(cur.fetchone()[0])
        cur.execute(
            "SELECT v.channelId, COALESCE(c.channelname, v.channelId), "
            "COUNT(DISTINCT v.id), "
            "COALESCE(SUM(s.state='unavailable'),0), "
            "COALESCE(SUM(s.state='suspected_unavailable'),0), "
            "COALESCE(ev.event_count,0), ev.latest_event_at, "
            "COALESCE(ev.newly_unavailable_30d,0), "
            "COALESCE(ev.restored_30d,0), MAX(s.last_checked_at) "
            "FROM videos v LEFT JOIN channels c ON c.channelid=v.channelId "
            "LEFT JOIN sentinel_video_state s ON s.video_id=v.id "
            "LEFT JOIN ("
            "  SELECT v2.channelId, COUNT(*) event_count, MAX(e.observed_at) latest_event_at, "
            "  SUM(e.event_type='source_unavailable' AND e.observed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)) newly_unavailable_30d, "
            "  SUM(e.event_type='source_restored' AND e.observed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)) restored_30d "
            "  FROM sentinel_events e JOIN videos v2 ON v2.id=e.entity_id "
            "  GROUP BY v2.channelId"
            ") ev ON ev.channelId=v.channelId "
            "WHERE v.channelId IS NOT NULL "
            "GROUP BY v.channelId, c.channelname, ev.event_count, "
            "ev.latest_event_at, ev.newly_unavailable_30d, ev.restored_30d "
            "ORDER BY newly_unavailable_30d DESC, "
            "SUM(s.state='suspected_unavailable') DESC, "
            "SUM(s.state='unavailable') DESC, ev.latest_event_at DESC, "
            "COUNT(DISTINCT v.id) DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        items = []
        for row in cur.fetchall():
            unavailable = int(row[3])
            suspected = int(row[4])
            newly_unavailable = int(row[7])
            items.append({
                'channel_id': row[0],
                'channel_name': row[1],
                'video_count': int(row[2]),
                'unavailable': unavailable,
                'suspected': suspected,
                'event_count': int(row[5]),
                'latest_event_at': _iso(row[6]),
                'newly_unavailable_30d': newly_unavailable,
                'restored_30d': int(row[8]),
                'last_checked_at': _iso(row[9]),
                'status': _source_status(
                    unavailable, suspected, newly_unavailable,
                ),
            })
        cur.close()
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}
    finally:
        con.close()


def get_source_detail(channel_id, event_limit=20):
    """Current Phase 1 state and recent event history for one creator."""
    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel source')
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT COALESCE(c.channelname, %s), COUNT(v.id), "
            "COALESCE(SUM(s.state='unavailable'),0), "
            "COALESCE(SUM(s.state='suspected_unavailable'),0), "
            "MAX(s.last_checked_at) FROM videos v "
            "LEFT JOIN channels c ON c.channelid=v.channelId "
            "LEFT JOIN sentinel_video_state s ON s.video_id=v.id "
            "WHERE v.channelId=%s GROUP BY c.channelname",
            (channel_id, channel_id),
        )
        row = cur.fetchone()
        if row is None:
            cur.close()
            return None
        cur.execute(
            "SELECT v.id, v.title, v.PublishedAt, s.state, s.last_negative_at "
            "FROM videos v JOIN sentinel_video_state s ON s.video_id=v.id "
            "WHERE v.channelId=%s AND s.state IN "
            "('unavailable','suspected_unavailable') "
            "ORDER BY s.last_negative_at DESC, v.PublishedAt DESC LIMIT 20",
            (channel_id,),
        )
        affected = [
            {
                'id': item[0], 'title': item[1],
                'published_at': _iso(item[2]), 'state': item[3],
                'last_negative_at': _iso(item[4]),
            }
            for item in cur.fetchall()
        ]
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(e.event_type='source_unavailable' "
            "AND e.observed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)),0) "
            "FROM sentinel_events e JOIN videos v ON v.id=e.entity_id "
            "WHERE v.channelId=%s",
            (channel_id,),
        )
        event_count, newly_unavailable = cur.fetchone()
        cur.close()
    finally:
        con.close()

    events = get_events(limit=event_limit, channel_id=channel_id)
    unavailable = int(row[2])
    suspected = int(row[3])
    return {
        'channel_id': channel_id,
        'channel_name': row[0],
        'video_count': int(row[1]),
        'unavailable': unavailable,
        'suspected': suspected,
        'last_checked_at': _iso(row[4]),
        'event_count': int(event_count),
        'status': _source_status(
            unavailable, suspected, int(newly_unavailable),
        ),
        'affected_videos': affected,
        'events': events['items'],
    }


def export_sentinel_data():
    """Serializable Phase 1 ledger for the JSON backup export."""
    con = get_connection(logger)
    if con is None:
        raise RuntimeError('Unable to get database connection for Sentinel export')
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, provider, scan_type, source_type, source_id, status, "
            "started_at, completed_at, items_seen, requests_made, error_message "
            "FROM sentinel_scan_runs ORDER BY id"
        )
        scans = [
            {
                'id': int(r[0]), 'provider': r[1], 'scan_type': r[2],
                'source_type': r[3], 'source_id': r[4], 'status': r[5],
                'started_at': _iso(r[6]), 'completed_at': _iso(r[7]),
                'items_seen': int(r[8]), 'requests_made': int(r[9]),
                'error_message': r[10],
            }
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT video_id, provider, state, consecutive_negative_checks, "
            "last_scan_id, last_checked_at, last_positive_at, last_negative_at "
            "FROM sentinel_video_state ORDER BY video_id"
        )
        states = [
            {
                'video_id': r[0], 'provider': r[1], 'state': r[2],
                'consecutive_negative_checks': int(r[3]),
                'last_scan_id': r[4], 'last_checked_at': _iso(r[5]),
                'last_positive_at': _iso(r[6]),
                'last_negative_at': _iso(r[7]),
            }
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT id, entity_id, event_type, from_state, to_state, "
            "observed_at, scan_run_id, evidence_json, NULL, NULL, NULL "
            "FROM sentinel_events ORDER BY id"
        )
        events = [_event_row(r) for r in cur.fetchall()]
        cur.close()
        return {'scan_runs': scans, 'video_states': states, 'events': events}
    finally:
        con.close()
