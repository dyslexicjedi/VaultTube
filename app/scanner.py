import time
from database import get_active_subscriptions
from youtube import get_channel_video_list

def start_scanner(config,logger):
    while True:
        data = get_active_subscriptions(config,logger)
        for id in data:
            logger.info("Scanning Channel: %s"%id)
            get_channel_video_list(id,config,logger)
        time.sleep(3600)


