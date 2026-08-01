"""Durable, complete-only remote inventories for VaultTube Sentinel."""

import json
import logging
import os
import time

from database import get_active_playlist_subs, get_active_subscriptions
from database import get_connection
from providers.youtube import (
    YouTubePlaylistTruncated,
    YouTubeQuotaExceeded,
    YouTubeRequestBudget,
    YouTubeScanBudgetExceeded,
    YouTubeSourceCheckFailed,
    check_channel_presence,
    iter_playlist_pages,
)
from sentinel import finish_scan_run, start_scan_run
from sentinel_risk import (
    recalculate_all_source_risks,
    recalculate_source_risk,
    record_source_observation,
)


logger = logging.getLogger('sentinel_inventory')
DEFAULT_CENSUS_INTERVAL = 7 * 24 * 60 * 60
DEFAULT_CENSUS_BUDGET = 500
DEFAULT_CENSUS_MAX_PAGES = 2000


def _json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def inventory_due(source_type, source_id, interval_seconds=None):
    """Return true for resumable work or a source whose census is stale."""
    interval_seconds = (
        _env_int('VAULTTUBE_SENTINEL_CENSUS_INTERVAL', DEFAULT_CENSUS_INTERVAL)
        if interval_seconds is None else max(0, int(interval_seconds))
    )
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT status, completed_at FROM sentinel_inventory_runs "
            "WHERE provider='youtube' AND source_type=%s AND source_id=%s "
            "ORDER BY id DESC LIMIT 1",
            (source_type, source_id),
        )
        row = cur.fetchone()
        cur.close()
        if row is None or row[0] == 'running':
            return True
        if row[0] != 'complete' or row[1] is None:
            return True
        return (time.time() - row[1].timestamp()) >= interval_seconds
    finally:
        con.close()


def _get_or_start_run(source_type, source_id, collection_id):
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, continuation_token, pages_fetched, items_seen, "
            "continuation_history_json FROM sentinel_inventory_runs "
            "WHERE provider='youtube' AND source_type=%s AND source_id=%s "
            "AND status='running' ORDER BY id DESC LIMIT 1",
            (source_type, source_id),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO sentinel_inventory_runs "
                "(provider, source_type, source_id, remote_collection_id) "
                "VALUES('youtube', %s, %s, %s)",
                (source_type, source_id, collection_id),
            )
            row = (cur.lastrowid, None, 0, 0, None)
        cur.close()
        return {
            'id': int(row[0]), 'continuation_token': row[1],
            'pages_fetched': int(row[2]), 'items_seen': int(row[3]),
            'requested_page_tokens': _json_list(row[4]),
        }
    finally:
        con.close()


def _save_page(run_id, source_type, source_id, page):
    """Persist one page and its safe next token before another request."""
    con = get_connection(logger)
    try:
        con.begin()
        cur = con.cursor()
        cur.execute(
            "SELECT continuation_history_json FROM sentinel_inventory_runs "
            "WHERE id=%s AND status='running' FOR UPDATE",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError('Sentinel inventory run is not resumable')
        history = _json_list(row[0])
        requested = page.get('requested_page_token')
        if requested is not None and requested not in history:
            history.append(requested)
        start_position = max(0, int(page['items_seen']) - len(page['video_ids']))
        for index, video_id in enumerate(page['video_ids']):
            cur.execute(
                "INSERT IGNORE INTO sentinel_inventory "
                "(scan_run_id, provider, source_type, source_id, entity_id, position) "
                "VALUES(%s, 'youtube', %s, %s, %s, %s)",
                (run_id, source_type, source_id, video_id, start_position + index),
            )
        cur.execute(
            "UPDATE sentinel_inventory_runs SET continuation_token=%s, "
            "continuation_history_json=%s, pages_fetched=%s, items_seen=%s, "
            "requests_made=requests_made+1, error_message=NULL WHERE id=%s",
            (
                page['next_page_token'], json.dumps(history),
                page['pages_fetched'], page['items_seen'], run_id,
            ),
        )
        con.commit()
        cur.close()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _insert_inventory_event(cur, run_id, source_type, source_id, video_id,
                            event_type, from_state, to_state):
    evidence = json.dumps({
        'inventory_run_id': run_id,
        'source_type': source_type,
        'source_id': source_id,
    }, sort_keys=True)
    cur.execute(
        "INSERT IGNORE INTO sentinel_events "
        "(provider, entity_type, entity_id, event_type, from_state, to_state, "
        "evidence_json, dedupe_key, source_type, source_id) "
        "VALUES('youtube', 'video', %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            video_id, event_type, from_state, to_state, evidence,
            'inventory:%s:%s' % (run_id, video_id), source_type, source_id,
        ),
    )


