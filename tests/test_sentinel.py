import json

from tests.test_project import _db_connect


def _insert_video(video_id, is_deleted=0):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO videos(id, channel_name, channelId, json, filepath, "
        "PublishedAt, watched, `timestamp`, isDeleted, source) "
        "VALUES(%s, 'Sentinel', 'SentinelChannel', '{}', %s, "
        "'2024-01-01 10:00:00', 0, 0, %s, 'youtube')",
        (video_id, '/videos/%s.mp4' % video_id, is_deleted),
    )
    cur.close()
    con.close()


def _state_and_flag(video_id):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT s.state, s.consecutive_negative_checks, v.isDeleted "
        "FROM sentinel_video_state s JOIN videos v ON v.id=s.video_id "
        "WHERE s.video_id=%s",
        (video_id,),
    )
    row = cur.fetchone()
    cur.close()
    con.close()
    return row


def _events(video_id):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT event_type, from_state, to_state, evidence_json "
        "FROM sentinel_events WHERE entity_id=%s ORDER BY id",
        (video_id,),
    )
    rows = cur.fetchall()
    cur.close()
    con.close()
    return rows


def test_sentinel_tables_exist():
    con = _db_connect()
    cur = con.cursor()
    for table in ('sentinel_scan_runs', 'sentinel_video_state', 'sentinel_events'):
        cur.execute("SHOW TABLES LIKE %s", (table,))
        assert cur.fetchone() is not None
    cur.close()
    con.close()


def test_two_independent_negative_checks_confirm_once():
    from sentinel import start_scan_run, finish_scan_run, record_video_observation

    _insert_video('SentinelGone1')

    first_scan = start_scan_run()
    assert record_video_observation('SentinelGone1', False, first_scan) is None
    # Repeating a result within the same scan cannot confirm it.
    assert record_video_observation('SentinelGone1', False, first_scan) is None
    finish_scan_run(first_scan, 'complete', 1, 1)
    assert _state_and_flag('SentinelGone1') == ('suspected_unavailable', 1, 0)
    assert _events('SentinelGone1') == []

    second_scan = start_scan_run()
    assert record_video_observation(
        'SentinelGone1', False, second_scan,
        {'method': 'youtube.videos.list'},
    ) == 'source_unavailable'
    finish_scan_run(second_scan, 'complete', 1, 1)
    assert _state_and_flag('SentinelGone1') == ('unavailable', 2, 1)

    third_scan = start_scan_run()
    assert record_video_observation('SentinelGone1', False, third_scan) is None
    finish_scan_run(third_scan, 'complete', 1, 1)
    rows = _events('SentinelGone1')
    assert len(rows) == 1
    assert rows[0][:3] == (
        'source_unavailable', 'suspected_unavailable', 'unavailable',
    )
    assert json.loads(rows[0][3])['method'] == 'youtube.videos.list'


def test_positive_check_clears_suspicion_without_restoration_event():
    from sentinel import start_scan_run, finish_scan_run, record_video_observation

    _insert_video('SentinelSuspect1')
    first_scan = start_scan_run()
    record_video_observation('SentinelSuspect1', False, first_scan)
    finish_scan_run(first_scan, 'complete', 1, 1)

    second_scan = start_scan_run()
    assert record_video_observation('SentinelSuspect1', True, second_scan) is None
    finish_scan_run(second_scan, 'complete', 1, 1)

    assert _state_and_flag('SentinelSuspect1') == ('available', 0, 0)
    assert _events('SentinelSuspect1') == []


def test_negative_from_incomplete_prior_scan_cannot_confirm():
    from sentinel import start_scan_run, finish_scan_run, record_video_observation

    _insert_video('SentinelIncomplete1')
    first_scan = start_scan_run()
    record_video_observation('SentinelIncomplete1', False, first_scan)
    finish_scan_run(first_scan, 'partial', 1, 2, 'later batch failed')

    second_scan = start_scan_run()
    assert record_video_observation(
        'SentinelIncomplete1', False, second_scan,
    ) is None
    finish_scan_run(second_scan, 'complete', 1, 1)

    assert _state_and_flag('SentinelIncomplete1') == (
        'suspected_unavailable', 1, 0,
    )
    assert _events('SentinelIncomplete1') == []


def test_confirmed_unavailable_video_emits_one_restoration():
    from sentinel import start_scan_run, finish_scan_run, record_video_observation

    _insert_video('SentinelRestore1', is_deleted=1)
    scan_id = start_scan_run()
    assert record_video_observation(
        'SentinelRestore1', True, scan_id,
        {'method': 'youtube.videos.list'},
    ) == 'source_restored'
    finish_scan_run(scan_id, 'complete', 1, 1)

    assert _state_and_flag('SentinelRestore1') == ('available', 0, 0)
    rows = _events('SentinelRestore1')
    assert len(rows) == 1
    assert rows[0][:3] == ('source_restored', 'unavailable', 'available')


def test_legacy_unavailable_backfill_is_idempotent():
    import database

    _insert_video('SentinelLegacy1', is_deleted=1)
    assert database.checkdb() is True
    assert database.checkdb() is True

    assert _state_and_flag('SentinelLegacy1') == ('unavailable', 2, 1)
    rows = _events('SentinelLegacy1')
    assert len(rows) == 1
    assert rows[0][0] == 'imported_existing_state'
    evidence = json.loads(rows[0][3])
    assert evidence == {
        'source': 'videos.isDeleted',
        'observed_at_known': False,
    }


def test_failed_deleted_check_records_failed_scan_without_transition(client, monkeypatch):
    import backend
    import requests

    _insert_video('SentinelFailure1')

    def fail_get(*args, **kwargs):
        raise requests.ConnectionError('provider unavailable')

    monkeypatch.setattr(requests, 'get', fail_get)
    with client.application.app_context():
        backend.run_deleted_check(rows=[('SentinelFailure1', 0)])

    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT status, items_seen FROM sentinel_scan_runs ORDER BY id DESC LIMIT 1")
    assert cur.fetchone() == ('failed', 0)
    cur.execute("SELECT isDeleted FROM videos WHERE id='SentinelFailure1'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM sentinel_events WHERE entity_id='SentinelFailure1'")
    assert cur.fetchone()[0] == 0
    cur.close()
    con.close()


def test_partial_provider_pass_applies_no_observations(client, monkeypatch):
    import backend
    import requests

    _insert_video('SentinelPartial1')
    _insert_video('SentinelPartial2')
    calls = 0

    class Response:
        def json(self):
            return {'items': []}

        def close(self):
            pass

    def first_succeeds_second_fails(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise requests.ConnectionError('second batch failed')
        return Response()

    monkeypatch.setattr(requests, 'get', first_succeeds_second_fails)
    with client.application.app_context():
        backend.run_deleted_check(
            rows=[('SentinelPartial1', 0), ('SentinelPartial2', 0)],
            batch_size=1,
        )

    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT status, items_seen, requests_made FROM sentinel_scan_runs")
    assert cur.fetchone() == ('partial', 1, 2)
    cur.execute(
        "SELECT COUNT(*) FROM sentinel_video_state "
        "WHERE video_id IN ('SentinelPartial1','SentinelPartial2')"
    )
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT SUM(isDeleted) FROM videos "
        "WHERE id IN ('SentinelPartial1','SentinelPartial2')"
    )
    assert cur.fetchone()[0] == 0
    cur.close()
    con.close()
