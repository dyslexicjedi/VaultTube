import itertools
import logging
import queue
from database import insert_queue_item, queue_has_url

logger = logging.getLogger('queue')


class PriorityDownloadQueue(queue.PriorityQueue):
    """QueueObjects ordered by numeric priority while preserving FIFO ties."""

    def __init__(self):
        super().__init__()
        self._sequence = itertools.count()

    def put(self, qo, block=True, timeout=None):
        entry = (int(getattr(qo, 'priority', 0)), next(self._sequence), qo)
        return super().put(entry, block=block, timeout=timeout)

    def get(self, block=True, timeout=None):
        return super().get(block=block, timeout=timeout)[2]

    def snapshot(self):
        with self.mutex:
            return [entry[2] for entry in sorted(self.queue)]


def queue_snapshot(q):
    if hasattr(q, 'snapshot'):
        return q.snapshot()
    return list(q.queue)


def enqueue(qo, q):
    """Persist a QueueObject to the queue table and put it on the in-memory
    queue. The DB row survives restarts; main.py re-enqueues unfinished rows
    on startup. Enqueueing still works if the DB write fails (row_id None).

    Skips URLs that are already pending/downloading so periodic scans don't
    pile up duplicates while a backlog drains. Returns True if enqueued."""
    if queue_has_url(qo.url):
        logger.info("Already queued, skipping: %s" % qo.url)
        return False
    qo.row_id = insert_queue_item(qo)
    q.put(qo)
    return True
