from flask import Blueprint,current_app,send_file,Response,abort,stream_with_context
import mariadb,json,io,csv,math,os,queue as _queue,threading,tempfile
import subprocess
from providers.base import get_dl_status, get_cur_videoID, get_cur_videoTitle, get_status_copy, subscribe_sse, unsubscribe_sse
from backend import process_channel,save_uploaded_video_metadata
from database import checkdb,get_connection,insert_playlist,find_next_previous,insert_download_error,get_download_errors,clear_download_errors,delete_download_error, update_video_codec_info, export_video_rows, export_subscribed_channels, export_subscribed_playlists, export_pl2vid, export_tombstones, export_row_counts
from flask import request,jsonify,abort
import shutil
import datetime
import requests

from QueueObject import QueueObject
from queue_utils import enqueue
from chapters import parse_chapters
from transcoder import generate_hls, touch_cache_access, note_segment_request, is_apple_direct, get_codec_info, get_container_from_ext, get_transcode_cache_dir, get_duration, build_vod_playlist, segment_count_for_duration, wait_for_segment, transcode_key, source_path_for, cache_stats
from werkzeug.exceptions import HTTPException

api_bp = Blueprint('api',__name__)

def parse_response(cur,con):
    if(not cur.rowcount):
        cur.close()
        con.close()
        return "[]"
    # serialize results into JSON
    row_headers=[x[0] for x in cur.description]
    rv = cur.fetchall()
    json_data=[]
    for result in rv:
        json_data.append(dict(zip(row_headers,result)))
    cur.close()
    con.close()
    # return the results!
    return json.dumps(json_data, indent=4, sort_keys=True, default=str)

def api_success(data=None):
    return jsonify({"success": True, "data": data})

def api_error(error, status_code=400):
    return jsonify({"success": False, "error": error}), status_code

ALLOWED_SORT_COLUMNS = {
    'AddedAt', 'PublishedAt', 'v.AddedAt', 'v.PublishedAt',
    'v.timestamp', 'timestamp', 'v.title', 'title', 'v.watched', 'watched'
}
ALLOWED_DIRECTIONS = {'asc', 'desc'}

def parse_duration_to_seconds(duration_str):
    if not duration_str or duration_str == '0':
        return None
    try:
        parts = str(duration_str).split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1:
            return int(parts[0])
    except (ValueError, TypeError):
        return None

def build_duration_condition(min_dur, max_dur):
    conditions = []
    if min_dur is not None:
        conditions.append(f"TIME_TO_SEC(v.length) >= {int(min_dur)}")
    if max_dur is not None:
        conditions.append(f"TIME_TO_SEC(v.length) <= {int(max_dur)}")
    return conditions

