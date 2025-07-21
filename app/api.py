from flask import Blueprint,current_app,send_file,Response,abort
import mariadb,json,io,math,os
import subprocess
from youtube import get_dl_status,get_video,get_channel_video_list,get_cur_videoID,get_cur_videoTitle,get_playlist_info,dl_status_map as yt_dl_map
from patreon import dl_status_map as patreon_dl_map   
from backend import process_channel,save_uploaded_video_metadata
from database import checkdb,get_connection,insert_playlist,find_next_previous
from flask import request,jsonify
import shutil
import datetime

from QueueObject import QueueObject

api_bp = Blueprint('api',__name__)

def parse_response(cur,con):
    if(not cur.rowcount):
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

@api_bp.route('/latest/<string:opt>/<string:page>')
def latest(opt,page):
    try:
        current_app.logger.debug("Called Latest %s %s"%(opt,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(opt == "PublishedAt"):
            cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos order by PublishedAt desc limit 40 offset %s;"%(page,))
        elif(opt == "AddedAt"):
            cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos order by AddedAt desc limit 40 offset %s;"%(page,))
        else:
            cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos order by PublishedAt desc limit 40 offset %s;"%(page,))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Latest Failed: %s"%e)

@api_bp.route('/images/<string:id>')
def imgid(id):
    try:
        current_app.logger.debug('Called Image ID: '+id)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select image from images where id = '%s';"%(id,))
        if cur.rowcount > 0:
            img = cur.fetchone()[0]
        else:
            #Cannot find image, Send Default Image
            cur.execute("select image from images where id = '-1';")
            img = cur.fetchone()[0]
        cur.close()
        con.close()
        return send_file(io.BytesIO(img),mimetype='image/jpeg',as_attachment=True,download_name='%s.jpg' % id)
    except Exception as e:
        current_app.logger.error("API Image Failed: %s"%e)

@api_bp.route('/video/<string:id>')
def getVideo(id):
    try:
        current_app.logger.debug('Called Video ID: '+id)
        if(".mp4" in id):
            id = id.split(".")[0]
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos where id = %s;",(id,))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        json_data[0]['filepath'] = '/videos/'+json_data[0]['filepath']
        # cur.execute("Select distinct playlist from playlists p where videoid = '%s'"%id)
        # results = cur.fetchall()
        # json_data[0]['playlists'] = [x[0] for x in results]
        cur.close()
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Video Failed: %s"%e)

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
        # return the results!
        return "True"
    except Exception as e:
        current_app.logger.error("Mark Watched Failed: %s"%e)

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
        # return the results!
        return "True"
    except Exception as e:
        current_app.logger.error("Mark Unwatched Failed: %s"%e)

@api_bp.route("/set_timestamp/<string:ts>/<string:id>")
def set_timestamp(id,ts):
    try:
        current_app.logger.debug('Called Set Timestamp %s at %s'%(id,ts))
        ts = ts.split('.')[0]
        con = get_connection(current_app.logger)
        cur = con.cursor()
        sql = "Update videos set timestamp = '%s' where id = '%s';"%(ts,id)
        current_app.logger.info(sql)
        cur.execute(sql)
        con.commit()
        cur.close()
        con.close()
        # return the results!
        return "True"
    except Exception as e:
        current_app.logger.error("Set Timestamp Failed: %s"%e)

@api_bp.route("/list/resume/")
def list_resume():
    try:
        current_app.logger.debug("Called List Resume")
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos where not timestamp = 0 order by PublishedAt desc limit 40;")
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API List Resume Failed: %s"%e)

@api_bp.route("/download/single/<string:ytid>")
def api_download(ytid):
    try:
        url = "https://www.youtube.com/watch?v="+ytid
        i = QueueObject(url,"","youtube",0,"")
        current_app.config['queue'].put(i)
        return "True"
    except Exception as e:
        current_app.logger.error("API Download Failed: %s"%e)
        return "False"

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
        current_app.logger.error("API Image Failed: %s"%e)

@api_bp.route('/channels/<string:page>')
def channels(page):
    try:
        current_app.logger.debug("Called Channels %s"%(page,))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select channels.*,count(*) as vidcount,max(PublishedAt) as lastvidtime from channels left outer join videos on channels.channelId = videos.channelId group by channelId order by channelname limit 40 offset %s;"%(page,))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Channel Failed: %s"%e)


