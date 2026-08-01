import json

from tests.test_project import _db_connect


def _insert_channel(channel_id='SentinelChannel', name='Sentinel Creator'):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO channels(channelid, channelname, json, subscribed) "
        "VALUES(%s, %s, '{}', 0)",
        (channel_id, name),
    )
    cur.close()
    con.close()


def _insert_video(video_id, is_deleted=0, channel_id='SentinelChannel',
                  title=None):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO videos(id, channel_name, channelId, json, filepath, "
        "PublishedAt, watched, `timestamp`, isDeleted, source, title) "
        "VALUES(%s, 'Sentinel', %s, '{}', %s, "
        "'2024-01-01 10:00:00', 0, 0, %s, 'youtube', %s)",
        (
            video_id, channel_id, '/videos/%s.mp4' % video_id,
            is_deleted, title or video_id,
        ),
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


def _confirm_unavailable(video_id):
    from sentinel import finish_scan_run, record_video_observation, start_scan_run

    first = start_scan_run()
    record_video_observation(video_id, False, first)
    finish_scan_run(first, 'complete', 1, 1)
    second = start_scan_run()
    record_video_observation(
        video_id, False, second, {'method': 'youtube.videos.list'},
    )
    finish_scan_run(second, 'complete', 1, 1)


def test_observatory_page_and_navigation_render(client):
    response = client.get('/observatory.html')
    assert response.status_code == 200
    assert b'id="sentinel-event-list"' in response.data
    assert b'/static/js/observatory.js' in response.data
    assert b'/observatory.html' in client.get('/').data


def test_sentinel_summary_and_event_feed_apis(client):
    _insert_channel()
    _insert_video('SentinelApiGone', title='Preserved but gone')
    _insert_video('SentinelApiSuspect', title='Awaiting confirmation')
    _confirm_unavailable('SentinelApiGone')

    from sentinel import finish_scan_run, record_video_observation, start_scan_run
    scan = start_scan_run()
    record_video_observation('SentinelApiSuspect', False, scan)
    finish_scan_run(scan, 'complete', 1, 1)

    summary = client.get('/api/sentinel/summary').get_json()['data']
    assert summary['monitored_videos'] == 2
    assert summary['preserved_unavailable'] == 1
    assert summary['suspected_unavailable'] == 1
    assert summary['newly_unavailable_7d'] == 1
    assert summary['last_scan']['status'] == 'complete'

    payload = client.get(
        '/api/sentinel/events?event_type=source_unavailable&limit=1'
    ).get_json()['data']
    assert payload['total'] == 1
    assert payload['items'][0]['entity_id'] == 'SentinelApiGone'
    assert payload['items'][0]['title'] == 'Preserved but gone'
    assert payload['items'][0]['channel_name'] == 'Sentinel Creator'
    assert payload['items'][0]['evidence'] == {
        'method': 'youtube.videos.list',
    }

    bad = client.get('/api/sentinel/events?event_type=made_up')
    assert bad.status_code == 400
    assert bad.get_json()['success'] is False


def test_sentinel_source_list_and_detail_apis(client):
    _insert_channel('CreatorWithLoss', 'Fragile Films')
    _insert_channel('CreatorStable', 'Steady Studio')
    _insert_video('SentinelSourceGone', channel_id='CreatorWithLoss')
    _insert_video('SentinelSourceHere', channel_id='CreatorStable')
    _confirm_unavailable('SentinelSourceGone')

    from sentinel import finish_scan_run, record_video_observation, start_scan_run
    scan = start_scan_run()
    record_video_observation('SentinelSourceHere', True, scan)
    finish_scan_run(scan, 'complete', 1, 1)

    sources = client.get('/api/sentinel/sources').get_json()['data']
    assert sources['total'] == 2
    by_id = {item['channel_id']: item for item in sources['items']}
    assert by_id['CreatorWithLoss']['status'] == 'attention'
    assert by_id['CreatorWithLoss']['unavailable'] == 1
    assert by_id['CreatorStable']['status'] == 'stable'

    detail = client.get(
        '/api/sentinel/source/channel/CreatorWithLoss'
    ).get_json()['data']
    assert detail['channel_name'] == 'Fragile Films'
    assert detail['event_count'] == 1
    assert detail['affected_videos'][0]['id'] == 'SentinelSourceGone'
    assert detail['events'][0]['event_type'] == 'source_unavailable'

    assert client.get(
        '/api/sentinel/source/channel/DoesNotExist'
    ).status_code == 404
    assert client.get(
        '/api/sentinel/source/playlist/CreatorWithLoss'
    ).status_code == 404


def test_creator_page_includes_sentinel_context(client):
    response = client.get('/creator.html')
    assert response.status_code == 200
    assert b'id="creator-sentinel"' in response.data
    assert b'/api/sentinel/source/channel/' in response.data


def test_json_export_includes_sentinel_ledger(client):
    _insert_channel()
    _insert_video('SentinelExportGone')
    _confirm_unavailable('SentinelExportGone')

    response = client.get('/api/export?format=json')
    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload['meta']['counts']['sentinel_events'] == 1
    assert payload['meta']['counts']['sentinel_video_states'] == 1
    assert payload['meta']['counts']['sentinel_scan_runs'] == 2
    assert payload['sentinel']['events'][0]['entity_id'] == 'SentinelExportGone'
    assert payload['sentinel']['video_states'][0]['state'] == 'unavailable'
    assert len(payload['sentinel']['scan_runs']) == 2
