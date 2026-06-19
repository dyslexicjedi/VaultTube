# CLAUDE.md

VaultTube is a self-hosted video archive and player (Python/Flask/MariaDB) that
downloads YouTube/Patreon/Reddit content via yt-dlp. See `AGENTS.md` for the
full code map (files, DB tables, API routes).

## Local development

```bash
.venv/bin/python -m pytest tests/ -q   # run tests (a few seconds)
```

- Tests spin up an isolated MariaDB Docker container via `testcontainers`
  (`conftest.py`). No `.env` file or external database is used or needed.
- A pre-commit hook runs the full test suite on every commit; commits fail if
  tests fail.
- Branch `dev` is the working/default branch; `latest` image tag is stable.

## Debugging

- Failed downloads are recorded in the `download_errors` DB table (url,
  error_type, error_message, created_at) — check there for history.
- The download queue is dispatched from an in-memory `queue.Queue`
  (`app.config['queue']`) but every item is mirrored to the `queue` DB table
  (status: pending/downloading/done/failed, attempts, last_error). Unfinished
  rows are re-enqueued on startup, transient (network) failures retry up to 3
  times, and `queue_utils.enqueue()` skips URLs already pending. Inspect the
  table to see what's queued; enqueue ONLY via `queue_utils.enqueue()` so the
  DB row is written.

## Architecture notes (the non-obvious parts)

- **Provider contract** (`app/providers/__init__.py` auto-loads any module in
  `app/providers/` except `base`): a provider must implement
  `provider_domains() -> list[str]` (substring-matched against the URL) and
  `download(q, logger) -> bool` where `q` is a `QueueObject` (`q.url`,
  `q.source`, ...). Return `True` on success; raise or return `False` on
  failure (downloader logs both to `download_errors`).
- `app/downloader.py` dispatches by URL domain first, then by `q.source`.
- Background threads (backend file scanner, subscription scanner, downloader)
  are started from `app/main.py`; `VAULTTUBE_DISABLEBACK` controls them.
- Subscriptions live in the `channels` table; the scanner treats numeric
  channel IDs as Patreon campaign IDs (scanned via the Patreon posts API,
  only `*video*` post types enqueued) and `UC...` IDs as YouTube channels.
  Patreon campaigns get their `channels` row auto-created on first download
  or vault scan (`ensure_channel` in `app/providers/patreon.py`).
- Video files live at `$VAULTTUBE_VAULTDIR/{channel_id}/{video_id}.{ext}`;
  the backend thread scans the vault and adds any new files to the DB, so
  a download is "done" when the file lands in the right place.
- Thumbnails are stored as blobs in the `images` DB table (captured via
  ffmpeg for non-YouTube providers).

## yt-dlp gotchas (learned the hard way)

- **Cloudflare 403s (Patreon, possibly others)**: set impersonation globally —
  `'impersonate': ImpersonateTarget.from_str('chrome')` (from
  `yt_dlp.networking.impersonate`, needs `curl_cffi`, already in the image).
  The `extractor_args: {'generic': {'impersonate': ...}}` form only affects
  the *generic* extractor and does nothing once a site-specific extractor
  matches the URL.
- `'cookiefile'` must be a filesystem path, not a file-like object.
- Patreon URLs with a creator prefix (`patreon.com/{creator}/posts/{slug}`)
  do NOT match yt-dlp's Patreon extractor and silently fall through to the
  generic extractor. Normalize to `patreon.com/posts/{slug}` first
  (see `_normalize_url` in `app/providers/patreon.py`).
- Patreon posts made with the block editor report `post_type: "text_only"`
  with null `post_file`/`embed` even when they contain a video — the video is
  an inline block in the `content_json_string` attribute
  (`{"type":"video","attrs":{"media_id":...}}`), and yt-dlp's extractor fails
  with "No supported media found". The provider falls back to
  `_download_inline_video()`: resolve the media via
  `patreon.com/api/media/{id}` and download `display.url` (signed Mux HLS
  master). Truly media-less posts (announcements, polls) also exist; check
  `content_json_string` to tell them apart.

## Environment variables

Required: `VAULTTUBE_VAULTDIR`, `VAULTTUBE_DBHOST/DBUSER/DBPASS/DBNAME/DBPORT`,
`VAULTTUBE_YTKEY`.
Optional: `VAULTTUBE_YTCOOKIE`, `VAULTTUBE_PATREONCOOKIE` (Netscape cookies.txt
paths), `VAULTTUBE_PROXY` (HTTP/HTTPS/SOCKS proxy URL for yt-dlp to use when
YouTube returns a "blocked in your country" error),
`VAULTTUBE_DENOPATH` (deno binary for yt-dlp JS runtime;
`/root/.deno/bin/deno` in the image), `VAULTTUBE_REDDIT_CLIENT_ID/
CLIENT_SECRET/USERNAME/PASSWORD/USER_AGENT` (all five needed for the Reddit
provider), `VAULTTUBE_PORT`, `VAULTTUBE_DEBUG`, `VAULTTUBE_DISABLEBACK`,
`VAULTTUBE_DL_DELAY` (seconds between queued downloads, default 10; single
adds unaffected), `VAULTTUBE_DBPOOL` (DB connection pool size, default 8),
`VAULTTUBE_TRANSCODE_CACHE_DIR` (HLS cache directory, default
`<VAULTTUBE_VAULTDIR>/.transcode_cache`), `VAULTTUBE_TRANSCODE_TTL` (idle
cache TTL in seconds, default 86400), `VAULTTUBE_TRANSCODE_MAX_CACHE_GB`
(max cache size in GB, default 50), `VAULTTUBE_TRANSCODE_PRESET` (libx264
preset, default `veryfast`), `VAULTTUBE_TRANSCODE_CRF` (libx264 quality,
default 23), `VAULTTUBE_MAX_CONCURRENT_TRANSCODES` (default 1),
`VAULTTUBE_TRANSCODE_SEGMENT_TIMEOUT` (seconds a segment request long-polls
for a not-yet-produced segment before 404-ing, default 30).
