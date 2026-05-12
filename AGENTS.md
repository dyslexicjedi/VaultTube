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
- `providers/base.py` - Shared state (`dl_status_map`) for download progress tracking
- `providers/youtube.py` - YouTube download via yt-dlp
- `providers/patreon.py` - Patreon download with screenshot capture
- `providers/reddit.py` - Reddit/RedGifs download via praw + yt-dlp

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
- `VAULTTUBE_VAULTDIR` - Video storage path
- `VAULTTUBE_DBHOST/DBUSER/DBPASS/DBNAME/DBPORT` - Database credentials
- `VAULTTUBE_YTKEY` - YouTube API key
- `VAULTTUBE_YTCOOKIE` - YouTube cookies file
- `VAULTTUBE_PATREONCOOKIE` - Patreon cookies file
- `VAULTTUBE_REDDIT_CLIENT_ID` - Reddit API client ID
- `VAULTTUBE_REDDIT_CLIENT_SECRET` - Reddit API client secret
- `VAULTTUBE_REDDIT_USERNAME` - Reddit account username
- `VAULTTUBE_REDDIT_PASSWORD` - Reddit account password

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
1. User submits URL → `QueueObject` added to queue
2. `downloader.py` processes queue
3. Provider-specific download (`youtube.py`/`patreon.py`/`reddit.py`)
4. Progress tracked in `dl_status_map`
5. After download, `backend.py` scans and adds to DB

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
