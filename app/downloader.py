import time
import providers
from QueueObject import QueueObject

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
                    if provider:
                        logger.info("*Dispatching to provider: %s" % provider.__name__)
                        if provider.download(qo, logger):
                            logger.info("Download Successful")
                        else:
                            logger.error("Error occurred during download")
                    else:
                        logger.error("No provider found for URL: %s" % qo.url)
            else:
                logger.debug("No Items in Queue")
            time.sleep(60)
