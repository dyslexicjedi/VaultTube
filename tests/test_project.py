import pytest,os,json,mariadb
import threading
from providers.base import set_status, update_status, del_status, get_status_copy


def _db_connect():
    return mariadb.connect(
        host=os.environ['VAULTTUBE_DBHOST'],
        user=os.environ['VAULTTUBE_DBUSER'],
        password=os.environ['VAULTTUBE_DBPASS'],
        database=os.environ['VAULTTUBE_DBNAME'],
        autocommit=True,
        port=int(os.environ['VAULTTUBE_DBPORT'])
    )


def test_home(client):
    response = client.get("/")
    assert response.status_code == 200


def test_populate_db(client):
    try:
        response = client.get("/api/checkdb")
        assert response.text == "True"
        con = _db_connect()
        cur = con.cursor()
        cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('Test123','Test123','Test123',0);")
        con.commit()
        cur.execute("Select * from channels limit 1;")
        assert 1 == cur.rowcount
        cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('Test123','Test123','Test123','TestJSON','/videos/1','2023-10-21 15:15:15',0,0);")
        con.commit()
        cur.execute("Select * from videos limit 1;")
        assert 1 == cur.rowcount
        con.close()
    except Exception as e:
        print(e)
        assert True == False


def test_subscribe(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute("REPLACE INTO channels(channelid,channelname,json,subscribed) values('SubTest123','SubTest123','{}',0);")
    con.commit()
    # Query the DB directly for the test channel's ID
    cur.execute("SELECT channelid FROM channels WHERE channelid = %s;", ("SubTest123",))
    ch_row = cur.fetchone()
    assert ch_row is not None, "Test channel should exist in DB"
    ch_id = ch_row[0]
    con.close()

    response = client.get("/api/sub_status/channel/%s" % ch_id)
    assert response.text == '0'

    response = client.get("/api/subscribe/channel/%s" % ch_id)
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/channel/%s" % ch_id)
    assert response.text == '1'

    response = client.get("/api/unsubscribe/channel/%s" % ch_id)
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/channel/%s" % ch_id)
    assert response.text == '0'


def test_search_fulltext(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, youtuber, channelId, json, filepath, PublishedAt, title, description) "
        "VALUES('SearchFT1', 'TestCreator', 'TestChannel1', '{}', '/videos/search1.mp4', "
        "'2023-10-21 15:15:15', 'Python Tutorial Advanced', 'Learn advanced Python programming techniques for vaulttubefulltextkw');"
    )
    con.commit()
    con.close()

    # Search for a unique keyword that only exists in our test data - guarantees top rank
    response = client.get("/api/search/vaulttubefulltextkw/0")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert 'SearchFT1' in ids, "FULLTEXT search should find SearchFT1 by description with unique keyword"

    # Search for a common word - verify search returns results containing the term
    response = client.get("/api/search/advanced/0")
    data = json.loads(response.get_data(as_text=True))
    assert len(data) > 0, "Search for 'advanced' should return results"
    found_advanced = any('advanced' in (v.get('title') or '').lower() or
                         'advanced' in (v.get('description') or '').lower()
                         for v in data)
    assert found_advanced, "At least one result should contain 'advanced'"

    response = client.get("/api/search/xyznonexistent/0")
    data = json.loads(response.get_data(as_text=True))
    assert data == [], "Search for nonexistent term should return empty list"


def test_search_short_query(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, youtuber, channelId, json, filepath, PublishedAt, title, description) "
        "VALUES('SearchLIKE1', 'TestCreator', 'TestChannel1', '{}', '/videos/search2.mp4', "
        "'2023-10-21 15:15:15', 'Zynced Workflow Tool', 'A unique workflow tool');"
    )
    con.commit()
    con.close()

    response = client.get("/api/search/Zy/0")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert 'SearchLIKE1' in ids, "Short query LIKE fallback should find SearchLIKE1 by title prefix"


