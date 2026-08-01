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


def test_sentinel_inventory_tables_exist():
    con = _db_connect()
    cur = con.cursor()
    for table in ('sentinel_inventory_runs', 'sentinel_inventory'):
        cur.execute("SHOW TABLES LIKE %s", (table,))
        assert cur.fetchone() is not None
    cur.close()
    con.close()


def test_sentinel_source_risk_table_exists():
    con = _db_connect()
    cur = con.cursor()
    cur.execute("SHOW TABLES LIKE 'sentinel_sources'")
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
    assert payload['meta']['counts']['sentinel_sources'] == 1
    assert payload['sentinel']['events'][0]['entity_id'] == 'SentinelExportGone'
    assert payload['sentinel']['video_states'][0]['state'] == 'unavailable'
    assert len(payload['sentinel']['scan_runs']) == 2
    assert payload['sentinel']['sources'][0]['source_id'] == 'SentinelChannel'
    assert payload['sentinel']['sources'][0]['risk_level'] == 'low'


def _inventory_page(video_ids, complete=True, token=None, pages=1,
                    items_seen=None, requested=None):
    return {
        'video_ids': video_ids,
        'requested_page_token': requested,
        'next_page_token': token,
        'complete': complete,
        'items_seen': len(video_ids) if items_seen is None else items_seen,
        'pages_fetched': pages,
    }


def test_complete_inventory_exposes_coverage_and_unarchived_ids(
        client, monkeypatch):
    import sentinel_inventory

    _insert_channel('InventoryCreator', 'Inventory Films')
    _insert_video('InventorySaved1', channel_id='InventoryCreator')
    _insert_video('InventorySaved2', channel_id='InventoryCreator')
    monkeypatch.setattr(
        sentinel_inventory, 'iter_playlist_pages',
        lambda *args, **kwargs: iter([_inventory_page([
            'InventorySaved1', 'InventorySaved2', 'InventoryRemoteOnly',
        ])]),
    )

    result = sentinel_inventory.census_source(
        'channel', 'InventoryCreator', 'UUInventoryCreator',
    )
    assert result['status'] == 'complete'

    detail = client.get(
        '/api/sentinel/source/channel/InventoryCreator'
    ).get_json()['data']
    assert detail['inventory']['known_remote'] == 3
    assert detail['inventory']['preserved_remote'] == 2
    assert detail['inventory']['remote_unarchived'] == 1
    assert detail['inventory']['coverage_percent'] == 66.7
    assert detail['inventory']['unarchived_video_ids'] == [
        'InventoryRemoteOnly',
    ]
    summary = client.get('/api/sentinel/summary').get_json()['data']
    assert summary['known_remote_videos'] == 3
    assert summary['preserved_remote_videos'] == 2
    assert summary['archive_coverage_percent'] == 66.7
    sources = client.get('/api/sentinel/sources').get_json()['data']
    source = next(
        item for item in sources['items']
        if item['channel_id'] == 'InventoryCreator'
    )
    assert source['known_remote'] == 3
    assert source['remote_unarchived'] == 1