def _complete_run(run_id):
    """Atomically publish a completed snapshot and its inventory diff."""
    con = get_connection(logger)
    try:
        con.begin()
        cur = con.cursor()
        cur.execute(
            "SELECT source_type, source_id FROM sentinel_inventory_runs "
            "WHERE id=%s AND status='running' FOR UPDATE",
            (run_id,),
        )
        source = cur.fetchone()
        if source is None:
            raise RuntimeError('Sentinel inventory run is not running')
        source_type, source_id = source
        cur.execute(
            "SELECT id FROM sentinel_inventory_runs WHERE provider='youtube' "
            "AND source_type=%s AND source_id=%s AND status='complete' "
            "AND id<>%s ORDER BY completed_at DESC, id DESC LIMIT 1",
            (source_type, source_id, run_id),
        )
        previous = cur.fetchone()
        if previous is not None:
            previous_id = previous[0]
            cur.execute(
                "SELECT old.entity_id FROM sentinel_inventory old "
                "LEFT JOIN sentinel_inventory new ON new.scan_run_id=%s "
                "AND new.entity_id=old.entity_id "
                "WHERE old.scan_run_id=%s AND new.entity_id IS NULL",
                (run_id, previous_id),
            )
            for (video_id,) in cur.fetchall():
                _insert_inventory_event(
                    cur, run_id, source_type, source_id, video_id,
                    'inventory_removed', 'present', 'absent',
                )
            cur.execute(
                "SELECT new.entity_id FROM sentinel_inventory new "
                "LEFT JOIN sentinel_inventory old ON old.scan_run_id=%s "
                "AND old.entity_id=new.entity_id "
                "WHERE new.scan_run_id=%s AND old.entity_id IS NULL "
                "AND EXISTS (SELECT 1 FROM sentinel_inventory historic "
                "JOIN sentinel_inventory_runs hr ON hr.id=historic.scan_run_id "
                "WHERE historic.entity_id=new.entity_id "
                "AND hr.provider='youtube' AND hr.source_type=%s "
                "AND hr.source_id=%s AND hr.status='complete' AND hr.id<>%s)",
                (previous_id, run_id, source_type, source_id, run_id),
            )
            for (video_id,) in cur.fetchall():
                _insert_inventory_event(
                    cur, run_id, source_type, source_id, video_id,
                    'inventory_restored', 'absent', 'present',
                )
        cur.execute(
            "UPDATE sentinel_inventory_runs SET status='complete', "
            "continuation_token=NULL, completed_at=NOW(), error_message=NULL "
            "WHERE id=%s",
            (run_id,),
        )
        con.commit()
        cur.close()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    if source_type == 'channel':
        try:
            recalculate_source_risk(source_type, source_id)
        except Exception as exc:
            logger.error('Risk calculation failed after inventory %s: %s', run_id, exc)


def _fail_run(run_id, status, error, failure_class='incomplete'):
    con = get_connection(logger)
    source = None
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT source_type, source_id FROM sentinel_inventory_runs "
            "WHERE id=%s",
            (run_id,),
        )
        source = cur.fetchone()
        cur.execute(
            "UPDATE sentinel_inventory_runs SET status=%s, error_message=%s, "
            "failure_class=%s, "
            "completed_at=NOW() WHERE id=%s",
            (status, str(error), failure_class, run_id),
        )
        cur.close()
    finally:
        con.close()
    if source and source[0] == 'channel':
        try:
            recalculate_source_risk(source[0], source[1])
        except Exception as exc:
            logger.error('Risk calculation failed after census failure: %s', exc)


