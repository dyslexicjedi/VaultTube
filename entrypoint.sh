#!/bin/sh
# Upgrade yt-dlp before the app imports it, so every container restart picks up
# the latest YouTube extractor/signature fixes (a stale yt-dlp extracts metadata
# fine but 403s on the media fetch). Best-effort: if the upgrade fails (e.g. no
# network at boot), fall back to the version baked into the image rather than
# blocking startup.
# The curl-cffi extra keeps curl_cffi inside yt-dlp's supported version window
# (an unpinned curl_cffi can drift out of range and silently disable browser
# impersonation, breaking Patreon behind Cloudflare)
python3 -m pip install -U --pre "yt-dlp[default,curl-cffi]" \
  || echo "entrypoint: yt-dlp upgrade failed, continuing with bundled version"

# exec so python runs as PID 1 and receives docker stop's SIGTERM directly
# (otherwise the shell stays PID 1 and swallows the signal)
exec python /app/app/main.py
