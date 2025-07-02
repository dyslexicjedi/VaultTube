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
                    qo = q.get()
                    logger.info("Downloading %s"%qo.url)
                    if "youtube.com" in qo.url:
                        logger.info("*Found Youtube URL")
                        single_download(qo.url,logger)
                    elif "patreon.com" in qo.url:
                        logger.info("*Found Patreon URL")
                        patreon_download(qo,logger)
            else:
                logger.debug("No Items in Queue")
            time.sleep(60)