def test_get_video(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO channels(channelid,channelname,json,subscribed) "
        "VALUES('GetVidCh1', 'GetVidYoutuber', '{}', 0);"
    )
    con.commit()
    cur.execute(
        "REPLACE INTO videos(id, youtuber, channelId, json, filepath, PublishedAt, title, description, watched) "
        "VALUES('GetVid1', 'GetVidYoutuber', 'GetVidCh1', '{}', '/videos/getvid1.mp4', "
        "'2024-01-01 10:00:00', 'Get Video Test', 'Testing get video API', 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/video/GetVid1")
    data = json.loads(response.get_data(as_text=True))
    assert len(data) > 0
    assert data[0]['id'] == 'GetVid1'
    assert data[0]['youtuber'] == 'GetVidYoutuber'
    assert data[0]['title'] == 'Get Video Test'


def test_get_video_mp4_suffix(client):
    """Test that video lookup strips .mp4 from the id."""
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, youtuber, channelId, json, filepath, PublishedAt, title, watched) "
        "VALUES('GetVidMp4', 'TestYt', 'Ch1', '{}', '/videos/GetVidMp4.mp4', "
        "'2024-01-01 10:00:00', 'Mp4 Test', 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/video/GetVidMp4.mp4")
    data = json.loads(response.get_data(as_text=True))
    assert len(data) > 0
    assert data[0]['id'] == 'GetVidMp4'


def test_mark_watched(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, youtuber, channelId, json, filepath, PublishedAt, title, watched, timestamp) "
        "VALUES('WatchVid1', 'TestYt', 'Ch1', '{}', '/videos/watchvid1.mp4', "
        "'2024-01-01 10:00:00', 'Watch Test', 0, 0);"
    )
    con.commit()
    con.close()

    # Verify not watched
    response = client.get("/api/watch_status/WatchVid1")
    assert response.text == "0"

    # Mark watched
    response = client.get("/api/watched/WatchVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    # Verify watched
    response = client.get("/api/watch_status/WatchVid1")
    assert response.text == "1"


def test_mark_unwatched(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, youtuber, channelId, json, filepath, PublishedAt, title, watched, timestamp) "
        "VALUES('UnwatchVid1', 'TestYt', 'Ch1', '{}', '/videos/unwatchvid1.mp4', "
        "'2024-01-01 10:00:00', 'Unwatch Test', 1, 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/watch_status/UnwatchVid1")
    assert response.text == "1"

    response = client.get("/api/unwatched/UnwatchVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/watch_status/UnwatchVid1")
    assert response.text == "0"


def test_set_timestamp(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, youtuber, channelId, json, filepath, PublishedAt, title, watched, timestamp) "
        "VALUES('TsVid1', 'TestYt', 'Ch1', '{}', '/videos/tsvid1.mp4', "
        "'2024-01-01 10:00:00', 'Timestamp Test', 0, 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/set_timestamp/1515/TsVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/video/TsVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data[0]['timestamp'] == "1515"


