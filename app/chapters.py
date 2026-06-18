"""Chapter parsing for the video player.

YouTube encodes chapters as timestamp-prefixed lines in the video
description (e.g. ``0:00 Intro / 2:15 Setup / 10:42 Demo``). The
description is already stored on every YouTube video in the vault (the
``videos.description`` column, backfilled from the JSON snippet), so
chapters are pure derived data — no re-download or schema change required.

``parse_chapters`` is a pure function with no Flask/DB dependencies so it
can be unit-tested directly and reused by the ``/api/chapters/<id>`` route.
"""

import re

# A chapter timestamp at the start of a line. Accepts MM:SS, H:MM:SS, and
# HH:MM:SS (single- or double-digit hours/minutes). Anchored at line start
# (after stripping) via the match() call rather than the regex itself, so
# the same pattern can be applied per-line.
_TIMESTAMP_RE = re.compile(r'(?:(\d{1,2}):)?(\d{1,2}):(\d{2})')


def _ts_to_seconds(hours, minutes, seconds):
    """Convert regex groups (strings) to total seconds, or None if invalid."""
    try:
        h = int(hours) if hours is not None else 0
        m = int(minutes)
        s = int(seconds)
    except (ValueError, TypeError):
        return None
    # Minutes/hours have no fixed upper bound in practice, but reject
    # obviously malformed fields (e.g. "99:99") that would never be a real
    # timestamp. YouTube caps seconds/minutes at 59.
    if s > 59 or m > 59:
        return None
    return h * 3600 + m * 60 + s


def parse_chapters(description):
    """Parse chapter markers from a video description.

    Returns a list of ``{"start": <seconds>, "title": <str>}`` dicts, or
    ``[]`` when the description doesn't contain a valid chapter list.

    Rules (per issue #27):
      - A line that *starts with* a timestamp is a candidate chapter; the
        title is the remaining text on that line.
      - The first timestamp must resolve to 0 seconds (``0:00`` /
        ``00:00:00``).
      - Reject if fewer than 2 valid timestamps, if the first isn't 0, or
        if timestamps are not strictly increasing.
      - Timestamps beyond the video length are left intact here — duration
        is only known client-side, so the player clamps when rendering.
    """
    if not description:
        return []

    chapters = []
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _TIMESTAMP_RE.match(line)
        if not m:
            continue
        start = _ts_to_seconds(m.group(1), m.group(2), m.group(3))
        if start is None:
            continue
        title = line[m.end():].strip()
        # Strip a leading separator commonly used after the timestamp
        # ("0:00 - Intro", "0:00: Intro", "0:00 | Intro").
        title = re.sub(r'^[\s\-–—:|>+]+', '', title).strip()
        chapters.append({"start": start, "title": title})

    if len(chapters) < 2:
        return []
    if chapters[0]["start"] != 0:
        return []
    # Strictly increasing timestamps; a duplicate or out-of-order entry
    # means this isn't a real chapter list.
    for prev, cur in zip(chapters, chapters[1:]):
        if cur["start"] <= prev["start"]:
            return []
    return chapters