@api_bp.route('/getvids/<string:status>/<string:opt>/<string:direction>/<string:page>')
def getvids(status,opt,direction,page):
    try:
        current_app.logger.debug("Called Latest %s %s %s"%(opt,direction,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        page_num = int(page) if page.isdigit() else 0
        
        if status == "unwatched":
            status_cond = "v.watched = 0"
        else:
            status_cond = ""
        
        safe_opt = opt if opt in ALLOWED_SORT_COLUMNS else 'PublishedAt'
        safe_direction = direction if direction in ALLOWED_DIRECTIONS else 'desc'
        
        where_clauses = []
        if status_cond:
            where_clauses.append(status_cond)
        
        channel_ids = request.args.getlist('channel_ids[]')
        single_channel = request.args.get('channelId', '').strip()
        if single_channel:
            channel_ids.append(single_channel)
        if channel_ids:
            channel_ids_safe = [cid for cid in channel_ids if cid]
            if channel_ids_safe:
                channel_placeholder = ','.join(['%s'] * len(channel_ids_safe))
                where_clauses.append(f"v.channelId IN ({channel_placeholder})")

        from_date = request.args.get('from_date', '').strip()
        to_date = request.args.get('to_date', '').strip()
        if from_date:
            where_clauses.append(f"v.PublishedAt >= %s")
        if to_date:
            where_clauses.append(f"v.PublishedAt <= %s")
        
        if request.args.get('deleted') == '1':
            where_clauses.append("v.isDeleted = 1")

        min_duration = request.args.get('min_duration', '').strip()
        max_duration = request.args.get('max_duration', '').strip()
        min_dur_sec = int(min_duration) if min_duration and min_duration.isdigit() else None
        max_dur_sec = int(max_duration) if max_duration and max_duration.isdigit() else None
        duration_conditions = build_duration_condition(min_dur_sec, max_dur_sec)
        where_clauses.extend(duration_conditions)
        
        where_clause = "where " + " AND ".join(where_clauses) if where_clauses else ""
        
        sql = f"select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid {where_clause} order by {safe_opt} {safe_direction} limit 40 offset %s"
        
        params = []
        if channel_ids:
            params.extend(channel_ids_safe)
        if from_date:
            params.append(from_date)
        if to_date:
            params.append(to_date)
        params.append(page_num)
        
        current_app.logger.debug("SQL: %s, Params: %s", sql, params)
        cur.execute(sql, tuple(params))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Latest Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route('/channel/<string:channelid>/<string:page>')
def api_channel_videos(channelid, page):
    """Paged videos for a single channel, using the same row shape as
    /api/getvids so clients can reuse VTVideo decoding.

    Query parameters (all optional):
      - status:   all | unwatched | watched   (default: all)
      - sort:     any column allowed by getvids (default: PublishedAt)
      - direction: asc | desc                (default: desc)
    """
    try:
        current_app.logger.debug("Called Channel Videos %s %s", channelid, page)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        page_num = int(page) if page.isdigit() else 0

        status = request.args.get('status', 'all').lower()
        if status == "unwatched":
            status_cond = "v.watched = 0"
        elif status == "watched":
            status_cond = "v.watched = 1"
        else:
            status_cond = ""

        safe_opt = request.args.get('sort', 'PublishedAt')
        safe_opt = safe_opt if safe_opt in ALLOWED_SORT_COLUMNS else 'PublishedAt'
        safe_direction = request.args.get('direction', 'desc')
        safe_direction = safe_direction if safe_direction in ALLOWED_DIRECTIONS else 'desc'

        where_clauses = ["v.channelId = %s"]
        if status_cond:
            where_clauses.append(status_cond)

        where_clause = "where " + " AND ".join(where_clauses)
        sql = f"select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid {where_clause} order by {safe_opt} {safe_direction} limit 40 offset %s"

        cur.execute(sql, (channelid, page_num))
        return parse_response(cur, con)
    except Exception as e:
        current_app.logger.error("API Channel Videos Failed: %s" % e)
        return api_error(str(e), 500)

_thumbnail_inflight = set()
_thumbnail_inflight_lock = threading.Lock()

def _fetch_and_store_thumbnail(video_id):
    """On cache-miss: fetch the thumbnail from the appropriate source and persist it.
    Returns image bytes on success, None on failure (caller retries next request).
    An in-flight guard collapses concurrent requests for the same video to one fetch."""
    with _thumbnail_inflight_lock:
        if video_id in _thumbnail_inflight:
            return None
        _thumbnail_inflight.add(video_id)
    try:
        try:
            con = get_connection(current_app.logger)
            cur = con.cursor()
            cur.execute("SELECT source, filepath FROM videos WHERE id = %s", (video_id,))
            row = cur.fetchone()
            cur.close()
            con.close()
        except Exception as e:
            current_app.logger.error("Thumbnail lookup failed for %s: %s" % (video_id, e))
            return None

        if not row:
            return None

        source, filepath = row
        img = None

        if source == 'youtube':
            for quality in ('hqdefault', 'sddefault'):
                url = 'https://img.youtube.com/vi/%s/%s.jpg' % (video_id, quality)
                try:
                    r = requests.get(url, timeout=5)
                    # YouTube serves a tiny grey placeholder for missing/private videos
                    if r.status_code == 200 and len(r.content) > 5000:
                        img = r.content
                        break
                except Exception as e:
                    current_app.logger.debug("Thumbnail CDN miss %s/%s: %s" % (video_id, quality, e))
        elif filepath:
            fpath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], filepath.lstrip('/'))
            if os.path.exists(fpath):
                fd, out = tempfile.mkstemp(suffix='.jpg')
                os.close(fd)
                try:
                    rc = subprocess.call(
                        ['ffmpeg', '-y', '-i', fpath, '-ss', '00:00:01.000', '-vframes', '1', out],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    if rc == 0 and os.path.exists(out):
                        with open(out, 'rb') as f:
                            img = f.read()
                except Exception as e:
                    current_app.logger.error("ffmpeg thumbnail failed for %s: %s" % (video_id, e))
                finally:
                    try:
                        os.unlink(out)
                    except OSError:
                        pass
            else:
                current_app.logger.debug("Thumbnail: no file on disk for %s (%s)" % (video_id, fpath))
        else:
            current_app.logger.debug("Thumbnail: no filepath in DB for %s" % video_id)

        if img:
            try:
                con = get_connection(current_app.logger)
                cur = con.cursor()
                # COALESCE ensures a racing NULL result never overwrites real bytes
                cur.execute(
                    "INSERT INTO images(id, image) VALUES(%s, %s) "
                    "ON DUPLICATE KEY UPDATE image = COALESCE(VALUES(image), image)",
                    (video_id, img)
                )
                con.commit()
                cur.close()
                con.close()
            except Exception as e:
                current_app.logger.error("Thumbnail store failed for %s: %s" % (video_id, e))

        return img
    finally:
        with _thumbnail_inflight_lock:
            _thumbnail_inflight.discard(video_id)

@api_bp.route('/images/<string:id>')
def imgid(id):
    try:
        current_app.logger.debug('Called Image ID: '+id)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("SELECT image FROM images WHERE id = %s", (id,))
        row = cur.fetchone()
        cur.close()
        con.close()

        if row is None or row[0] is None:
            # No row, or a NULL row left by a prior failed fetch — retry
            img = _fetch_and_store_thumbnail(id)
        else:
            img = row[0]

        if img:
            resp = send_file(io.BytesIO(img), mimetype='image/jpeg')
            resp.cache_control.no_cache = None
            resp.cache_control.public = True
            resp.cache_control.max_age = 30 * 86400
            resp.cache_control.immutable = True
            return resp

        # Fallback to placeholder (either fetch failed or sentinel NULL stored)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("SELECT image FROM images WHERE id = '-1'")
        result = cur.fetchone()
        cur.close()
        con.close()
        if not result or not result[0]:
            return "Image not found", 404
        resp = send_file(io.BytesIO(result[0]), mimetype='image/jpeg')
        resp.cache_control.no_cache = None
        resp.cache_control.public = True
        # Placeholder: keep it short so the real thumbnail shows up soon
        resp.cache_control.max_age = 300
        return resp
    except Exception as e:
        current_app.logger.error("API Image Failed: %s"%e)
        return "Image error", 500

@api_bp.route('/video/<string:id>')
def getVideo(id):
    try:
        current_app.logger.debug('Called Video ID: '+id)
        if(".mp4" in id):
            id = id.split(".")[0]
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute(f"select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid where id = %s;",(id,))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        if not json_data:
            return api_error("Video not found", 404)
        json_data[0]['filepath'] = '/videos/'+json_data[0]['filepath']
        # Lazy backfill codec info if any field is missing
        v = json_data[0]
        if v.get('vcodec') is None or v.get('acodec') is None or v.get('container') is None:
            try:
                # filepath in response is /videos/<rel>; DB stores <rel>
                rel = (v['filepath'][len('/videos/'):] if v['filepath'].startswith('/videos/') else v['filepath']).lstrip('/')
                source_path = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], rel)
                if os.path.isfile(source_path):
                    info = get_codec_info(source_path, current_app.logger)
                    if not info['container']:
                        info['container'] = get_container_from_ext(source_path)
                    if info['vcodec'] or info['acodec']:
                        update_video_codec_info(id, info['vcodec'], info['acodec'], info['container'], current_app.logger)
                        v['vcodec'] = info['vcodec']
                        v['acodec'] = info['acodec']
                        v['container'] = info['container']
            except Exception as e:
                current_app.logger.error("Lazy codec backfill failed for %s: %s", id, e)
        cur.close()
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Video Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/watched/<string:id>")
def watched(id):
    try:
        current_app.logger.debug('Called Watched: '+id)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Update videos set watched = 1 where id = %s;",(id,))
        cur.execute("Update videos set timestamp = 0 where id = %s;",(id,))
        con.commit()
        cur.close()
        con.close()
        return api_success()
    except Exception as e:
        current_app.logger.error("Mark Watched Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/unwatched/<string:id>")
