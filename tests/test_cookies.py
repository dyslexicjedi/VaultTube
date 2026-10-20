import os
import subprocess

import pytest

SIGNED_IN = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tabc\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1999999999\t__Secure-3PSID\tsid1\n"
    ".google.com\tTRUE\t/\tTRUE\t1999999999\tSID\tg1\n"
    ".mybank.com\tTRUE\t/\tTRUE\t1999999999\tsession\tsecret\n"
    "notyoutube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tnope\n"
)


@pytest.fixture
def cookie_path(tmp_path, monkeypatch):
    path = tmp_path / 'yt' / 'cookies.txt'
    monkeypatch.setenv('VAULTTUBE_YTCOOKIE', str(path))
    monkeypatch.delenv('VAULTTUBE_COOKIE_TOKEN', raising=False)
    from providers.base import _alerts, _alerts_lock
    with _alerts_lock:
        _alerts.clear()
    return path


def test_filter_keeps_only_youtube_and_google():
    import cookie_store
    body, stats = cookie_store.filter_cookies(SIGNED_IN)
    assert 'mybank' not in body and 'notyoutube' not in body
    assert '#HttpOnly_.youtube.com' in body
    assert stats == {'count': 3, 'youtube': 2, 'logged_in': True}


def test_default_path_under_vault(monkeypatch):
    import cookie_store
    monkeypatch.delenv('VAULTTUBE_YTCOOKIE', raising=False)
    monkeypatch.setenv('VAULTTUBE_VAULTDIR', '/vault')
    assert cookie_store.youtube_cookie_path() == '/vault/.cookies/youtube.txt'


def test_upload_raw_body_stores_filtered_and_clears_alert(client, cookie_path):
    from providers.base import raise_alert, get_alerts
    raise_alert('youtube_cookies_invalid', 't', 'm')
    r = client.post('/api/cookies/youtube', data=SIGNED_IN, content_type='text/plain')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['count'] == 3
    stored = cookie_path.read_text()
    assert 'secret' not in stored and 'sid1' in stored
    assert oct(cookie_path.stat().st_mode & 0o777) == '0o600'
    assert not any(a['id'] == 'youtube_cookies_invalid' for a in get_alerts())

    status = client.get('/api/cookies/youtube').get_json()['data']
    assert status['exists'] and status['logged_in'] and status['count'] == 3
    assert not status['invalid_alert']
    assert 'sid1' not in str(status)


def test_upload_multipart(client, cookie_path):
    import io
    r = client.post('/api/cookies/youtube',
                    data={'cookies': (io.BytesIO(SIGNED_IN.encode()), 'cookies.txt')},
                    content_type='multipart/form-data')
    assert r.status_code == 200
    assert 'LOGIN_INFO' in cookie_path.read_text()


@pytest.mark.parametrize('payload,err', [
    ('.mybank.com\tTRUE\t/\tTRUE\t1\tsession\tx\n', 'No youtube.com cookies'),
    ('.youtube.com\tTRUE\t/\tTRUE\t1\tPREF\tx\n', 'not from a signed-in'),
    ('garbage', 'No youtube.com cookies'),
])
def test_upload_rejects_unusable(client, cookie_path, payload, err):
    r = client.post('/api/cookies/youtube', data=payload, content_type='text/plain')
    assert r.status_code == 400
    assert err in r.get_json()['error']
    assert not cookie_path.exists()


def test_upload_token(client, cookie_path, monkeypatch):
    monkeypatch.setenv('VAULTTUBE_COOKIE_TOKEN', 's3cret')
    r = client.post('/api/cookies/youtube', data=SIGNED_IN, content_type='text/plain')
    assert r.status_code == 401
    assert client.get('/api/cookies/youtube').status_code == 401
    r = client.post('/api/cookies/youtube', data=SIGNED_IN, content_type='text/plain',
                    headers={'Authorization': 'Bearer s3cret'})
    assert r.status_code == 200


def _rotating_attempt(new_sid):
    """Fake _download_attempt that rewrites the jar like yt-dlp does on exit."""
    seen = {}

    def attempt(url, ydl_opts, cookie_path, label=''):
        seen['path'] = cookie_path
        with open(cookie_path) as f:
            seen['loaded'] = f.read()
        with open(cookie_path, 'w') as f:
            f.write(seen['loaded'].replace('sid1', new_sid))
        return True
    return attempt, seen


def test_download_saves_refreshed_cookies(cookie_path, monkeypatch):
    import providers.youtube as youtube
    cookie_path.parent.mkdir()
    cookie_path.write_text(SIGNED_IN)
    attempt, seen = _rotating_attempt('sid2')
    monkeypatch.setattr(youtube, '_download_attempt', attempt)

    assert youtube.download_video('https://www.youtube.com/watch?v=abcdefghijk') is True
    assert seen['path'] != str(cookie_path)
    assert not os.path.exists(seen['path'])
    assert 'sid2' in cookie_path.read_text()


def test_download_keeps_file_replaced_mid_download(cookie_path, monkeypatch):
    """An upload during a download wins over the download's rotated jar."""
    import providers.youtube as youtube
    cookie_path.parent.mkdir()
    cookie_path.write_text(SIGNED_IN)
    attempt, _ = _rotating_attempt('sid2')

    def attempt_with_upload(*args, **kw):
        attempt(*args, **kw)
        cookie_path.write_text(SIGNED_IN.replace('sid1', 'uploaded'))
        st = cookie_path.stat()
        os.utime(cookie_path, (st.st_atime, st.st_mtime + 5))
        return True
    monkeypatch.setattr(youtube, '_download_attempt', attempt_with_upload)

    youtube.download_video('https://www.youtube.com/watch?v=abcdefghijk')
    assert 'uploaded' in cookie_path.read_text()


def test_download_with_explicit_cookies_does_not_touch_store(cookie_path, monkeypatch):
    import providers.youtube as youtube
    attempt, seen = _rotating_attempt('sid2')
    monkeypatch.setattr(youtube, '_download_attempt', attempt)
    youtube.download_video('https://www.youtube.com/watch?v=abcdefghijk', cookies=SIGNED_IN)
    assert 'sid1' in seen['loaded']
    assert not cookie_path.exists()


def test_download_without_cookies_raises(cookie_path):
    import providers.youtube as youtube
    with pytest.raises(RuntimeError, match='No YouTube cookies'):
        youtube.download_video('https://www.youtube.com/watch?v=abcdefghijk')


def test_script_syntax():
    script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'vaulttube-cookies.sh')
    subprocess.run(['bash', '-n', script], check=True)
