import threading
import providers
from QueueObject import QueueObject
from flask import current_app
from database import insert_download_error, update_queue_status

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 60

def get_error_type(error_msg):
    msg_lower = error_msg.lower()
    if 'not found' in msg_lower or '404' in msg_lower or 'content was not found' in msg_lower:
        return 'Content Not Found'
    elif 'timeout' in msg_lower or 'connection' in msg_lower or 'network' in msg_lower:
        return 'Network Error'
    elif 'cookie' in msg_lower or 'auth' in msg_lower or '403' in msg_lower:
        return 'Authentication Error'
    else:
        return 'Provider Error'

def get_provider_by_source(source):
    for provider in providers._providers:
        if provider.__name__ == 'providers.' + source:
            return provider
    return None

def handle_failure(qo, q, error_type, error_msg, logger):
    """Retry transient failures up to MAX_ATTEMPTS; record permanent ones."""
    if error_type == 'Network Error' and qo.attempts + 1 < MAX_ATTEMPTS:
        qo.attempts += 1
        update_queue_status(qo.row_id, 'pending', logger, error=error_msg, attempts=qo.attempts)
        logger.info("Retrying %s in %ds (attempt %d/%d)" % (qo.url, RETRY_DELAY_SECONDS, qo.attempts + 1, MAX_ATTEMPTS))
        threading.Timer(RETRY_DELAY_SECONDS, q.put, args=(qo,)).start()
    else:
        update_queue_status(qo.row_id, 'failed', logger, error=error_msg, attempts=qo.attempts + 1)
        insert_download_error(qo.url, error_type, error_msg, logger)

def start_dl_queue(logger, app):
    logger.info("Starting Download Queue Process")
    q = app.config['queue']
    while 1:
        qo = q.get()  # blocks until an item is enqueued
        with app.app_context():
            logger.info("Downloading %s" % qo.url)
            update_queue_status(qo.row_id, 'downloading', logger)
            provider = providers.get_provider(qo.url)
            if not provider and qo.source:
                provider = get_provider_by_source(qo.source)
            if provider:
                logger.info("*Dispatching to provider: %s" % provider.__name__)
                try:
                    if provider.download(qo, logger):
                        logger.info("Download Successful")
                        update_queue_status(qo.row_id, 'done', logger)
                    else:
                        logger.error("Error occurred during download")
                        handle_failure(qo, q, 'Provider Error', 'Download returned False', logger)
                except Exception as e:
                    logger.error("Exception during download: %s" % e)
                    handle_failure(qo, q, get_error_type(str(e)), str(e), logger)
            else:
                logger.error("No provider found for URL: %s" % qo.url)
                update_queue_status(qo.row_id, 'failed', logger, error='No provider found for URL')
                insert_download_error(qo.url, 'Provider Error', 'No provider found for URL', logger)
