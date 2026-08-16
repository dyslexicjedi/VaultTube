[![Dev Build](https://github.com/dyslexicjedi/VaultTube/actions/workflows/docker.yml/badge.svg?branch=dev)](https://github.com/dyslexicjedi/VaultTube/actions/workflows/docker.yml)
# VaultTube

VaultTube is a video archive and player application written in Python/Flask with a vanilla-JS/HTML5 frontend

## Status: 
[![Build Docker](https://github.com/dyslexicjedi/VaultTube/actions/workflows/docker.yml/badge.svg?branch=dev)](https://github.com/dyslexicjedi/VaultTube/actions/workflows/docker.yml)

VaultTube is current pre-alpha (hot code!), expect bugs, crashes and similar issues. Treat it like hot code, because it is, use at your own risk.

Who should use this: Alpha testers, people who don't mind "early-access" to help improve software.

## Jellyfin companion playback

VaultTube can pair a reaction video with an episode owned and hosted in Jellyfin.
Before synchronization, the reaction and episode are positioned independently.
Pressing **Sync here** creates one side-by-side H.264/AAC HLS stream, so desktop
and iPad browsers use the same playback path and one play/pause action.

The pairing, fractional-second offset, and canonical reaction position are saved
in MariaDB. Reopening the reaction creates a fresh composite session at the saved
position and leaves it paused for the next user gesture. Jellyfin credentials are
applied only on the server and are not included in browser URLs or manifests.

Current setup uses a Jellyfin item ID from the player's **Companion** dialog.
Library browsing and multi-server profiles remain future work. See
[COMPANION_PLAYER_PLAN.md](COMPANION_PLAYER_PLAN.md) for architecture, routes,
verification, limitations, and the remaining roadmap.

## How-TO:
Below is a docker compose entry for the database and vaulttube

```
version: "3"
services:
  db:
    image: mariadb
    environment:
      MYSQL_ROOT_PASSWORD: SuperSecretPassword
      MYSQL_DATABASE: vaulttube
      MYSQL_USER: vaulttube
      MYSQL_PASSWORD: SuperSecretPassword
    volumes:
      - /docker/mariadb:/var/lib/mysql
    ports:
      - "3306:3306"
  vaulttube:
    image: dyslexicjedi/vaulttube:<dev or latest>
    container_name: vaulttube
    environment:
      VAULTTUBE_VAULTDIR: <path to videos, generally will be /videos>
      VAULTTUBE_DBUSER: <database user>
      VAULTTUBE_DBPASS: <database password>
      VAULTTUBE_DBPORT: <database port>
      VAULTTUBE_DBHOST: <database ip>
      VAULTTUBE_DBNAME: <database name>
      VAULTTUBE_YTKEY: <YTKEY>
      VAULTTUBE_YTCOOKIE: <location to cookies.txt file>
    ports:
      - 5000:5000
    volumes:
      - <path to videos>:/videos
    restart: unless-stopped
    depends_on:
      - db
```

## Environment Variables

Required:

| Variable | Purpose |
|----------|---------|
| `VAULTTUBE_VAULTDIR` | Video storage path (generally `/videos`) |
| `VAULTTUBE_DBHOST` / `DBUSER` / `DBPASS` / `DBNAME` / `DBPORT` | MariaDB connection |
| `VAULTTUBE_YTKEY` | YouTube Data API key (used by `deleted_check`, playlist-info lookup, and subscription scanning) |

Optional:

| Variable | Purpose |
|----------|---------|
| `VAULTTUBE_YTCOOKIE` | Path to a Netscape-format cookies.txt for YouTube (used by downloads and subscription scanning) |
| `VAULTTUBE_PATREONCOOKIE` | Path to a Netscape-format cookies.txt for Patreon |
| `VAULTTUBE_DENOPATH` | Path to a deno binary for yt-dlp's JS runtime |
| `VAULTTUBE_REDDIT_CLIENT_ID` / `CLIENT_SECRET` / `USERNAME` / `PASSWORD` / `USER_AGENT` | Reddit API credentials (all five required to use the Reddit provider) |
| `VAULTTUBE_PORT` | Listen port (default 5000) |
| `VAULTTUBE_DEBUG` | Enable Flask debug mode |
| `VAULTTUBE_DISABLEBACK` | Set to anything but `False` to disable background scan/download threads |
| `VAULTTUBE_DL_DELAY` | Seconds between queued downloads (default 30) |
| `VAULTTUBE_DBPOOL` | Database connection pool size (default 8) |
| `VAULTTUBE_SENTINEL_CENSUS_INTERVAL` | Seconds between complete remote inventory censuses (default 604800 / 7 days) |
| `VAULTTUBE_SENTINEL_CENSUS_BUDGET` | YouTube API requests reserved for each Sentinel census pass (default 500) |
| `VAULTTUBE_SENTINEL_CENSUS_MAX_PAGES` | Safety cap per remote inventory snapshot (default 2000 pages) |
| `VAULTTUBE_SENTINEL_MANUAL_CENSUS_BUDGET` | YouTube API request cap for an explicitly requested Sentinel census (default 2000) |
| `VAULTTUBE_JELLYFIN_URL` | Jellyfin server base URL for companion playback |
| `VAULTTUBE_JELLYFIN_TOKEN` | Access token for a dedicated, restricted Jellyfin user; kept server-side |
| `VAULTTUBE_JELLYFIN_USER_ID` | Optional Jellyfin user ID sent with companion playback requests |
| `VAULTTUBE_JELLYFIN_VERIFY_TLS` | Verify Jellyfin TLS certificates (default `true`; disable only for a trusted local test server) |

## Contributing / Architecture

See [AGENTS.md](AGENTS.md) for a code map (components, DB tables, API routes)
and [CLAUDE.md](CLAUDE.md) for the development/testing workflow.

## Info

Problem? Open an issue [Issues](https://github.com/jedihomelab/VaultTube/issues) 

## Legally: 
Software is provided as is, use at own risk.

The authors are not to be held responsible for misuse, reuse, recycled and unintended uses of content within this repository by others. Only archive content that you have legal rights to or is public domain. 

This is free software under the GPL 2.0 open source license.

### Tags: 
`latest` will be current stable build, `dev` will be experimental builds based on each commit (bleeding edge)

## Video File Name:

VaultTube only cares about the folder and file name, specifically the folder must have the ID of the Channel and the file must be the id of the video. Example:

`/UC_aabbbcccdddeeefffgggg/abcdefghijk.mkv`

This means you can easily import already downloaded content by placing it in the folder structure and waiting. The periodic scan will pick it up and add it to the database.

## Issues

**Q:** It's broken?
**A:** yes and?
##

**Q:** Why was this created?
**A:** I wanted an archiving solution that worked for my needs, others didn't fit what I wanted so I rolled my own. 
##

**Q:** I want *this*?
**A:** Open an issue and I'll see what I can do. This is a hobby project for my own archiving but I'll try to accommodate 
