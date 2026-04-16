import time
import providers
from QueueObject import QueueObject
from flask import current_app
from database import insert_download_error

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

def start_dl_queue(logger, app):
    logger.info("Starting Download Queue Process")
    while 1:
        with app.app_context():
            q = app.config['queue']
            if q.qsize() > 0:
                logger.info("*Found Queue Items")
                while q.qsize() > 0:
                    qo = q.get()
                    logger.info("Downloading %s" % qo.url)
                    provider = providers.get_provider(qo.url)
                    if not provider and qo.source:
                        provider = get_provider_by_source(qo.source)
                    if provider:
                        logger.info("*Dispatching to provider: %s" % provider.__name__)
                        try:
                            if provider.download(qo, logger):
                                logger.info("Download Successful")
                            else:
                                logger.error("Error occurred during download")
                                insert_download_error(qo.url, 'Provider Error', 'Download returned False', logger)
                        except Exception as e:
                            logger.error("Exception during download: %s" % e)
                            insert_download_error(qo.url, get_error_type(str(e)), str(e), logger)
                    else:
                        logger.error("No provider found for URL: %s" % qo.url)
                        insert_download_error(qo.url, 'Provider Error', 'No provider found for URL', logger)
            else:
                logger.debug("No Items in Queue")
            time.sleep(60)
