import glob,time,os,re,requests,datetime,json,cv2,logging
from flask import current_app
from database import check_db_video,save_video,check_db_channel,save_channel,check_db_video_length,update_length,insert_not_found,get_oldest_video_check,update_video_deleted,get_video_index
from transcoder import get_codec_info, get_container_from_ext

# yt-dlp working files: *.part, *.part-FragN, *.ytdl, and pre-merge *.fNNN.* streams
_PARTIAL_RE = re.compile(r'\.part(-Frag\d+)?$|\.ytdl$|\.f\d+\.')

def is_partial_download(fname):
    return bool(_PARTIAL_RE.search(fname))

def looks_like_youtube(fpath):
    """Only files shaped like <UC-channel-dir>/<11-char-id>.<ext> may be sent
    to the YouTube API; anything else (Patreon/Reddit strays, files caught
    mid-pipeline) would come back not-found and poison IgnoreVid."""
    vid = os.path.basename(fpath).split('.')[0]
    parent = os.path.basename(os.path.dirname(fpath))
    return parent.startswith('UC') and len(vid) == 11

def backend_thread(logger,app):
    logger.info("*Starting Backend")
    while 1:
        with app.app_context():
            scan_vault(logger)
        time.sleep(5000)

def scan_vault(logger):
    """Walk the vault and reconcile it with the DB. The dedupe index is
    fetched once up front (two queries) instead of two queries per file."""
    started = time.time()
    index = get_video_index(logger)
    if index is None:
        logger.error("Vault scan skipped: could not load video index")
        return
    lengths, ignored = index
    files = 0
    for filename in glob.iglob(os.environ['VAULTTUBE_VAULTDIR']+'/**/*', recursive=True):
        fpath = os.path.abspath(filename)
        if(os.path.isfile(fpath)):
            if is_partial_download(os.path.basename(fpath)):
                continue
            files += 1
            id = os.path.basename(fpath).split('.')[0]
            if id in ignored:
                continue
            if id in lengths:
                if lengths[id] == "0":
                    _update_video_length(id, fpath, logger)
                    lengths[id] = "updated"
                # Backfill codec/container metadata lazily during scan
                _maybe_update_codec_info(id, fpath, logger)
            elif looks_like_youtube(fpath):
                logger.info("Processing New Video: %s"%fpath)
                process_new_video(id,fpath,logger)
                lengths[id] = "added"
            else:
                # Likely a non-YouTube download caught before its DB row was
                # written; the provider/upload paths own importing these
                logger.debug("Skipping non-YouTube file with no DB row: %s"%fpath)
        else:
            process_channel(filename,logger)
    logger.info("Vault scan complete: %d files in %.1fs" % (files, time.time() - started))


def _maybe_update_codec_info(id, fpath, logger):
    from database import get_connection
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("SELECT vcodec, acodec, container FROM videos WHERE id = %s", (id,))
        row = cur.fetchone()
        if row and (row[0] is None or row[1] is None or row[2] is None):
            info = get_codec_info(fpath, logger)
            if info['vcodec'] or info['acodec']:
                cur.execute(
                    "UPDATE videos SET vcodec=%s, acodec=%s, container=%s WHERE id=%s",
                    (info['vcodec'], info['acodec'], info.get('container') or get_container_from_ext(fpath), id)
                )
                con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error updating codec info for %s: %s" % (id, e))

def _update_video_length(id, fpath, logger):
    try:
        logger.info("Updating Length for id: %s"%fpath)
        data = cv2.VideoCapture(fpath)
        frames = data.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = data.get(cv2.CAP_PROP_FPS)
        seconds = round(frames / fps)
        data.release()
        update_length(id,datetime.timedelta(seconds=seconds),logger)
    except Exception as e:
        logger.error("Error updating video length for %s: %s"%(id,e))

def get_video(fpath,logger):
    """Reconcile a single just-downloaded file with the DB (provider path)."""
    try:
        fname = os.path.basename(fpath)
        id = fname.split('.')[0]
        if(check_db_video(id,logger)):
            #In database
            if(not check_db_video_length(id,logger)):
                _update_video_length(id, fpath, logger)
            _maybe_update_codec_info(id, fpath, logger)
        else:
            #Missing from database
            logger.info("Processing New Video: %s"%fpath)
            process_new_video(id,fpath,logger)
    except Exception as e:
        logger.error("Error in get_video Failed: %s"%e)

def _extract_codec_info(fpath, logger):
    """Probe codec/container for a newly-discovered file."""
    try:
        info = get_codec_info(fpath, logger)
        if not info['container']:
            info['container'] = get_container_from_ext(fpath)
        return info
    except Exception as e:
        logger.error("Error extracting codec info for %s: %s" % (fpath, e))
        return {'vcodec': None, 'acodec': None, 'container': get_container_from_ext(fpath)}