def test_download_queue(client, monkeypatch):
    """Test that downloading a URL enqueues a job and returns success."""
    import queue as _queue

    # Ensure queue is initialized for test client
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = _queue.Queue()
        q = client.application.config['queue']
    # Ensure queue is empty before test
    while not q.empty():
        try:
            q.get_nowait()
        except _queue.Empty:
            break

    response = client.post("/api/download/single", json={"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    with client.application.app_context():
        assert q.qsize() == 1


def test_download_queue_missing_url(client):
    """Test that downloading without a URL returns an error."""
    response = client.post("/api/download/single", json={})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is False


def test_video_count(client):
    response = client.get("/api/stats/video/count")
    assert response.status_code == 200
    # Should return a non-negative integer string
    count = int(response.text)
    assert count >= 0


def test_video_unwatched_count(client):
    response = client.get("/api/stats/video/unwatched")
    assert response.status_code == 200
    count = int(response.text)
    assert count >= 0


def test_stats(client):
    response = client.get("/api/stats")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, dict)
    totals = data['totals']
    for key in ('videos', 'watched', 'unwatched', 'total_seconds', 'avg_seconds', 'channels', 'playlists'):
        assert isinstance(totals[key], int)
    assert totals['watched'] + totals['unwatched'] == totals['videos']
    assert isinstance(data['by_source'], list)
    assert isinstance(data['top_channels'], list)
    assert len(data['top_channels']) <= 10
    assert len(data['added_per_week']) == 26
    assert len(data['duration_buckets']) == 5
    assert 'deleted' in data['deleted'] and 'youtube_total' in data['deleted']


def test_random_video(client):
    response = client.get("/api/random")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_random_video_include_reddit(client):
    response = client.get("/api/random?include_reddit=1")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_channels_page(client):
    response = client.get("/api/channels/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    for ch in data:
        assert 'unwatched' in ch
        assert 'vidcount' in ch
        assert 'source' in ch


def test_channels_order_activity(client):
    response = client.get("/api/channels/0?order=activity")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_browse_page(client):
    response = client.get("/browse.html")
    assert response.status_code == 200
    assert b'browse-grid' in response.data


def test_up_next(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('UpNextCh1','UpNextCh1','{}',0);")
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('UpNext1','UpNextCh1','UpNextCh1','{}','/videos/1','2023-01-02 12:00:00',0,0);")
    # Published after the current video and unwatched -> must be first in the list
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('UpNext2','UpNextCh1','UpNextCh1','{}','/videos/2','2023-01-03 12:00:00',0,0);")
    # Watched -> must never appear
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('UpNext3','UpNextCh1','UpNextCh1','{}','/videos/3','2023-01-01 12:00:00',1,0);")
    con.close()

    response = client.get("/api/up_next/UpNext1?limit=5")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]['id'] == 'UpNext2'
    ids = [v['id'] for v in data]
    assert 'UpNext1' not in ids   # never suggest the current video
    assert 'UpNext3' not in ids   # never suggest watched videos
    assert len(data) <= 5


def test_up_next_unknown_video(client):
    response = client.get("/api/up_next/NonexistentVid123")
    data = json.loads(response.get_data(as_text=True))
    assert data == []


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def close(self):
        pass


def _playlist_page(video_ids, next_token=None):
    page = {'items': [{'contentDetails': {'videoId': v}} for v in video_ids]}
    if next_token:
        page['nextPageToken'] = next_token
    return page


def _ensure_queue(client):
    import queue as _queue
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = _queue.Queue()
        q = client.application.config['queue']
    while not q.empty():
        try:
            q.get_nowait()
        except _queue.Empty:
            break
    return q


def test_uploads_playlist_id():
    from scanner import uploads_playlist_id
    assert uploads_playlist_id('UCVtTestChannel1') == 'UUVtTestChannel1'


def test_error_type_classification():
    from downloader import get_error_type
    # Throttling responses must be transient (retried), not permanent failures
    assert get_error_type('HTTP Error 429: Too Many Requests') == 'Network Error'
    assert get_error_type('Download throttled by server') == 'Network Error'
    assert get_error_type('Connection reset by peer') == 'Network Error'
    assert get_error_type('Video not found') == 'Content Not Found'
    assert get_error_type('HTTP Error 403: Forbidden') == 'Authentication Error'
    assert get_error_type('something exploded') == 'Provider Error'


def test_channel_scan_paginates(client, monkeypatch):
    """The scan must walk every playlistItems page, not just the first."""
    import logging, requests, scanner
    q = _ensure_queue(client)

    pages = [
        _playlist_page(['VtScanVid1', 'VtScanVid2'], next_token='p2'),
        _playlist_page(['VtScanVid3']),
    ]
    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **kw: (calls.append(url), _FakeResp(pages[len(calls) - 1]))[1])

    with client.application.app_context():
        scanner.get_channel_video_list(('UCVtTestChannel1',), logging.getLogger('test'))

    assert len(calls) == 2
    assert 'UUVtTestChannel1' in calls[0]          # uploads playlist derived, no channels.list call
    assert 'maxResults=50' in calls[0]
    assert 'pageToken=p2' in calls[1]
    assert q.qsize() == 3
    urls = sorted(qo.url for qo in list(q.queue))
    assert urls == [
        'https://www.youtube.com/watch?v=VtScanVid1',
        'https://www.youtube.com/watch?v=VtScanVid2',
        'https://www.youtube.com/watch?v=VtScanVid3',
    ]


def test_channel_scan_stops_when_caught_up(client, monkeypatch):
    """A page with nothing new means everything older is known: stop paging."""
    import logging, requests, scanner
    q = _ensure_queue(client)

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('VtScanVid1','UCVtTestChannel1','UCVtTestChannel1','{}','/videos/1','2024-01-01 10:00:00',0,0);")
    con.close()

    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **kw: (calls.append(url), _FakeResp(_playlist_page(['VtScanVid1'], next_token='p2')))[1])

    with client.application.app_context():
        scanner.get_channel_video_list(('UCVtTestChannel1',), logging.getLogger('test'))

    assert len(calls) == 1   # did not fetch page 2
    assert q.qsize() == 0


