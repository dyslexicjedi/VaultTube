# VaultTube Sentinel

VaultTube Sentinel turns source-disappearance tracking into an observatory and
an explicitly controlled rescue system. It records what changes, distinguishes
provider failures from genuine disappearance, explains why a source appears at
risk, and can prioritize still-available media after the user approves a
bounded rescue plan.

## Implementation status

- Phase 1 complete: durable scan evidence, two-check availability state, and
  the append-only event ledger.
- Phase 2 complete: Observatory read APIs and interface, creator context, and
  Sentinel data in JSON exports. The interface deliberately reports observed
  state rather than a risk score; risk scoring begins in Phase 4.
- Phase 3 complete: low-frequency complete remote inventories, durable
  pagination continuations, archive-coverage reporting, and inventory
  removal/restoration events kept separate from source availability.
- Phase 4 complete: deterministic source scores, stored evidence and reasons,
  two-check source availability, risk-change history, and High/Critical alerts
  in observation-only mode.
- Phase 5 next: deterministic rescue previews and storage estimates, with no
  enqueueing.

## Design principles

1. Observation, risk assessment, and rescue execution are separate stages.
2. A failed, quota-limited, or partial scan never counts as disappearance.
3. Inventory removal and source unavailability are different events.
4. Events are append-only and current-state fields are compatibility caches.
5. Rescue Mode previews its scope and storage estimate before enqueueing.
6. Automatic rescue remains disabled by default until the risk model has been
   validated against real-world observations.

## System flow

```text
Source inventory scan
  -> persist complete snapshot
  -> diff against previous complete snapshot
  -> verify suspected disappearances
  -> append events
  -> calculate explainable risk
  -> Observatory alert
  -> rescue preview
  -> user approval
  -> prioritized rescue queue
```

## Data model

### Phase 1: event ledger

- `sentinel_scan_runs` records the status and evidence quality of each check.
- `sentinel_video_state` records confirmed and suspected availability without
  overloading `videos.isDeleted`.
- `sentinel_events` is the append-only historical event stream.

Availability uses the following state machine:

```text
available
  -> suspected_unavailable   first independent negative observation
  -> unavailable             second independent negative observation
  -> available               later positive observation (restoration event)
```

Existing `isDeleted=1` rows are imported as `unavailable` with an
`imported_existing_state` event. The import timestamp records when Sentinel
learned about the legacy state, not when the video originally disappeared.

### Phases 3-4

- `sentinel_inventory` stores the latest complete remote inventory, including
  known videos that are not locally archived.
- `sentinel_sources` stores explainable source risk and its contributing facts.

Source-level availability has its own two-check state machine. A successful
`youtube.channels.list` response with no matching channel is a negative
observation; quota, authentication, network, and malformed responses are scan
failures and never observations. Risk-change and source restoration events are
append-only. High and Critical scores raise sticky alerts, but Phase 4 performs
no queue or rescue actions.

### Later phases

- `rescue_sessions` stores an approved bounded rescue operation.
- `rescue_items` tracks every candidate through queued, preserved, failed, or
  skipped states.

## Risk model

The first model is deterministic and stores its reasons. Candidate signals:

| Signal | Points |
|---|---:|
| Channel terminally unavailable in two complete checks | +45 |
| Five or more confirmed disappearances in 24 hours | +25 |
| More than 5% of known inventory vanished in seven days | +20 |
| Inventory shrank by more than 10% | +15 |
| Three consecutive non-quota source failures | +10 |
| More than 25 available videos are not preserved | +10 |
| No adverse events for 30 days | -15 |

Risk levels are Low (0-24), Elevated (25-49), High (50-74), and Critical
(75-100). Quota exhaustion, invalid credentials, network errors, and incomplete
pagination do not increase risk.

## Observatory interface

The Observatory provides:

- Saved-just-in-time and newly unavailable counts
- Unavailable/restored event timeline
- Risk-ranked sources with reasons
- Known-versus-preserved archive coverage
- Creator-specific disappearance history
- Rescue previews and active-session progress

Planned API surface:

