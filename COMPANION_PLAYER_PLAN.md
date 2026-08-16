# Jellyfin Companion Player Plan

The companion player lets a locally owned Jellyfin video run beside a VaultTube
reaction video. VaultTube is the only browser-facing origin: Jellyfin credentials
remain on the server and media is delivered through the VaultTube proxy.

## Phase 1 — Playback and synchronization spike

Status: implemented for development testing.

- Load a Jellyfin item beside a VaultTube reaction.
- Keep both streams independent while staging the sync point.
- Press **Sync here** to store `offset = companion time - reaction time` and
  activate linked playback.
- Once linked, play, pause, seek, buffering pauses, and drift correction apply to
  both streams.
- Present equal-size 16:9 viewports and letterbox differing source aspect ratios
  without cropping or stretching.
- Fall back to VaultTube HLS when the browser cannot decode the stored reaction
  container/codec combination (notably VP9-in-MP4 on iPad/WebKit).
- Keep Jellyfin tokens server-side and rewrite HLS manifests through same-origin
  VaultTube routes.

Phase 1 deliberately keeps the mapping in the page URL and memory. It proves the
browser, proxy, codec, and synchronization behavior before database persistence.

## Phase 1.5 — Unified composite playback spike

Status: implemented and validated in Brave on desktop and iPad.

Use one composite HLS playback path on every device after synchronization. The
two independent elements are retained only for positioning the sources before
**Sync here** is pressed.

- Before synchronization, play the reaction independently and show the companion
  paused at its selected start position.
- **Sync here** captures the exact reaction and companion positions and creates an
  ephemeral composite session.
- FFmpeg scales and letterboxes both pictures into equal panes, places them side by
  side, mixes both audio tracks, and emits one H.264/AAC HLS stream.
- Replace the staging players with a single composite player on desktop and mobile.
- Treat the composite player's time as the canonical session clock for playback,
  seeking, later persistence, and resume.
- Keep all Jellyfin credentials server-side. Composite caches are temporary and
  must be reaped; they are never inserted into the VaultTube library.

The initial spike targets a 1280x360 output (two 640x360 panes), the `veryfast`
x264 preset, six-second HLS segments, and equal-gain audio mixed through a limiter.
The disposable Jellyfin fixture produced a valid 1280x360 H.264/AAC stream, and
the complete workflow was validated in Brave on both desktop and iPad.

## Phase 2 — Saved pairing, sync point, and resume

Status: implemented and validated in Brave on desktop and iPad. A real pairing
was synchronized, played, paused, closed, and reopened at its saved synchronized
position.

Persist one companion configuration per reaction video. The stored record should
contain:

- VaultTube reaction video ID.
- Jellyfin server/profile ID and Jellyfin item ID.
- Synchronization offset in fractional seconds.
- Canonical reaction resume position in fractional seconds.
- Whether synchronization has been completed.
- Created and updated timestamps.

Add API operations to read, create/update, and remove that pairing. These routes
currently share VaultTube's existing trust boundary because the application does
not yet have an authentication layer. Validate a new or changed Jellyfin item
through the configured server before saving it; progress-only writes reuse the
validated pairing and do not repeatedly query Jellyfin.

Save behavior:

- Save the pairing immediately when **Sync here** is pressed.
- Save the adjusted offset after **−1s/+1s** corrections or a later resync.
- While linked, periodically save the reaction position as the canonical clock.
- Flush the latest position on pause, page hide, and navigation using a request
  suitable for page teardown (`sendBeacon` or `fetch(..., {keepalive: true})`).
- Coalesce writes so ordinary playback does not create excessive database traffic.

Resume behavior:

1. Opening a paired reaction automatically loads its saved Jellyfin item.
2. Create a fresh composite session starting at the saved canonical reaction
   position.
3. Start its companion input at
   `max(0, reaction position + saved offset)`.
4. Restore the composite as linked but paused, avoiding mobile autoplay
   restrictions.
5. The first user play gesture starts the single composite stream on desktop and
   iPad through the same playback path.

The saved position is always an absolute position in the reaction. The composite
player's own timeline begins at zero for each generated session, so progress is
saved as `composite reaction start + current composite time`.

The reaction position is canonical so progress remains recoverable even if a
Jellyfin transcode session expires. Jellyfin playback reporting can be added for
its own watched history, but it must not be required to restore VaultTube state.

Acceptance coverage:

- Pairing and offset survive a server restart.
- Pausing, closing, and reopening restores both videos within the drift threshold.
- Fine adjustments persist and are used on the next visit.
- Missing/deleted Jellyfin items show a recoverable error without losing the saved
  reaction position.
- Removing a pairing returns the player to ordinary single-video behavior.
- Tests cover schema migration, API authorization/validation, save coalescing,
  resume calculations, negative offsets, end-of-media bounds, and page teardown.

