# Companion playback deployment

Companion playback is designed for a trusted household network. VaultTube holds
the Jellyfin token and proxies all manifests and media; browsers never need
direct Jellyfin access. Do not expose an unauthenticated VaultTube instance to
the public internet.

## Jellyfin and VaultTube configuration

- Create a dedicated Jellyfin user with access only to the libraries used for
  companion playback. Use its token and, if library browsing or progress
  reporting is enabled, its user ID.
- Keep `VAULTTUBE_JELLYFIN_VERIFY_TLS=true` for HTTPS. Disabling verification is
  appropriate only for an isolated local test server.
- Start conservatively with `VAULTTUBE_MAX_CONCURRENT_COMPOSITES=1`. Each active
  composite owns an FFmpeg process and performs real-time H.264/AAC encoding.
- Size the volume containing the composite cache for the configured
  `VAULTTUBE_COMPOSITE_MAX_SESSIONS`. Cached sessions are disposable and do not
  belong in backups.
- Keep `VAULTTUBE_JELLYFIN_REPORT_PLAYBACK=false` unless VaultTube should update
  the dedicated Jellyfin user's resume and watched state.

## Reverse proxy

The routes below carry long-lived manifests or streaming responses:

```text
/api/jellyfin/phase1/
/api/companion/composite/
/api/transcode/
```

For those paths, allow range requests, disable response buffering, and use a
read timeout of at least 120 seconds. Preserve the original `Range` header and
do not cache authenticated API responses. A representative nginx location is:

```nginx
location /api/ {
    proxy_pass http://vaulttube:5000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Range $http_range;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}
```

Terminate TLS at the reverse proxy when clients cross an untrusted network.
Avoid logging query strings at debug level because library item identifiers may
appear in requests, even though credentials do not.

## Backup, rollout, and rollback

Back up MariaDB before rollout; the `companion_links` table contains pairings,
offsets, and canonical resume positions. Continue backing up the video vault and
Jellyfin configuration/library metadata through their existing mechanisms.
Composite and transcode caches are rebuildable and should be excluded.

1. Deploy the candidate image to a test stack with a disposable Jellyfin user.
2. Run `scripts/verify_companion_stack.py` against a known reaction fixture.
3. Back up MariaDB and record the currently deployed image digest.
4. Deploy, check `/api/health` and `/api/jellyfin/phase1/status`, then create and
   reopen one pairing before inviting users to test.
5. Roll back to the recorded image if necessary. The additive companion table
   may remain; older builds ignore it.

Example verification for the repository's Docker fixture:

```bash
.venv/bin/python scripts/verify_companion_stack.py \
  --base-url http://127.0.0.1:5001 \
  --reaction-id Bhh_IjnJwIU
```

## Human acceptance checklist

Run on both iPhone and iPad in Brave, using portrait and landscape:

- Create a pairing, seek both sources, synchronize, play, pause, and reopen it.
- Rotate while paused and while playing; confirm both halves remain letterboxed
  and controls remain reachable.
- Background and foreground Brave, then lock and unlock the device. Playback
  must return paused at the saved reaction position and resume on one tap.
- Interrupt Wi-Fi for at least 15 seconds and reconnect. Confirm the recovery
  message appears, playback remains paused, and manual retry is available if
  automatic recovery cannot finish.
- Enter and exit fullscreen and Picture-in-Picture where the browser offers it.
- Confirm only the composite audio plays, hardware volume controls work, and no
  second audio stream continues after pausing or leaving the page.
- Confirm all touch controls are comfortable and no horizontal scrolling occurs.
