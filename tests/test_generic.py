import json
import os

import mariadb
import pytest

from providers import generic


def _db_connect():
    return mariadb.connect(
        host=os.environ['VAULTTUBE_DBHOST'],
        user=os.environ['VAULTTUBE_DBUSER'],
        password=os.environ['VAULTTUBE_DBPASS'],
        database=os.environ['VAULTTUBE_DBNAME'],
        autocommit=True,
        port=int(os.environ['VAULTTUBE_DBPORT'])
    )


def _query(sql, args=()):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(sql, args)
    rows = cur.fetchall()
    con.close()
    return rows


# ---------------- URL claiming and naming ----------------

def test_claims_known_site_but_not_own_providers():
    assert generic.claims('https://vimeo.com/76979871') == 'Vimeo'
    assert generic.claims('https://www.youtube.com/watch?v=dQw4w9WgXcQ') is None
    assert generic.claims('https://www.patreon.com/posts/some-post-123') is None
    assert generic.claims('dQw4w9WgXcQ') is None
    assert generic.claims('https://example.com/some/page') is None


def test_claims_any_page_only_when_allowed(monkeypatch):
    monkeypatch.setenv('VAULTTUBE_GENERIC_ALLOW_ANY', 'true')
    assert generic.claims('https://example.com/some/page') == 'Generic'


def test_source_name_uses_site_part_of_extractor():
    assert generic.source_name({'extractor': 'twitch:vod'}) == 'twitch'
    assert generic.source_name({'extractor': 'Vimeo'}) == 'vimeo'
    assert generic.source_name({}) == 'generic'


def test_namespaced_id_is_filename_safe_and_bounded():
    assert generic.namespaced_id('vimeo', '76979871') == 'vimeo-76979871'
    assert generic.namespaced_id('odysee', 'name:1.a/b') == 'odysee-name_1_a_b'
    long_a = generic.namespaced_id('site', 'x' * 80)
    long_b = generic.namespaced_id('site', 'x' * 79 + 'y')
    assert len(long_a) == 50 and long_a != long_b
    assert '.' not in long_a


def test_channel_ref_fallbacks():
    cid, name, url = generic.channel_ref({
        'extractor': 'vimeo', 'uploader_id': 'user12345', 'uploader': 'Some One',
        'uploader_url': 'https://vimeo.com/user12345'})
    assert (cid, name, url) == ('vimeo-user12345', 'Some One', 'https://vimeo.com/user12345')
    cid, name, _ = generic.channel_ref({
        'extractor': 'generic', 'webpage_url': 'https://media.example.org/v/1'})
    assert cid == 'generic-media_example_org'


def test_cookie_file_matches_parent_domain(tmp_path, monkeypatch):
    (tmp_path / 'nebula.tv.txt').write_text('# Netscape HTTP Cookie File\n')
    monkeypatch.setenv('VAULTTUBE_COOKIES_DIR', str(tmp_path))
    assert generic._cookie_file('https://www.nebula.tv/videos/x') == str(tmp_path / 'nebula.tv.txt')
    assert generic._cookie_file('https://vimeo.com/1') is None


# ---------------- download ----------------

VIDEO_INFO = {
    'id': '76979871', 'title': 'A Vimeo video', 'extractor': 'vimeo',
    'extractor_key': 'Vimeo', 'uploader': 'Some One', 'uploader_id': 'user12345',
    'uploader_url': 'https://vimeo.com/user12345', 'description': 'About it',
    'webpage_url': 'https://vimeo.com/76979871', 'timestamp': 1700000000,
    'chapters': [{'start_time': 0, 'title': 'Start'}, {'start_time': 61.5, 'title': 'Middle'}],
}


class FakeYDL:
    infos = {}
    downloaded = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return dict(FakeYDL.infos[url])

    def process_ie_result(self, info, download=True):
        path = self.opts['outtmpl'].replace('%(ext)s', 'mp4')
        with open(path, 'wb') as f:
            f.write(b'not really a video')
        FakeYDL.downloaded.append(path)
        return dict(info, requested_downloads=[{'filepath': path}])