def process_new_video(id,fpath,logger):
    ret = {}
    try:
        r = requests.get('https://www.googleapis.com/youtube/v3/videos?part=snippet&id='+id+'&key='+os.environ['VAULTTUBE_YTKEY'], timeout=30)
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
            ret['description'] = retj["items"][0]["snippet"].get("description", "")
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
            # Codec/container metadata
            codec_info = _extract_codec_info(fpath, logger)
            ret.update(codec_info)
            if("high" in retj["items"][0]["snippet"]["thumbnails"]):
                ret['ImageURL'] = retj["items"][0]["snippet"]["thumbnails"]["high"]["url"]
            elif("standard" in retj["items"][0]["snippet"]["thumbnails"]):
                ret['ImageURL'] = retj["items"][0]["snippet"]["thumbnails"]["standard"]["url"]
            else:
                logger.error("Unable to find Thumbnail")
            if('ImageURL' in ret):
                data = requests.get(ret['ImageURL'], timeout=30)
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
    if id.isdigit():
        # Numeric directory names are Patreon campaign IDs
        from providers.patreon import ensure_channel
        ensure_channel(id, logger)
        return
    if not id.startswith('UC'):
        logger.debug("Skipping non-YouTube channel directory: %s" % id)
        return
    try:
        if(check_db_channel(id,logger)):
            pass
        else:
            logger.info("Processing Channel: "+id)
            r = requests.get('https://www.googleapis.com/youtube/v3/channels?part=snippet&id='+id+'&key='+os.environ['VAULTTUBE_YTKEY'], timeout=30).json()
            if(r['pageInfo']['totalResults'] > 0):
                save_channel(r['items'][0]['id'],r['items'][0]['snippet']['title'],r,logger)
            else:
                logger.info("Unable to find Channel: %s"%id)
    except Exception as e:
        logger.error("Error in Channel %s: %s"%(id,e))


def deleted_check_thread(logger,app):
    logger.info("*Starting Deleted Check")
    while 1:
        with app.app_context():
            run_deleted_check(logger)
        time.sleep(86400)

def run_deleted_check(logger, rows=None, batch_size=50):
    """Mark YouTube videos that were removed at the source (feeds the
    isDeleted flag behind browse's "Gone from source" view). Batched 50 IDs
    per videos.list call: a 1,000-video pass costs 20 quota units, not 1,000.
    Only status *changes* are logged, plus one summary line per pass."""
    if rows is None:
        rows = get_oldest_video_check(logger)
    if not rows:
        return
    checked = newly_gone = restored = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i+batch_size]
        try:
            r = requests.get('https://www.googleapis.com/youtube/v3/videos?part=id&maxResults=50&id='
                             + ','.join(vid for vid, _ in chunk)
                             + '&key=' + os.environ['VAULTTUBE_YTKEY'], timeout=30)
            retj = r.json()
            r.close()
        except Exception as e:
            logger.error("Deleted check batch failed: %s" % e)
            return
        if 'error' in retj:
            logger.error("Deleted check API error: %s" % retj['error'].get('message', retj['error']))
            return
        alive = {item['id'] for item in retj.get('items', [])}
        for vid, was_deleted in chunk:
            is_deleted = 0 if vid in alive else 1
            if is_deleted != (was_deleted or 0):
                if is_deleted:
                    newly_gone += 1
                    logger.info("Video gone from YouTube: %s" % vid)
                else:
                    restored += 1
                    logger.info("Video back on YouTube: %s" % vid)
            update_video_deleted(vid, is_deleted, logger)
            checked += 1
    logger.info("Deleted check: %d checked, %d newly gone, %d restored" % (checked, newly_gone, restored))

def save_uploaded_video_metadata(video_id, file_path, title, channel_id, published_at,db_path,source,webpage_url=None):
    """Save a non-YouTube video (Reddit download or manual upload) to the DB.
    The json column gets a plain metadata dict; webpage_url, when known,
    powers the player's copy-source-link button."""
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

        # Insert video record
        ret = {}
        ret["Youtuber"] = ""
        ret["Json"] = {
            'id': video_id,
            'title': title,
            'channelId': channel_id,
            'publishedAt': published_at.isoformat(),
            'source': source,
            'webpage_url': webpage_url,
        }
        ret["Filepath"] = db_path
        ret['PublishedAt'] = published_at.isoformat()
        ret['channelId'] = channel_id
        ret['length'] = length_td
        ret['title'] = title
        ret['description'] = ""
        codec_info = _extract_codec_info(file_path, current_app.logger)
        ret.update(codec_info)

        save_video(video_id,ret,thumbnail_img,current_app.logger,source)
    except Exception as e:
        # Raise exception so api can log & handle
        current_app.logger.error("Error in Save_Uploaded_Video_Metadata: %s"%e)
        raise e