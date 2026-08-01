"""Deterministic, observation-only source risk for VaultTube Sentinel."""

import datetime
import json
import logging
import uuid

from database import get_connection
from providers.base import clear_alert, raise_alert


logger = logging.getLogger('sentinel_risk')

ADVERSE_EVENT_TYPES = (
    'source_unavailable', 'inventory_removed',
    'source_terminal_unavailable',
)


def risk_level(score):
    score = max(0, min(100, int(score)))
    if score >= 75:
        return 'critical'
    if score >= 50:
        return 'high'
    if score >= 25:
        return 'elevated'
    return 'low'


def _loads(value, fallback):
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
        return parsed
    except (TypeError, ValueError):
        return fallback


def _risk_alert_id(source_type, source_id):
    return 'sentinel_risk_%s_%s' % (source_type, source_id)


def _sync_alert(result):
    alert_id = _risk_alert_id(result['source_type'], result['source_id'])
    if result['level'] in {'high', 'critical'}:
        reasons = '; '.join(reason['label'] for reason in result['reasons'])
        raise_alert(
            alert_id,
            'Sentinel: %s risk source' % result['level'].title(),
            '%s scored %s/100. %s Observation only; no downloads were queued.' % (
                result.get('source_name') or result['source_id'],
                result['score'], reasons,
            ),
            kind='error' if result['level'] == 'critical' else 'warning',
        )
    else:
        clear_alert(alert_id)


def _source_row(cur, source_type, source_id):
    cur.execute(
        "INSERT IGNORE INTO sentinel_sources "
        "(provider, source_type, source_id) VALUES('youtube', %s, %s)",
        (source_type, source_id),
    )
    cur.execute(
        "SELECT availability_state, consecutive_terminal_checks, "
        "last_observation_scan_id, risk_score, risk_level, "
        "risk_reasons_json, risk_facts_json, risk_calculated_at "
        "FROM sentinel_sources WHERE provider='youtube' "
        "AND source_type=%s AND source_id=%s",
        (source_type, source_id),
    )
    return cur.fetchone()


def record_source_observation(source_type, source_id, available, scan_id,
                              evidence=None):
    """Apply one successful source-level check; negatives require two scans."""
    con = get_connection(logger)
    try:
        con.begin()
        cur = con.cursor()
        cur.execute(
            "SELECT status FROM sentinel_scan_runs WHERE id=%s "
            "AND provider='youtube' AND source_type=%s AND source_id=%s",
            (scan_id, source_type, source_id),
        )
        scan = cur.fetchone()
        if scan is None or scan[0] != 'complete':
            raise ValueError('Source observations require a complete scan')
        _source_row(cur, source_type, source_id)
        cur.execute(
            "SELECT availability_state, consecutive_terminal_checks, "
            "last_observation_scan_id FROM sentinel_sources "
            "WHERE provider='youtube' AND source_type=%s AND source_id=%s "
            "FOR UPDATE",
            (source_type, source_id),
        )
        state, checks, last_scan_id = cur.fetchone()
        if last_scan_id == scan_id:
            cur.close()
            con.commit()
            return None

        event_type = None
        if available:
            next_state = 'available'
            next_checks = 0
            if state == 'unavailable':
                event_type = 'source_terminal_restored'
        elif state == 'unavailable':
            next_state = 'unavailable'
            next_checks = max(2, int(checks) + 1)
        elif state == 'suspected_unavailable':
            next_state = 'unavailable'
            next_checks = int(checks) + 1
            event_type = 'source_terminal_unavailable'
        else:
            next_state = 'suspected_unavailable'
            next_checks = 1

        if event_type:
            cur.execute(
                "INSERT IGNORE INTO sentinel_events "
                "(provider, entity_type, entity_id, event_type, from_state, "
                "to_state, scan_run_id, evidence_json, dedupe_key, "
                "source_type, source_id) "
                "VALUES('youtube','source',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    source_id, event_type, state, next_state, scan_id,
                    json.dumps(evidence or {}, sort_keys=True),
                    'source-observation:%s:%s' % (scan_id, event_type),
                    source_type, source_id,
                ),
            )
        cur.execute(
            "UPDATE sentinel_sources SET availability_state=%s, "
            "consecutive_terminal_checks=%s, last_observation_scan_id=%s, "
            "last_observed_at=NOW() WHERE provider='youtube' "
            "AND source_type=%s AND source_id=%s",
            (next_state, next_checks, scan_id, source_type, source_id),
        )
        con.commit()
        cur.close()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    recalculate_source_risk(source_type, source_id)
    return event_type


