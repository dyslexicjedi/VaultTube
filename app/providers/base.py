
# Shared download status map: videoID -> {progress, title, type, ...}
dl_status_map = {}


def get_dl_status():
    """Return the progress of the first active download, or 0 if none."""
    try:
        if not dl_status_map:
            return 0
        first = next(iter(dl_status_map.values()))
        return first.get('progress', 0)
    except Exception:
        return 0


def get_cur_videoID():
    """Return the ID of the first actively downloading video."""
    try:
        if not dl_status_map:
            return ""
        return next(iter(dl_status_map))
    except Exception:
        return ""


def get_cur_videoTitle():
    """Return the title of the first actively downloading video."""
    try:
        if not dl_status_map:
            return ""
        first = next(iter(dl_status_map.values()))
        return first.get('title', "")
    except Exception:
        return ""