## Implemented architecture

- `app/jellyfin.py` is a narrowly scoped Jellyfin client. It validates item IDs,
  applies credentials server-side, rejects cross-host manifest assets, strips
  secret query parameters, and exposes browser-safe same-origin HLS URLs.
- `app/composite.py` owns deterministic composite session metadata, FFmpeg
  process lifecycle, cache paths, segment waiting, and idle-process reaping.
- `app/api.py` exposes Jellyfin proxy routes, composite HLS routes, and durable
  companion-state CRUD operations.
- `companion_links` stores one pairing per reaction. Deleting the reaction
  cascades to its pairing; transient composite session IDs are never persisted.
- `player.html` stages the two sources independently, captures their positions,
  switches to the single composite stream, saves canonical reaction progress,
  and reconstructs a fresh paused composite when the page is reopened.
- The existing transcode cache root contains composite caches under
  `composite/<session-id>/`; the normal cache cleanup policy applies.

### Routes

| Route | Purpose |
|---|---|
| `GET /api/jellyfin/phase1/status` | Report whether Jellyfin is configured |
| `GET /api/jellyfin/phase1/<item>/manifest.m3u8` | Same-origin staging manifest |
| `GET /api/jellyfin/phase1/<item>/asset/<encoded>` | Relay a validated manifest asset |
| `POST /api/companion/composite` | Create or reuse a deterministic composite session |
| `GET /api/companion/composite/<session>/playlist.m3u8` | Composite VOD playlist |
| `GET /api/companion/composite/<session>/seg_<n>.ts` | Composite media segment |
| `GET/POST/PUT/DELETE /api/companion/state/<reaction>` | Read, save, update, or remove durable state |

### Verification completed

- Real VP9/AAC reaction plus H.264/E-AC-3 Jellyfin episode.
- FFmpeg output verified as 1280x360 H.264 with stereo AAC.
- Desktop Brave and iPad Brave composite playback.
- iPad play/pause through one media element, avoiding WebKit's competing-audio
  arbitration behavior.
- Durable save on synchronization, periodic progress, pause, and page teardown.
- Automatic paused restoration and restart-safe database persistence.
- Full automated suite: 240 tests passing at the Phase 2 checkpoint.

### Known limitations

- Jellyfin items are entered by ID; library browsing/search is Phase 3.
- Only one configured Jellyfin server profile is active, stored as `default`.
- The original post-mix audio was quieter than typical streaming sources. A
  measured sample peaked around -10.4 dB versus -6.6 dB for the episode source.
  Pipeline version 2 removes the per-input 80% reduction, explicitly downmixes
  both inputs to stereo, and applies single-pass EBU R128 normalization after the
  mix (default -16 LUFS, 11 LU range, and -1.5 dB true peak). The targets are
  configurable through `VAULTTUBE_COMPOSITE_LOUDNESS`,
  `VAULTTUBE_COMPOSITE_LOUDNESS_RANGE`, and
  `VAULTTUBE_COMPOSITE_TRUE_PEAK`. Audio settings and pipeline version participate
  in the session cache key so old quiet segments are never reused. On the same
  synchronized one-minute sample, pipeline v2 increased average level from
  approximately -36.5 dB to -22.8 dB (about 14 dB) while the measured encoded
  peak remained below full scale at approximately -0.9 dB. Browser playback and
  saved-session restoration were reverified, and the full suite passed with 242
  tests.
- The first balance adjustment applies +6 dB to the reaction and -3 dB to the
  Jellyfin input before mixing and final normalization. Both gains are configurable
  through `VAULTTUBE_COMPOSITE_REACTION_GAIN_DB` and
  `VAULTTUBE_COMPOSITE_COMPANION_GAIN_DB`, and both participate in the composite
  cache key. A later mixer UI can persist per-pairing overrides without changing
  the underlying FFmpeg model.
- Offset corrections after synchronization create a new composite session;
  playback seeking within the current composite uses its HLS VOD timeline.

## Phase 3 — Library selection and durable configuration

- Replace manual item IDs with a server-side Jellyfin search/browser.
- Support named Jellyfin server profiles without exposing credentials.
- Add polished **Change companion** and **Resync** actions. Pairing removal is
  already available from the Companion dialog.
- Report optional Jellyfin play progress and watched state.
- Add operational limits, structured proxy errors, and transcode/session cleanup.

## Phase 4 — Mobile and production hardening

- Exercise Brave on iPad/iPhone across rotation, background/foreground, lock-screen,
  and interrupted-network scenarios.
- Verify touch targets, stacked layout, fullscreen behavior, and audio focus.
- Add integration tests against a disposable Jellyfin fixture with direct-play and
  audio-transcode media.
- Document reverse-proxy timeouts, trusted-network deployment, backups, and rollout.
