# VaultTube CodeMap

## Project Overview
VaultTube is a **video archive and player application** built with Python/Flask/Bootstrap5/HTML5. It downloads and manages YouTube/Patreon/Reddit content with a web interface for browsing, watching, and organizing videos.

## Core Architecture

### Entry Point
- `app/main.py` - Flask app startup, logging, environment validation, background thread initialization

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| **API Layer** | `app/api.py` | REST endpoints for videos, channels, playlists, downloads, search, stats |
| **Backend** | `app/backend.py` | File scanning, video metadata fetching, channel processing, deletion checks |
| **Scanner** | `app/scanner.py` | Periodic subscription scanning (channels/playlists) |
| **Downloader** | `app/downloader.py` | Queue-based download processing |
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
- `tags` - Video tags
- `IgnoreVid` - Ignored videos
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
│   ├── database.py          # DB operations
│   ├── QueueObject.py       # Download queue item
│   ├── providers/
│   │   ├── __init__.py      # Provider loader
│   │   ├── base.py          # Shared download state
│   │   ├── youtube.py       # YouTube provider
│   │   ├── patreon.py       # Patreon provider
│   │   └── reddit.py        # Reddit/RedGifs provider
│   ├── static/              # JS/CSS assets
│   └── templates/           # HTML templates
│       ├── base.html        # Base template
│       ├── index.html       # Home
│       ├── player.html      # Video player
│       ├── channels.html    # Channel browser
│       ├── playlists.html   # Playlist browser
│       ├── search.html      # Search results
│       ├── stats.html       # Statistics
│       ├── random.html      # Random video
│       ├── upload.html      # Video upload
│       ├── download.html    # Download interface
│       ├── creator.html     # Creator page
│       └── playlist.html    # Single playlist view
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
- `VAULTTUBE_PATREONCOOKIE` - Patreon cookies file (Netscape cookies.txt path)
- `VAULTTUBE_DENOPATH` - Deno binary path for yt-dlp's JS runtime (`/root/.deno/bin/deno` in the Docker image)
- `VAULTTUBE_REDDIT_CLIENT_ID` - Reddit API client ID
- `VAULTTUBE_REDDIT_CLIENT_SECRET` - Reddit API client secret
- `VAULTTUBE_REDDIT_USERNAME` - Reddit account username
- `VAULTTUBE_REDDIT_PASSWORD` - Reddit account password
- `VAULTTUBE_PORT` - Flask listen port (default 5000)
- `VAULTTUBE_DEBUG` - Enable Flask debug mode
- `VAULTTUBE_DISABLEBACK` - Set to anything but "False" to skip starting background threads

## File Naming Convention
```
/videos/{channel_id}/{video_id}.{ext}
/videos/UC_aabbbcccdddeeefffgggg/abcdefghijk.mkv
```

## Background Threads
1. **Backend thread** - Scans vault directory for new files
2. **Scanner thread** - Polls subscriptions (hourly)
3. **Downloader thread** - Processes download queue
4. **Deleted check thread** - Checks for deleted videos (disabled)

## Download Flow
1. URL enqueued via `queue_utils.enqueue()` → writes a `queue` table row + puts a `QueueObject` on the in-memory `queue.Queue` (`app.config['queue']`). URLs already pending/downloading are skipped (periodic scans don't duplicate a draining backlog)
2. `downloader.py` blocks on `q.get()` — items are picked up instantly
3. Provider-specific download (`youtube.py`/`patreon.py`/`reddit.py`); row status tracked pending → downloading → done/failed
4. Progress tracked in `dl_status_map` and broadcast to SSE subscribers
5. Transient (network) failures retry up to 3 times with a 60s delay; permanent failures land in `download_errors`
6. On startup, `main.py` re-enqueues any rows still pending/downloading from the last run
7. After download, `backend.py` scans and adds to DB

## Subscription Scanning (`scanner.py`, hourly)
- YouTube channels (`UC...` IDs) and playlists: polled via the YouTube Data API
- Patreon campaigns (numeric IDs in `channels`): polled via the Patreon posts API with cookies + impersonation (`scan_campaign` in `providers/patreon.py`); only viewable `*video*` post types are enqueued — `text_only`/`image_file`/`poll` posts carry no media
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
- `/api/getvids/<status>/<opt>/<direction>/<page>` - Get videos
- `/api/video/<id>` - Get single video
- `/api/watched/<id>` - Mark watched
- `/api/download/single` - Download single URL
- `/api/channels/<page>` - List channels
- `/api/creator/<creator>/<page>` - Creator videos
- `/api/search/<query>/<page>` - Search
- `/api/stats` - Statistics
- `/api/subscribe/unsubscribe/<type>/<value>` - Manage subscriptions
