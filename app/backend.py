import glob,time,os,requests,datetime,json,cv2,logging
from flask import current_app
from database import check_db_video,save_video,check_db_channel,save_channel,check_db_video_length,update_length

def backend_thread(logger):
    logger.info("*Starting Backend")
    while 1:
        logger.info("Scanning Vault")
        for filename in glob.iglob(os.environ['VAULTTUBE_VAULTDIR']+'/**/*', recursive=True):
            if(os.path.isfile(os.path.abspath(filename))):
                logger.debug("Path is file: %s"%filename)
                get_video(os.path.abspath(filename),logger)
                #time.sleep(5)
            else:
                process_channel(filename,logger)
                #time.sleep(5)
        time.sleep(5000)

def get_video(fpath,logger):
    try:
        fname = os.path.basename(fpath)
        id = fname.split('.')[0]
        if(check_db_video(id,logger)):
            #In database
            if(not check_db_video_length(id,logger)):
                logger.info("Updating Length for id: %s"%fpath)
                data = cv2.VideoCapture(fpath)
                frames = data.get(cv2.CAP_PROP_FRAME_COUNT) 
                fps = data.get(cv2.CAP_PROP_FPS) 
                # calculate duration of the video 
                seconds = round(frames / fps) 
                update_length(id,datetime.timedelta(seconds=seconds),logger)
        else:
            #Missing from database
            logger.info("Processing New Video: %s"%fpath)
            process_new_video(id,fpath,logger)
    except Exception as e:
        logger.error("Error in get_video Failed: %s"%e)

def process_new_video(id,fpath,logger):
    ret = {}
    try:
        r = requests.get('https://www.googleapis.com/youtube/v3/videos?part=snippet&id='+id+'&key='+os.environ['VAULTTUBE_YTKEY']).json()
        if(r['pageInfo']['totalResults'] == 1):
            ret["PublishedAt"] = datetime.datetime.strptime(r["items"][0]["snippet"]["publishedAt"], '%Y-%m-%dT%H:%M:%SZ')
            ret['Youtuber'] = r["items"][0]["snippet"]["channelTitle"]
            ret['channelId'] = r["items"][0]["snippet"]["channelId"]
            ret['Json'] = r
            ret['Filepath'] = fpath
            #Get Length
            data = cv2.VideoCapture(fpath)
            frames = data.get(cv2.CAP_PROP_FRAME_COUNT) 
            fps = data.get(cv2.CAP_PROP_FPS) 
            # calculate duration of the video 
            seconds = round(frames / fps) 
            ret['length'] = datetime.timedelta(seconds=seconds) 
            if("high" in r["items"][0]["snippet"]["thumbnails"]):
                ret['ImageURL'] = r["items"][0]["snippet"]["thumbnails"]["high"]["url"]
            elif("standard" in r["items"][0]["snippet"]["thumbnails"]):
                ret['ImageURL'] = r["items"][0]["snippet"]["thumbnails"]["standard"]["url"]
            else:
                logger.error("Unable to find Thumbnail")
            if('ImageURL' in ret):
                data = requests.get(ret['ImageURL'])
                img = data.content
            else:
                img = None
            save_video(id,ret,img,logger)
        else:
            logger.info("Unable to import video: %s"%str(r))
    except Exception as e:
        logger.error("Error in Process_new_video: %s"%e)

def process_channel(fname,logger):
    id = fname.split('/')[-1]
    try:
        if(check_db_channel(id,logger)):
            pass
        else:
            logger.info("Processing Channel: "+id)
            r = requests.get('https://www.googleapis.com/youtube/v3/channels?part=snippet&id='+id+'&key='+os.environ['VAULTTUBE_YTKEY']).json()
            if(r['pageInfo']['totalResults'] > 0):
                save_channel(r['items'][0]['id'],r['items'][0]['snippet']['title'],r,logger)
            else:
                logger.info("Unable to find Channel: %s"%id)
    except Exception as e:
        logger.error("Error in Channel: %s"%e)
        logger.error(json.dumps(r, indent=4))