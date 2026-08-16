"""Ephemeral side-by-side companion transcodes.

The browser supplies only media IDs and captured start positions. Jellyfin
credentials stay server-side and FFmpeg writes a temporary HLS cache beneath
VaultTube's existing transcode cache root.
"""

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from jellyfin import get_config, item_info, validate_item_id
from transcoder import SEGMENT_SECONDS, get_duration, source_path_for


logger = logging.getLogger("composite")

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SESSION_ID_RE = re.compile(r"^[a-f0-9]{24}$")
_active = {}
_lock = threading.Lock()
_reaper_started = False
_PIPELINE_VERSION = 2


class CompositeError(RuntimeError):
    pass


def _cache_root():
    default = os.path.join(
        os.environ.get("VAULTTUBE_VAULTDIR", "/tmp"), ".transcode_cache"
    )
    return os.path.join(
        os.environ.get("VAULTTUBE_TRANSCODE_CACHE_DIR", default), "composite"
    )


def validate_session_id(session_id):
    if not _SESSION_ID_RE.fullmatch(session_id or ""):
        raise CompositeError("Invalid composite session ID")
    return session_id


def _validate_start(value, name):
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise CompositeError("%s must be a number" % name) from exc
    if value < 0 or value > 24 * 60 * 60:
        raise CompositeError("%s is out of range" % name)
    return round(value, 3)


def _audio_target(env_name, default, minimum, maximum):
    raw = os.environ.get(env_name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise CompositeError("%s must be a number" % env_name) from exc
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise CompositeError("%s is out of range" % env_name)
    return value


def _session_payload(reaction_id, item_id, reaction_start, companion_start):
    if not _VIDEO_ID_RE.fullmatch(reaction_id or ""):
        raise CompositeError("Invalid reaction video ID")
    validate_item_id(item_id)
    return {
        "reaction_id": reaction_id,
        "item_id": item_id,
        "reaction_start": _validate_start(reaction_start, "reaction_start"),
        "companion_start": _validate_start(companion_start, "companion_start"),
        "width": 1280,
        "height": 360,
        "pipeline_version": _PIPELINE_VERSION,
        "audio_loudness_i": _audio_target(
            "VAULTTUBE_COMPOSITE_LOUDNESS", -16.0, -70.0, -5.0
        ),
        "audio_loudness_lra": _audio_target(
            "VAULTTUBE_COMPOSITE_LOUDNESS_RANGE", 11.0, 1.0, 50.0
        ),
        "audio_true_peak": _audio_target(
            "VAULTTUBE_COMPOSITE_TRUE_PEAK", -1.5, -9.0, 0.0
        ),
        "reaction_gain_db": _audio_target(
            "VAULTTUBE_COMPOSITE_REACTION_GAIN_DB", 12.0, -30.0, 30.0
        ),
        "companion_gain_db": _audio_target(
            "VAULTTUBE_COMPOSITE_COMPANION_GAIN_DB", -8.0, -30.0, 30.0
        ),
    }


def _session_id(payload):
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def cache_dir_for(session_id):
    validate_session_id(session_id)
    return os.path.join(_cache_root(), session_id)


def _metadata_path(session_id):
    return os.path.join(cache_dir_for(session_id), "session.json")


def _write_metadata(session_id, metadata):
    cache_dir = cache_dir_for(session_id)
    os.makedirs(cache_dir, exist_ok=True)
    path = _metadata_path(session_id)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, sort_keys=True)
    os.replace(temporary, path)


def load_session(session_id):
    path = _metadata_path(session_id)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise CompositeError("Composite session was not found") from exc


def create_session(reaction_id, item_id, reaction_start, companion_start):
    payload = _session_payload(
        reaction_id, item_id, reaction_start, companion_start
    )
    source_path = source_path_for(reaction_id)
    if not source_path or not os.path.isfile(source_path):
        raise CompositeError("Reaction video was not found")

    config = get_config()
    details = item_info(config, item_id)
    companion_duration = details.get("duration")
    reaction_duration = get_duration(source_path)
    if not reaction_duration or not companion_duration:
        raise CompositeError("Could not determine both source durations")

    duration = min(
        reaction_duration - payload["reaction_start"],
        companion_duration - payload["companion_start"],
    )
    if duration <= 0:
        raise CompositeError("A start position is beyond the end of its source")

    payload.update({
        "duration": round(duration, 3),
        "created_at": int(time.time()),
    })
    session_id = _session_id({
        key: payload[key]
        for key in (
            "reaction_id", "item_id", "reaction_start", "companion_start",
            "width", "height", "pipeline_version", "audio_loudness_i",
            "audio_loudness_lra", "audio_true_peak", "reaction_gain_db",
            "companion_gain_db",
        )
    })
    _write_metadata(session_id, payload)
    _ensure_reaper()
    return session_id, payload


def _is_running(session_id):
    with _lock:
        item = _active.get(session_id)
        return bool(item and item["process"].poll() is None)


