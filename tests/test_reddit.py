import json
import os

import mariadb
import pytest

from providers import reddit


def _query(sql, args=()):
    con = mariadb.connect(
        host=os.environ['VAULTTUBE_DBHOST'],
        user=os.environ['VAULTTUBE_DBUSER'],
        password=os.environ['VAULTTUBE_DBPASS'],
        database=os.environ['VAULTTUBE_DBNAME'],
        autocommit=True,
        port=int(os.environ['VAULTTUBE_DBPORT'])
    )
    cur = con.cursor()
    cur.execute(sql, args)
    rows = cur.fetchall() if cur.description else None
    con.close()
    return rows


class FakePost:
    def __init__(self, id, created_utc, url=None, is_video=True):
        self.id = id
        self.created_utc = created_utc
        self.is_video = is_video
        self.url = url or 'https://v.redd.it/%s' % id
        self.permalink = '/r/videos/comments/%s/post/' % id


class FakeSubreddit:
    def __init__(self, name, posts):
        self.display_name = name
        self.posts = posts

    @property
    def id(self):
        if self.posts is None:
            raise Exception('received 404 HTTP response')
        return 't5_x'

    def new(self, limit=100):
        return sorted(self.posts, key=lambda p: -p.created_utc)[:limit]


class FakeReddit:
    subreddits = {}

    def subreddit(self, name):
        for key, sub in self.subreddits.items():
            if key.lower() == name.lower():
                return sub
        return FakeSubreddit(name, None)


@pytest.fixture
def fake_reddit(monkeypatch):
    FakeReddit.subreddits = {}
    monkeypatch.setattr(reddit, '_get_reddit_client', lambda: FakeReddit())
    for var in ('VAULTTUBE_REDDIT_CLIENT_ID', 'VAULTTUBE_REDDIT_CLIENT_SECRET',
                'VAULTTUBE_REDDIT_USER_AGENT', 'VAULTTUBE_REDDIT_USERNAME',
                'VAULTTUBE_REDDIT_PASSWORD'):
        monkeypatch.setenv(var, 'x')
    return FakeReddit


@pytest.fixture
def app_queue(client):
    import queue as _queue
    client.application.config['queue'] = _queue.Queue()
    return client.application.config['queue']


@pytest.mark.parametrize('value,expected', [
    ('videos', 'videos'),
    ('r/videos', 'videos'),
    ('/r/Videos/', 'Videos'),
    ('https://www.reddit.com/r/videos/new/', 'videos'),
    ('', None),
    ('not a sub', None),
])
def test_normalize_subreddit(value, expected):
    assert reddit.normalize_subreddit(value) == expected


def test_watch_then_scan_queues_only_new_media_posts(client, fake_reddit, app_queue):
    from scanner import scan_watched_subreddits
    fake_reddit.subreddits['Videos'] = FakeSubreddit('Videos', [FakePost('old1', 100), FakePost('old2', 200)])
    resp = client.post('/api/reddit/subreddits', json={'name': 'r/videos'})
    data = json.loads(resp.get_data(as_text=True))
    assert data['success'] and data['data']['name'] == 'Videos'

    # Existing posts were recorded as seen
    scan_watched_subreddits(client.application)
    assert _query("select count(*) from queue")[0][0] == 0

    sub = fake_reddit.subreddits['Videos']
    sub.posts += [
        FakePost('new1', 300),
        FakePost('text1', 310, url='https://www.reddit.com/r/videos/comments/text1/', is_video=False),
        FakePost('img1', 320, url='https://i.redd.it/abc.jpg', is_video=False),
    ]
    scan_watched_subreddits(client.application)
    assert sorted(_query("select url, source from queue")) == [
        ('https://www.reddit.com/r/videos/comments/img1/post/', 'reddit'),
        ('https://www.reddit.com/r/videos/comments/new1/post/', 'reddit'),
    ]
    assert _query("select last_seen_utc from watched_subreddits")[0][0] == 320

    # Nothing is queued twice
    scan_watched_subreddits(client.application)
    assert _query("select count(*) from queue")[0][0] == 2


def test_scan_skips_posts_already_in_vault(client, fake_reddit, app_queue):
    from scanner import scan_watched_subreddits
    fake_reddit.subreddits['videos'] = FakeSubreddit('videos', [])
    client.post('/api/reddit/subreddits', json={'name': 'videos'})
    _query("insert into videos (id, channelId, source) values ('have1', 'someone', 'reddit')")
    fake_reddit.subreddits['videos'].posts += [FakePost('have1', 10), FakePost('want1', 20)]
    scan_watched_subreddits(client.application)
    assert _query("select url from queue") == [('https://www.reddit.com/r/videos/comments/want1/post/',)]


def test_watch_rejects_unknown_subreddit_and_bad_names(client, fake_reddit):
    assert client.post('/api/reddit/subreddits', json={'name': 'doesnotexist'}).status_code == 400
    assert client.post('/api/reddit/subreddits', json={'name': 'bad name!'}).status_code == 400
    assert _query("select count(*) from watched_subreddits")[0][0] == 0


def test_list_and_unwatch(client, fake_reddit):
    fake_reddit.subreddits['videos'] = FakeSubreddit('videos', [])
    client.post('/api/reddit/subreddits', json={'name': 'videos'})
    data = json.loads(client.get('/api/reddit/subreddits').get_data(as_text=True))
    assert [s['name'] for s in data['data']] == ['videos']
    assert client.delete('/api/reddit/subreddits/videos').status_code == 200
    assert client.delete('/api/reddit/subreddits/videos').status_code == 404
    assert _query("select count(*) from watched_subreddits")[0][0] == 0


def test_scan_without_credentials_is_skipped(client, app_queue, monkeypatch):
    from scanner import scan_watched_subreddits
    from database import add_watched_subreddit
    add_watched_subreddit('videos', None)
    for var in ('VAULTTUBE_REDDIT_CLIENT_ID', 'VAULTTUBE_REDDIT_PASSWORD'):
        monkeypatch.delenv(var, raising=False)
    scan_watched_subreddits(client.application)
    assert _query("select count(*) from queue")[0][0] == 0


def test_download_saves_subreddit_in_metadata(client, monkeypatch):
    import types
    from QueueObject import QueueObject

    class Sub:
        display_name = 'videos'

    post = types.SimpleNamespace(
        id='abc123', title='A post', author=types.SimpleNamespace(name='someone'),
        created_utc=1700000000, is_video=False, url='https://i.redd.it/abc.jpg',
        permalink='/r/videos/comments/abc123/a_post/', subreddit=Sub())
    monkeypatch.setattr(reddit, '_get_reddit_client',
                        lambda: types.SimpleNamespace(submission=lambda id: post))

    class Resp:
        content = b'not really an image'
        def raise_for_status(self):
            pass
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Resp())

    assert reddit.download(QueueObject('https://www.reddit.com/r/videos/comments/abc123/a_post/', '', 'reddit'))
    saved = json.loads(_query("select json from videos where id = 'abc123'")[0][0])
    assert saved['subreddit'] == 'videos'
    assert saved['webpage_url'] == 'https://www.reddit.com/r/videos/comments/abc123/a_post/'
    os.remove(os.path.join(os.environ['VAULTTUBE_VAULTDIR'], 'someone', 'abc123.jpg'))