def unwatched(id):
    try:
        current_app.logger.debug('Called UnWatched: '+id)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Update videos set watched = 0 where id = %s;",(id,))
        con.commit()
        cur.close()
        con.close()
        return api_success()
    except Exception as e:
        current_app.logger.error("Mark Unwatched Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/set_timestamp/<string:ts>/<string:id>")
def set_timestamp(id,ts):
    try:
        current_app.logger.debug('Called Set Timestamp %s at %s'%(id,ts))
        ts = ts.split('.')[0]
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Update videos set timestamp = %s where id = %s;",(ts,id))
        con.commit()
        cur.close()
        con.close()
        return api_success()
    except Exception as e:
        current_app.logger.error("Set Timestamp Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/list/resume/")
def list_resume():
    try:
        current_app.logger.debug("Called List Resume")
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute(f"select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid where not timestamp = 0 order by PublishedAt desc limit 40;")
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API List Resume Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/download/single", methods=["POST"])
def api_download():
    try:
        url = request.get_json(force=True).get('url', '').strip()
        if not url:
            return api_error("Missing URL", 400)
        source = "youtube"
        if len(url) == 11 and not url.startswith('http'):
            source = "youtube"
        elif url.startswith(('PL', 'LL', 'UL')):
            source = "youtube"
        elif url.startswith('UC'):
            source = "youtube"
        elif 'youtube.com' in url or 'youtu.be' in url:
            source = "youtube"
        i = QueueObject(url, "", source, 0, "")
        enqueue(i, current_app.config['queue'], current_app.logger)
        return api_success()
    except Exception as e:
        current_app.logger.error("API Download Failed: %s" % e)
        return api_error(str(e), 500)

