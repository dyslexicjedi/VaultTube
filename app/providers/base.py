
import threading
import queue as _queue

dl_status_map = {}
dl_status_lock = threading.RLock()

# --- SSE pub/sub ---

_sse_subscribers = []
_sse_lock = threading.Lock()


def subscribe_sse():
    """Create a per-client queue and register it for broadcast.  Returns the queue."""
    q = _queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_subscribers.append(q)
    return q


def unsubscribe_sse(q):
    """Remove a client queue from the broadcast list."""
    with _sse_lock:
        try:
            _sse_subscribers.remove(q)
        except ValueError:
            pass


def _broadcast_sse(event):
    """Push an event dict to every subscribed client queue (non-blocking)."""
    with _sse_lock:
        subscribers = list(_sse_subscribers)
    for q in subscribers:
        try:
            q.put_nowait(event)
        except _queue.Full:
            pass


# --- Download status helpers ---

def set_status(video_id, status_dict):
    with dl_status_lock:
        dl_status_map[video_id] = status_dict
    _broadcast_sse({'type': 'progress', 'id': video_id, **status_dict})


def update_status(video_id, status_dict):
    with dl_status_lock:
        dl_status_map.setdefault(video_id, {}).update(status_dict)
        full_status = dict(dl_status_map[video_id])
    _broadcast_sse({'type': 'progress', 'id': video_id, **full_status})


def del_status(video_id):
    with dl_status_lock:
        if video_id in dl_status_map:
            del dl_status_map[video_id]
    _broadcast_sse({'type': 'complete', 'id': video_id})


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
