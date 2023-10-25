import time
from youtube import single_download

def start_dl_queue(logger,app):
    logger.info("Starting Download Queue Process")
    while 1:
        with app.app_context():
            q = app.config['queue']
            if(q.qsize() > 0):
                logger.info("*Found Queue Items")
                while q.qsize() > 0:
                    url = q.get()
                    logger.info("Downloading %s"%url)
                    single_download(url,logger)
            else:
                logger.debug("No Items in Queue")
            time.sleep(60)