def test_channel_scan_mixed_page_takes_new_only(client, monkeypatch):
    """New uploads on a page with known videos are enqueued, but paging stops
    there — a subscription must not backfill deep history."""
    import logging, requests, scanner
    q = _ensure_queue(client)

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('VtScanVid1','UCVtTestChannel1','UCVtTestChannel1','{}','/videos/1','2024-01-01 10:00:00',0,0);")
    con.close()

    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **kw: (calls.append(url), _FakeResp(_playlist_page(['VtScanVid3', 'VtScanVid1'], next_token='p2')))[1])

    with client.application.app_context():
        scanner.get_channel_video_list(('UCVtTestChannel1',), logging.getLogger('test'))

    assert len(calls) == 1
    assert q.qsize() == 1
    assert list(q.queue)[0].url == 'https://www.youtube.com/watch?v=VtScanVid3'


def test_download_playlist_writes_queue_rows(client, monkeypatch):
    """Playlist expansion must go through enqueue() so queue rows persist."""
    import logging, requests
    import providers.youtube as yt
    q = _ensure_queue(client)

    monkeypatch.setattr(requests, 'get', lambda url, **kw: _FakeResp(_playlist_page(['VtPlVid1'])))

    from QueueObject import QueueObject
    with client.application.app_context():
        result = yt.download_playlist(QueueObject('PLVtTest123', '', 'youtube', 0, ''), logging.getLogger('test'))

    assert result is True
    assert q.qsize() == 1

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select status from queue where url = 'https://www.youtube.com/watch?v=VtPlVid1'")
    row = cur.fetchone()
    assert row is not None and row[0] == 'pending'
    cur.execute("Select count(*) from pl2vid where playlistId = 'PLVtTest123' and videoId = 'VtPlVid1'")
    assert cur.fetchone()[0] == 1
    con.close()


