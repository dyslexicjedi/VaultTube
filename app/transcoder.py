import json, os, re, subprocess, threading, time, shutil, glob, hashlib
from pathlib import Path

_FFPROBE_LOCK = threading.Lock()
_ffprobe_cache = {}



def _ffprobe_key(path):
    try:
        return path, os.path.getmtime(path)
    except OSError:
        return path, None


def get_codec_info(filepath, logger=None):
    """Return {'vcodec': ..., 'acodec': ..., 'container': ...} from ffprobe.

    Uses a small in-process cache keyed by (path, mtime). Empty/missing
    values are returned as None. Probe failures are logged and returned
    as all-None so callers can fail open to transcode.
    """
    filepath = os.path.abspath(filepath)
    key = _ffprobe_key(filepath)
    with _FFPROBE_LOCK:
        cached = _ffprobe_cache.get(filepath)
        if cached and cached[0] == key:
            return cached[1].copy()

    vcodec = acodec = None
    try:
        output = subprocess.check_output(
            [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                filepath,
            ],
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        data = json.loads(output.decode('utf-8', errors='replace'))
        for stream in data.get('streams', []):
            codec_type = stream.get('codec_type')
            codec_name = stream.get('codec_name')
            if codec_type == 'video' and not vcodec:
                vcodec = codec_name
            elif codec_type == 'audio' and not acodec:
                acodec = codec_name
    except Exception as e:
        if logger:
            logger.error("ffprobe failed for %s: %s", filepath, e)

    ext = os.path.splitext(filepath)[1].lstrip('.').lower()
    container = ext if ext else None
    result = {'vcodec': vcodec, 'acodec': acodec, 'container': container}
    with _FFPROBE_LOCK:
        _ffprobe_cache[filepath] = (key, result)
    return result


def get_container_from_ext(filepath):
    ext = os.path.splitext(filepath)[1].lstrip('.').lower()
    return ext if ext else None


def is_apple_direct(vcodec, acodec, container):
    """True if this streams H.264 video + AAC audio in an MP4-ish container."""
    if not vcodec or not acodec or not container:
        return False
    v_ok = re.match(r'^(avc1|h264)$', vcodec, re.I) is not None
    a_ok = re.match(r'^(aac|mp4a)$', acodec, re.I) is not None
    c_ok = re.match(r'^(mp4|mov|m4v)$', container, re.I) is not None
    return v_ok and a_ok and c_ok


# ---------------------------------------------------------------------------
# HLS transcode state
# ---------------------------------------------------------------------------

_active = {}            # key -> {'process': Popen, 'last_request': float, 'dir': str}
_active_lock = threading.Lock()
_settings_order = ['preset', 'crf', 'vcodec', 'acodec', 'video_filter']


def _settings_hash(settings):
    stable = json.dumps(settings, sort_keys=True)
    return hashlib.sha256(stable.encode()).hexdigest()[:16]


def _cache_base_dir():
    default = os.path.join(os.environ.get('VAULTTUBE_VAULTDIR', '/tmp'), '.transcode_cache')
    return os.environ.get('VAULTTUBE_TRANSCODE_CACHE_DIR', default)


def get_transcode_cache_dir(video_id, settings=None):
    settings = settings or get_transcode_settings()
    return os.path.join(_cache_base_dir(), video_id, _settings_hash(settings))


def get_transcode_settings():
    return {
        'preset': os.environ.get('VAULTTUBE_TRANSCODE_PRESET', 'veryfast'),
        'crf': int(os.environ.get('VAULTTUBE_TRANSCODE_CRF', '23')),
        'vcodec': 'libx264',
        'acodec': 'aac',
        'video_filter': 'format=yuv420p',
    }


def _source_path_for(video_id, logger):
    """Resolve a vault-relative filepath from the DB by video_id."""
    from database import get_connection
    con = get_connection(logger)
    cur = con.cursor()
    cur.execute("SELECT filepath FROM videos WHERE id = %s", (video_id,))
    row = cur.fetchone()
    cur.close()
    con.close()
    if not row or not row[0]:
        return None
    rel = row[0].lstrip('/')
    return os.path.join(os.environ['VAULTTUBE_VAULTDIR'], rel)


def _is_running(key):
    with _active_lock:
        item = _active.get(key)
        if item is None:
            return False
        proc = item.get('process')
        return proc is not None and proc.poll() is None


def _wait_for_first_segment(cache_dir, timeout=30.0, interval=0.2):
    playlist = os.path.join(cache_dir, 'playlist.m3u8')
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(playlist):
            try:
                with open(playlist, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('seg_') and line.endswith('.ts'):
                            seg = os.path.join(cache_dir, line)
                            if os.path.exists(seg) and os.path.getsize(seg) > 0:
                                return True
            except Exception:
                pass
        time.sleep(interval)
    return False


def _max_concurrent():
    try:
        return max(1, int(os.environ.get('VAULTTUBE_MAX_CONCURRENT_TRANSCODES', '1')))
    except ValueError:
        return 1


def _count_running():
    with _active_lock:
        return sum(
            1 for item in _active.values()
            if item.get('process') and item['process'].poll() is None
        )


def generate_hls(video_id, logger, source_path=None):
    """Ensure an HLS cache exists for video_id. Launch FFmpeg if needed.

    Blocks up to ~30s until playlist + first segment exist.
    Returns the cache directory path, or None on failure.
    """
    cache_dir = get_transcode_cache_dir(video_id)
    os.makedirs(cache_dir, exist_ok=True)
    playlist_path = os.path.join(cache_dir, 'playlist.m3u8')
    settings_hash = _settings_hash(get_transcode_settings())
    key = (video_id, settings_hash)

    # Fast path: already complete enough to play
    if os.path.exists(playlist_path):
        touch_cache_access(video_id)
        return cache_dir

    # Determine source path outside any lock
    if source_path is None:
        source_path = _source_path_for(video_id, logger)
    if not source_path or not os.path.isfile(source_path):
        return None

    # Try to start a new encode. Do expensive/size checks first, then lock.
    _enforce_cache_size_cap(logger)

    if _count_running() >= _max_concurrent():
        logger.info("Max concurrent transcodes reached for %s", video_id)
        return cache_dir if os.path.exists(playlist_path) else None

    acquired = False
    with _active_lock:
        if key in _active:
            _active[key]['last_request'] = time.time()
        else:
            settings = get_transcode_settings()
            err_log = os.path.join(cache_dir, 'ffmpeg.log')
            cmd = [
                'ffmpeg', '-y', '-i', source_path,
                '-c:v', settings['vcodec'],
                '-preset', settings['preset'],
                '-crf', str(settings['crf']),
                '-vf', settings['video_filter'],
                '-c:a', settings['acodec'], '-b:a', '128k',
                '-f', 'hls',
                '-hls_time', '6',
                '-hls_list_size', '0',
                '-hls_segment_filename', os.path.join(cache_dir, 'seg_%05d.ts'),
                playlist_path,
            ]
            logger.info("Starting HLS transcode for %s", video_id)
            try:
                err_fh = open(err_log, 'wb')
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=err_fh,
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                )
                # Avoid leaking the open file handle; we only need the PID.
                try:
                    proc.stderr = None
                except Exception:
                    pass
                err_fh.close()
            except Exception as e:
                logger.error("Failed to launch FFmpeg for %s: %s", video_id, e)
                return None
            _active[key] = {
                'process': proc,
                'last_request': time.time(),
                'dir': cache_dir,
                'video_id': video_id,
            }
            acquired = True

    if acquired:
        if not _wait_for_first_segment(cache_dir):
            logger.warning("HLS first segment timeout for %s", video_id)
            # Dump last lines of ffmpeg log for diagnostics
            err_log = os.path.join(cache_dir, 'ffmpeg.log')
            try:
                with open(err_log, 'r', errors='replace') as f:
                    tail = ''.join(f.readlines()[-20:])
                logger.error("FFmpeg log tail for %s:\n%s", video_id, tail)
            except Exception:
                pass
            # Kill a transcode that never produced a single segment; it's not usable
            with _active_lock:
                item = _active.get(key)
                if item:
                    _kill_proc(item)
                    del _active[key]
            return None
    else:
        # Another request started it; wait for it too
        if not os.path.exists(playlist_path):
            if not _wait_for_first_segment(cache_dir):
                logger.warning("HLS first segment timeout (waiter) for %s", video_id)
                return None

    return cache_dir


def touch_cache_access(video_id, settings=None):
    """Update the atime on the cache dir so cleanup knows it was used."""
    cache_dir = get_transcode_cache_dir(video_id, settings)
    try:
        Path(cache_dir).touch()
    except Exception:
        pass


def note_segment_request(video_id, settings=None):
    """Mark a live encode as active so the reaper doesn't kill it mid-playback.

    The disk-cache TTL is handled by touch_cache_access; this updates the
    in-memory last_request the reaper checks, which playlist requests bump
    but segment requests otherwise would not.
    """
    settings_hash = _settings_hash(settings or get_transcode_settings())
    key = (video_id, settings_hash)
    with _active_lock:
        item = _active.get(key)
        if item:
            item['last_request'] = time.time()


def _kill_proc(item):
    proc = item.get('process')
    if proc is None:
        return False
    if proc.poll() is not None:
        return True
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        pass
    return True


def transcode_reaper(logger, idle_timeout=60.0):
    """Killed orphaned encodes that haven't served a segment recently."""
    cutoff = time.time() - idle_timeout
    removed = []
    with _active_lock:
        for key, item in list(_active.items()):
            if item['last_request'] < cutoff:
                _kill_proc(item)
                removed.append(key)
        for key in removed:
            del _active[key]
    if removed and logger:
        logger.debug("Reaper killed %d idle transcode(s): %s", len(removed), removed)


def start_reaper_thread(logger, interval=30.0, idle_timeout=60.0):
    def loop():
        while True:
            time.sleep(interval)
            transcode_reaper(logger, idle_timeout)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def shutdown_transcoder(logger=None):
    """Terminate every active transcode. Call on SIGTERM/exit."""
    with _active_lock:
        for item in _active.values():
            _kill_proc(item)
        _active.clear()
    if logger:
        logger.info("Transcoder shutdown complete")


# ---------------------------------------------------------------------------
# Cache cleanup
# ---------------------------------------------------------------------------

def _cache_dir_size(cache_dir):
    total = 0
    try:
        for dirpath, _, fnames in os.walk(cache_dir):
            for f in fnames:
                total += os.path.getsize(os.path.join(dirpath, f))
    except OSError:
        pass
    return total


def _enforce_cache_size_cap(logger):
    max_bytes = _max_cache_bytes()
    base = _cache_base_dir()
    if not os.path.isdir(base):
        return

    dirs = []
    total = 0
    for entry in glob.glob(os.path.join(base, '*', '*')):
        if not os.path.isdir(entry):
            continue
        size = _cache_dir_size(entry)
        atime = _dir_atime(entry)
        dirs.append((atime, size, entry))
        total += size

    if total <= max_bytes:
        return

    dirs.sort(key=lambda x: x[0])
    for atime, size, d in dirs:
        if total <= max_bytes:
            break
        try:
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            if logger:
                logger.info("Evicted transcode cache %s (%.1f MB)", d, size / 1e6)
        except Exception as e:
            if logger:
                logger.error("Failed to evict %s: %s", d, e)


def cleanup_stale_caches(logger, ttl_seconds=None):
    """Delete cache dirs not accessed within ttl_seconds."""
    ttl_seconds = ttl_seconds or int(os.environ.get('VAULTTUBE_TRANSCODE_TTL', '86400'))
    cutoff = time.time() - ttl_seconds
    base = _cache_base_dir()
    if not os.path.isdir(base):
        return
    removed = 0
    for entry in glob.glob(os.path.join(base, '*', '*')):
        if not os.path.isdir(entry):
            continue
        try:
            atime = _dir_atime(entry)
            if atime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except Exception as e:
            if logger:
                logger.error("Failed to prune %s: %s", entry, e)
    if removed and logger:
        logger.info("Pruned %d stale transcode cache(s)", removed)


def _dir_atime(d):
    try:
        return os.path.getatime(d)
    except OSError:
        try:
            return os.path.getmtime(d)
        except OSError:
            return 0


def _max_cache_bytes():
    try:
        gb = float(os.environ.get('VAULTTUBE_TRANSCODE_MAX_CACHE_GB', '50'))
    except ValueError:
        gb = 50
    return int(gb * 1024 * 1024 * 1024)


def start_cleanup_thread(logger, interval=300.0, ttl_seconds=None):
    def loop():
        while True:
            time.sleep(interval)
            cleanup_stale_caches(logger, ttl_seconds)
            _enforce_cache_size_cap(logger)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
