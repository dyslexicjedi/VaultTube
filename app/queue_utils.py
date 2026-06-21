import logging
from database import insert_queue_item, queue_has_url

logger = logging.getLogger('queue')


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