def test_inventory_removal_is_not_source_unavailability(client, monkeypatch):
    import sentinel_inventory

    _insert_channel('InventoryDiffCreator', 'Diff Films')
    _insert_video('InventoryStillListed', channel_id='InventoryDiffCreator')
    _insert_video('InventoryRemoved', channel_id='InventoryDiffCreator')
    snapshots = iter([
        ['InventoryStillListed', 'InventoryRemoved'],
        ['InventoryStillListed'],
        ['InventoryStillListed', 'InventoryRemoved'],
    ])

    def one_page(*args, **kwargs):
        return iter([_inventory_page(next(snapshots))])

    monkeypatch.setattr(sentinel_inventory, 'iter_playlist_pages', one_page)
    for _ in range(2):
        sentinel_inventory.census_source(
            'channel', 'InventoryDiffCreator', 'UUDiffCreator',
        )

    events = client.get(
        '/api/sentinel/events?channel_id=InventoryDiffCreator'
        '&event_type=inventory_removed'
    ).get_json()['data']['items']
    assert [event['entity_id'] for event in events] == ['InventoryRemoved']
    assert events[0]['from_state'] == 'present'
    assert events[0]['to_state'] == 'absent'

    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT isDeleted FROM videos WHERE id='InventoryRemoved'")
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT COUNT(*) FROM sentinel_video_state "
        "WHERE video_id='InventoryRemoved'"
    )
    assert cur.fetchone()[0] == 0
    cur.close()
    con.close()

    sentinel_inventory.census_source(
        'channel', 'InventoryDiffCreator', 'UUDiffCreator',
    )
    restored = client.get(
        '/api/sentinel/events?channel_id=InventoryDiffCreator'
        '&event_type=inventory_restored'
    ).get_json()['data']['items']
    assert [event['entity_id'] for event in restored] == ['InventoryRemoved']


def test_partial_inventory_is_durable_but_never_published(client, monkeypatch):
    import sentinel_inventory
    from providers.youtube import YouTubePlaylistTruncated

    _insert_channel('InventoryPartialCreator', 'Partial Films')
    _insert_video('InventoryBaseline', channel_id='InventoryPartialCreator')
    calls = 0

    def pages(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _inventory_page(['InventoryBaseline'])
            return
        yield _inventory_page(
            ['InventoryDifferent'], complete=False, token='next',
        )
        raise YouTubePlaylistTruncated('synthetic partial response')

    monkeypatch.setattr(sentinel_inventory, 'iter_playlist_pages', pages)
    sentinel_inventory.census_source(
        'channel', 'InventoryPartialCreator', 'UUPartialCreator',
    )
    try:
        sentinel_inventory.census_source(
            'channel', 'InventoryPartialCreator', 'UUPartialCreator',
        )
        assert False, 'partial census should raise'
    except YouTubePlaylistTruncated:
        pass

    detail = client.get(
        '/api/sentinel/source/channel/InventoryPartialCreator'
    ).get_json()['data']
    assert detail['inventory']['known_remote'] == 1
    assert detail['inventory']['unarchived_video_ids'] == []

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT status, continuation_token, items_seen "
        "FROM sentinel_inventory_runs ORDER BY id"
    )
    assert cur.fetchall() == [
        ('complete', None, 1),
        ('partial', 'next', 1),
    ]
    cur.execute("SELECT COUNT(*) FROM sentinel_events")
    assert cur.fetchone()[0] == 0
    cur.close()
    con.close()


def test_inventory_census_resumes_persisted_page_token(client, monkeypatch):
    import requests
    import sentinel_inventory
    from providers.youtube import YouTubeRequestBudget, YouTubeScanBudgetExceeded

    _insert_channel('InventoryResumeCreator', 'Resume Films')
    calls = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def close(self):
            pass

    def fake_get(url, **kwargs):
        token = kwargs['params'].get('pageToken')
        calls.append(token)
        if token is None:
            return Response({
                'items': [{'contentDetails': {'videoId': 'ResumeRemote1'}}],
                'nextPageToken': 'page-two',
            })
        assert token == 'page-two'
        return Response({
            'items': [{'contentDetails': {'videoId': 'ResumeRemote2'}}],
        })

    monkeypatch.setattr(requests, 'get', fake_get)
    try:
        sentinel_inventory.census_source(
            'channel', 'InventoryResumeCreator', 'UUResumeCreator',
            YouTubeRequestBudget(1),
        )
        assert False, 'budget exhaustion should suspend the run'
    except YouTubeScanBudgetExceeded:
        pass

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT status, continuation_token, pages_fetched, items_seen "
        "FROM sentinel_inventory_runs"
    )
    assert cur.fetchone() == ('running', 'page-two', 1, 1)
    cur.close()
    con.close()

    sentinel_inventory.census_source(
        'channel', 'InventoryResumeCreator', 'UUResumeCreator',
        YouTubeRequestBudget(1),
    )
    assert calls == [None, 'page-two']
    detail = client.get(
        '/api/sentinel/source/channel/InventoryResumeCreator'
    ).get_json()['data']
    assert detail['inventory']['known_remote'] == 2
    assert detail['inventory']['remote_unarchived'] == 2


