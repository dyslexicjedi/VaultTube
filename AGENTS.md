# VaultTube CodeMap

## Project Overview
VaultTube is a **video archive and player application** built with Python/Flask and a vanilla-JS frontend (no Bootstrap/jQuery; `theme.css` is the single stylesheet/design system). It downloads and manages YouTube/Patreon/Reddit content with a web interface for browsing, watching, and organizing videos.

## Core Architecture

### Entry Point
- `app/main.py` - Flask app startup, logging, environment validation, background thread initialization

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| **API Layer** | `app/api.py` | REST endpoints for videos, channels, playlists, downloads, search, stats, HLS transcode |
| **Backend** | `app/backend.py` | File scanning, video metadata fetching, channel processing, deletion checks |
| **Scanner** | `app/scanner.py` | Periodic subscription scanning (channels/playlists) |
| **Downloader** | `app/downloader.py` | Download queue processor |
| **Transcoder** | `app/transcoder.py` | HLS transcode cache, FFmpeg lifecycle, reaper/cleanup threads |
| **Database** | `app/database.py` | DB connection, table creation, CRUD operations for videos/channels/playlists |
| **Providers** | `app/providers/` | Download logic for YouTube (`youtube.py`), Patreon (`patreon.py`), and Reddit/RedGifs (`reddit.py`) |

### Providers
- `providers/__init__.py` - Auto-loads every module in `providers/` (except `base`) that implements the contract
- `providers/base.py` - Shared state (`dl_status_map`) for download progress tracking + SSE pub/sub for live progress events
- `providers/youtube.py` - YouTube download via yt-dlp
- `providers/patreon.py` - Patreon download with screenshot capture (URL normalization + browser impersonation for Cloudflare)
- `providers/reddit.py` - Reddit/RedGifs download via praw + yt-dlp

**Provider contract** (required to be picked up by the loader):
```python
provider_domains() -> list[str]   # substring-matched against the queued URL
download(q, logger) -> bool       # q is a QueueObject (q.url, q.source, ...)
```
`download()` returns `True` on success; returning `False` or raising logs the
failure to the `download_errors` table. Dispatch is by URL domain first, then
by `q.source` as a fallback (see `downloader.py`).

### Data Models

**Database Tables:**
- `videos` - Video metadata, filepath, watched status, timestamps
- `channels` - Channel info, subscription status
- `playlists` - Playlist metadata, subscription status
- `pl2vid` - Playlist-to-video mappings
- `images` - Thumbnail blobs
- `IgnoreVid` - Ignored videos + not-found tombstones (videos gone from the source; these used to be fake `youtuber='404'` rows in `videos`)
- `download_errors` - Download error logging
- `queue` - Persisted download queue (status, attempts, last_error); done/failed rows auto-pruned after 7 days

## Directory Structure

```
VaultTube/
├── app/
│   ├── main.py              # Flask entry point
│   ├── api.py               # REST API endpoints
│   ├── backend.py           # File scanning & metadata
│   ├── scanner.py           # Subscription scanning
│   ├── downloader.py        # Download queue processor
│   ├── transcoder.py        # HLS transcode cache, FFmpeg lifecycle, reaper/cleanup threads
│   ├── database.py          # DB operations
│   ├── QueueObject.py       # Download queue item
│   ├── providers/
│   │   ├── __init__.py      # Provider loader
│   │   ├── base.py          # Shared download state
│   │   ├── youtube.py       # YouTube provider
│   │   ├── patreon.py       # Patreon provider
│   │   └── reddit.py        # Reddit/RedGifs provider
│   ├── static/              # theme.css + cards.js/shell.js (+ video.js)
│   └── templates/           # HTML templates
│       ├── base.html        # Shell: sidebar, topbar search, Add modal
│       ├── index.html       # Home (hero + rails)
│       ├── browse.html      # Paginated grid, filters, deleted view
│       ├── player.html      # Video player with Up Next
│       ├── queue.html       # Download queue + failed downloads
│       ├── channels.html    # Channel browser
│       ├── playlists.html   # Playlist browser
│       ├── search.html      # Search results
│       ├── stats.html       # Statistics dashboard
│       ├── random.html      # Random video
│       ├── creator.html     # Creator page
│       └── playlist.html    # Single playlist view
│       (old /download.html and /upload.html 301 to /queue.html)
├── tests/
│   ├── conftest.py          # pytest fixtures
│   ├── __init__.py          # Package init
│   └── test_project.py      # Project tests
├── requirements.txt         # Python dependencies
└── .env                     # Environment variables
```

## Key Environment Variables

Required at startup (validated in `main.py`):
- `VAULTTUBE_VAULTDIR` - Video storage path
- `VAULTTUBE_DBHOST/DBUSER/DBPASS/DBNAME/DBPORT` - Database credentials
- `VAULTTUBE_YTKEY` - YouTube API key

