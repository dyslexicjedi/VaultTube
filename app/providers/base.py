
import threading

dl_status_map = {}
dl_status_lock = threading.RLock()


def set_status(video_id, status_dict):
    with dl_status_lock:
        dl_status_map[video_id] = status_dict


def update_status(video_id, status_dict):
    with dl_status_lock:
        dl_status_map.setdefault(video_id, {}).update(status_dict)


def del_status(video_id):
    with dl_status_lock:
        if video_id in dl_status_map:
            del dl_status_map[video_id]


def get_status_copy():
    with dl_status_lock:
        return dict(dl_status_map)


def get_dl_status():
    with dl_status_lock:
        try:
            if not dl_status_map:
                return 0
            first = next(iter(dl_status_map.values()))
            return first.get('progress', 0)
        except Exception:
            return 0


def get_cur_videoID():
    with dl_status_lock:
        try:
            if not dl_status_map:
                return ""
            return next(iter(dl_status_map))
        except Exception:
            return ""


def get_cur_videoTitle():
    with dl_status_lock:
        try:
            if not dl_status_map:
                return ""
            first = next(iter(dl_status_map.values()))
            return first.get('title', "")
        except Exception:
            return ""