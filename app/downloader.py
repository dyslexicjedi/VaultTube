import time
from youtube import single_download
from patreon import patreon_download
from QueueObject import QueueObject

def start_dl_queue(logger,app):
    logger.info("Starting Download Queue Process")
    while 1:
        with app.app_context():
            q = app.config['queue']
            if(q.qsize() > 0):
                logger.info("*Found Queue Items")
                while q.qsize() > 0:
                    q = QueueObject(q.get())
                    logger.info("Downloading %s"%q.url)
                    if "youtube.com" in q.url:
                        logger.info("*Found Youtube URL")
                        single_download(q.url,logger)
                    elif "patreon.com" in q.url:
                        logger.info("*Found Patreon URL")
                        patreon_download(q,logger)
            else:
                logger.debug("No Items in Queue")
            time.sleep(60)
