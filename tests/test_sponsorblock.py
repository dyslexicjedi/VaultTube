import datetime
import hashlib
import json
import os

import mariadb
import pytest

import sponsorblock


def _db_connect():
    return mariadb.connect(
        host=os.environ['VAULTTUBE_DBHOST'],
        user=os.environ['VAULTTUBE_DBUSER'],
        password=os.environ['VAULTTUBE_DBPASS'],
        database=os.environ['VAULTTUBE_DBNAME'],
        autocommit=True,
        port=int(os.environ['VAULTTUBE_DBPORT'])
    )


def _insert_video(vid, source='youtube', published=None, is_deleted=0, description=''):
    published = published or datetime.datetime.now() - datetime.timedelta(days=400)
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, "
        "title, description, source, isDeleted) "
        "VALUES(%s, 'SB', 'SBCh', '{}', %s, %s, 'SB test', %s, %s, %s)",
        (vid, '/videos/%s.mp4' % vid, published, description, source, is_deleted))
    con.close()


def _seg(uuid, start, end, category='sponsor', action='skip', description=''):
    return {'segment': [start, end], 'UUID': uuid, 'category': category,
            'actionType': action, 'votes': 3, 'description': description}


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('HTTP %d' % self.status_code)


@pytest.fixture
def fake_api(monkeypatch):
    """Patch the SponsorBlock HTTP call; set .response (or .error) per test."""
    class Api:
        calls = []
        response = _Resp([])
        error = None

    def fake_get(url, params=None, timeout=None):
        Api.calls.append((url, params))
        if Api.error:
            raise Api.error
        return Api.response

    monkeypatch.setattr(sponsorblock.requests, 'get', fake_get)
    Api.calls = []
    return Api


def _set_fetch_times(vid, fetched_at, attempted_at=None):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "UPDATE sponsorblock_fetch SET fetched_at = %s, attempted_at = %s WHERE video_id = %s",
        (fetched_at, attempted_at or fetched_at, vid))
    con.close()


# ---------------- pure helpers ----------------

def test_hash_prefix_is_first_four_sha256_chars():
    assert sponsorblock.hash_prefix('abc') == hashlib.sha256(b'abc').hexdigest()[:4]


def test_parse_segments_picks_video_and_sorts():
    payload = [
        {'videoID': 'other', 'segments': [_seg('x', 1, 2)]},
        {'videoID': 'vid1', 'segments': [
            _seg('b', 60, 90),
            _seg('a', 10, 20, category='intro'),
            {'segment': ['bad'], 'UUID': 'c', 'category': 'sponsor'},
            _seg('d', 50, 40),   # end before start
        ]},
    ]
    segs = sponsorblock.parse_segments(payload, 'vid1')
    assert [s['uuid'] for s in segs] == ['a', 'b']
    assert segs[0]['category'] == 'intro'
    assert segs[1]['start'] == 60.0 and segs[1]['end'] == 90.0


def test_parse_segments_rejects_non_list():
    assert sponsorblock.parse_segments({'error': 'x'}, 'vid1') == []
    assert sponsorblock.parse_segments(None, 'vid1') == []


def test_chapters_from_segments():
    segs = sponsorblock.parse_segments([{'videoID': 'v', 'segments': [
        _seg('c1', 0, 30, category='chapter', action='chapter', description='Intro'),
        _seg('s1', 40, 50),
        _seg('c2', 30.7, 100, category='chapter', action='chapter', description='Main'),
    ]}], 'v')
    assert sponsorblock.chapters_from_segments(segs) == [
        {'start': 0, 'title': 'Intro'}, {'start': 30, 'title': 'Main'}]


# ---------------- fetch + cache ----------------

def test_first_play_fetches_and_caches(client, fake_api):
    _insert_video('SbVid1')
    fake_api.response = _Resp([{'videoID': 'SbVid1', 'segments': [
        _seg('s1', 10, 20),
        _seg('c1', 0, 60, category='chapter', action='chapter', description='Part one'),
    ]}])
    data = json.loads(client.get('/api/segments/SbVid1').get_data(as_text=True))
    assert [s['uuid'] for s in data['segments']] == ['s1']
    assert data['chapters'] == [{'start': 0, 'title': 'Part one'}]
    url, params = fake_api.calls[0]
    assert url.endswith('/api/skipSegments/' + sponsorblock.hash_prefix('SbVid1'))
    assert 'chapter' in json.loads(params['categories'])

    # Second play is served from the cache
    data = json.loads(client.get('/api/segments/SbVid1').get_data(as_text=True))
    assert [s['uuid'] for s in data['segments']] == ['s1']
    assert len(fake_api.calls) == 1


def test_non_youtube_and_unknown_videos_skip_the_api(client, fake_api):
    _insert_video('SbReddit1', source='reddit')
    for vid in ('SbReddit1', 'SbMissing'):
        data = json.loads(client.get('/api/segments/%s' % vid).get_data(as_text=True))
        assert data == {'segments': [], 'chapters': []}
    assert fake_api.calls == []