def test_json_export_includes_inventory_snapshots(client, monkeypatch):
    import sentinel_inventory

    _insert_channel('InventoryExportCreator', 'Export Films')
    monkeypatch.setattr(
        sentinel_inventory, 'iter_playlist_pages',
        lambda *args, **kwargs: iter([
            _inventory_page(['InventoryExportRemote']),
        ]),
    )
    sentinel_inventory.census_source(
        'channel', 'InventoryExportCreator', 'UUExportCreator',
    )
    payload = json.loads(
        client.get('/api/export?format=json').get_data(as_text=True)
    )
    assert payload['meta']['counts']['sentinel_inventory_runs'] == 1
    assert payload['meta']['counts']['sentinel_inventory_items'] == 1
    assert payload['sentinel']['inventory_runs'][0]['status'] == 'complete'
    assert payload['sentinel']['inventory'][0]['entity_id'] == \
        'InventoryExportRemote'


def test_empty_complete_inventory_is_visible_as_zero_coverage(client, monkeypatch):
    import sentinel_inventory

    _insert_channel('InventoryEmptyCreator', 'Empty Films')
    monkeypatch.setattr(
        sentinel_inventory, 'iter_playlist_pages',
        lambda *args, **kwargs: iter([_inventory_page([])]),
    )
    sentinel_inventory.census_source(
        'channel', 'InventoryEmptyCreator', 'UUEmptyCreator',
    )
    detail = client.get(
        '/api/sentinel/source/channel/InventoryEmptyCreator'
    ).get_json()['data']
    assert detail['inventory']['known_remote'] == 0
    assert detail['inventory']['preserved_remote'] == 0
    assert detail['inventory']['coverage_percent'] == 0


def test_risk_model_scores_and_caps_deterministically():
    from sentinel_risk import _score_facts, risk_level

    facts = {
        'availability_state': 'unavailable',
        'consecutive_terminal_checks': 2,
        'confirmed_disappearances_24h': 5,
        'inventory_removed_7d': 20,
        'latest_inventory_count': 80,
        'previous_inventory_count': 100,
        'inventory_removed_percent_7d': 25.0,
        'inventory_shrink_percent': 20.0,
        'consecutive_source_failures': 3,
        'unpreserved_available_count': 80,
        'adverse_events_30d': 25,
        'monitored_since': '2024-01-01T00:00:00',
        'quiet_30d': False,
    }
    score, reasons = _score_facts(facts)
    assert score == 100
    assert risk_level(score) == 'critical'
    assert {reason['code'] for reason in reasons} == {
        'terminal_unavailable', 'disappearance_burst',
        'weekly_inventory_loss', 'inventory_shrink', 'source_failures',
        'preservation_gap',
    }
    assert risk_level(24) == 'low'
    assert risk_level(25) == 'elevated'
    assert risk_level(50) == 'high'
    assert risk_level(75) == 'critical'
    vanished = dict(facts)
    vanished.update({
        'availability_state': 'available',
        'consecutive_terminal_checks': 0,
        'confirmed_disappearances_24h': 0,
        'inventory_removed_7d': 100,
        'latest_inventory_count': 0,
        'previous_inventory_count': 100,
        'inventory_removed_percent_7d': 100.0,
        'inventory_shrink_percent': 100.0,
        'consecutive_source_failures': 0,
        'unpreserved_available_count': 0,
        'adverse_events_30d': 100,
    })
    vanished_score, vanished_reasons = _score_facts(vanished)
    assert vanished_score == 35
    assert {reason['code'] for reason in vanished_reasons} == {
        'weekly_inventory_loss', 'inventory_shrink',
    }


