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