def census_source(source_type, source_id, collection_id, request_budget=None):
    """Resume or start one source census; publish only on full completion."""
    run = _get_or_start_run(source_type, source_id, collection_id)
    try:
        pages = iter_playlist_pages(
            collection_id,
            request_budget=request_budget,
            max_pages=max(1, _env_int(
                'VAULTTUBE_SENTINEL_CENSUS_MAX_PAGES',
                DEFAULT_CENSUS_MAX_PAGES,
            )),
            start_page_token=run['continuation_token'],
            pages_already_fetched=run['pages_fetched'],
            items_already_seen=run['items_seen'],
            requested_page_tokens=run['requested_page_tokens'],
            include_page_info=True,
        )
        for page in pages:
            _save_page(run['id'], source_type, source_id, page)
            if page['complete']:
                _complete_run(run['id'])
                return {'run_id': run['id'], 'status': 'complete'}
        raise YouTubePlaylistTruncated('Inventory pager ended without completion')
    except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded):
        raise
    except YouTubePlaylistTruncated as exc:
        _fail_run(
            run['id'], 'partial', exc,
            getattr(exc, 'failure_class', 'incomplete'),
        )
        raise
    except Exception as exc:
        # Network/provider errors can safely resume the same uncommitted page.
        con = get_connection(logger)
        try:
            cur = con.cursor()
            cur.execute(
                "UPDATE sentinel_inventory_runs SET error_message=%s WHERE id=%s",
                (str(exc), run['id']),
            )
            cur.close()
        finally:
            con.close()
        raise


def census_once(app, request_budget=None, interval_seconds=None):
    """Run due channel/playlist censuses with a separate request budget."""
    budget = request_budget or YouTubeRequestBudget(
        _env_int('VAULTTUBE_SENTINEL_CENSUS_BUDGET', DEFAULT_CENSUS_BUDGET)
    )
    work = [
        ('channel', row[0], 'UU' + row[0][2:])
        for row in get_active_subscriptions()
        if row[0].startswith('UC')
    ]
    work.extend(
        ('playlist', row[0], row[0]) for row in get_active_playlist_subs()
    )
    results = []
    with app.app_context():
        for source_type, source_id, collection_id in work:
            if not inventory_due(source_type, source_id, interval_seconds):
                continue
            try:
                if source_type == 'channel':
                    scan_id = start_scan_run(
                        scan_type='source_presence', source_type=source_type,
                        source_id=source_id,
                    )
                    try:
                        present = check_channel_presence(source_id, budget)
                    except (YouTubeQuotaExceeded, YouTubeScanBudgetExceeded):
                        finish_scan_run(scan_id, 'failed', 0, 1, 'quota or budget')
                        raise
                    except YouTubeSourceCheckFailed as exc:
                        finish_scan_run(scan_id, 'failed', 0, 1, str(exc))
                        logger.warning(
                            'Source presence check failed for %s: %s',
                            source_id, exc,
                        )
                        continue
                    finish_scan_run(scan_id, 'complete', 1, 1)
                    record_source_observation(
                        source_type, source_id, present, scan_id,
                        {'method': 'youtube.channels.list'},
                    )
                    if not present:
                        continue
                results.append(census_source(
                    source_type, source_id, collection_id, budget,
                ))
            except YouTubeScanBudgetExceeded:
                break
            except YouTubeQuotaExceeded as exc:
                logger.warning('Sentinel census stopped by YouTube quota: %s', exc)
                break
            except Exception as exc:
                logger.error(
                    'Sentinel census failed for %s:%s: %s',
                    source_type, source_id, exc,
                )
    return results


def _env_int(name, default):
    try:
        value = int(os.environ.get(name, default))
        return max(0, value)
    except (TypeError, ValueError):
        logger.warning('Invalid %s; using %s', name, default)
        return default


def start_census(app):
    """Low-frequency inventory loop, independent of hourly catch-up scans."""
    while True:
        recalculate_all_source_risks()
        census_once(app)
        # Interrupted runs retry hourly; completed sources retain the much
        # lower census frequency.
        con = get_connection(logger)
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM sentinel_inventory_runs "
                "WHERE status='running'"
            )
            has_running = cur.fetchone()[0] > 0
            cur.close()
        finally:
            con.close()
        interval = 3600 if has_running else _env_int(
            'VAULTTUBE_SENTINEL_CENSUS_INTERVAL', DEFAULT_CENSUS_INTERVAL,
        )
        time.sleep(max(60, interval))
