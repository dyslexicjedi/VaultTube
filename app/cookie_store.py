"""YouTube cookies.txt storage: where it lives, validating uploads, and
writing back the cookies YouTube rotates during a download.

Cookies arrive either from POST /api/cookies/youtube (pushed by
scripts/vaulttube-cookies.sh on a desktop, which reads them straight from the
browser with ``yt-dlp --cookies-from-browser``) or from a hand-copied file at
$VAULTTUBE_YTCOOKIE. Only youtube.com/google.com cookies are ever kept.
"""
import os
import logging
import threading
import time

from providers.base import clear_alert, get_alerts

logger = logging.getLogger('cookies')

YOUTUBE_ALERT_ID = 'youtube_cookies_invalid'
MAX_COOKIE_BYTES = 1024 * 1024

# Domains kept from an upload (a browser dump holds every site's cookies).
_KEEP_DOMAINS = ('youtube.com', 'google.com')
# Any of these on youtube.com means the browser session is signed in.
_LOGIN_COOKIES = {'SID', '__Secure-1PSID', '__Secure-3PSID', 'SAPISID', 'LOGIN_INFO'}
_HTTPONLY_PREFIX = '#HttpOnly_'
_HEADER = '# Netscape HTTP Cookie File\n'

_write_lock = threading.Lock()


class CookieFileError(ValueError):
    """An uploaded cookies.txt is unusable (wrong format, not signed in)."""


def youtube_cookie_path():
    """$VAULTTUBE_YTCOOKIE, else <vault>/.cookies/youtube.txt (the vault
    scanner's glob skips dot-directories)."""
    path = os.environ.get('VAULTTUBE_YTCOOKIE')
    if path:
        return path
    vault = os.environ.get('VAULTTUBE_VAULTDIR')
    if not vault:
        return None
    return os.path.join(vault, '.cookies', 'youtube.txt')


def _domain_matches(domain):
    domain = domain.lstrip('.').lower()
    return any(domain == d or domain.endswith('.' + d) for d in _KEEP_DOMAINS)


def filter_cookies(text):
    """Return (netscape_text, stats) keeping only YouTube/Google cookies.

    stats: {'count': n, 'youtube': n, 'logged_in': bool}. Malformed lines
    are dropped rather than rejected so a slightly odd export still works.
    """
    kept = []
    youtube = 0
    logged_in = False
    for raw in text.splitlines():
        line = raw.rstrip('\r')
        domain_field = line
        if line.startswith(_HTTPONLY_PREFIX):
            domain_field = line[len(_HTTPONLY_PREFIX):]
        elif not line.strip() or line.startswith('#'):
            continue
        fields = domain_field.split('\t')
        if len(fields) != 7 or not _domain_matches(fields[0]):
            continue
        kept.append(line)
        if fields[0].lstrip('.').lower().endswith('youtube.com'):
            youtube += 1
            if fields[5] in _LOGIN_COOKIES:
                logged_in = True
    body = _HEADER + ''.join(l + '\n' for l in kept)
    return body, {'count': len(kept), 'youtube': youtube, 'logged_in': logged_in}


def _write_atomic(path, text):
    """Replace ``path`` with ``text`` (mode 0600). Falls back to rewriting in
    place when the file is a Docker single-file bind mount, where rename()
    over it fails with EBUSY."""
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, '.%s.%d.tmp' % (os.path.basename(path), os.getpid()))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)


def save_youtube_cookies(text):
    """Validate, filter and store an uploaded cookies.txt. Clears the
    expired-cookies alert. Returns the stats dict; raises CookieFileError."""
    path = youtube_cookie_path()
    if not path:
        raise CookieFileError('No cookie path: set VAULTTUBE_YTCOOKIE or VAULTTUBE_VAULTDIR')
    body, stats = filter_cookies(text)
    if not stats['youtube']:
        raise CookieFileError('No youtube.com cookies found in the upload')
    if not stats['logged_in']:
        raise CookieFileError('The cookies are not from a signed-in YouTube session')
    with _write_lock:
        _write_atomic(path, body)
    clear_alert(YOUTUBE_ALERT_ID)
    logger.info("Stored %d YouTube/Google cookies at %s" % (stats['count'], path))
    return stats


def persist_rotated_cookies(path, read_mtime, original, rotated):
    """Write cookies yt-dlp saved after a download back to ``path``.

    YouTube refreshes session cookies in its responses; keeping them makes an
    export last longer. Skipped when nothing changed, when the result no
    longer looks signed in, or when the file was replaced (e.g. a fresh
    upload) since it was read at ``read_mtime``.
    """
    if not rotated:
        return False
    body, stats = filter_cookies(rotated)
    if not stats['logged_in'] or body == filter_cookies(original)[0]:
        return False
    with _write_lock:
        try:
            if os.path.getmtime(path) != read_mtime:
                return False
            _write_atomic(path, body)
        except OSError as e:
            logger.warning("Could not save refreshed YouTube cookies to %s: %s" % (path, e))
            return False
    logger.debug("Saved refreshed YouTube cookies to %s" % path)
    return True


def youtube_cookie_status():
    """What the server knows about its YouTube cookies (never the values)."""
    path = youtube_cookie_path()
    status = {
        'path': path,
        'exists': False,
        'updated_at': None,
        'age_hours': None,
        'count': 0,
        'logged_in': False,
        'invalid_alert': any(a['id'] == YOUTUBE_ALERT_ID for a in get_alerts()),
    }
    if path and os.path.isfile(path):
        mtime = os.path.getmtime(path)
        with open(path, encoding='utf-8', errors='replace') as f:
            _, stats = filter_cookies(f.read())
        status.update({
            'exists': True,
            'updated_at': mtime,
            'age_hours': round((time.time() - mtime) / 3600, 1),
            'count': stats['count'],
            'logged_in': stats['logged_in'],
        })
    return status
