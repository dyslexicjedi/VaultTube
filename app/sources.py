"""Display names for video sources.

YouTube, Patreon and Reddit have their own providers; every other source is
a site name from the generic yt-dlp provider (``vimeo``, ``twitch``, ...).
Legacy channel rows carry no source and are classified by ID shape.
"""

import re

KNOWN = {
    'youtube': 'YouTube',
    'patreon': 'Patreon',
    'reddit': 'Reddit',
    'twitch': 'Twitch',
    'peertube': 'PeerTube',
    'soundcloud': 'SoundCloud',
    'bandcamp': 'Bandcamp',
    'dailymotion': 'Dailymotion',
    'odysee': 'Odysee',
    'lbry': 'Odysee',
}

SOURCE_RE = re.compile(r'^[a-z0-9]{1,100}$')


def label(source):
    if not source:
        return ''
    return KNOWN.get(source) or source[:1].upper() + source[1:]


def channel_source_sql(channel_col='channels.channelId', source_col='channels.source'):
    """SQL expression classifying a channel row, for rows with no videos."""
    return (
        "coalesce({src}, case when left({ch}, 2) = 'UC' then 'youtube' "
        "when {ch} regexp '^[0-9]+$' then 'patreon' else 'reddit' end)"
    ).format(src=source_col, ch=channel_col)