@pytest.fixture
def app_queue(client):
    import queue as _queue
    client.application.config['queue'] = _queue.Queue()
    return client.application.config['queue']


@pytest.fixture
def fake_ytdlp(monkeypatch):
    FakeYDL.infos = {'https://vimeo.com/76979871': VIDEO_INFO}
    FakeYDL.downloaded = []
    monkeypatch.setattr(generic.yt_dlp, 'YoutubeDL', FakeYDL)
    monkeypatch.setattr(generic, '_thumbnail', lambda info: None)
    yield FakeYDL
    for path in FakeYDL.downloaded:
        try:
            os.remove(path)
        except OSError:
            pass


def test_download_saves_namespaced_video_and_channel(client, fake_ytdlp):
    from QueueObject import QueueObject
    from app.main import app
    with app.app_context():
        assert generic.download(QueueObject('https://vimeo.com/76979871', '', 'generic'))

    rows = _query("select channelId, source, filepath, title, description, json from videos where id = 'vimeo-76979871'")
    assert len(rows) == 1
    channel_id, source, filepath, title, description, raw = rows[0]
    assert (channel_id, source, title, description) == ('vimeo-user12345', 'vimeo', 'A Vimeo video', 'About it')
    assert filepath == 'vimeo-user12345/vimeo-76979871.mp4'
    stored = json.loads(raw)
    assert stored['webpage_url'] == 'https://vimeo.com/76979871'
    assert stored['source'] == 'vimeo'

    assert _query("select channelname, source from channels where channelid = 'vimeo-user12345'") == [('Some One', 'vimeo')]

    chapters = json.loads(client.get('/api/chapters/vimeo-76979871').get_data(as_text=True))['chapters']
    assert chapters == [{'start': 0, 'title': 'Start'}, {'start': 61, 'title': 'Middle'}]

    info = json.loads(client.get('/api/channel/vimeo-user12345').get_data(as_text=True))
    assert info['source_url'] == 'https://vimeo.com/user12345'
    assert info['source'] == 'vimeo'

    sources = json.loads(client.get('/api/sources').get_data(as_text=True))
    assert {'source': 'vimeo', 'label': 'Vimeo', 'videos': 1} in sources

    listed = json.loads(client.get('/api/channels/0?source=vimeo').get_data(as_text=True))
    assert [c['channelid'] for c in listed] == ['vimeo-user12345']
    assert listed[0]['source'] == 'vimeo'
    assert json.loads(client.get('/api/channels/0?source=reddit').get_data(as_text=True)) == []

    # Second run is a no-op
    with app.app_context():
        assert generic.download(QueueObject('https://vimeo.com/76979871', '', 'generic'))
    assert len(fake_ytdlp.downloaded) == 1


def test_download_into_subscribed_channel(client, fake_ytdlp):
    from QueueObject import QueueObject
    from app.main import app
    with app.app_context():
        generic.download(QueueObject('https://vimeo.com/76979871', 'vimeo-subscribed', 'generic'))
    assert _query("select channelId from videos where id = 'vimeo-76979871'") == [('vimeo-subscribed',)]


def test_playlist_url_queues_each_entry(client, fake_ytdlp, app_queue):
    from QueueObject import QueueObject
    from app.main import app
    fake_ytdlp.infos['https://vimeo.com/showcase/1'] = {
        '_type': 'playlist', 'extractor': 'vimeo:showcase', 'entries': [
            {'url': 'https://vimeo.com/1'}, {'url': 'https://vimeo.com/2'}, None]}
    with app.app_context():
        assert generic.download(QueueObject('https://vimeo.com/showcase/1', '', 'generic'))
    rows = _query("select url, source from queue order by url")
    assert rows == [('https://vimeo.com/1', 'generic'), ('https://vimeo.com/2', 'generic')]


def test_add_url_routes_unknown_sites_to_generic(client, app_queue):
    resp = client.post('/api/download/single', json={'url': 'https://vimeo.com/76979871'})
    assert resp.status_code == 200
    assert _query("select source from queue where url = 'https://vimeo.com/76979871'") == [('generic',)]