def _calculate_facts(cur, source_type, source_id):
    source = _source_row(cur, source_type, source_id)
    availability_state = source[0]
    terminal_checks = int(source[1])

    cur.execute(
        "SELECT COUNT(*) FROM sentinel_events e LEFT JOIN videos v "
        "ON v.id=e.entity_id WHERE COALESCE(v.channelId,e.source_id)=%s "
        "AND e.event_type='source_unavailable' "
        "AND e.observed_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)",
        (source_id,),
    )
    confirmed_24h = int(cur.fetchone()[0])

    cur.execute(
        "SELECT COUNT(*) FROM sentinel_events e WHERE e.source_type=%s "
        "AND e.source_id=%s AND e.event_type='inventory_removed' "
        "AND e.observed_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)",
        (source_type, source_id),
    )
    removed_7d = int(cur.fetchone()[0])

    cur.execute(
        "SELECT r.id, r.completed_at, COUNT(i.entity_id), "
        "COUNT(CASE WHEN i.entity_id IS NOT NULL AND v.id IS NULL THEN 1 END) "
        "FROM sentinel_inventory_runs r LEFT JOIN sentinel_inventory i "
        "ON i.scan_run_id=r.id LEFT JOIN videos v ON v.id=i.entity_id "
        "WHERE r.provider='youtube' AND r.source_type=%s AND r.source_id=%s "
        "AND r.status='complete' GROUP BY r.id, r.completed_at "
        "ORDER BY r.id DESC LIMIT 2",
        (source_type, source_id),
    )
    inventories = cur.fetchall()
    latest_count = int(inventories[0][2]) if inventories else 0
    unpreserved_count = int(inventories[0][3]) if inventories else 0
    previous_count = int(inventories[1][2]) if len(inventories) > 1 else None
    shrink_percent = 0.0
    if previous_count:
        shrink_percent = max(
            0.0, ((previous_count - latest_count) / previous_count) * 100,
        )
    removal_denominator = previous_count or (latest_count + removed_7d)
    removal_percent = (
        (removed_7d / removal_denominator) * 100
        if removal_denominator else 0.0
    )

    cur.execute(
        "SELECT status, failure_class FROM sentinel_inventory_runs "
        "WHERE provider='youtube' AND source_type=%s AND source_id=%s "
        "ORDER BY id DESC LIMIT 3",
        (source_type, source_id),
    )
    recent_runs = cur.fetchall()
    consecutive_source_failures = (
        len(recent_runs) == 3 and all(
            row[0] == 'partial' and row[1] == 'source'
            for row in recent_runs
        )
    )

    cur.execute(
        "SELECT MIN(observed_at) FROM ("
        " SELECT completed_at observed_at FROM sentinel_inventory_runs "
        " WHERE provider='youtube' AND source_type=%s AND source_id=%s "
        " AND status='complete' "
        " UNION ALL SELECT e.observed_at FROM sentinel_events e "
        " LEFT JOIN videos v ON v.id=e.entity_id "
        " WHERE COALESCE(v.channelId,e.source_id)=%s) observations",
        (source_type, source_id, source_id),
    )
    monitored_since = cur.fetchone()[0]
    placeholders = ','.join(['%s'] * len(ADVERSE_EVENT_TYPES))
    cur.execute(
        "SELECT COUNT(*) FROM sentinel_events e LEFT JOIN videos v "
        "ON v.id=e.entity_id WHERE COALESCE(v.channelId,e.source_id)=%s "
        "AND e.event_type IN (" + placeholders + ") "
        "AND e.observed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)",
        tuple([source_id] + list(ADVERSE_EVENT_TYPES)),
    )
    adverse_30d = int(cur.fetchone()[0])
    quiet_30d = bool(
        monitored_since
        and monitored_since <= datetime.datetime.now() - datetime.timedelta(days=30)
        and adverse_30d == 0
    )
    return {
        'availability_state': availability_state,
        'consecutive_terminal_checks': terminal_checks,
        'confirmed_disappearances_24h': confirmed_24h,
        'inventory_removed_7d': removed_7d,
        'latest_inventory_count': latest_count,
        'previous_inventory_count': previous_count,
        'inventory_removed_percent_7d': round(removal_percent, 2),
        'inventory_shrink_percent': round(shrink_percent, 2),
        'consecutive_source_failures': 3 if consecutive_source_failures else 0,
        'unpreserved_available_count': unpreserved_count,
        'adverse_events_30d': adverse_30d,
        'monitored_since': monitored_since.isoformat() if monitored_since else None,
        'quiet_30d': quiet_30d,
    }


def _score_facts(facts):
    reasons = []

    def add(code, label, points, evidence):
        reasons.append({
            'code': code, 'label': label, 'points': points,
            'evidence': evidence,
        })

    if (facts['availability_state'] == 'unavailable'
            and facts['consecutive_terminal_checks'] >= 2):
        add('terminal_unavailable', 'Source unavailable in two complete checks',
            45, {'checks': facts['consecutive_terminal_checks']})
    if facts['confirmed_disappearances_24h'] >= 5:
        add('disappearance_burst', 'Five or more confirmed disappearances in 24 hours',
            25, {'count': facts['confirmed_disappearances_24h']})
    if facts['inventory_removed_percent_7d'] > 5:
        add('weekly_inventory_loss', 'More than 5% of known inventory vanished in seven days',
            20, {
                'removed': facts['inventory_removed_7d'],
                'percent': facts['inventory_removed_percent_7d'],
            })
    if facts['inventory_shrink_percent'] > 10:
        add('inventory_shrink', 'Latest complete inventory shrank by more than 10%',
            15, {
                'previous': facts['previous_inventory_count'],
                'current': facts['latest_inventory_count'],
                'percent': facts['inventory_shrink_percent'],
            })
    if facts['consecutive_source_failures'] >= 3:
        add('source_failures', 'Three consecutive explicit source failures',
            10, {'count': facts['consecutive_source_failures']})
    if facts['unpreserved_available_count'] > 25:
        add('preservation_gap', 'More than 25 known videos are not preserved',
            10, {'count': facts['unpreserved_available_count']})
    if facts['quiet_30d']:
        add('quiet_30d', 'No adverse events for 30 days', -15,
            {'since': facts['monitored_since']})
    score = max(0, min(100, sum(reason['points'] for reason in reasons)))
    return score, reasons


