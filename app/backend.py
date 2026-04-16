import glob,time,os,requests,datetime,json,cv2,logging
from flask import current_app
from database import check_db_video,save_video,check_db_channel,save_channel,check_db_video_length,update_length,insert_not_found,get_oldest_video_check,update_video_deleted

def backend_thread(logger,app):
    logger.info("*Starting Backend")
    while 1:
        with app.app_context():
            logger.info("Scanning Vault")
            for filename in glob.iglob(os.environ['VAULTTUBE_VAULTDIR']+'/**/*', recursive=True):
                if(os.path.isfile(os.path.abspath(filename))):
                    if(not ".mp4.part" in filename):
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
                data.release()
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
        r = requests.get('https://www.googleapis.com/youtube/v3/videos?part=snippet&id='+id+'&key='+os.environ['VAULTTUBE_YTKEY'])
        retj = r.json()
        r.close()
        if "error" in retj:
            if "code" in retj['error']:
                if retj['error']['code'] == 403:
                    logger.error("Quota limit reached, sleeping for 1 hour.")
                    time.sleep(3600)
                    return
        if(retj['pageInfo']['totalResults'] > 0):
            ret["PublishedAt"] = datetime.datetime.strptime(retj["items"][0]["snippet"]["publishedAt"], '%Y-%m-%dT%H:%M:%SZ')
            ret['Youtuber'] = retj["items"][0]["snippet"]["channelTitle"]
            ret['channelId'] = retj["items"][0]["snippet"]["channelId"]
            ret['title'] = retj["items"][0]["snippet"]["title"]
            ret['Json'] = retj
            ret['Filepath'] = fpath
            #Get Length
            data = cv2.VideoCapture(fpath)
            frames = data.get(cv2.CAP_PROP_FRAME_COUNT)
            fps = data.get(cv2.CAP_PROP_FPS)
            # calculate duration of the video
            seconds = round(frames / fps)
            data.release()
            ret['length'] = datetime.timedelta(seconds=seconds) 
            if("high" in retj["items"][0]["snippet"]["thumbnails"]):
                ret['ImageURL'] = retj["items"][0]["snippet"]["thumbnails"]["high"]["url"]
            elif("standard" in retj["items"][0]["snippet"]["thumbnails"]):
                ret['ImageURL'] = retj["items"][0]["snippet"]["thumbnails"]["standard"]["url"]
            else:
                logger.error("Unable to find Thumbnail")
            if('ImageURL' in ret):
                data = requests.get(ret['ImageURL'])
                img = data.content
            else:
                img = None
            save_video(id,ret,img,logger)
        else:
            logger.info("Unable to import video: %s"%str(retj))
            insert_not_found(id,logger)
            
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


def deleted_check_thread(logger,app):
    logger.info("*Starting Deleted Check")
    while 1:
        with app.app_context():
            logger.info("Getting Video List for deletion check")
            videos = get_oldest_video_check(logger)
            for video in videos:
                r = requests.get('https://www.googleapis.com/youtube/v3/videos?part=snippet&id='+video[0]+'&key='+os.environ['VAULTTUBE_YTKEY'])
                retj = r.json()
                r.close()
                if "error" in retj:
                    logger.info("Found error: %s",retj['error'])
                    #Error handling
                    pass
                else:
                    if(retj['pageInfo']['totalResults'] > 0):
                        #Video is still there
                        update_video_deleted(video[0],0,logger)
                        logger.info("Updating video as not deleted. Video ID: %s" %video[0])
                        pass
                    else:
                        #Video has been deleted
                        update_video_deleted(video[0],1,logger)
                        logger.info("Updating video as deleted. Video ID: %s" %video[0])
                        pass
        time.sleep(86400)

def save_uploaded_video_metadata(video_id, file_path, title, channel_id, published_at,db_path,source):
    """
    Save metadata about uploaded video to database. 
    """
    try:
        # For length, try to read video length as in get_video
        try:
            import cv2
            data = cv2.VideoCapture(file_path)
            frames = data.get(cv2.CAP_PROP_FRAME_COUNT)
            fps = data.get(cv2.CAP_PROP_FPS)
            length_seconds = round(frames / fps) if fps > 0 else 0
            length_td = datetime.timedelta(seconds=length_seconds)
        except Exception:
            length_td = datetime.timedelta(seconds=0)


        # Extract frame at 1 second (or nearest frame)
        thumbnail_img = None
        try:
            if fps > 0 and frames > fps:
                data.set(cv2.CAP_PROP_POS_FRAMES, int(fps))  # frame at 1s
                ret, frame = data.read()
                if ret:
                    # Encode frame as JPEG bytes
                    ret_jpg, buf = cv2.imencode('.jpg', frame)
                    if ret_jpg:
                        thumbnail_img = buf.tobytes()
                data.release()
        except Exception:
            current_app.logger.error("Unable to Extract Frame")

        t = json.loads(open('template','r').read())
        t['items'][0]['snippet']['title'] = title
        t['items'][0]['snippet']['channelId'] = channel_id
        t['items'][0]['snippet']['channelTitle'] = ""
        t['items'][0]['snippet']['publishedAt'] = published_at.isoformat()
        t['items'][0]['id'] = video_id
        
        # Insert video record
        ret = {}
        ret["Youtuber"] = ""
        ret["Json"] = t
        ret["Filepath"] = db_path
        ret['PublishedAt'] = published_at.isoformat()
        ret['channelId'] = channel_id
        ret['length'] = length_td
        ret['title'] = title

        save_video(video_id,ret,thumbnail_img,current_app.logger,source)
    except Exception as e:
        # Raise exception so api can log & handle
        current_app.logger.error("Error in Save_Uploaded_Video_Metadata: %s"%e)
        raise e