def test_inventory_risk_is_explainable_and_exposed(client, monkeypatch):
    import sentinel_inventory

    _insert_channel('RiskInventoryCreator', 'Risk Inventory Films')
    snapshots = iter([
        ['RiskRemote%03d' % index for index in range(100)],
        ['RiskRemote%03d' % index for index in range(80)],
    ])
    monkeypatch.setattr(
        sentinel_inventory, 'iter_playlist_pages',
        lambda *args, **kwargs: iter([_inventory_page(next(snapshots))]),
    )
    sentinel_inventory.census_source(
        'channel', 'RiskInventoryCreator', 'UURiskInventory',
    )
    sentinel_inventory.census_source(
        'channel', 'RiskInventoryCreator', 'UURiskInventory',
    )

    detail = client.get(
        '/api/sentinel/source/channel/RiskInventoryCreator'
    ).get_json()['data']
    assert detail['risk']['score'] == 45
    assert detail['risk']['level'] == 'elevated'
    assert detail['risk']['observation_only'] is True
    assert {reason['code'] for reason in detail['risk']['reasons']} == {
        'weekly_inventory_loss', 'inventory_shrink', 'preservation_gap',
    }
    source = next(
        item for item in client.get('/api/sentinel/sources').get_json()['data']['items']
        if item['channel_id'] == 'RiskInventoryCreator'
    )
    assert source['risk']['score'] == 45
    assert source['risk']['reasons'] == detail['risk']['reasons']
    summary = client.get('/api/sentinel/summary').get_json()['data']
    assert summary['risk_sources']['elevated'] == 1
    assert summary['risk_sources']['observation_only'] is True

    risk_events = client.get(
        '/api/sentinel/events?channel_id=RiskInventoryCreator'
        '&event_type=risk_changed'
    ).get_json()['data']['items']
    assert len(risk_events) == 1
    assert risk_events[0]['entity_type'] == 'source'
    assert risk_events[0]['to_state'] == 'elevated:45'


def test_terminal_source_requires_two_complete_checks_and_restores(
        client, monkeypatch):
    from providers.base import get_alerts
    from sentinel import finish_scan_run, start_scan_run
    from sentinel_risk import record_source_observation
    import sentinel_inventory

    source_id = 'RiskTerminalCreator'
    _insert_channel(source_id, 'Terminal Films')
    monkeypatch.setattr(
        sentinel_inventory, 'iter_playlist_pages',
        lambda *args, **kwargs: iter([_inventory_page([
            'TerminalRemote%02d' % index for index in range(30)
        ])]),
    )
    sentinel_inventory.census_source('channel', source_id, 'UUTerminal')

    first = start_scan_run(
        scan_type='source_presence', source_type='channel', source_id=source_id,
    )
    finish_scan_run(first, 'complete', 1, 1)
    assert record_source_observation('channel', source_id, False, first) is None
    assert client.get(
        '/api/sentinel/source/channel/' + source_id
    ).get_json()['data']['risk']['score'] == 10

    second = start_scan_run(
        scan_type='source_presence', source_type='channel', source_id=source_id,
    )
    finish_scan_run(second, 'complete', 1, 1)
    assert record_source_observation(
        'channel', source_id, False, second,
    ) == 'source_terminal_unavailable'
    risk = client.get(
        '/api/sentinel/source/channel/' + source_id
    ).get_json()['data']['risk']
    assert risk['score'] == 55
    assert risk['level'] == 'high'
    assert any(alert['id'] == 'sentinel_risk_channel_' + source_id
               and 'no downloads were queued' in alert['message']
               for alert in get_alerts())

    third = start_scan_run(
        scan_type='source_presence', source_type='channel', source_id=source_id,
    )
    finish_scan_run(third, 'complete', 1, 1)
    assert record_source_observation(
        'channel', source_id, True, third,
    ) == 'source_terminal_restored'
    restored = client.get(
        '/api/sentinel/source/channel/' + source_id
    ).get_json()['data']['risk']
    assert restored['score'] == 10
    assert restored['level'] == 'low'
    assert not any(alert['id'] == 'sentinel_risk_channel_' + source_id
                   for alert in get_alerts())


