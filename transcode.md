# Plan: Seekable HLS Transcode for Apple / Cross-Codec Playback

Unified implementation plan for [issue #23](https://github.com/dyslexicjedi/VaultTube/issues/23),
synthesized from the Kimi and DeepSeek proposals plus a pass over the actual codebase.

## Problem

`/api/transcode/<path:videopath>` (`app/api.py:814`) pipes a fragmented MP4 straight
from FFmpeg stdout with `mimetype="video/mp4"`. It returns **HTTP 200** and ignores
`Range`, so there is **no seeking, scrubbing, or resume** on transcoded content. It
also `os.path.join`s raw user-supplied path into the vault (path-traversal surface,
inconsistent with the traversal hardening already done on upload).

Separately, the player (`app/templates/player.html:159`) hard-codes
`type: 'video/webm'` for every video and never uses codec info to decide whether a
transcode is even needed.

Goal: replace the forward-only pipe with **HLS** (`.m3u8` + MPEG-TS segments) that
plays natively on Apple devices and supports seeking, and route the player to direct
playback when the source is already Apple-compatible.

## Facts established from the codebase (don't re-derive)

- Direct files are already served with full `Range`/206 support via a Flask static
  blueprint: `videos = Blueprint('videos', ..., static_url_path='/videos', static_folder=VAULTTUBE_VAULTDIR)`
  (`app/main.py:69`). **So the "direct play" path needs no new serving code** — only
  the correct MIME type in the player.
- `/api/video/<id>` (`app/api.py:170`) returns the row and rewrites `filepath` to
  `/videos/<channelId>/<video_id>.<ext>` (`app/api.py:185`). This is the natural place
  to add codec fields, and the natural key for ID-based transcode routing.
- `videos` table (`app/database.py:51`) has no codec columns. Migrations follow an
  idempotent `information_schema` check + `ALTER TABLE` pattern (`app/database.py:71-96`)
  — match it exactly.
- FFmpeg (and therefore ffprobe) is already in the image.
- **The bundled `app/static/js/video.min.js` (618 KB) already includes VHS
  (`@videojs/http-streaming`)** — verified by grep for `m3u8`/`http-streaming`. So HLS
  will play in Chrome/Firefox too, not just Safari. *No new front-end dependency.*
  (This was DeepSeek's top risk; it is resolved.)

## Design decisions (where the two plans differed)

| Topic | Choice | Why |
|-------|--------|-----|
| Routing | **ID-based** (`/api/transcode/<video_id>/...`), resolve path from DB (Kimi) | No path traversal; consistent with existing hardening. DeepSeek's path-based form re-trusts raw input. |
| Codec metadata | **DB columns + ffprobe**, lazy-populate on access *and* on vault scan (both) | Persisted, so the player can route without a probe per request. |
| `container` | Derive from file extension (cheap), no probe needed | Apple-direct check needs it; extension is authoritative here. |
| Missing-segment behavior | **Return 404, let video.js retry** (DeepSeek) | Simpler and more robust than a blocking 503 wait; VHS retries by default. Keep a short bounded wait only for the *playlist + first segment*. |
| Orphaned FFmpeg | **Kill if no segment requested for 60 s** (DeepSeek) + kill on shutdown (Kimi) | Stops wasted CPU when a viewer leaves mid-encode. |
| Cache invalidation | Include a **settings hash** (preset/crf/codec) in the cache path (Kimi) | Changing encode settings auto-orphans stale caches. |
| Concurrency | One transcode per cache key via a lock; duplicate requests poll the partial output (both) | Prevents double-encoding the same video. |
| Standalone `/api/codec/<id>` | **Drop it**; fold codec into `/api/video/<id>` | Avoids a second round-trip; the player already fetches `/api/video`. |

## The seekability tradeoff (called out honestly — neither source plan did)

On-demand HLS where FFmpeg transcodes the file **sequentially** lets the player seek
**within the region already generated**, plus resume and back-scrub. Seeking *far ahead*
of the encode frontier will stall until FFmpeg reaches it. For a single-viewer home
server this is a massive improvement over today (zero seeking) and is the recommended
build.

True arbitrary forward-seek (Plex/Jellyfin style) requires pre-computing a full **VOD
playlist** from the known duration and encoding each segment on demand with `-ss/-t`.
That is more code (keyframe alignment, per-segment processes) and is documented below as
a follow-up, not the first cut.

**Recommended first cut:** sequential on-demand HLS, `-hls_list_size 0` (all segments
stay listed), playlist finalized with `#EXT-X-ENDLIST` when encoding completes.

## Implementation

### Phase 1 — Codec metadata (also closes #22, independently mergeable)

1. `app/database.py` migration (match the existing idempotent pattern): add
   `vcodec VARCHAR(20)`, `acodec VARCHAR(20)`, `container VARCHAR(20)`, all
   `DEFAULT NULL`, to `videos`.
2. New helper in `app/transcoder.py` (see Phase 2):
   `get_codec_info(filepath) -> {"vcodec","acodec"}` running
   `ffprobe -v quiet -print_format json -show_streams -select_streams v:0,a:0 <file>`,
   with a small in-process cache keyed by `(path, mtime)`.
3. Populate codec/container columns during the vault scan (`app/backend.py`) and on
   save (`save_video`); back-fill lazily on `/api/video/<id>` when null.
4. `/api/video/<id>` (`app/api.py:170`) returns `vcodec`, `acodec`, `container`.

### Phase 2 — HLS transcode backend (`app/transcoder.py` + routes)

New module `app/transcoder.py`:

- `generate_hls(video_id, source_path) -> cache_dir`
  - Cache dir: `<CACHE_DIR>/<video_id>/<settings_hash>/`.
  - Acquire a per-key lock. If `playlist.m3u8` + first segment exist, return immediately.
  - Otherwise launch FFmpeg writing into the cache dir; **block only until the playlist
    and first `.ts` exist** (bounded timeout), then return.
  - Track PID + last-segment-request time per key; a reaper kills FFmpeg with no segment
    request for 60 s, and kills all on shutdown (extend the existing PID-1 signal
    handling in `app/main.py:53`).

  ```
  ffmpeg -i <source> \
    -c:v libx264 -preset <PRESET> -crf <CRF> -vf format=yuv420p \
    -c:a aac -b:a 128k \
    -f hls -hls_time 6 -hls_list_size 0 \
    -hls_segment_filename <dir>/seg_%05d.ts \
    <dir>/playlist.m3u8
  ```

- `cleanup_stale_caches()` — background thread (reuse the `VAULTTUBE_DISABLEBACK`
  gating in `app/main.py`): every few minutes delete cache dirs not accessed within TTL;
  if total size exceeds the cap, delete oldest first. Also run the size check **inline
  before** starting a new transcode.

Routes in `app/api.py` — **replace** the current `/transcode/<path:videopath>`:

- `GET /api/transcode/<video_id>/playlist.m3u8`
  - Look up `filepath` from DB by `video_id` (404 if absent/missing) — **no raw path
    input**.
  - `generate_hls(...)`; return the playlist with
    `Content-Type: application/vnd.apple.mpegurl`; bump cache access time.
- `GET /api/transcode/<video_id>/seg_<n>.ts`
  - Serve the segment from the cache dir with `Content-Type: video/MP2T` and
    `Accept-Ranges: bytes`. If the segment isn't written yet, return **404** (VHS
    retries). Validate `<n>`/filename to keep serving confined to the cache dir.

### Phase 3 — Player routing (`app/templates/player.html`)

Replace the hard-coded source at `:158-159`. Using the new codec fields:

```js
var appleDirect = /^(avc1|h264)$/i.test(v.vcodec || '') &&
                  /^(aac|mp4a)$/i.test(v.acodec || '') &&
                  /^(mp4|mov|m4v)$/i.test(v.container || '');

vPlayer = videojs('player', { techOrder: ['html5'], autoplay: false });
if (appleDirect) {
    vPlayer.src([{ type: 'video/mp4', src: filepath }]);            // existing /videos static serve, Range/206
} else {
    vPlayer.src([{ type: 'application/x-mpegURL',
                   src: '/api/transcode/' + encodeURIComponent(v.id) + '/playlist.m3u8' }]);
}
```

Fail open: if `vcodec`/`acodec` is unknown (null/probe failed), route to transcode.
Preserve the existing resume-timestamp, watched-marking, and poll logic below it
(`:161-174`).

### Phase 4 — Config, cleanup, tests

Optional env vars (document in CLAUDE.md "Environment variables"):

| Variable | Default | Purpose |
|----------|---------|---------|
| `VAULTTUBE_TRANSCODE_CACHE_DIR` | `<VAULTTUBE_VAULTDIR>/.transcode_cache` | HLS segment storage (on the vault volume) |
| `VAULTTUBE_TRANSCODE_TTL` | `86400` s | Idle cache lifetime before pruning |
| `VAULTTUBE_TRANSCODE_MAX_CACHE_GB` | `50` | Hard cap; oldest evicted first |
| `VAULTTUBE_TRANSCODE_PRESET` | `veryfast` | libx264 preset |
| `VAULTTUBE_TRANSCODE_CRF` | `23` | libx264 quality |
| `VAULTTUBE_MAX_CONCURRENT_TRANSCODES` | `1` | Cap simultaneous encodes |

Tests (`tests/`, follow conftest cleanup rules — register any DB rows written):
- Generate a tiny non-H.264 fixture (e.g. VP9 webm via `ffmpeg lavfi`).
- Assert `/api/transcode/<id>/playlist.m3u8` returns 200, the Apple HLS MIME type, and
  valid `#EXTM3U` content with at least one segment.
- Assert a segment request returns `video/MP2T`.
- Assert `/api/video/<id>` includes `vcodec`/`acodec`/`container`.
- Assert the route rejects/cannot reach paths outside the cache dir.

## Foreseen issues & mitigations

| Issue | Mitigation |
|-------|-----------|
| Forward-seek past encode frontier stalls | Documented tradeoff; sequential encode keeps ahead of playback; per-segment VOD design is the follow-up for full seek. |
| First-play latency (FFmpeg startup) | Block only until playlist + first segment exist. |
| Duplicate encodes for same video | Per-key lock; second request polls partial output. |
| Orphaned FFmpeg after viewer leaves | Reaper kills after 60 s of no segment requests; kill-all on shutdown. |
| Disk growth | TTL prune + size cap (inline check before new encode) on the vault volume. |
| Segment-not-ready race | 404 → VHS retries (no blocking 503). |
| Path traversal | ID→DB path resolution; segment filename validation. |
| ffprobe fails on corrupt file | Fail open: route to transcode. |
| Weak CPU (e.g. Pi) can't keep realtime | `VAULTTUBE_TRANSCODE_PRESET` knob; HW-accel encoders as a later add. |
| Subtitles / multiple audio tracks dropped | Documented limitation; extend stream mapping later. |
| Settings change leaves stale caches | `settings_hash` in cache path auto-orphans them; TTL prunes. |

## Follow-ups (out of scope for first cut)

- **Full arbitrary forward-seek**: pre-compute a VOD playlist from probed duration and
  transcode segments on demand with `-ss/-t` keyframe-aligned.
- **Hardware acceleration** with autodetect + `libx264` fallback: `h264_videotoolbox`
  (macOS), `h264_vaapi`/`h264_qsv` (Linux), `h264_nvenc` (NVIDIA).
- **Adaptive bitrate** (multiple renditions in a master playlist).

## Suggested merge order

1. Phase 1 (codec metadata) — standalone, also closes #22.
2. Phase 2 (transcode module + routes).
3. Phase 3 (player routing) — flips users onto the new path.
4. Phase 4 (config/cleanup/tests).

Per CLAUDE.md: validate Phase 2/4 against the live `vaulttube` container on
`rob@10.0.10.5` (real FFmpeg/ffprobe, env, DB) before pushing to `dev`.