def test_404_is_cached_as_none(client, fake_api):
    _insert_video('SbVid2')
    fake_api.response = _Resp(None, status_code=404)
    assert sponsorblock.get_segments('SbVid2') == []
    assert sponsorblock.get_segments('SbVid2') == []
    assert len(fake_api.calls) == 1


def test_error_backs_off_and_keeps_cache(client, fake_api):
    _insert_video('SbVid3', published=datetime.datetime.now() - datetime.timedelta(days=10))
    fake_api.response = _Resp([{'videoID': 'SbVid3', 'segments': [_seg('s1', 5, 9)]}])
    assert len(sponsorblock.get_segments('SbVid3')) == 1

    # Stale cache for a recent video triggers a refetch; the refetch fails
    _set_fetch_times('SbVid3', datetime.datetime.now() - datetime.timedelta(days=8))
    fake_api.error = RuntimeError('boom')
    assert [s['uuid'] for s in sponsorblock.get_segments('SbVid3')] == ['s1']
    assert len(fake_api.calls) == 2
    # Within the error backoff, no further requests
    assert [s['uuid'] for s in sponsorblock.get_segments('SbVid3')] == ['s1']
    assert len(fake_api.calls) == 2


def test_recent_video_refreshes_and_empty_answer_keeps_segments(client, fake_api):
    _insert_video('SbVid4', published=datetime.datetime.now() - datetime.timedelta(days=10))
    fake_api.response = _Resp([{'videoID': 'SbVid4', 'segments': [_seg('s1', 5, 9)]}])
    sponsorblock.get_segments('SbVid4')

    _set_fetch_times('SbVid4', datetime.datetime.now() - datetime.timedelta(days=8))
    fake_api.response = _Resp([{'videoID': 'SbVid4', 'segments': [_seg('s2', 30, 40)]}])
    assert [s['uuid'] for s in sponsorblock.get_segments('SbVid4')] == ['s2']

    # SponsorBlock losing the data doesn't wipe the archive's copy
    _set_fetch_times('SbVid4', datetime.datetime.now() - datetime.timedelta(days=8))
    fake_api.response = _Resp(None, status_code=404)
    assert [s['uuid'] for s in sponsorblock.get_segments('SbVid4')] == ['s2']
    assert len(fake_api.calls) == 3


def test_old_and_deleted_videos_are_not_refreshed(client, fake_api):
    _insert_video('SbOld', published=datetime.datetime.now() - datetime.timedelta(days=400))
    _insert_video('SbGone', published=datetime.datetime.now() - datetime.timedelta(days=10),
                  is_deleted=1)
    for vid in ('SbOld', 'SbGone'):
        sponsorblock.get_segments(vid)
        _set_fetch_times(vid, datetime.datetime.now() - datetime.timedelta(days=30))
        sponsorblock.get_segments(vid)
    assert len(fake_api.calls) == 2


def test_disabled_serves_cache_only(client, fake_api, monkeypatch):
    _insert_video('SbVid5')
    monkeypatch.setenv('VAULTTUBE_SPONSORBLOCK', 'false')
    assert sponsorblock.get_segments('SbVid5') == []
    assert fake_api.calls == []


def test_chapters_endpoint_falls_back_to_cached_sponsorblock(client, fake_api):
    _insert_video('SbVid6', description='No timestamps here.')
    data = json.loads(client.get('/api/chapters/SbVid6').get_data(as_text=True))
    assert data['chapters'] == []
    fake_api.response = _Resp([{'videoID': 'SbVid6', 'segments': [
        _seg('c1', 0, 60, category='chapter', action='chapter', description='Opening'),
    ]}])
    client.get('/api/segments/SbVid6')
    data = json.loads(client.get('/api/chapters/SbVid6').get_data(as_text=True))
    assert data['chapters'] == [{'start': 0, 'title': 'Opening'}]
    # Description chapters still win when present
    _insert_video('SbVid7', description='0:00 Intro\n1:00 Body\n')
    data = json.loads(client.get('/api/chapters/SbVid7').get_data(as_text=True))
    assert [c['title'] for c in data['chapters']] == ['Intro', 'Body']


def test_segments_cascade_with_video_delete(client, fake_api):
    _insert_video('SbVid8')
    fake_api.response = _Resp([{'videoID': 'SbVid8', 'segments': [_seg('s1', 5, 9)]}])
    sponsorblock.get_segments('SbVid8')
    con = _db_connect()
    cur = con.cursor()
    cur.execute("DELETE FROM videos WHERE id = 'SbVid8'")
    cur.execute("SELECT COUNT(*) FROM skip_segments WHERE video_id = 'SbVid8'")
    assert cur.fetchone()[0] == 0
    con.close()