def recalculate_source_risk(source_type, source_id):
    """Recalculate, persist, alert, and ledger one source's risk."""
    con = get_connection(logger)
    try:
        con.begin()
        cur = con.cursor()
        _source_row(cur, source_type, source_id)
        cur.execute(
            "SELECT availability_state, consecutive_terminal_checks, "
            "last_observation_scan_id, risk_score, risk_level, "
            "risk_reasons_json, risk_facts_json, risk_calculated_at "
            "FROM sentinel_sources WHERE provider='youtube' "
            "AND source_type=%s AND source_id=%s FOR UPDATE",
            (source_type, source_id),
        )
        row = cur.fetchone()
        old_score, old_level, old_calculated = int(row[3]), row[4], row[7]
        facts = _calculate_facts(cur, source_type, source_id)
        score, reasons = _score_facts(facts)
        level = risk_level(score)
        cur.execute(
            "UPDATE sentinel_sources SET risk_score=%s, risk_level=%s, "
            "risk_reasons_json=%s, risk_facts_json=%s, "
            "risk_calculated_at=NOW() WHERE provider='youtube' "
            "AND source_type=%s AND source_id=%s",
            (
                score, level, json.dumps(reasons, sort_keys=True),
                json.dumps(facts, sort_keys=True), source_type, source_id,
            ),
        )
        if old_calculated is not None and (score != old_score or level != old_level):
            cur.execute(
                "INSERT INTO sentinel_events "
                "(provider, entity_type, entity_id, event_type, from_state, "
                "to_state, evidence_json, dedupe_key, source_type, source_id) "
                "VALUES('youtube','source',%s,'risk_changed',%s,%s,%s,%s,%s,%s)",
                (
                    source_id, '%s:%s' % (old_level, old_score),
                    '%s:%s' % (level, score),
                    json.dumps({
                        'score': score, 'level': level, 'reasons': reasons,
                        'facts': facts,
                    }, sort_keys=True),
                    'risk:%s' % uuid.uuid4().hex, source_type, source_id,
                ),
            )
        cur.execute(
            "SELECT channelname FROM channels WHERE channelid=%s",
            (source_id,),
        )
        name = cur.fetchone()
        con.commit()
        cur.close()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    result = {
        'source_type': source_type, 'source_id': source_id,
        'source_name': name[0] if name else source_id,
        'score': score, 'level': level, 'reasons': reasons, 'facts': facts,
        'observation_only': True,
    }
    _sync_alert(result)
    return result


def recalculate_all_source_risks():
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT source_type, source_id FROM ("
            " SELECT 'channel' source_type, channelId source_id FROM videos "
            " WHERE channelId IS NOT NULL GROUP BY channelId "
            " UNION SELECT source_type, source_id FROM sentinel_inventory_runs "
            " WHERE provider='youtube' AND source_type='channel' "
            " GROUP BY source_type, source_id "
            " UNION SELECT source_type, source_id FROM sentinel_sources "
            " WHERE provider='youtube' AND source_type='channel') all_sources"
        )
        sources = cur.fetchall()
        cur.close()
    finally:
        con.close()
    results = []
    for source_type, source_id in sources:
        try:
            results.append(recalculate_source_risk(source_type, source_id))
        except Exception as exc:
            logger.error('Risk calculation failed for %s:%s: %s',
                         source_type, source_id, exc)
    return results


def get_source_risk(source_type, source_id):
    con = get_connection(logger)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT risk_score, risk_level, risk_reasons_json, "
            "risk_facts_json, risk_calculated_at, availability_state, "
            "consecutive_terminal_checks FROM sentinel_sources "
            "WHERE provider='youtube' AND source_type=%s AND source_id=%s",
            (source_type, source_id),
        )
        row = cur.fetchone()
        cur.close()
        if row is None or row[4] is None:
            return None
        return {
            'score': int(row[0]), 'level': row[1],
            'reasons': _loads(row[2], []), 'facts': _loads(row[3], {}),
            'calculated_at': row[4].isoformat(),
            'availability_state': row[5],
            'consecutive_terminal_checks': int(row[6]),
            'observation_only': True,
        }
    finally:
        con.close()
