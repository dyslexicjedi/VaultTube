import os
import sys
import time
import logging
import pytest
import mariadb as _mariadb

_test_container = None


def pytest_configure(config):
    global _test_container
    from testcontainers.core.container import DockerContainer

    # Ryuk is the testcontainers reaper daemon; on macOS it can kill the
    # container prematurely during a long test run. We stop it explicitly in
    # pytest_unconfigure so the reaper isn't needed.
    os.environ.setdefault('TESTCONTAINERS_RYUK_DISABLED', 'true')

    print('\n[conftest] Starting MariaDB test container...', file=sys.stderr, flush=True)
    try:
        _test_container = (
            DockerContainer('mariadb:latest')
            .with_env('MARIADB_ROOT_PASSWORD', 'vaulttest')
            .with_env('MARIADB_USER', 'vaulttest')
            .with_env('MARIADB_PASSWORD', 'vaulttest')
            .with_env('MARIADB_DATABASE', 'vaulttube')
            .with_exposed_ports(3306)
        )
        _test_container.start()
    except Exception as e:
        import pytest as _pytest
        _pytest.exit(f"Could not start MariaDB test container — is Docker running? ({e})", returncode=3)

    port = int(_test_container.get_exposed_port(3306))
    print(f'[conftest] Container up on port {port}, waiting for MariaDB...', file=sys.stderr, flush=True)
    for attempt in range(30):
        try:
            con = _mariadb.connect(
                host='127.0.0.1', port=port,
                user='vaulttest', password='vaulttest',
                database='vaulttube',
                connect_timeout=1,
            )
            con.close()
            break
        except Exception:
            if attempt == 29:
                pytest.exit('MariaDB test container did not become ready in 30 seconds', returncode=3)
            time.sleep(1)
    print('[conftest] MariaDB ready.', file=sys.stderr, flush=True)

    os.environ['VAULTTUBE_DBHOST'] = '127.0.0.1'
    os.environ['VAULTTUBE_DBPORT'] = str(port)
    os.environ['VAULTTUBE_DBUSER'] = 'vaulttest'
    os.environ['VAULTTUBE_DBPASS'] = 'vaulttest'
    os.environ['VAULTTUBE_DBNAME'] = 'vaulttube'
    os.environ['VAULTTUBE_VAULTDIR'] = '/tmp/vt_test_vault'
    os.environ['VAULTTUBE_YTKEY'] = 'test-key-not-real'
    os.environ['VAULTTUBE_DISABLEBACK'] = '1'
    os.makedirs('/tmp/vt_test_vault', exist_ok=True)

    # Ensure app/ is on sys.path so `import database` resolves to the same
    # module object the app and tests use — prevents duplicate pool names.
    app_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app')
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


def pytest_unconfigure(config):
    if _test_container:
        _test_container.stop()


@pytest.fixture(scope='session', autouse=True)
def _db_schema():
    import database

    # Replace the pool with direct connections for the test session.
    # The pool blocks forever once all slots are leaked (app code has several
    # paths that don't close connections). Direct connections are truly closed
    # on close(), so leaks don't accumulate and nothing hangs.
    def _direct_connect(logger=None):
        return _mariadb.connect(
            host=os.environ['VAULTTUBE_DBHOST'],
            user=os.environ['VAULTTUBE_DBUSER'],
            password=os.environ['VAULTTUBE_DBPASS'],
            database=os.environ['VAULTTUBE_DBNAME'],
            autocommit=True,
            port=int(os.environ['VAULTTUBE_DBPORT']),
        )
    database.get_connection = _direct_connect

    result = database.checkdb(logging.getLogger('test'))
    assert result, "checkdb() returned False — schema creation failed (check logs above)"


@pytest.fixture
def client():
    from app.main import app
    with app.test_client() as c:
        yield c


_TABLES = ['videos', 'channels', 'images', 'playlists', 'pl2vid', 'IgnoreVid', 'queue', 'download_errors']


@pytest.fixture(autouse=True)
def db_cleanup():
    con = _mariadb.connect(
        host=os.environ['VAULTTUBE_DBHOST'],
        user=os.environ['VAULTTUBE_DBUSER'],
        password=os.environ['VAULTTUBE_DBPASS'],
        database=os.environ['VAULTTUBE_DBNAME'],
        autocommit=True,
        port=int(os.environ['VAULTTUBE_DBPORT']),
    )
    cur = con.cursor()
    for table in _TABLES:
        cur.execute(f'DELETE FROM `{table}`')
    cur.close()
    con.close()
    yield