def test_channel_source_url(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('UCVtTestChannel1','YT Test','{}',0);")
    cur.execute("""Insert into channels(channelid,channelname,json,subscribed) values('987654321099','Patreon Test','{"data":{"attributes":{"name":"Patreon Test","url":"https://www.patreon.com/vttest"}}}',0);""")
    cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('VtTestRedditUser','VtTestRedditUser','{}',0);")
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('RedVid1','VtTestRedditUser','VtTestRedditUser','{}','/videos/1','2023-03-01 12:00:00',0,0);")
    cur.execute("Update videos set source='reddit' where id='RedVid1';")
    con.close()

    cases = {
        'UCVtTestChannel1': 'https://www.youtube.com/channel/UCVtTestChannel1',
        '987654321099': 'https://www.patreon.com/vttest',
        'VtTestRedditUser': 'https://www.reddit.com/user/VtTestRedditUser',
    }
    for channelid, expected in cases.items():
        response = client.get("/api/channel/" + channelid)
        data = json.loads(response.get_data(as_text=True))
        assert data['source_url'] == expected, channelid


def test_channel_source_url_unknown(client):
    response = client.get("/api/channel/NoSuchChannelXyz")
    data = json.loads(response.get_data(as_text=True))
    assert data['source_url'] is None
    assert data['channelname'] is None


def test_insert_not_found_goes_to_ignorevid(client):
    import logging
    from database import insert_not_found
    insert_not_found('TombVid2', logging.getLogger('test'))

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select count(*) from IgnoreVid where id = 'TombVid2'")
    assert cur.fetchone()[0] == 1
    cur.execute("Select count(*) from videos where id = 'TombVid2'")
    assert cur.fetchone()[0] == 0
    con.close()


def test_tombstone_migration(client):
    con = _db_connect()
    cur = con.cursor()
    # A legacy-style tombstone row, as old insert_not_found wrote them
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,watched,timestamp,length) values('TombVid1','404','404','404','404',1,0,'0');")
    con.close()

    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select count(*) from videos where id = 'TombVid1'")
    assert cur.fetchone()[0] == 0
    cur.execute("Select count(*) from IgnoreVid where id = 'TombVid1'")
    assert cur.fetchone()[0] == 1
    con.close()


def test_getvids_deleted_filter(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp,isDeleted) values('DelVid1','GetVidCh1','GetVidCh1','{}','/videos/1','2023-02-01 12:00:00',0,0,1);")
    cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp,isDeleted) values('DelVid2','GetVidCh1','GetVidCh1','{}','/videos/2','2023-02-02 12:00:00',0,0,0);")
    con.close()

    response = client.get("/api/getvids/all/PublishedAt/desc/0?deleted=1&channel_ids[]=GetVidCh1")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert 'DelVid1' in ids
    assert 'DelVid2' not in ids


def test_queue_page(client):
    response = client.get("/queue.html")
    assert response.status_code == 200
    assert b'active-list' in response.data


def test_download_upload_redirect_to_queue(client):
    for path in ("/download.html", "/upload.html"):
        response = client.get(path)
        assert response.status_code == 301
        assert response.headers['Location'].endswith('/queue.html')


def test_retry_download(client):
    import queue as _queue
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = _queue.Queue()
        q = client.application.config['queue']
    while not q.empty():
        try:
            q.get_nowait()
        except _queue.Empty:
            break

    # URL is registered in conftest's _TEST_QUEUE_URLS for DB cleanup
    response = client.post("/api/downloads/retry", json={"url": "https://example.com/vt-test-queue-row"})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True
    assert data['data']['enqueued'] is True
    assert q.qsize() == 1

    # Same URL again: still pending, so it must be skipped
    response = client.post("/api/downloads/retry", json={"url": "https://example.com/vt-test-queue-row"})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True
    assert data['data']['enqueued'] is False
    assert q.qsize() == 1


def test_retry_download_missing_url(client):
    response = client.post("/api/downloads/retry", json={})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is False


@pytest.mark.parametrize("path,marker", [
    ("/", b"vt-hero"),
    ("/browse.html", b"browse-grid"),
    ("/player.html", b"upnext-list"),
    ("/queue.html", b"active-list"),
    ("/channels.html", b"channels-grid"),
    ("/creator.html", b"creator-grid"),
    ("/search.html", b"search-grid"),
    ("/stats.html", b"channelChart"),
    ("/playlists.html", b"playlists-grid"),
    ("/playlist.html", b"playlist-grid"),
    ("/random.html", b"random-grid"),
])
def test_page_renders(client, path, marker):
    response = client.get(path)
    assert response.status_code == 200
    assert marker in response.data
    assert b"vt-sidebar" in response.data  # every page carries the app shell


