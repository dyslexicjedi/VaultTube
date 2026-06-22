
import threading
import queue as _queue
import time

dl_status_map = {}
dl_status_lock = threading.RLock()

# --- Alerts ---
# Provider/scan-time problems (e.g. "YouTube cookies expired") that the user
# needs to know about but that don't surface as a download_errors row. Each
# alert is a dict with at least: id, title, message, created_at. Alerts are
# sticky: they remain in the map (and re-broadcast to new clients via
# /api/status/alerts) until explicitly cleared with clear_alert().

_alerts = {}
_alerts_lock = threading.RLock()

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


def raise_alert(alert_id, title, message, kind='error'):
    """Register a sticky alert and broadcast it to all SSE clients.

    Alerts persist until clear_alert() is called so a page opened after the
    problem first occurred still sees it (via /api/status/alerts). Re-raising
    the same alert_id updates the message and refreshes created_at so clients
    see the most recent occurrence.
    """
    now = time.time()
    with _alerts_lock:
        _alerts[alert_id] = {
            'id': alert_id,
            'title': title,
            'message': message,
            'kind': kind,
            'created_at': now,
        }
        alert = dict(_alerts[alert_id])
    _broadcast_sse({'type': 'alert', **alert})


def clear_alert(alert_id):
    """Remove a sticky alert and broadcast the clearance."""
    with _alerts_lock:
        _alerts.pop(alert_id, None)
    _broadcast_sse({'type': 'alert_clear', 'id': alert_id})


def get_alerts():
    """Return a list of current alerts (snapshot copy)."""
    with _alerts_lock:
        return [dict(a) for a in _alerts.values()]


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