def _insert_failed_inventory_runs(source_id, failure_class):
    con = _db_connect()
    cur = con.cursor()
    for index in range(3):
        cur.execute(
            "INSERT INTO sentinel_inventory_runs "
            "(provider, source_type, source_id, remote_collection_id, status, "
            "failure_class, completed_at, error_message) "
            "VALUES('youtube','channel',%s,%s,'partial',%s,NOW(),'test')",
            (source_id, 'UUFailure%d' % index, failure_class),
        )
    cur.close()
    con.close()


def test_only_explicit_source_failures_increase_risk():
    from sentinel_risk import recalculate_source_risk

    _insert_channel('RiskIncompleteCreator', 'Incomplete Films')
    _insert_failed_inventory_runs('RiskIncompleteCreator', 'incomplete')
    incomplete = recalculate_source_risk('channel', 'RiskIncompleteCreator')
    assert incomplete['score'] == 0
    assert incomplete['facts']['consecutive_source_failures'] == 0

    _insert_channel('RiskQuotaCreator', 'Quota Films')
    _insert_failed_inventory_runs('RiskQuotaCreator', 'quota')
    quota = recalculate_source_risk('channel', 'RiskQuotaCreator')
    assert quota['score'] == 0
    assert quota['facts']['consecutive_source_failures'] == 0

    _insert_channel('RiskSourceFailureCreator', 'Failure Films')
    _insert_failed_inventory_runs('RiskSourceFailureCreator', 'source')
    source = recalculate_source_risk('channel', 'RiskSourceFailureCreator')
    assert source['score'] == 10
    assert source['facts']['consecutive_source_failures'] == 3
    assert [reason['code'] for reason in source['reasons']] == [
        'source_failures',
    ]


def test_quiet_period_is_a_stored_negative_reason(monkeypatch):
    import sentinel_inventory
    from sentinel_risk import recalculate_source_risk

    source_id = 'RiskQuietCreator'
    _insert_channel(source_id, 'Quiet Films')
    monkeypatch.setattr(
        sentinel_inventory, 'iter_playlist_pages',
        lambda *args, **kwargs: iter([_inventory_page([])]),
    )
    sentinel_inventory.census_source('channel', source_id, 'UUQuiet')
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "UPDATE sentinel_inventory_runs SET completed_at=DATE_SUB(NOW(), "
        "INTERVAL 31 DAY) WHERE source_id=%s",
        (source_id,),
    )
    cur.close()
    con.close()

    result = recalculate_source_risk('channel', source_id)
    assert result['score'] == 0
    assert result['facts']['unpreserved_available_count'] == 0
    assert result['facts']['quiet_30d'] is True
    quiet = next(reason for reason in result['reasons']
                 if reason['code'] == 'quiet_30d')
    assert quiet['points'] == -15


def test_incomplete_source_scan_cannot_change_terminal_state():
    from sentinel import finish_scan_run, start_scan_run
    from sentinel_risk import record_source_observation

    source_id = 'RiskFailedObservation'
    _insert_channel(source_id, 'Failed Observation Films')
    scan = start_scan_run(
        scan_type='source_presence', source_type='channel', source_id=source_id,
    )
    finish_scan_run(scan, 'failed', 0, 1, 'quota')
    try:
        record_source_observation('channel', source_id, False, scan)
        assert False, 'failed source scan must not be observed'
    except ValueError:
        pass

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM sentinel_sources WHERE source_id=%s",
        (source_id,),
    )
    assert cur.fetchone()[0] == 0
    cur.close()
    con.close()