@api_bp.route('/creator/<string:creator>/<string:page>')
def api_creator(creator,page):
    try:
        current_app.logger.debug("Called Creator %s %s"%(creator,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        offset = int(page) * 40  # fixed offset
        cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos where channelId = %s order by PublishedAt desc limit 40 offset %s;", (creator, offset))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Creator Failed: %s"%e)

@api_bp.route('/unwatched/<string:opt>/<string:page>')
def get_unwatched(opt,page):
    try:
        current_app.logger.debug("Called Unwatched %s %s"%(opt,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(opt == "PublishedAt"):
            cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos where watched = 0 order by PublishedAt desc limit 40 offset %s;"%(page,))
        elif(opt == "AddedAt"):
            cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos order watched = 0 by AddedAt desc limit 40 offset %s;"%(page,))
        else:
            cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos order watched = 0 by PublishedAt desc limit 40 offset %s;"%(page,))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Unwatched Failed: %s"%e)

@api_bp.route('/search/<string:searchtxt>/<string:page>')
def api_search(searchtxt,page):
    try:
        current_app.logger.debug("Called Creator %s %s"%(searchtxt,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos where lower(json) like lower('%s') order by PublishedAt desc limit 40 offset %s;"%("%"+searchtxt+"%",page))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Creator Failed: %s"%e)

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

@api_bp.route("/watch_status/<string:vid>")
def watch_status(vid):
    try:
        current_app.logger.debug('Called Watch Status: '+vid)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Select watched from videos where id = %s;",(vid,))
        data = cur.fetchone()[0]
        con.commit()
        cur.close()
        con.close()
        # return the results!
        return str(data)
    except Exception as e:
        current_app.logger.error("Watch Status Failed: %s"%e)

@api_bp.route("/checkdb")
def api_checkdb():
    return str(checkdb(current_app.logger))

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
    yt_active_ids = list(yt_dl_map.keys())
    data['active'] = [
        {'id': k, **yt_dl_map[k]} for k in yt_active_ids
    ]
    patreon_active_ids = list(patreon_dl_map.keys())
    data['active'].extend([{'id': k, **patreon_dl_map[k]} for k in patreon_active_ids])

    return json.dumps(data, indent=4, sort_keys=True, default=str)

@api_bp.route("/subscribe/<string:type>/<string:value>")
def api_subscribe(type,value):
    ret = False
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(type == "playlist"):
            current_app.logger.debug('Called Playlist Subscribe: '+value)
            cur.execute("Select * from playlists where playlistId = '%s'"%(value))
            if(not cur.rowcount):
                #Create Playlist Item
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
        # return the results!
        return str(ret)
    except Exception as e:
        current_app.logger.error("Playlist Subscribe Failed: %s"%e)

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
        # return the results!
        return "True"
    except Exception as e:
        current_app.logger.error("Playlist Unsubscribe Failed: %s"%e)

@api_bp.route('/playlists/<string:page>')
def playlists(page):
    try:
        current_app.logger.debug("Called Playlists %s"%(page,))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select * from playlists order by playlistName limit 40 offset %s;"%(page,))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Channel Failed: %s"%e)

@api_bp.route('/playlist/<string:playlist>/<string:page>')
def api_playlist(playlist,page):
    try:
        current_app.logger.debug("Called playlist %s %s"%(playlist,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select videos.*,playlistName from videos left outer join pl2vid on videos.id = pl2vid.videoId left outer join playlists on pl2vid.playlistId = playlists.playlistId where pl2vid.playlistId = '%s' order by PublishedAt desc limit 40 offset %s;"%(playlist,page))
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Playlist Failed: %s"%e)

@api_bp.route('/random')
def api_random():
    try:
        current_app.logger.debug("Called Random")
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select *,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos order by RAND() LIMIT 40;")
        return parse_response(cur,con)
    except Exception as e:
        current_app.logger.error("API Random Fail: %s"%e)

@api_bp.route("/find_next_previous/<string:vid>")
def api_fnp(vid):
    try:
        current_app.logger.debug("Called FNP")
        ret = find_next_previous(vid,current_app.logger)
        return json.dumps(ret, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API FNP Fail: %s"%e)

@api_bp.route("/delete/<string:vid>")
def api_delete(vid):
    try:
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select filepath from videos where id = %s",(vid,))
        filepath = cur.fetchone()[0]
        filepath = '/videos'+filepath
        try:
            os.remove(filepath)
        except Exception as e:
            current_app.logger.error("API Delete File Missing: %s"%e)
        cur.execute("Delete from videos where id = %s",(vid,))
        cur.execute("Delete from images where id = %s",(vid,))
        cur.execute("Insert ignore into IgnoreVid(id) values('%s')"%vid)
        con.commit()
        cur.close()
        current_app.logger.info("Deleted Video %s"%vid)
        return "True"
    except Exception as e:
        current_app.logger.error("API Delete: %s"%e)
        return "False"
    
@api_bp.route("/stats")
def api_stats():
    try:
        data = {}
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Select Youtuber,count(*) from vaulttube.videos Group By Youtuber Having count(*) > 1 and not Youtuber = '404'")
        data['countbyyoutuber'] = cur.fetchall()
        cur.execute("Select count(*) from vaulttube.videos Where not youtuber = '404'")
        data['totalcount'] = cur.fetchall()
        cur.execute("select videos.watched,count(*) from vaulttube.videos where not youtuber = '404' group by videos.watched")
        data['watched'] = cur.fetchall()
        cur.execute("select round(avg(TIME_TO_SEC(videos.length)),0) from vaulttube.videos where not youtuber = '404'")
        data['avg_length_seconds'] = cur.fetchall()
        cur.execute("select isDeleted,count(*) from vaulttube.videos where source='youtube' group by isDeleted ")
        data['deleted'] = cur.fetchall()
        cur.close()
        return json.dumps(data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Stats Error: %s"%e)
        return "False"
    
@api_bp.route('/transcode/<path:videopath>')
def transcode(videopath):
    # locate the source file
    source_path = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], videopath)
    if not os.path.isfile(source_path):
        abort(404)
    cmd = [
        "ffmpeg",
        "-i", source_path,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-movflags", "+frag_keyframe+empty_moov",
        "-f", "mp4",
        "pipe:1"
    ]

    # launch FFmpeg as a subprocess
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # generator that yields chunks of transcoded data
    def generate():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            process.stdout.close()
            process.wait()

    # return streaming response
    return Response(generate(), mimetype="video/mp4")

@api_bp.route("/download/patreon/<string:patreonchannelid>/<string:patreonurl>")
def api_patreon_download(patreonchannelid,patreonurl):
    try:
        url = "https://www.patreon.com/posts/"+patreonurl
        i = QueueObject(url,patreonchannelid,"patreon",0,"")
        current_app.config['queue'].put(i)
        return "True"
    except Exception as e:
        current_app.logger.error("API Download Failed: %s"%e)
        return "False"

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

        try:
            published_at = datetime.datetime.strptime(published_at_str, "%Y-%m-%d")
        except Exception:
            return "Invalid published date format", 400

        # Save to vault directory
        vault_dir = os.environ['VAULTTUBE_VAULTDIR']
        file_path = os.path.join(vault_dir,channel_id, f"{video_id}.mp4")

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