import logging
import os
from database import get_connection

_log = logging.getLogger('settings')

# Keys managed in the settings table. .env values for these keys are ignored
# once a DB row exists — DB always wins after hydration.
DB_MANAGED_KEYS = [
    'VAULTTUBE_YTKEY',
    'VAULTTUBE_YTCOOKIE',
    'VAULTTUBE_PATREONCOOKIE',
    'VAULTTUBE_REDDIT_CLIENT_ID',
    'VAULTTUBE_REDDIT_CLIENT_SECRET',
    'VAULTTUBE_REDDIT_USER_AGENT',
    'VAULTTUBE_REDDIT_USERNAME',
    'VAULTTUBE_REDDIT_PASSWORD',
    'VAULTTUBE_PROXY',
    'VAULTTUBE_DL_DELAY',
    'VAULTTUBE_DBPOOL',
    'VAULTTUBE_DENOPATH',
    'VAULTTUBE_TRANSCODE_CACHE_DIR',
    'VAULTTUBE_TRANSCODE_TTL',
    'VAULTTUBE_TRANSCODE_MAX_CACHE_GB',
    'VAULTTUBE_TRANSCODE_PRESET',
    'VAULTTUBE_TRANSCODE_CRF',
    'VAULTTUBE_MAX_CONCURRENT_TRANSCODES',
    'VAULTTUBE_TRANSCODE_SEGMENT_TIMEOUT',
    'VAULTTUBE_DEBUG',
    'VAULTTUBE_DISABLEBACK',
]

# These settings require an app restart to take effect.
RESTART_REQUIRED_KEYS = {'VAULTTUBE_DBPOOL', 'VAULTTUBE_DISABLEBACK'}


def get_setting(key, default=None):
    """Read a setting: DB first, then os.environ, then default."""
    try:
        con = get_connection(_log)
        cur = con.cursor()
        cur.execute("SELECT setting_value FROM settings WHERE setting_key = %s", (key,))
        row = cur.fetchone()
        cur.close()
        con.close()
        if row is not None:
            return row[0]
    except Exception:
        pass
    return os.environ.get(key, default)


def set_setting(key, value, logger=None):
    """Persist a setting to DB and update os.environ immediately."""
    try:
        con = get_connection(_log)
        cur = con.cursor()
        cur.execute(
            "INSERT INTO settings (setting_key, setting_value) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
            (key, value),
        )
        cur.close()
        con.close()
    except Exception as e:
        if logger:
            logger.error("set_setting failed for %s: %s", key, e)
        raise
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def hydrate_settings(logger):
    """
    Read all settings from DB into os.environ (DB wins unconditionally).
    On first boot, seed the DB from any DB-managed keys already in os.environ
    so existing installs preserve their config without manual re-entry.
    """
    try:
        con = get_connection(_log)
        cur = con.cursor()

        # Seed: write env values for DB-managed keys that have no DB row yet.
        for key in DB_MANAGED_KEYS:
            if key in os.environ:
                cur.execute(
                    "INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)",
                    (key, os.environ[key]),
                )

        # Hydrate: write all DB rows into os.environ unconditionally.
        cur.execute("SELECT setting_key, setting_value FROM settings")
        rows = cur.fetchall()
        for key, value in rows:
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        cur.close()
        con.close()
        logger.info("Settings hydrated from DB (%d rows)", len(rows))
    except Exception as e:
        logger.error("Failed to hydrate settings from DB: %s", e)