```text
GET  /api/sentinel/summary
GET  /api/sentinel/events
GET  /api/sentinel/sources
GET  /api/sentinel/source/<type>/<id>
POST /api/sentinel/source/<type>/<id>/scan
POST /api/sentinel/source/<type>/<id>/rescue-preview
POST /api/sentinel/rescues
GET  /api/sentinel/rescues/<id>
POST /api/sentinel/rescues/<id>/pause
POST /api/sentinel/rescues/<id>/resume
POST /api/sentinel/rescues/<id>/cancel
```

## Rescue planning

A rescue candidate must be present in the latest complete inventory, believed
available, absent from `videos`, absent from `IgnoreVid`, and not already queued.
The preview reports candidate count, existing coverage, estimated storage
range, date range, and expected provider work.

User-defined limits include maximum videos, maximum estimated bytes, ordering,
download delay, and a stop threshold for repeated authentication or throttling
failures. Storage is expressed as a range derived from duration and observed
bitrate because providers do not reliably expose final download size.

The queue will eventually gain `priority`, `origin`, `rescue_session_id`, and
`target_item_id`. Manual downloads retain precedence over rescue work, and all
rescue state survives restart.

## Delivery phases

### Phase 1 - Event ledger

- Create the scan-run, video-state, and event tables.
- Record confirmed unavailable/restored transitions from the existing daily
  YouTube availability check.
- Require two independent negative checks before confirmation.
- Preserve `videos.isDeleted` as a denormalized compatibility field.
- Import legacy deleted state without inventing historical dates.
- Add transition, idempotency, migration, and provider-failure tests.

Exit criterion: every confirmed transition creates exactly one durable event,
and failed checks cannot create disappearance events.

### Phase 2 - Observatory MVP

- Add summary, event-feed, and source-detail APIs.
- Add `observatory.html`, its client script, and navigation.
- Add creator-page risk/event summaries.
- Include Sentinel data in JSON export.

Exit criterion: current and historical unavailable state is explorable without
using the old flat “Gone from source” filter.

### Phase 3 - Durable remote inventories

- Run a lower-frequency full census separately from the normal catch-up scan.
- Persist continuations and commit only complete inventory snapshots.
- Track remote-but-unarchived IDs.
- Distinguish playlist/feed removal from verified source unavailability.

Exit criterion: VaultTube can accurately report channel coverage without
queueing the channel's full history.

The inventory worker runs independently of the hourly catch-up scanner. Its
defaults are a seven-day interval, a 500-request pass budget, and a 2,000-page
per-source safety cap; configure these with
`VAULTTUBE_SENTINEL_CENSUS_INTERVAL`, `VAULTTUBE_SENTINEL_CENSUS_BUDGET`, and
`VAULTTUBE_SENTINEL_CENSUS_MAX_PAGES`. Interrupted runs retry hourly from their
persisted continuation token.

### Phase 4 - Explainable risk

- Calculate deterministic source risk.
- Store and display contributing reasons.
- Raise alerts and record risk changes.
- Operate in observation-only mode.

Exit criterion: disappearance bursts, restorations, outages, and quota failures
produce stable expected scores without false mass-deletion events.

### Phase 5 - Rescue preview

- Build the candidate planner and storage estimator.
- Add manual census and preview actions.
- Enqueue nothing during this phase.

Exit criterion: previews are deterministic, restart-safe, and exclude archived,
ignored, unavailable, and already queued videos.

### Phase 6 - Manual Rescue Mode

- Add rescue sessions/items and queue provenance.
- Introduce priority scheduling.
- Add start, pause, resume, cancel, and live progress.
- Enforce all approved limits.

Exit criterion: a rescue survives restart, obeys its caps, and does not starve
manual or ordinary subscription downloads.

### Phase 7 - Guarded automation

- Optionally trigger critical-risk rescues.
- Require hard byte/video limits and provide a global emergency stop.
- Audit every automatic decision.

Automation stays off by default.

## Testing invariants

- One negative observation never confirms disappearance.
- Two negative observations from different completed scans create one event.
- Repeated negative observations do not duplicate that event.
- A later positive observation creates one restoration event.
- Provider errors and partial scans create no availability transitions.
- Partial inventories create no inventory diff.
- Playlist removal is not reported as source deletion.
- Rescue preview excludes tombstones, duplicates, and queued items.
- Rescue limits are enforced before enqueueing.
- Pause, restart, and resume preserve rescue progress.
