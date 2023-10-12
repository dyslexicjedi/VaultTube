import glob,time,os,requests,datetime
from flask import current_app
from database import check_db_video,save_video

def backend_thread(config,logger):
    while 1:
        logger.info("Scanning Vault")
        for filename in glob.iglob(config['Vault']['DIR']+'/**/*', recursive=True):
            get_video(os.path.abspath(filename),config,logger)
            time.sleep(5)
        time.sleep(5000)

def get_video(fpath,config,logger):
    fname = os.path.basename(fpath)
    id = fname.split('.')[0]
    if(check_db_video(id,config,logger)):
        pass
    else:
        logger.info("Processing New Video: %s"%id)
        process_new_video(id,fpath,config,logger)

def process_new_video(id,fpath,config,logger):
    ret = {}
    try:
        r = requests.get('https://www.googleapis.com/youtube/v3/videos?part=snippet&id='+id+'&key='+config['YouTube']['KEY']).json()
        if(r['pageInfo']['totalResults'] == 1):
            ret["PublishedAt"] = datetime.datetime.strptime(r["items"][0]["snippet"]["publishedAt"], '%Y-%m-%dT%H:%M:%SZ')
            ret['Youtuber'] = r["items"][0]["snippet"]["channelTitle"]
            ret['Json'] = r
            ret['Filepath'] = fpath
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
            save_video(id,ret,img,config,logger)

    except Exception as e:
        logger.error("Error in Process_new_video: %s"%e)