def test_census_source_presence_integration_confirms_without_inventory(
        client, monkeypatch):
    import sentinel_inventory
    from providers.youtube import YouTubeRequestBudget

    source_id = 'UCRiskCensusMissingCreator'
    _insert_channel(source_id, 'Missing Census Films')
    monkeypatch.setattr(
        sentinel_inventory, 'get_active_subscriptions',
        lambda: [(source_id,)],
    )
    monkeypatch.setattr(
        sentinel_inventory, 'get_active_playlist_subs', lambda: [],
    )
    monkeypatch.setattr(
        sentinel_inventory, 'check_channel_presence',
        lambda channel_id, budget: False,
    )
    inventory_calls = []
    monkeypatch.setattr(
        sentinel_inventory, 'census_source',
        lambda *args, **kwargs: inventory_calls.append(args),
    )

    sentinel_inventory.census_once(
        client.application, YouTubeRequestBudget(10), interval_seconds=0,
    )
    first = client.get(
        '/api/sentinel/source/channel/' + source_id
    ).get_json()['data']['risk']
    assert first['availability_state'] == 'suspected_unavailable'
    assert first['score'] == 0

    sentinel_inventory.census_once(
        client.application, YouTubeRequestBudget(10), interval_seconds=0,
    )
    second = client.get(
        '/api/sentinel/source/channel/' + source_id
    ).get_json()['data']['risk']
    assert second['availability_state'] == 'unavailable'
    assert second['score'] == 45
    assert second['level'] == 'elevated'
    assert inventory_calls == []


def test_failed_presence_check_is_score_neutral(client, monkeypatch):
    import sentinel_inventory
    from providers.youtube import YouTubeRequestBudget, YouTubeSourceCheckFailed

    source_id = 'UCRiskPresenceAuthCreator'
    _insert_channel(source_id, 'Auth Failure Films')
    monkeypatch.setattr(
        sentinel_inventory, 'get_active_subscriptions',
        lambda: [(source_id,)],
    )
    monkeypatch.setattr(
        sentinel_inventory, 'get_active_playlist_subs', lambda: [],
    )

    def auth_failure(channel_id, budget):
        error = YouTubeSourceCheckFailed('invalid credentials')
        error.failure_class = 'auth'
        raise error

    monkeypatch.setattr(
        sentinel_inventory, 'check_channel_presence', auth_failure,
    )
    sentinel_inventory.census_once(
        client.application, YouTubeRequestBudget(10), interval_seconds=0,
    )

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT status FROM sentinel_scan_runs WHERE source_id=%s",
        (source_id,),
    )
    assert cur.fetchone()[0] == 'failed'
    cur.execute(
        "SELECT COUNT(*) FROM sentinel_sources WHERE source_id=%s",
        (source_id,),
    )
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT COUNT(*) FROM sentinel_events WHERE source_id=%s",
        (source_id,),
    )
    assert cur.fetchone()[0] == 0
    cur.close()
    con.close()


def test_channel_presence_requires_a_successful_provider_response(monkeypatch):
    import requests
    from providers.youtube import (
        YouTubeQuotaExceeded, YouTubeRequestBudget,
        check_channel_presence,
    )

    payloads = iter([
        {'items': [{'id': 'UCConfirmedPresent'}]},
        {'items': []},
        {'error': {'errors': [{'reason': 'quotaExceeded'}]}},
    ])
    calls = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def close(self):
            pass

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(next(payloads))

    monkeypatch.setattr(requests, 'get', fake_get)
    budget = YouTubeRequestBudget(3)
    assert check_channel_presence('UCConfirmedPresent', budget) is True
    assert check_channel_presence('UCConfirmedMissing', budget) is False
    try:
        check_channel_presence('UCQuotaUnknown', budget)
        assert False, 'quota response must not become a negative observation'
    except YouTubeQuotaExceeded:
        pass
    assert budget.used == 3
    assert all(call[0].endswith('/channels') for call in calls)
    assert calls[0][1]['params']['part'] == 'id'