def _touch(session_id):
    cache_dir = cache_dir_for(session_id)
    try:
        Path(cache_dir).touch()
    except OSError:
        pass
    with _lock:
        if session_id in _active:
            _active[session_id]["last_request"] = time.time()


def _ffmpeg_command(metadata, cache_dir):
    config = get_config()
    source_path = source_path_for(metadata["reaction_id"])
    if not source_path or not os.path.isfile(source_path):
        raise CompositeError("Reaction video was not found")
    jellyfin_url = "%s/Videos/%s/stream?Static=true" % (
        config["base_url"], metadata["item_id"]
    )
    pane_width = metadata["width"] // 2
    pane_height = metadata["height"]
    filter_graph = (
        "[0:v:0]setpts=PTS-STARTPTS,fps=24,"
        "scale=%d:%d:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v0];"
        "[1:v:0]setpts=PTS-STARTPTS,fps=24,"
        "scale=%d:%d:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v1];"
        "[v0][v1]hstack=inputs=2[vout];"
        "[0:a:0]asetpts=PTS-STARTPTS,aresample=async=1,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,volume=%.1fdB[a0];"
        "[1:a:0]asetpts=PTS-STARTPTS,aresample=async=1,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,volume=%.1fdB[a1];"
        "[a0][a1]amix=inputs=2:duration=shortest:normalize=0,"
        "loudnorm=I=%.1f:LRA=%.1f:TP=%.1f:linear=false,"
        "aresample=48000[aout]"
    ) % (
        pane_width, pane_height, pane_width, pane_height,
        pane_width, pane_height, pane_width, pane_height,
        metadata.get("reaction_gain_db", 12.0),
        metadata.get("companion_gain_db", -8.0),
        metadata.get("audio_loudness_i", -16.0),
        metadata.get("audio_loudness_lra", 11.0),
        metadata.get("audio_true_peak", -1.5),
    )
    return [
        "ffmpeg", "-y",
        "-ss", "%.3f" % metadata["reaction_start"],
        "-i", source_path,
        "-ss", "%.3f" % metadata["companion_start"],
        "-headers", "X-Emby-Token: %s\r\n" % config["token"],
        "-i", jellyfin_url,
        "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", "[aout]",
        "-t", "%.3f" % metadata["duration"],
        "-c:v", "libx264", "-preset", os.environ.get(
            "VAULTTUBE_TRANSCODE_PRESET", "veryfast"
        ),
        "-crf", os.environ.get("VAULTTUBE_TRANSCODE_CRF", "23"),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:gte(t,n_forced*%d)" % SEGMENT_SECONDS,
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-f", "hls", "-hls_time", str(SEGMENT_SECONDS),
        "-hls_list_size", "0", "-hls_flags", "temp_file",
        "-hls_segment_filename", os.path.join(cache_dir, "seg_%05d.ts"),
        os.path.join(cache_dir, "playlist.m3u8"),
    ]


def ensure_running(session_id, require_index=0):
    metadata = load_session(session_id)
    cache_dir = cache_dir_for(session_id)
    required_segment = os.path.join(cache_dir, "seg_%05d.ts" % require_index)
    if os.path.isfile(required_segment) and os.path.getsize(required_segment) > 0:
        _touch(session_id)
        return metadata

    with _lock:
        current = _active.get(session_id)
        if current and current["process"].poll() is None:
            current["last_request"] = time.time()
            return metadata

        os.makedirs(cache_dir, exist_ok=True)
        log_path = os.path.join(cache_dir, "ffmpeg.log")
        command = _ffmpeg_command(metadata, cache_dir)
        log_handle = open(log_path, "ab")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        finally:
            log_handle.close()
        _active[session_id] = {
            "process": process,
            "last_request": time.time(),
            "dir": cache_dir,
        }
        logger.info("Started composite session %s", session_id)
    return metadata


def wait_for_segment(session_id, index, timeout=45.0, interval=0.2):
    metadata = ensure_running(session_id, require_index=index)
    cache_dir = cache_dir_for(session_id)
    path = os.path.join(cache_dir, "seg_%05d.ts" % index)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            _touch(session_id)
            return path, metadata
        if not _is_running(session_id):
            break
        _touch(session_id)
        time.sleep(interval)
    return None, metadata


def _kill(item):
    process = item.get("process")
    if not process or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _ensure_reaper():
    global _reaper_started
    with _lock:
        if _reaper_started:
            return
        _reaper_started = True

    def loop():
        while True:
            time.sleep(30)
            cutoff = time.time() - 90
            with _lock:
                stale = [key for key, item in _active.items()
                         if item["last_request"] < cutoff]
                for key in stale:
                    _kill(_active.pop(key))

    threading.Thread(target=loop, daemon=True).start()


def shutdown_composites():
    with _lock:
        for item in _active.values():
            _kill(item)
        _active.clear()