Optional:
- `VAULTTUBE_YTCOOKIE` - YouTube cookies file (Netscape cookies.txt path)
- `VAULTTUBE_PROXY` - Optional HTTP/HTTPS/SOCKS proxy URL for yt-dlp to use when YouTube returns a "blocked in your country" error (e.g. `http://10.0.10.5:8888`)
- `VAULTTUBE_PATREONCOOKIE` - Patreon cookies file (Netscape cookies.txt path)
- `VAULTTUBE_DENOPATH` - Deno binary path for yt-dlp's JS runtime (`/root/.deno/bin/deno` in the Docker image)
- `VAULTTUBE_REDDIT_CLIENT_ID` - Reddit API client ID
- `VAULTTUBE_REDDIT_CLIENT_SECRET` - Reddit API client secret
- `VAULTTUBE_REDDIT_USERNAME` - Reddit account username
- `VAULTTUBE_REDDIT_PASSWORD` - Reddit account password
- `VAULTTUBE_REDDIT_USER_AGENT` - User agent for the Reddit API (required if the other Reddit vars are set)
- `VAULTTUBE_PORT` - Flask listen port (default 5000)
- `VAULTTUBE_DEBUG` - Enable Flask debug mode
- `VAULTTUBE_DISABLEBACK` - Set to anything but "False" to skip starting background threads
- `VAULTTUBE_DL_DELAY` - Seconds to wait between queued downloads (default 10; single adds skip the delay)
- `VAULTTUBE_DBPOOL` - DB connection pool size (default 8; overflow falls back to direct connections)
- `VAULTTUBE_TRANSCODE_CACHE_DIR` - HLS segment cache directory (default `<VAULTTUBE_VAULTDIR>/.transcode_cache`)
- `VAULTTUBE_TRANSCODE_TTL` - Idle cache lifetime in seconds before pruning (default 86400)
- `VAULTTUBE_TRANSCODE_MAX_CACHE_GB` - Hard cache cap in GB; oldest idle caches evicted first (default 50)
- `VAULTTUBE_TRANSCODE_PRESET` - libx264 preset for HLS transcodes (default `veryfast`)
- `VAULTTUBE_TRANSCODE_CRF` - libx264 quality for HLS transcodes (default 23)
- `VAULTTUBE_MAX_CONCURRENT_TRANSCODES` - Maximum simultaneous HLS encodes (default 1)

## File Naming Convention
```
/videos/{channel_id}/{video_id}.{ext}
/videos/UC_aabbbcccdddeeefffgggg/abcdefghijk.mkv
```

## Background Threads
1. **Backend thread** - Scans vault directory for new files
2. **Scanner thread** - Polls subscriptions (hourly)
3. **Downloader thread** - Processes download queue
4. **Deleted check thread** - Checks YouTube for source-deleted videos in batches of 50 IDs/call; found videos are tombstoned into `IgnoreVid`
5. **Transcoder reaper thread** - Kills idle HLS encodes that haven't served a segment recently
6. **Transcoder cleanup thread** - Prunes stale HLS cache entries by TTL and size cap

## Download Flow
1. URL enqueued via `queue_utils.enqueue()` → writes a `queue` table row + puts a `QueueObject` on the in-memory `queue.Queue` (`app.config['queue']`). URLs already pending/downloading are skipped (periodic scans don't duplicate a draining backlog)
2. `downloader.py` blocks on `q.get()` — items are picked up instantly
3. Provider-specific download (`youtube.py`/`patreon.py`/`reddit.py`); row status tracked pending → downloading → done/failed
4. Progress tracked in `dl_status_map` and broadcast to SSE subscribers
5. Transient failures (network errors, 429/throttling) retry up to 3 times with a 60s delay; permanent failures land in `download_errors`. Queued downloads are paced `VAULTTUBE_DL_DELAY` seconds apart
6. On startup, `main.py` re-enqueues any rows still pending/downloading from the last run
7. After download, `backend.py` scans and adds to DB

## Subscription Scanning (`scanner.py`, hourly)
- YouTube channels (`UC...` IDs) and playlists: polled via the YouTube Data API
- Patreon campaigns (numeric IDs in `channels`): polled via the Patreon posts API with cookies + impersonation (`scan_campaign` in `providers/patreon.py`); a post is enqueued if it's a `*video*` post type OR a block-editor post with an inline video block in `content_json_string` (these report `text_only`). Posts with neither are skipped
- Patreon campaigns get their `channels` row auto-created on first download or vault scan (`ensure_channel`); subscribe via the normal `/api/subscribe/channel/<campaign_id>` endpoint

## Testing & CI
- `.venv/bin/python -m pytest tests/ -q` — tests need a reachable MariaDB
  (connection from `.env`, loaded by `load_dotenv()` in `main.py`); they
  insert/delete real rows and `conftest.py` cleans up known test IDs.
- A pre-commit hook runs the full suite on every commit.
- Pushing to `dev` runs tests in GitHub Actions (with a MariaDB service
  container) and builds/pushes `dyslexicjedi/vaulttube:dev`.
- See `CLAUDE.md` for the deployment layout and how to test changes inside
  the production container before pushing.

## API Routes (key)
- `/api/getvids/<status>/<opt>/<direction>/<page>` - Get videos (`?deleted=1` for gone-from-source view; `?channelId=<id>` or `?channel_ids[]=<id>` to filter by channel)
- `/api/video/<id>` - Get single video
- `/api/watched/<id>` - Mark watched
- `/api/up_next/<id>` - Series-ordered unwatched list for the player
- `/api/download/single` - Download single URL
- `/api/downloads/retry` (POST) - Re-enqueue a failed download
- `/api/channels/<page>` - List channels (per-channel unwatched counts, `?order=activity`)
- `/api/channel/<id>` - Channel info + derived source URL, plus `description`, `thumbnail_url`, and `video_count`
- `/api/channel/<id>/<page>` - Paged videos for a single channel (`?status=unwatched|watched`, `?sort=...`, `?direction=asc|desc`)
- `/api/creator/<creator>/<page>` - Creator videos
- `/api/search/<query>/<page>` - Search
- `/api/stats` - Statistics dashboard data
- `/api/status/stream` - SSE: live queue/download progress
- `/api/health` - Liveness probe (one SELECT 1; used by the Docker HEALTHCHECK)
- `/api/transcode/<video_id>/playlist.m3u8` - HLS playlist for non-Apple videos
- `/api/transcode/<video_id>/seg_<n>.ts` - HLS segment
- `/api/subscribe/unsubscribe/<type>/<value>` - Manage subscriptions