def test_playlists_page(client):
    response = client.get("/api/playlists/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_creator_count(client):
    response = client.get("/api/creator/count/TestCreator")
    data = json.loads(response.get_data(as_text=True))
    assert 'count' in data
    assert isinstance(data['count'], int)


def test_video_getvids_unwatched(client):
    response = client.get("/api/getvids/unwatched/PublishedAt/desc/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_video_getvids_watched(client):
    response = client.get("/api/getvids/watched/PublishedAt/desc/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_video_getvids_invalid_sort(client):
    """Test that invalid sort column falls back to default."""
    response = client.get("/api/getvids/unwatched/invalid_col/desc/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_search_empty_result(client):
    response = client.get("/api/search/zzzznonexistent12345/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    # Should be empty or not contain test-specific IDs
    for v in data:
        assert v.get('id') not in ('SearchFT1', 'SearchLIKE1')


def test_api_checkdb(client):
    response = client.get("/api/checkdb")
    assert response.status_code == 200
    assert response.text == "True"


def test_queue_status(client):
    # Initialize the queue for test client
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = __import__('queue').Queue()

    response = client.get("/api/status/queue/")
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert 'dl_status' in data
    assert 'queue_size' in data
    assert 'queue_value' in data
    assert 'cur_id' in data
    assert 'cur_title' in data
    assert 'active' in data


def test_subscribe_playlist(client):
    """Test subscribing to a playlist (existing playlist in DB)."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO playlists(playlistId,playlistName,channelId,json,subscribed) "
        "VALUES('PLTest123', 'Test Playlist', 'TestCh1', '{}', 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/subscribe/playlist/PLTest123")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/playlist/PLTest123")
    assert response.text == '1'

    response = client.get("/api/unsubscribe/playlist/PLTest123")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/playlist/PLTest123")
    assert response.text == '0'


def test_unsubscribe_nonexistent(client):
    """Test unsubscribing from a channel that doesn't exists (should not error)."""
    response = client.get("/api/unsubscribe/channel/NonExistentChannel12345")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True


def test_getvids_with_channel_filter(client):
    """Test getvids endpoint with channel_ids filter."""
    response = client.get("/api/getvids/unwatched/PublishedAt/desc/0?channel_ids[]=TestChannel1")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_download_error_entry(client):
    """Test that download errors can be logged and retrieved."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO download_errors(url, error_type, error_message) VALUES(%s, %s, %s);",
        ("https://example.com/bad", "TestError", "Test error message")
    )
    con.commit()
    con.close()

    response = client.get("/api/downloads/errors/")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(e['url'] == 'https://example.com/bad' for e in data)


def test_clear_download_errors(client):
    """Test clearing download errors."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute("INSERT INTO download_errors(url, error_type, error_message) VALUES(%s, %s, %s);",
                ("https://example.com/clear", "TestErr", "To clear"))
    con.commit()
    con.close()

    response = client.delete("/api/downloads/errors/")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/downloads/errors/")
    data = json.loads(response.get_data(as_text=True))
    assert not any(e['url'] == 'https://example.com/clear' for e in data)


def test_dl_status_set_update_get():
    """Test individual dl_status_map operations."""
    video_id = "dl_test_vid_001"
    set_status(video_id, {'progress': '0%', 'title': 'Test Title', 'type': 'youtube'})
    status = get_status_copy()
    assert video_id in status
    assert status[video_id]['progress'] == '0%'
    assert status[video_id]['title'] == 'Test Title'

    update_status(video_id, {'progress': '50%', 'title': 'Updated Title'})
    status = get_status_copy()
    assert status[video_id]['progress'] == '50%'
    assert status[video_id]['title'] == 'Updated Title'

    del_status(video_id)
    status = get_status_copy()
    assert video_id not in status


def test_dl_status_overwrite(client):
    """Test that set_status overwrites existing entries."""
    video_id = "dl_test_vid_002"
    set_status(video_id, {'progress': '10%', 'title': 'First'})
    set_status(video_id, {'progress': '90%', 'title': 'Second'})
    status = get_status_copy()
    assert status[video_id]['progress'] == '90%'
    assert status[video_id]['title'] == 'Second'
    del_status(video_id)


def test_getvids_sort_options(client):
    """Test getvids with various sort options."""
    for sort_col in ['PublishedAt', 'AddedAt', 'title', 'watched']:
        response = client.get(f"/api/getvids/unwatched/{sort_col}/desc/0")
        data = json.loads(response.get_data(as_text=True))
        assert isinstance(data, list), f"Sort column '{sort_col}' should return a list"


def test_getvids_direction_options(client):
    """Test getvids with sort directions."""
    for direction in ['asc', 'desc']:
        response = client.get(f"/api/getvids/unwatched/PublishedAt/{direction}/0")
        data = json.loads(response.get_data(as_text=True))
        assert isinstance(data, list), f"Direction '{direction}' should return a list"


def test_getvids_invalid_direction(client):
    """Test that invalid direction falls back to default."""
    response = client.get("/api/getvids/unwatched/PublishedAt/invalid/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)




def test_patreon_normalize_url():
    """Creator-prefixed Patreon URLs must be normalized for yt-dlp's extractor."""
    from providers.patreon import _normalize_url
    assert _normalize_url("https://www.patreon.com/TeeReacts/posts/foo-123") == "https://www.patreon.com/posts/foo-123"
    assert _normalize_url("https://www.patreon.com/posts/foo-123") == "https://www.patreon.com/posts/foo-123"
    assert _normalize_url("https://www.youtube.com/watch?v=abc") == "https://www.youtube.com/watch?v=abc"


def test_patreon_scan_campaign_filters(client, monkeypatch):
    """scan_campaign enqueues only new, viewable video posts."""
    import queue as _queue
    import providers.patreon as patreon

    posts = {'data': [
        {'id': '1', 'attributes': {'post_type': 'text_only', 'current_user_can_view': True, 'title': 'announcement'}},
        {'id': '2', 'attributes': {'post_type': 'video_external_file', 'current_user_can_view': False, 'title': 'locked'}},
        {'id': '3', 'attributes': {'post_type': 'video_external_file', 'current_user_can_view': True, 'title': 'already have'}},
        {'id': '4', 'attributes': {'post_type': 'video_external_file', 'current_user_can_view': True, 'title': 'new video'}},
    ]}
    monkeypatch.setattr(patreon, '_api_get', lambda url, logger: posts)
    monkeypatch.setattr(patreon, 'check_db_video', lambda id, logger: id == '3')
    # bypass DB persistence so the fake post URL never lands in the real queue table
    monkeypatch.setattr(patreon, 'enqueue', lambda qo, q, logger: q.put(qo))
    monkeypatch.setenv('VAULTTUBE_PATREONCOOKIE', '/tmp/fake-cookie')

    client.application.config['queue'] = _queue.Queue()
    with client.application.app_context():
        patreon.scan_campaign('11752268', client.application.logger)

    q = client.application.config['queue']
    assert q.qsize() == 1
    qo = q.get()
    assert qo.url == 'https://www.patreon.com/posts/4'
    assert qo.source == 'patreon'


def test_queue_persistence_roundtrip(client):
    """Queue rows are inserted as pending, resumable, and excluded once done."""
    from database import insert_queue_item, update_queue_status, get_resumable_queue_items
    from QueueObject import QueueObject

    logger = client.application.logger
    qo = QueueObject("https://example.com/vt-test-queue-row", "", "youtube", 0, "")
    rowid = qo.row_id = insert_queue_item(qo, logger)
    assert rowid is not None
    try:
        assert any(r[0] == rowid for r in get_resumable_queue_items(logger))
        update_queue_status(rowid, 'done', logger)
        assert not any(r[0] == rowid for r in get_resumable_queue_items(logger))
    finally:
        con = _db_connect()
        cur = con.cursor()
        cur.execute("DELETE FROM queue WHERE id = %s", (rowid,))
        con.commit()
        con.close()


def test_download_single_persists_queue_row(client):
    """POST /api/download/single writes a queue table row."""
    import queue as _queue
    client.application.config['queue'] = _queue.Queue()
    url = "https://www.youtube.com/watch?v=vt-test-1"
    response = client.post("/api/download/single", json={"url": url})
    assert response.status_code == 200
    con = _db_connect()
    cur = con.cursor()
    try:
        cur.execute("SELECT status FROM queue WHERE url = %s", (url,))
        row = cur.fetchone()
        assert row is not None and row[0] == 'pending'
    finally:
        cur.execute("DELETE FROM queue WHERE url = %s", (url,))
        con.commit()
        con.close()


def test_handle_failure_retries_transient(client, monkeypatch):
    """Network errors are requeued with attempt tracking; permanent errors are not."""
    import downloader
    from QueueObject import QueueObject

    updates = []
    errors = []
    timers = []
    monkeypatch.setattr(downloader, 'update_queue_status',
                        lambda rowid, status, logger, error=None, attempts=None: updates.append((status, attempts)))
    monkeypatch.setattr(downloader, 'insert_download_error',
                        lambda url, et, em, logger: errors.append(et))

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            timers.append((delay, fn, args))
        def start(self):
            pass
    monkeypatch.setattr(downloader.threading, 'Timer', FakeTimer)

    logger = client.application.logger

    class FakeQueue:
        def put(self, item):
            pass
    q = FakeQueue()

    # Transient: requeued, no download_errors row
    qo = QueueObject("https://example.com/a")
    downloader.handle_failure(qo, q, 'Network Error', 'Connection timed out', logger)
    assert updates == [('pending', 1)] and timers and not errors

    # Exhausted attempts: marked failed and recorded
    qo2 = QueueObject("https://example.com/b")
    qo2.attempts = downloader.MAX_ATTEMPTS - 1
    downloader.handle_failure(qo2, q, 'Network Error', 'Connection timed out', logger)
    assert updates[-1] == ('failed', downloader.MAX_ATTEMPTS) and errors == ['Network Error']

    # Permanent: marked failed immediately
    qo3 = QueueObject("https://example.com/c")
    downloader.handle_failure(qo3, q, 'Provider Error', 'No supported media', logger)
    assert updates[-1] == ('failed', 1) and errors[-1] == 'Provider Error'


def test_enqueue_dedups_pending_urls(client):
    """enqueue skips URLs already pending in the queue table."""
    import queue as _queue
    from queue_utils import enqueue
    from QueueObject import QueueObject

    logger = client.application.logger
    q = _queue.Queue()
    url = "https://example.com/vt-test-queue-row"
    try:
        assert enqueue(QueueObject(url), q, logger) is True
        assert enqueue(QueueObject(url), q, logger) is False
        assert q.qsize() == 1
    finally:
        con = _db_connect()
        cur = con.cursor()
        cur.execute("DELETE FROM queue WHERE url = %s", (url,))
        con.commit()
        con.close()


def test_patreon_inline_video_detection():
    """Video blocks in block-editor post bodies are detected; plain text is not."""
    from providers.patreon import _inline_video_media_ids, _post_has_video

    cjs_video = '{"type":"doc","content":[{"type":"video","attrs":{"fallback_strategy":"fallback","media_id":"645278519"}},{"type":"paragraph","content":[]}]}'
    cjs_text = '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"hi"}]}]}'

    assert _inline_video_media_ids(cjs_video) == ['645278519']
    assert _inline_video_media_ids(cjs_text) == []
    assert _inline_video_media_ids(None) == []
    assert _inline_video_media_ids('not json') == []

    assert _post_has_video({'post_type': 'video_external_file'}) is True
    assert _post_has_video({'post_type': 'text_only', 'content_json_string': cjs_video}) is True
    assert _post_has_video({'post_type': 'text_only', 'content_json_string': cjs_text}) is False
    assert _post_has_video({'post_type': 'text_only'}) is False