@api_bp.route("/stats/video/count")
def get_video_count():
    try:
        current_app.logger.debug('Called Get_Video_Count')
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select count(*) from videos;")
        count = cur.fetchone()[0]
        cur.close()
        con.close()
        return str(count)
    except Exception as e:
        current_app.logger.error("API Video Count Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route('/channels/<string:page>')
def channels(page):
    try:
        current_app.logger.debug("Called Channels %s"%(page,))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        page_num = int(page) if page.isdigit() else 0
        # order=activity sorts by most recent video (used by the home page rails)
        order_by = "lastvidtime desc" if request.args.get('order') == 'activity' else "channelname"
        having = ""
        params = []
        src = request.args.get('source')
        if src in ('youtube', 'patreon', 'reddit'):
            # Empty channels have no videos.source; classify by ID shape, same
            # heuristic as channels.html / /api/channel
            having = " having coalesce(source, case when left(channels.channelId, 2) = 'UC' then 'youtube' when channels.channelId regexp '^[0-9]+$' then 'patreon' else 'reddit' end) = %s"
            params.append(src)
        cur.execute(f"select channels.*,count(videos.id) as vidcount,max(PublishedAt) as lastvidtime,coalesce(sum(videos.watched = 0),0) as unwatched,max(videos.source) as source from channels left outer join videos on channels.channelId = videos.channelId group by channels.channelId{having} order by {order_by} limit 40 offset %s;",(*params, page_num))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Channel Failed: %s"%e)
        return api_error(str(e), 500)


def _channel_description(jdata, channelid):
    """Best-effort channel description from stored channel JSON."""
    if not jdata:
        return None
    try:
        data = json.loads(jdata)
    except Exception:
        return None
    # YouTube channel JSON from the Data API
    try:
        desc = data['items'][0]['snippet']['description']
        if desc:
            return desc
    except Exception:
        pass
    # Patreon campaign JSON
    for key in ('creation_name', 'summary', 'name'):
        try:
            val = data['data']['attributes'][key]
            if val:
                return val
        except Exception:
            pass
    return None


def _channel_thumbnail_url(jdata, channelid):
    """Best-effort channel thumbnail URL from stored channel JSON."""
    if not jdata:
        return None
    try:
        data = json.loads(jdata)
    except Exception:
        return None
    # YouTube channel thumbnails
    try:
        thumbs = data['items'][0]['snippet']['thumbnails']
        for quality in ('high', 'medium', 'default'):
            if quality in thumbs and thumbs[quality].get('url'):
                return thumbs[quality]['url']
    except Exception:
        pass
    # Patreon campaign avatar / image
    try:
        attrs = data['data']['attributes']
        for key in ('avatar_photo_url', 'image_url', 'image_small_url'):
            if attrs.get(key):
                return attrs[key]
    except Exception:
        pass
    return None


@api_bp.route('/channel/<string:channelid>')
def api_channel(channelid):
    """Single channel row plus a derived link to the creator's page at the
    source (YouTube by UC-prefixed ID, Patreon from the stored campaign json,
    Reddit by author name)."""
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select channelname, subscribed, json from channels where channelid = %s;", (channelid,))
        row = cur.fetchone()
        name, subscribed, jdata = row if row else (None, None, None)

        source_url = None
        if channelid.startswith('UC'):
            source_url = 'https://www.youtube.com/channel/' + channelid
        elif channelid.isdigit():
            try:
                url = json.loads(jdata)['data']['attributes']['url']
                if url:
                    source_url = url if url.startswith('http') else 'https://www.patreon.com' + url
            except Exception:
                pass
        elif channelid != 'unknown':
            cur.execute("select source from videos where channelId = %s order by AddedAt desc limit 1;", (channelid,))
            vrow = cur.fetchone()
            if vrow and vrow[0] == 'reddit':
                source_url = 'https://www.reddit.com/user/' + channelid

        cur.execute("select count(*) from videos where channelId = %s;", (channelid,))
        vidcount = cur.fetchone()[0]

        cur.close()
        con.close()
        return json.dumps({
            'channelid': channelid,
            'channelname': name,
            'subscribed': subscribed,
            'source_url': source_url,
            'description': _channel_description(jdata, channelid),
            'thumbnail_url': _channel_thumbnail_url(jdata, channelid),
            'video_count': int(vidcount),
        }, default=str)
    except Exception as e:
        current_app.logger.error("API Channel Info Failed: %s" % e)
        return api_error(str(e), 500)

@api_bp.route('/creator/<string:creator>/<string:page>')
def api_creator(creator,page):
    try:
        current_app.logger.debug("Called Creator %s %s"%(creator,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        # Raw row offset, pre-multiplied by the caller like every other endpoint
        offset = int(page) if page.isdigit() else 0
        cur.execute(f"select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid where v.channelId = %s order by v.PublishedAt desc limit 40 offset %s;", (creator, offset))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Creator Failed: %s"%e)
        return api_error(str(e), 500)

#Removed 11/20/25
# @api_bp.route('/unwatched/<string:opt>/<string:page>')
# def get_unwatched(opt,page):
#     try:
#         current_app.logger.debug("Called Unwatched %s %s"%(opt,page))
#         con = get_connection(current_app.logger)
#         cur = con.cursor()
        
#         # Get sort parameters from query string
#         sort_release = request.args.get('sort_release', 'desc')
#         sort_added = request.args.get('sort_added', 'desc')
        
#         # Validate sort parameters
#         if sort_release not in ['asc', 'desc']:
#             sort_release = 'desc'
#         if sort_added not in ['asc', 'desc']:
#             sort_added = 'desc'
        
#         if(opt == "PublishedAt"):
#             cur.execute("select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid where v.watched = 0 order by v.PublishedAt %s limit 40 offset %s;"%(sort_release, page))
#         elif(opt == "AddedAt"):
#             cur.execute("select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid where v.watched = 0 order by v.AddedAt %s limit 40 offset %s;"%(sort_added, page))
#         else:
#             cur.execute("select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid where v.watched = 0 order by v.PublishedAt %s limit 40 offset %s;"%(sort_release, page))
#         return parse_response(cur,con)
#     except Exception as e:
#         current_app.logger.error("API Unwatched Failed: %s"%e)
#         return "[]"

@api_bp.route('/search/<string:searchtxt>/<string:page>')
def api_search(searchtxt,page):
    try:
        current_app.logger.debug("Called Search %s %s"%(searchtxt,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        try:
            offset = int(page)
        except (ValueError, TypeError):
            offset = 0
        if len(searchtxt) >= 3:
            # Full-text search with relevance ranking
            cur.execute(
                "SELECT *, MATCH(title, description) AGAINST(%s IN BOOLEAN MODE) AS relevance "
                "FROM videos "
                "WHERE MATCH(title, description) AGAINST(%s IN BOOLEAN MODE) "
                "ORDER BY relevance DESC "
                "LIMIT 40 OFFSET %s;",
                (searchtxt, searchtxt, offset)
            )
        else:
            # Fall back to LIKE on title for short queries below the FULLTEXT minimum token size
            cur.execute(
                "SELECT * FROM videos WHERE title LIKE %s ORDER BY PublishedAt DESC LIMIT 40 OFFSET %s;",
                ("%" + searchtxt + "%", offset)
            )
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Search Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/sub_status/<string:type>/<string:value>")
def sub_status(type,value):
    try:
        current_app.logger.debug('Called Sub_status: %s %s'%(type,value))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(type == "channel"):
            cur.execute("Select subscribed from channels where channelid = %s;",(value,))
            if(not cur.rowcount):
                current_app.logger.error("sub_status: Channel not found")
                process_channel('/videos/'+value,current_app.logger)
                data = ""
            else:
                data = cur.fetchone()[0]
        elif(type == "playlist"):
            cur.execute("Select subscribed from playlists where playlistId = %s;",(value,))
            if(not cur.rowcount):
                current_app.logger.error("sub_status: playlist not found")
                data = ""
            else:
                data = cur.fetchone()[0]
        con.commit()
        cur.close()
        con.close()
        # return the results!
        return str(data)
    except Exception as e:
        current_app.logger.error("sub_status Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/watch_status/<string:vid>")
def watch_status(vid):
    try:
        current_app.logger.debug('Called Watch Status: '+vid)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Select watched from videos where id = %s;",(vid,))
        row = cur.fetchone()
        cur.close()
        con.close()
        if row is None:
            return api_error("Video not found", 404)
        return str(row[0])
    except Exception as e:
        current_app.logger.error("Watch Status Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/checkdb")
def api_checkdb():
    return str(checkdb(current_app.logger))

@api_bp.route("/health")
def api_health():
    """Liveness probe for the Docker HEALTHCHECK: one pooled SELECT 1
    (checkdb is too heavy to run every probe interval)."""
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        con.close()
        return api_success()
    except Exception as e:
        current_app.logger.error("Health check failed: %s" % e)
        return api_error("unhealthy: %s" % e, 503)

@api_bp.route('/status/queue/')
def queue_status():
    data = {}
    data['dl_status'] = get_dl_status()
    data['queue_size'] = current_app.config['queue'].qsize()
    data['queue_value'] = [
        {'url': q.url}
        for q in current_app.config['queue'].queue
    ]
    data['cur_id'] = get_cur_videoID()
    data['cur_title'] = get_cur_videoTitle()

    # Add active or all current download statuses for youtube and optionally patreon
    status_copy = get_status_copy()
    data['active'] = [
        {'id': k, **v} for k, v in status_copy.items()
    ]

    return json.dumps(data, indent=4, sort_keys=True, default=str)

@api_bp.route('/status/stream')
def status_stream():
    """Server-Sent Events endpoint that pushes real-time download progress to clients.

    Events are JSON objects with a ``type`` field:
      - ``{"type": "progress", "id": "...", "title": "...", "progress": "50%", ...}``
      - ``{"type": "complete", "id": "..."}``

    A keepalive comment (``: keepalive``) is sent every 30 s so proxies and
    load-balancers do not terminate idle connections.
    """
    def generate():
        q = subscribe_sse()
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                except _queue.Empty:
                    # SSE comment – ignored by clients, keeps the connection alive
                    yield ": keepalive\n\n"
        finally:
            unsubscribe_sse(q)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # prevent nginx from buffering the stream
        },
    )


@api_bp.route('/downloads/errors/')
def get_download_errors_api():
    try:
        logger = current_app.logger
        errors = get_download_errors(logger, 50)
        data = []
        for err in errors:
            data.append({
                'id': err[0],
                'url': err[1],
                'error_type': err[2],
                'error_message': err[3],
                'created_at': str(err[4])
            })
        return json.dumps(data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Get Download Errors Failed: %s" % e)
        return json.dumps([])

@api_bp.route('/downloads/retry', methods=['POST'])
def retry_download_api():
    """Re-enqueue a failed URL. Optional 'id' removes the download_errors row
    once the URL is back in the queue."""
    try:
        body = request.get_json(force=True)
        url = (body.get('url') or '').strip()
        if not url:
            return api_error("Missing URL", 400)
        qo = QueueObject(url, "", body.get('source') or 'youtube', 0, "")
        enqueued = enqueue(qo, current_app.config['queue'], current_app.logger)
        if body.get('id') is not None:
            delete_download_error(body['id'], current_app.logger)
        return api_success({"enqueued": enqueued})
    except Exception as e:
        current_app.logger.error("API Retry Download Failed: %s" % e)
        return api_error(str(e), 500)

@api_bp.route('/downloads/errors/', methods=['DELETE'])
def clear_download_errors_api():
    try:
        logger = current_app.logger
        clear_download_errors(logger)
        return api_success()
    except Exception as e:
        current_app.logger.error("API Clear Download Errors Failed: %s" % e)
        return api_error(str(e), 500)

@api_bp.route("/subscribe/<string:type>/<string:value>")
def api_subscribe(type,value):
    ret = False
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(type == "playlist"):
            current_app.logger.debug('Called Playlist Subscribe: '+value)
            cur.execute("Select * from playlists where playlistId = %s",(value,))
            if(not cur.rowcount):
                plinfo = get_playlist_info(value,current_app.logger)
                insert_playlist(plinfo,current_app.logger)
                ret = True
            else:
                cur.execute("Update playlists set subscribed = 1 where playlistId = %s;",(value,))
                ret = True
        elif(type == "channel"):
            current_app.logger.debug('Called Channel Subscribe: '+value)
            cur.execute("Update channels set subscribed = 1 where channelid = %s;",(value,))
            ret = True
        con.commit()
        cur.close()
        con.close()
        return api_success(ret)
    except Exception as e:
        current_app.logger.error("Playlist Subscribe Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/unsubscribe/<string:type>/<string:value>")
def api_unsubscribe(type,value):
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(type == "playlist"):
            current_app.logger.debug('Called Playlist Unsubscribe: '+value)
            cur.execute("Update playlists set subscribed = 0 where playlistId = %s;",(value,))
        elif(type == "channel"):
            current_app.logger.debug('Called Channel Unsubscribe: '+value)
            cur.execute("Update channels set subscribed = 0 where channelid = %s;",(value,))
        con.commit()
        cur.close()
        con.close()
        return api_success()
    except Exception as e:
        current_app.logger.error("Playlist Unsubscribe Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route('/playlists/<string:page>')
def playlists(page):
    try:
        current_app.logger.debug("Called Playlists %s"%(page,))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        page_num = int(page) if page.isdigit() else 0
        cur.execute("select * from playlists order by playlistName desc limit 40 offset %s;",(page_num,))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Playlists Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route('/playlist/<string:playlist>/<string:page>')
def api_playlist(playlist,page):
    try:
        current_app.logger.debug("Called playlist %s %s"%(playlist,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        page_num = int(page) if page.isdigit() else 0
        cur.execute("select videos.*,playlistName from videos left outer join pl2vid on videos.id = pl2vid.videoId left outer join playlists on pl2vid.playlistId = playlists.playlistId where pl2vid.playlistId = %s order by PublishedAt desc limit 40 offset %s;",(playlist,page_num))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Playlist Failed: %s"%e)
        return api_error(str(e), 500)

@api_bp.route('/random')
def api_random():
    try:
        current_app.logger.debug("Called Random")
        con = get_connection(current_app.logger)
        cur = con.cursor()
        include_reddit = request.args.get('include_reddit', '0')
        if include_reddit == '1':
            cur.execute(f"select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid order by RAND() LIMIT 40;")
        else:
            cur.execute(f"select v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container from {os.environ['VAULTTUBE_DBNAME']}.videos v left outer join {os.environ['VAULTTUBE_DBNAME']}.channels c on v.channelId = c.channelid where v.source in ('youtube','patreon') order by RAND() LIMIT 40;")
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Random Fail: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/up_next/<string:vid>")
def api_up_next(vid):
    """Ordered list of what to play after <vid>: unwatched videos from the same
    channel in series order (published after the current one first, then older
    ones newest-first), topped up with recent unwatched from other channels."""
    try:
        current_app.logger.debug("Called Up Next %s" % vid)
        try:
            limit = min(max(int(request.args.get('limit', 10)), 1), 25)
        except ValueError:
            limit = 10
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select channelId, PublishedAt from videos where id = %s;", (vid,))
        if not cur.rowcount:
            cur.close()
            con.close()
            return "[]"
        channel_id, published_at = cur.fetchone()
        cols = "v.id,c.channelname as youtuber,v.channelId,v.json,v.filepath,v.AddedAt,v.PublishedAt,v.watched,v.`timestamp`,v.`length`,v.lastScanned,v.isDeleted,v.source,v.title,v.vcodec,v.acodec,v.container"
        base = f"select {cols} from videos v left outer join channels c on v.channelId = c.channelid where v.watched = 0 and v.id != %s and "
        queries = [
            (base + "v.channelId = %s and v.PublishedAt > %s order by v.PublishedAt asc limit %s;", (vid, channel_id, published_at, limit)),
            (base + "v.channelId = %s and v.PublishedAt <= %s order by v.PublishedAt desc limit %s;", (vid, channel_id, published_at, limit)),
            (base + "v.channelId != %s order by v.PublishedAt desc limit %s;", (vid, channel_id, limit)),
        ]
        results = []
        seen = {vid}
        for sql, params in queries:
            if len(results) >= limit:
                break
            cur.execute(sql, params)
            headers = [x[0] for x in cur.description]
            for row in cur.fetchall():
                item = dict(zip(headers, row))
                if item['id'] in seen or len(results) >= limit:
                    continue
                seen.add(item['id'])
                results.append(item)
        cur.close()
        con.close()
        return json.dumps(results, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Up Next Failed: %s" % e)
        return "[]"

@api_bp.route("/chapters/<string:vid>")
def api_chapters(vid):
    """Parsed chapter markers for a video, derived from its stored description.

    Returns ``{"chapters": [{"start": <seconds>, "title": <str>}, ...]}``.
    Always returns a list (empty when the description has no parseable
    chapters, or the video doesn't exist) so the player's single code path
    is a graceful no-op for non-YouTube / chapter-less sources. See
    ``chapters.parse_chapters`` for the parsing rules.
    """
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select description from videos where id = %s;", (vid,))
        row = cur.fetchone()
        cur.close()
        con.close()
        description = row[0] if row else None
        return jsonify({"chapters": parse_chapters(description)})
    except Exception as e:
        current_app.logger.error("API Chapters Failed: %s" % e)
        return jsonify({"chapters": []})


@api_bp.route("/find_next_previous/<string:vid>")
def api_fnp(vid):
    try:
        current_app.logger.debug("Called FNP")
        ret = find_next_previous(vid,current_app.logger)
        return json.dumps(ret, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API FNP Fail: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/delete/<string:vid>")
def api_delete(vid):
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select filepath from videos where id = %s",(vid,))
        row = cur.fetchone()
        if row is None:
            cur.close()
            con.close()
            return api_error("Video not found", 404)
        filepath = row[0]
        # DB stores the path relative to the vault root (with a leading slash)
        filepath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], filepath.lstrip('/'))
        try:
            os.remove(filepath)
        except Exception as e:
            current_app.logger.error("API Delete File Missing: %s"%e)
        cur.execute("Delete from videos where id = %s",(vid,))
        cur.execute("Delete from images where id = %s",(vid,))
        cur.execute("Insert ignore into IgnoreVid(id) values(%s)",(vid,))
        con.commit()
        cur.close()
        con.close()
        current_app.logger.info("Deleted Video %s"%vid)
        return api_success()
    except Exception as e:
        current_app.logger.error("API Delete: %s"%e)
        return api_error(str(e), 500)
    
@api_bp.route("/stats")
def api_stats():
    """Dashboard data."""
    try:
        data = {}
        con = get_connection(current_app.logger)
        cur = con.cursor()

        cur.execute("select count(*), coalesce(sum(watched=1),0), coalesce(sum(TIME_TO_SEC(`length`)),0), coalesce(round(avg(TIME_TO_SEC(`length`))),0) from videos")
        total, watched, total_seconds, avg_seconds = cur.fetchone()
        cur.execute("select count(*) from channels")
        channels = cur.fetchone()[0]
        cur.execute("select count(*) from playlists")
        playlists = cur.fetchone()[0]
        data['totals'] = {
            'videos': int(total), 'watched': int(watched), 'unwatched': int(total - watched),
            'total_seconds': int(total_seconds), 'avg_seconds': int(avg_seconds),
            'channels': int(channels), 'playlists': int(playlists),
        }

        # Deleted-at-source is only tracked for YouTube
        cur.execute("select coalesce(sum(isDeleted=1),0), count(*) from videos where source='youtube'")
        deleted, yt_total = cur.fetchone()
        data['deleted'] = {'deleted': int(deleted), 'youtube_total': int(yt_total)}

        cur.execute("select coalesce(source,'unknown'), count(*) from videos group by source order by count(*) desc")
        data['by_source'] = [[r[0], int(r[1])] for r in cur.fetchall()]

        cur.execute("select coalesce(c.channelname, v.channelId), count(*) as total, coalesce(sum(v.watched=0),0) from videos v left outer join channels c on v.channelId = c.channelid group by v.channelId order by total desc limit 10")
        data['top_channels'] = [[r[0], int(r[1]), int(r[2])] for r in cur.fetchall()]

        # Vault growth: videos added per week for the last 26 weeks, zero-filled
        cur.execute("select date(date_sub(AddedAt, interval weekday(AddedAt) day)) as wk, count(*) from videos where AddedAt >= date_sub(curdate(), interval 26 week) group by wk order by wk")
        weekly = {str(r[0]): int(r[1]) for r in cur.fetchall()}
        monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
        data['added_per_week'] = [
            [str(monday - datetime.timedelta(weeks=i)), weekly.get(str(monday - datetime.timedelta(weeks=i)), 0)]
            for i in range(25, -1, -1)
        ]

        cur.execute("select case when TIME_TO_SEC(`length`) < 300 then 0 when TIME_TO_SEC(`length`) < 600 then 1 when TIME_TO_SEC(`length`) < 1800 then 2 when TIME_TO_SEC(`length`) < 3600 then 3 else 4 end as bucket, count(*) from videos where TIME_TO_SEC(`length`) > 0 group by bucket")
        buckets = {int(r[0]): int(r[1]) for r in cur.fetchall()}
        labels = ['Under 5 min', '5–10 min', '10–30 min', '30–60 min', 'Over 60 min']
        data['duration_buckets'] = [[labels[i], buckets.get(i, 0)] for i in range(5)]

        cur.close()
        con.close()
        return json.dumps(data, default=str)
    except Exception as e:
        current_app.logger.error("API Stats Error: %s"%e)
        return api_error(str(e), 500)

@api_bp.route("/storage")
def api_storage():
    """Disk-usage dashboard data: vault bytes by source/channel/week + transcode cache status."""
    try:
        data = {}
        con = get_connection(current_app.logger)
        cur = con.cursor()

        # Total vault bytes + video count
        cur.execute("select coalesce(sum(filesize),0), count(*) from videos")
        total_bytes, total_count = cur.fetchone()
        data['totals'] = {'bytes': int(total_bytes or 0), 'videos': int(total_count)}

        # Per-source breakdown (bytes + count), biggest source first
        cur.execute("select coalesce(source,'unknown'), coalesce(sum(filesize),0), count(*) from videos group by source order by sum(filesize) desc")
        data['by_source'] = [[r[0], int(r[1] or 0), int(r[2])] for r in cur.fetchall()]

        # Top channels by bytes (limit 10); join channels for display name.
        # 4th element is the channelId (for creator-page links), distinct from
        # the display name which may be the channelname or fall back to the ID.
        cur.execute("select coalesce(c.channelname, v.channelId), coalesce(sum(v.filesize),0), count(*), v.channelId from videos v left outer join channels c on v.channelId = c.channelid group by v.channelId order by sum(v.filesize) desc limit 10")
        data['top_channels'] = [[r[0], int(r[1] or 0), int(r[2]), r[3]] for r in cur.fetchall()]

        # Vault growth by bytes per week, last 26 weeks, zero-filled (mirrors api_stats added_per_week)
        cur.execute("select date(date_sub(AddedAt, interval weekday(AddedAt) day)) as wk, coalesce(sum(filesize),0) from videos where AddedAt >= date_sub(curdate(), interval 26 week) group by wk order by wk")
        weekly = {str(r[0]): int(r[1] or 0) for r in cur.fetchall()}
        monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
        data['added_per_week_bytes'] = [
            [str(monday - datetime.timedelta(weeks=i)), weekly.get(str(monday - datetime.timedelta(weeks=i)), 0)]
            for i in range(25, -1, -1)
        ]

        cur.close()
        con.close()

        # Transcode cache (filesystem, not DB)
        cache = cache_stats()
        cache['cap_gb'] = round(cache['cap_bytes'] / (1024 ** 3), 1)
        data['cache'] = cache

        return json.dumps(data, default=str)
    except Exception as e:
        current_app.logger.error("API Storage Error: %s"%e)
        return api_error(str(e), 500)
    
@api_bp.route('/transcode/<string:video_id>/playlist.m3u8')
def transcode_playlist(video_id):
    """Return a stable VOD HLS playlist for video_id. Launches FFmpeg on demand.

    The playlist is synthesized from the source duration so it carries the
    full segment list, #EXT-X-PLAYLIST-TYPE:VOD and #EXT-X-ENDLIST from the
    very first request — even while the transcode is still in progress. This
    keeps HLS clients (AVPlayer) in VOD mode playing from seg_00000 instead of
    latching the live edge of an endless playlist (issue #24). Segments that
    aren't on disk yet block in the segment endpoint rather than 404-ing.
    """
    try:
        current_app.logger.debug('Called HLS playlist for: %s', video_id)
        source_path = source_path_for(video_id, current_app.logger)
        if not source_path or not os.path.isfile(source_path):
            abort(404)

        # Kick off (or confirm) the encode so segments start being produced.
        cache_dir = generate_hls(video_id, current_app.logger, source_path=source_path)
        if not cache_dir:
            abort(404)
        touch_cache_access(video_id)

        body = build_vod_playlist(get_duration(source_path, current_app.logger))
        if body is not None:
            return current_app.response_class(
                body, mimetype='application/vnd.apple.mpegurl')

        # Duration unknown — fall back to FFmpeg's own (live) playlist.
        playlist_path = os.path.join(cache_dir, 'playlist.m3u8')
        if not os.path.exists(playlist_path):
            abort(404)
        return send_file(
            playlist_path,
            mimetype='application/vnd.apple.mpegurl',
            conditional=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error("HLS playlist failed for %s: %s", video_id, e)
        return api_error(str(e), 500)


@api_bp.route('/transcode/<string:video_id>/seg_<string:segment>.ts')
def transcode_segment(video_id, segment):
    """Serve a single HLS segment, blocking until it's produced if needed.

    The synthesized VOD playlist lists every segment up front, so clients will
    request segments the transcode hasn't reached yet. Rather than 404-ing
    instantly (which fails the AVPlayer item — issue #24), we long-poll until
    FFmpeg writes the segment, restarting the encode if it isn't running.
    A genuine out-of-range index or a timeout still 404s.
    """
    try:
        current_app.logger.debug('Called HLS segment %s for: %s', segment, video_id)
        if not segment.isdigit():
            abort(404)
        index = int(segment)
        cache_dir = get_transcode_cache_dir(video_id)
        seg_path = os.path.join(cache_dir, 'seg_%05d.ts' % index)
        real_cache = os.path.realpath(cache_dir)
        real_seg = os.path.realpath(seg_path)
        if not real_seg.startswith(real_cache + os.sep):
            abort(404)

        note_segment_request(video_id)
        touch_cache_access(video_id)

        # Fast path: already on disk.
        if not (os.path.isfile(real_seg) and os.path.getsize(real_seg) > 0):
            source_path = source_path_for(video_id, current_app.logger)
            if not source_path or not os.path.isfile(source_path):
                abort(404)

            # Reject indices beyond the real end of the video.
            total = segment_count_for_duration(get_duration(source_path, current_app.logger))
            if total and index >= total:
                abort(404)

            # Ensure an encode is running (it may have been reaped mid-session).
            generate_hls(video_id, current_app.logger, source_path=source_path)

            timeout = _segment_wait_timeout()
            ready = wait_for_segment(
                cache_dir, index, transcode_key(video_id), timeout=timeout)
            if not ready:
                current_app.logger.warning(
                    "HLS segment %s timed out/unavailable for %s", index, video_id)
                abort(404)

        note_segment_request(video_id)
        touch_cache_access(video_id)
        resp = send_file(
            real_seg,
            mimetype='video/MP2T',
            conditional=True,
        )
        resp.headers['Accept-Ranges'] = 'bytes'
        return resp
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error("HLS segment failed for %s/%s: %s", video_id, segment, e)
        return api_error(str(e), 500)


def _segment_wait_timeout():
    try:
        return max(1.0, float(os.environ.get('VAULTTUBE_TRANSCODE_SEGMENT_TIMEOUT', '30')))
    except ValueError:
        return 30.0


# Legacy transcoding endpoint removed; use /api/transcode/<id>/playlist.m3u8
@api_bp.route('/transcode/<path:videopath>')
def transcode_legacy(videopath):
    return api_error("Use /api/transcode/<video_id>/playlist.m3u8", 410)

@api_bp.route("/upload/video", methods=["POST"])
def api_upload_video():
    try:
        video_file = request.files.get('videoFile')
        video_id = request.form.get('videoId', '').strip()
        title = request.form.get('title', '').strip()
        channel_id = request.form.get('channelId', '').strip()
        published_at_str = request.form.get('publishedAt', '').strip()
        source = request.form.get('source', 'youtube').strip().lower()

        if not video_file or not video_id or not title or not channel_id or not published_at_str:
            return "Missing required fields", 400

        # Both IDs become path components under the vault
        for component in (video_id, channel_id):
            if '/' in component or '\\' in component or component in ('.', '..'):
                return "Invalid videoId or channelId", 400

        try:
            published_at = datetime.datetime.strptime(published_at_str, "%Y-%m-%d")
        except Exception:
            return "Invalid published date format", 400

        # Save to vault directory
        vault_dir = os.environ['VAULTTUBE_VAULTDIR']
        file_path = os.path.join(vault_dir,channel_id, f"{video_id}.mp4")
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Ensure no overwrite, skip if exists
        if not os.path.exists(file_path):
            video_file.save(file_path)

        db_path = os.path.join(channel_id, f"{video_id}.mp4")
        # Save metadata & insert into DB via backend helper
        save_uploaded_video_metadata(video_id, file_path, title, channel_id, published_at,db_path,source)

        return "Upload successful", 200
    except Exception as e:
        current_app.logger.error(f"API Upload Video Failed: {e}")
        return "Internal server error", 500

@api_bp.route("/reddit/saved", methods=["POST"])
def api_reddit_saved():
    try:
        reddit_vars = [
            'VAULTTUBE_REDDIT_CLIENT_ID', 'VAULTTUBE_REDDIT_CLIENT_SECRET',
            'VAULTTUBE_REDDIT_USER_AGENT', 'VAULTTUBE_REDDIT_USERNAME',
            'VAULTTUBE_REDDIT_PASSWORD',
        ]
        missing = [v for v in reddit_vars if v not in os.environ]
        if missing:
            return api_error("Reddit credentials not configured: %s" % ', '.join(missing), 400)

        import praw
        from providers.reddit import _get_reddit_client

        def _has_media(submission):
            if submission.is_video:
                return True
            url = (submission.url or '').lower()
            if any(url.split('?')[0].endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4')):
                return True
            return any(host in url for host in ('redgifs.com', 'i.redd.it', 'i.imgur.com'))

        reddit = _get_reddit_client()
        q = current_app.config['queue']
        enqueued = 0
        skipped = 0

        for item in reddit.user.me().saved(limit=100):
            if not isinstance(item, praw.models.Submission):
                continue
            if not item.permalink or not _has_media(item):
                skipped += 1
                continue
            url = "https://www.reddit.com" + item.permalink
            qo = QueueObject(url, "", "reddit", 0, "", unsave=True)
            enqueue(qo, q, current_app.logger)
            enqueued += 1

        return api_success({"enqueued": enqueued, "skipped": skipped})
    except Exception as e:
        current_app.logger.error("API Reddit Saved Failed: %s" % e)
        return api_error(str(e), 500)

@api_bp.route('/creator/count/<string:creator>')
def api_creator_count(creator):
    try:
        current_app.logger.debug("Called Creator Count %s"%(creator,))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select count(*) from videos where channelId = %s;", (creator,))
        count = cur.fetchone()[0]
        cur.close()
        con.close()
        return jsonify({'count': count})
    except Exception as e:
        current_app.logger.error("API Creator Count Failed: %s"%e)
        return jsonify({'count': 0})

@api_bp.route("/stats/video/unwatched")
def get_video_unwatched_count():
    try:
        current_app.logger.debug('Called Get_Video_Unwatched_Count')
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select count(*) from videos where watched = 0;")
        count = cur.fetchone()[0]
        cur.close()
        con.close()
        return str(count)
    except Exception as e:
        current_app.logger.error("API Unwatched Count Failed: %s"%e)
        return api_error(str(e), 500)

def _build_export_config():
    env = os.environ
    return {
        'vault_dir': env.get('VAULTTUBE_VAULTDIR'),
        'port': env.get('VAULTTUBE_PORT', '5000'),
        'dl_delay': env.get('VAULTTUBE_DL_DELAY', '10'),
        'dbpool': env.get('VAULTTUBE_DBPOOL', '8'),
        'transcode_cache_dir': env.get('VAULTTUBE_TRANSCODE_CACHE_DIR'),
        'transcode_ttl': env.get('VAULTTUBE_TRANSCODE_TTL', '86400'),
        'transcode_max_cache_gb': env.get('VAULTTUBE_TRANSCODE_MAX_CACHE_GB', '50'),
        'transcode_preset': env.get('VAULTTUBE_TRANSCODE_PRESET', 'veryfast'),
        'transcode_crf': env.get('VAULTTUBE_TRANSCODE_CRF', '23'),
        'max_concurrent_transcodes': env.get('VAULTTUBE_MAX_CONCURRENT_TRANSCODES', '1'),
        'transcode_segment_timeout': env.get('VAULTTUBE_TRANSCODE_SEGMENT_TIMEOUT', '30'),
        'proxy': env.get('VAULTTUBE_PROXY'),
        'deno_path': env.get('VAULTTUBE_DENOPATH'),
        'providers': {
            'youtube': {
                'api_key_configured': bool(env.get('VAULTTUBE_YTKEY')),
                'cookie_path': env.get('VAULTTUBE_YTCOOKIE'),
            },
            'patreon': {
                'cookie_path': env.get('VAULTTUBE_PATREONCOOKIE'),
            },
            'reddit': {
                'configured': all(env.get(k) for k in [
                    'VAULTTUBE_REDDIT_CLIENT_ID',
                    'VAULTTUBE_REDDIT_CLIENT_SECRET',
                    'VAULTTUBE_REDDIT_USERNAME',
                    'VAULTTUBE_REDDIT_PASSWORD',
                    'VAULTTUBE_REDDIT_USER_AGENT',
                ]),
            },
        },
    }


@api_bp.route('/export')
def api_export():
    include_json = request.args.get('include_json', '0') == '1'
    fmt = request.args.get('format', 'json').lower()
    logger = current_app.logger
    ts = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')

    if fmt == 'csv':
        def generate_csv():
            buf = io.StringIO()
            writer = csv.writer(buf)
            cols = ['id', 'title', 'channelId', 'channelName', 'source',
                    'AddedAt', 'PublishedAt', 'watched', 'timestamp', 'length',
                    'filepath', 'vcodec', 'acodec', 'container', 'filesize',
                    'isDeleted', 'description']
            writer.writerow(cols)
            yield buf.getvalue()
            for row in export_video_rows(logger, include_json=False):
                buf.seek(0)
                buf.truncate()
                writer.writerow([row.get(c) for c in cols])
                yield buf.getvalue()
        return Response(
            stream_with_context(generate_csv()),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename="vaulttube_export_%s.csv"' % ts},
        )

    def generate_json():
        counts = export_row_counts(logger)
        meta = {
            'exported_at': datetime.datetime.utcnow().isoformat() + 'Z',
            'include_json': include_json,
            'counts': counts,
        }
        yield '{"meta":' + json.dumps(meta, default=str) + ',"catalog":['
        first = True
        for row in export_video_rows(logger, include_json):
            if not first:
                yield ','
            yield json.dumps(row, default=str)
            first = False
        channels = export_subscribed_channels(logger)
        playlists = export_subscribed_playlists(logger)
        yield '],"subscriptions":' + json.dumps({'channels': channels, 'playlists': playlists}, default=str)
        yield ',"mappings":' + json.dumps(export_pl2vid(logger), default=str)
        yield ',"tombstones":' + json.dumps(export_tombstones(logger), default=str)
        yield ',"config":' + json.dumps(_build_export_config(), default=str) + '}'

    return Response(
        stream_with_context(generate_json()),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename="vaulttube_export_%s.json"' % ts},
    )


def get_playlist_info(playlistid, logger):
    try:
        curl = "https://www.googleapis.com/youtube/v3/playlists?part=snippet,contentDetails&id=%s&key=%s" % (playlistid, os.environ['VAULTTUBE_YTKEY'])
        r = requests.get(curl, timeout=30)
        retj = r.json()
        r.close()
        return retj
    except Exception as e:
        logger.error("Failed to get playlist info: %s" % e)