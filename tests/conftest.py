import pytest
import os
import mariadb
from app.main import app

# All test-only row IDs that tests insert into the DB
_TEST_VIDEO_IDS   = [
    'Test123', 'SearchFT1', 'SearchLIKE1',
    'GetVid1', 'GetVidMp4',
    'WatchVid1', 'UnwatchVid1', 'TsVid1',
    'UpNext1', 'UpNext2', 'UpNext3',
    'DelVid1', 'DelVid2',
    'TombVid1', 'TombVid2',
    'RedVid1',
]
_TEST_CHANNEL_IDS = [
    'Test123', 'SubTest123',
    'GetVidCh1', 'TestCh1', 'UpNextCh1',
    'UCVtTestChannel1', '987654321099', 'VtTestRedditUser',
]
# Every URL a test may enqueue — queue rows persist to the real DB, so any
# test that hits an enqueue path MUST list its URL here or clean up itself.
_TEST_QUEUE_URLS = [
    'https://youtube.com/watch?v=dQw4w9WgXcQ',
    'https://www.youtube.com/watch?v=vt-test-1',
    'https://example.com/vt-test-queue-row',
]


def _delete_test_rows():
    """Remove known test rows from the DB. Silently no-ops if DB is unreachable."""
    try:
        con = mariadb.connect(
            host=os.environ['VAULTTUBE_DBHOST'],
            user=os.environ['VAULTTUBE_DBUSER'],
            password=os.environ['VAULTTUBE_DBPASS'],
            database=os.environ['VAULTTUBE_DBNAME'],
            autocommit=True,
            port=int(os.environ['VAULTTUBE_DBPORT'])
        )
        cur = con.cursor()
        for vid_id in _TEST_VIDEO_IDS:
            cur.execute("DELETE FROM videos WHERE id = %s", (vid_id,))
            cur.execute("DELETE FROM IgnoreVid WHERE id = %s", (vid_id,))
        for ch_id in _TEST_CHANNEL_IDS:
            cur.execute("DELETE FROM channels WHERE channelid = %s", (ch_id,))
        for q_url in _TEST_QUEUE_URLS:
            cur.execute("DELETE FROM queue WHERE url = %s", (q_url,))
        cur.close()
        con.close()
    except Exception:
        pass  # DB may not exist yet or env vars not set for non-DB tests


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def db_cleanup():
    """Ensure test DB rows are absent before and after every test."""
    _delete_test_rows()
    yield
    _delete_test_rows()
