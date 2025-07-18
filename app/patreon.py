import os
import yt_dlp
import datetime
import json
from io import StringIO
import subprocess
import mariadb
from flask import current_app
# from downloader import updated_dl_progress

from QueueObject import QueueObject

dl_status_map = {}  # videoID -> dict(progress, title, channel, type, etc.)

def dl_progress_hook(d):
    try:
        video_id = d.get('info_dict', {}).get('id', None)
        if not video_id:
            # fallback to global, if needed
            video_id = globals().get('videoID', '')
        status_obj = dl_status_map.setdefault(video_id, {})
        if d["status"] == "downloading":
            status_obj['progress'] = d['_percent_str']
            status_obj['title'] = d.get('info_dict', {}).get('title', "")
            status_obj['type'] = 'patreon'
        elif d["status"] == "finished":
            status_obj['progress'] = "100%"
        # Optionally add more info (like ETA, speed) here
    except Exception as e:
        current_app.logger.error("dl_progress_hook Failed: %s" % e)

def patreon_download(q,logger):
    logger.debug("Starting Patreon Download: %s"%q.url)
    #Set Cookie
    f = open(os.environ['VAULTTUBE_PATREONCOOKIE'])
    contents = f.read()
    f.close()
    cookies = StringIO(contents)
    ydl_opts = {
        'cookiefile': cookies,
        'outtmpl': os.environ['VAULTTUBE_VAULTDIR']+"/"+q.channel_id+"/%(id)s.mp4",
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        "progress_hooks": [dl_progress_hook],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        data = ydl.extract_info(q.url,download=False)
        videoid = data['id']
        title = data['title']
        PublishedAt = datetime.datetime.strptime(data['upload_date'], '%Y%m%d')
        dl_status_map[videoid] = {'progress': '0%', 'title': title, 'type': 'patreon'}
        ydl.download(q.url)
    patreon_screenshot(videoid,q.channel_id,logger)
    patreon_db_info(videoid,q.channel_id,PublishedAt,title,logger)
    #dl_progress = 0
    cookies.close()
    return "True"

def patreon_screenshot(videoid,channelid,logger):
    #print(t)
    input_video = os.environ['VAULTTUBE_VAULTDIR']+"/"+channelid+"/"+str(videoid)+".mp4"
    output_img = videoid+".jpg"
    subprocess.call(['ffmpeg', '-i', input_video, '-ss', '00:00:01.000', '-vframes', '1', output_img])
    img = open(videoid+".jpg",'rb').read()
    con = mariadb.connect(host=os.environ['VAULTTUBE_DBHOST'],user=os.environ['VAULTTUBE_DBUSER'],password=os.environ['VAULTTUBE_DBPASS'],database=os.environ['VAULTTUBE_DBNAME'],autocommit=True,port=int(os.environ['VAULTTUBE_DBPORT']))
    cur = con.cursor()
    sql = "Insert Ignore into images(id,image) values(%s,%s)"
    cur.execute(sql,(videoid,img))
    con.commit()
    con.close()
    logger.debug("Screenshot saved")
    os.remove(videoid+".jpg")
    return "True"

def patreon_db_info(videoid,channelid,PublishedAt,title,logger):
    t = json.loads(open('template','r').read())
    t['items'][0]['snippet']['title'] = title
    t['items'][0]['snippet']['channelId'] = channelid
    t['items'][0]['snippet']['channelTitle'] = ""
    t['items'][0]['snippet']['publishedAt'] = PublishedAt.strftime('%Y-%m-%d %H:%M:%S.%f')
    t['items'][0]['id'] = videoid
    source = "patreon"

    con = mariadb.connect(host=os.environ['VAULTTUBE_DBHOST'],user=os.environ['VAULTTUBE_DBUSER'],password=os.environ['VAULTTUBE_DBPASS'],database=os.environ['VAULTTUBE_DBNAME'],autocommit=True,port=int(os.environ['VAULTTUBE_DBPORT']))
    cur = con.cursor()
    sql = "Select * from videos where id = %s"
    cur.execute(sql,(videoid,))
    if cur.fetchone():
        logger.info("Video already exists in database, updating")
        sql = "Update videos set youtuber=%s,channelId=%s,filepath=%s,PublishedAt=%s,json=%s,source=%s  where id=%s"
    else:
        sql = "Insert into videos(youtuber,channelId,filepath,PublishedAt,json,source,id) values(%s,%s,%s,%s,%s,%s,%s)"
    cur.execute(sql,("",channelid,"/"+channelid+"/"+str(videoid)+".mp4",PublishedAt.strftime('%Y-%m-%d %H:%M:%S.%f'),json.dumps(t),source,videoid))
    con.commit()
    con.close()
    logger.debug("Metadata saved")
    return "True"