def test_legacy_channels_still_classified_by_id_shape(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("insert into channels(channelid, channelname, json) values "
                "('UCabc', 'Yt', '{}'), ('12345', 'Pat', '{}'), ('someuser', 'Red', '{}')")
    con.close()
    for src, expected in (('youtube', 'UCabc'), ('patreon', '12345'), ('reddit', 'someuser')):
        listed = json.loads(client.get('/api/channels/0?source=' + src).get_data(as_text=True))
        assert [c['channelid'] for c in listed] == [expected]


# ---------------- subscriptions ----------------

def _listing(ids, extractor='vimeo:user'):
    return {
        '_type': 'playlist', 'extractor': extractor, 'id': 'user12345',
        'uploader': 'Some One', 'uploader_id': 'user12345',
        'webpage_url': 'https://vimeo.com/user12345',
        'entries': [{'id': i, 'ie_key': 'Vimeo', 'url': 'https://vimeo.com/%s' % i} for i in ids],
    }


def test_subscribe_only_new_then_scan_queues_new_uploads(client, fake_ytdlp, app_queue):
    from scanner import scan_url_subscriptions
    channel = 'https://vimeo.com/user12345'
    fake_ytdlp.infos[channel] = _listing(['3', '2', '1'])
    resp = client.post('/api/subscribe/url', json={'url': channel})
    data = json.loads(resp.get_data(as_text=True))
    assert data['success'] and data['channelid'] == 'vimeo-user12345'
    assert _query("select subscribed, source, source_url from channels where channelid = 'vimeo-user12345'") == [
        (1, 'vimeo', channel)]

    # Nothing new yet: the existing listing was recorded as seen
    scan_url_subscriptions(client.application)
    assert _query("select count(*) from queue")[0][0] == 0

    # A new upload appears; a rescan within the interval does nothing
    fake_ytdlp.infos[channel] = _listing(['4', '3', '2', '1'])
    scan_url_subscriptions(client.application)
    assert _query("select count(*) from queue")[0][0] == 0
    con = _db_connect()
    import datetime
    con.cursor().execute("update channels set last_scanned_at = %s", (datetime.datetime.now() - datetime.timedelta(hours=7),))
    con.close()
    scan_url_subscriptions(client.application)
    assert _query("select url, channel_id, source from queue") == [
        ('https://vimeo.com/4', 'vimeo-user12345', 'generic')]


def test_subscribe_with_backfill_queues_everything_not_in_vault(client, fake_ytdlp, app_queue):
    from scanner import scan_url_subscriptions
    channel = 'https://vimeo.com/user12345'
    fake_ytdlp.infos[channel] = _listing(['3', '2', '1'])
    con = _db_connect()
    con.cursor().execute("insert into videos(id, channelId, json, filepath, source) "
                         "values ('vimeo-2', 'vimeo-user12345', '{}', 'x/vimeo-2.mp4', 'vimeo')")
    con.close()
    client.post('/api/subscribe/url', json={'url': channel, 'backfill': True})
    scan_url_subscriptions(client.application)
    assert sorted(r[0] for r in _query("select url from queue")) == [
        'https://vimeo.com/1', 'https://vimeo.com/3']


def test_url_subscriptions_stay_out_of_youtube_scan(client, fake_ytdlp, app_queue):
    from database import get_active_subscriptions
    fake_ytdlp.infos['https://vimeo.com/user12345'] = _listing(['1'])
    client.post('/api/subscribe/url', json={'url': 'https://vimeo.com/user12345'})
    assert ('vimeo-user12345',) not in get_active_subscriptions()
    # Unsubscribe through the normal channel endpoint
    client.get('/api/unsubscribe/channel/vimeo-user12345')
    from database import get_url_subscriptions
    assert get_url_subscriptions() == []


def test_subscribe_url_rejects_own_provider_sites(client):
    resp = client.post('/api/subscribe/url', json={'url': 'https://www.youtube.com/@someone'})
    assert resp.status_code == 400
