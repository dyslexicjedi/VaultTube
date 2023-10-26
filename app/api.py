from flask import Blueprint,current_app,send_file,Response
import mariadb,json,io,math
from youtube import get_dl_status,get_video,get_channel_video_list,get_cur_videoID,get_cur_videoTitle,get_playlist_info
from backend import process_channel
from database import checkdb,get_connection,insert_playlist

api_bp = Blueprint('api',__name__)

@api_bp.route('/latest/<string:opt>/<string:page>')
def latest(opt,page):
    try:
        current_app.logger.debug("Called Latest %s %s"%(opt,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(opt == "PublishedAt"):
            cur.execute("select * from videos order by PublishedAt desc limit 40 offset %s;"%(page,))
        elif(opt == "AddedAt"):
            cur.execute("select * from videos order by AddedAt desc limit 40 offset %s;"%(page,))
        else:
            cur.execute("select * from videos order by PublishedAt desc limit 40 offset %s;"%(page,))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Latest Failed: %s"%e)

@api_bp.route('/images/<string:id>')
def imgid(id):
    try:
        current_app.logger.debug('Called Image ID: '+id)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select image from images where id = '%s';"%(id,))
        img = cur.fetchone()[0]
        con.close()
        return send_file(io.BytesIO(img),mimetype='image/jpeg',as_attachment=True,download_name='%s.jpg' % id)
    except Exception as e:
        current_app.logger.error("API Image Failed: %s"%e)

@api_bp.route('/video/<string:id>')
def getVideo(id):
    try:
        current_app.logger.debug('Called Video ID: '+id)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select * from videos where id = %s;",(id,))
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
        cur.execute("select * from videos where not timestamp = 0 order by PublishedAt desc limit 40;")
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API List Resume Failed: %s"%e)

@api_bp.route("/download/single/<string:ytid>")
def api_download(ytid):
    try:
        url = "https://www.youtube.com/watch?v="+ytid
        current_app.config['queue'].put(url)
        return "True"
    except Exception as e:
        current_app.logger.error("API Download Failed: %s"%e)

@api_bp.route("/stats/video/count")
def get_video_count():
    try:
        current_app.logger.debug('Called Get_Video_Count')
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select count(*) from videos;")
        count = cur.fetchone()[0]
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
        if(cur.rowcount):
            current_app.logger.debug("Found %s results"%cur.rowcount)
            # serialize results into JSON
            row_headers=[x[0] for x in cur.description]
            rv = cur.fetchall()
            json_data=[]
            for result in rv:
                json_data.append(dict(zip(row_headers,result)))
        else:
            current_app.logger.debug("Found no data, empty json response")
            json_data=[]
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Channel Failed: %s"%e)


@api_bp.route('/creator/<string:creator>/<string:page>')
def api_creator(creator,page):
    try:
        current_app.logger.debug("Called Creator %s %s"%(creator,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select * from videos where channelId = '%s' order by PublishedAt desc limit 40 offset %s;"%(creator,page))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Creator Failed: %s"%e)

@api_bp.route("/subscribe/channel/<string:channelid>")
def subscribe(channelid):
    try:
        current_app.logger.debug('Called Subscribe: '+channelid)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Update channels set subscribed = 1 where channelid = %s;",(channelid,))
        con.commit()
        con.close()
        # return the results!
        return "True"
    except Exception as e:
        current_app.logger.error("Subscribe Failed: %s"%e)

@api_bp.route("/unsubscribe/channel/<string:channelid>")
def unsubscribe(channelid):
    try:
        current_app.logger.debug('Called Unsubscribe: '+channelid)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Update channels set subscribed = 0 where channelid = %s;",(channelid,))
        con.commit()
        con.close()
        # return the results!
        return "True"
    except Exception as e:
        current_app.logger.error("Unsubscribe Failed: %s"%e)

@api_bp.route('/unwatched/<string:opt>/<string:page>')
def get_unwatched(opt,page):
    try:
        current_app.logger.debug("Called Unwatched %s %s"%(opt,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        if(opt == "PublishedAt"):
            cur.execute("select * from videos where watched = 0 order by PublishedAt desc limit 40 offset %s;"%(page,))
        elif(opt == "AddedAt"):
            cur.execute("select * from videos order watched = 0 by AddedAt desc limit 40 offset %s;"%(page,))
        else:
            cur.execute("select * from videos order watched = 0 by PublishedAt desc limit 40 offset %s;"%(page,))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Unwatched Failed: %s"%e)

@api_bp.route('/search/<string:searchtxt>/<string:page>')
def api_search(searchtxt,page):
    try:
        current_app.logger.debug("Called Creator %s %s"%(searchtxt,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select * from videos where lower(json) like lower('%s') order by PublishedAt desc limit 40 offset %s;"%("%"+searchtxt+"%",page))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
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
    data['queue_value'] = current_app.config['queue']
    data['cur_id'] = get_cur_videoID()
    data['cur_title'] = get_cur_videoTitle()
    return json.dumps(data, indent=4, sort_keys=True, default=str)

@api_bp.route("/subscribe/playlist/<string:playlistid>")
def subscribe_playlist(playlistid):
    ret = False
    try:
        current_app.logger.debug('Called Playlist Subscribe: '+playlistid)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Select * from playlists where playlistId = '%s'"%(playlistid))
        if(not cur.rowcount):
            #Create Playlist Item
            plinfo = get_playlist_info(playlistid,current_app.logger)
            insert_playlist(plinfo,current_app.logger)
            ret = True
        else:
            cur.execute("Update playlists set subscribed = 1 where playlistId = %s;",(playlistid,))
            ret = True
        con.commit()
        con.close()
        # return the results!
        return str(ret)
    except Exception as e:
        current_app.logger.error("Playlist Subscribe Failed: %s"%e)

@api_bp.route("/unsubscribe/playlist/<string:playlistid>")
def unsubscribe_playlist(playlistid):
    try:
        current_app.logger.debug('Called Playlist Unsubscribe: '+playlistid)
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("Update playlists set subscribed = 0 where playlistId = %s;",(playlistid,))
        con.commit()
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
        if(cur.rowcount):
            current_app.logger.debug("Found %s results"%cur.rowcount)
            # serialize results into JSON
            row_headers=[x[0] for x in cur.description]
            rv = cur.fetchall()
            json_data=[]
            for result in rv:
                json_data.append(dict(zip(row_headers,result)))
        else:
            current_app.logger.debug("Found no data, empty json response")
            json_data=[]
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Channel Failed: %s"%e)

@api_bp.route('/playlist/<string:playlist>/<string:page>')
def api_playlist(playlist,page):
    try:
        current_app.logger.debug("Called playlist %s %s"%(playlist,page))
        con = get_connection(current_app.logger)
        cur = con.cursor()
        cur.execute("select videos.*,playlistName from videos left outer join pl2vid on videos.id = pl2vid.videoId left outer join playlists on pl2vid.playlistId = playlists.playlistId where pl2vid.playlistId = '%s' order by PublishedAt desc limit 40 offset %s;"%(playlist,page))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Playlist Failed: %s"%e)