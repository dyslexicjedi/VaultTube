# CLAUDE.md

VaultTube is a self-hosted video archive and player (Python/Flask/MariaDB) that
downloads YouTube/Patreon/Reddit content via yt-dlp. See `AGENTS.md` for the
full code map (files, DB tables, API routes).

## Critical workflow: test in production container BEFORE pushing

Pushing to `dev` triggers a GitHub Actions build (`.github/workflows/docker.yml`)
of `dyslexicjedi/vaulttube:dev`, which is **slow**. The live deployment is the
docker container `vaulttube` on `rob@10.0.10.5` (passwordless SSH works).
Validate changes against the real container first:

```bash
# Inspect the running container (app code lives at /app/app/ inside it)
ssh rob@10.0.10.5 'docker exec vaulttube cat /app/app/providers/patreon.py'

# Run a test script inside the container (real env vars, cookies, DB, deno)
scp myscript.py rob@10.0.10.5:/tmp/
ssh rob@10.0.10.5 'docker cp /tmp/myscript.py vaulttube:/tmp/ && docker exec vaulttube python3 /tmp/myscript.py'
```

The container has all production env vars set, so scripts can use
`os.environ['VAULTTUBE_*']` directly. Clean up `/tmp` scripts when done.
Note: the container runs the last *pushed* image — local uncommitted changes
are not in it; copy them in or replicate their logic in the test script.

## Local development

```bash
.venv/bin/python -m pytest tests/ -q   # run tests (36 tests, ~1s)
```

- Tests need a reachable MariaDB; connection info comes from `.env` at the repo
  root (loaded by `load_dotenv()` in `app/main.py`). Tests insert/delete real
  rows; `tests/conftest.py` cleans up known test IDs before and after each test.
- A pre-commit hook runs the full test suite on every commit; commits fail if
  tests fail.
- Branch `dev` is the working/default branch; `latest` image tag is stable.

## Debugging production issues

- App logs: `ssh rob@10.0.10.5 'docker logs vaulttube --tail 100'`
- Failed downloads are recorded in the `download_errors` DB table (url,
  error_type, error_message, created_at) — check there for history.
- Query the DB from inside the container with the `VAULTTUBE_DB*` env vars and
  the `mariadb` Python module (no mysql CLI in the image).
- The download queue is **in-memory** (`queue.Queue` in `app.config['queue']`),
  not persisted — queued items are lost on restart and cannot be inspected
  from outside.

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
- Some Patreon posts are `post_type: "text_only"` with no media at all (the
  creator never attached a video) — "No supported media found in this post"
  can be correct behavior, not a bug. Verify via the Patreon API before
  debugging: `https://www.patreon.com/api/posts/{id}?json-api-version=1.0`.

## Environment variables

Required: `VAULTTUBE_VAULTDIR`, `VAULTTUBE_DBHOST/DBUSER/DBPASS/DBNAME/DBPORT`,
`VAULTTUBE_YTKEY`.
Optional: `VAULTTUBE_YTCOOKIE`, `VAULTTUBE_PATREONCOOKIE` (Netscape cookies.txt
paths), `VAULTTUBE_DENOPATH` (deno binary for yt-dlp JS runtime;
`/root/.deno/bin/deno` in the image), `VAULTTUBE_REDDIT_CLIENT_ID/
CLIENT_SECRET/USERNAME/PASSWORD`, `VAULTTUBE_PORT`, `VAULTTUBE_DEBUG`,
`VAULTTUBE_DISABLEBACK`.
