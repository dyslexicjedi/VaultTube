from flask import Blueprint,current_app,send_file,Response
import mariadb,json,io,math
from youtube import *

api_bp = Blueprint('api',__name__)

@api_bp.route('/latest/<string:opt>/<string:page>')
def latest(opt,page):
    try:
        current_app.logger.debug("Called Latest %s %s"%(opt,page))
        con = mariadb.connect(**current_app.config['dbconfig'])
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
        con = mariadb.connect(**current_app.config['dbconfig'])
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
        con = mariadb.connect(**current_app.config['dbconfig'])
        cur = con.cursor()
        cur.execute("select * from videos where id = %s;",(id,))
        # serialize results into JSON
        row_headers=[x[0] for x in cur.description]
        rv = cur.fetchall()
        json_data=[]
        for result in rv:
            json_data.append(dict(zip(row_headers,result)))
        json_data[0]['filepath'] = '/videos/'+json_data[0]['filepath']
        cur.execute("Select distinct playlist from playlists p where videoid = '%s'"%id)
        results = cur.fetchall()
        json_data[0]['playlists'] = [x[0] for x in results]
        con.close()
        # return the results!
        return json.dumps(json_data, indent=4, sort_keys=True, default=str)
    except Exception as e:
        current_app.logger.error("API Video Failed: %s"%e)

@api_bp.route("/watched/<string:id>")
def watched(id):
    try:
        current_app.logger.debug('Called Watched: '+id)
        con = mariadb.connect(**current_app.config['dbconfig'])
        cur = con.cursor()
        cur.execute("Update videos set watched = 1 where id = %s;",(id,))
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
        con = mariadb.connect(**current_app.config['dbconfig'])
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
        con = mariadb.connect(**current_app.config['dbconfig'])
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
        con = mariadb.connect(**current_app.config['dbconfig'])
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
        return single_download(url,current_app.logger,current_app.config['VaultDIR'])
    except Exception as e:
        current_app.logger.error("API Download Failed: %s"%e)

@api_bp.route("/stats/video/count")
def get_video_count():
    try:
        current_app.logger.debug('Called Get_Video_Count')
        con = mariadb.connect(**current_app.config['dbconfig'])
        cur = con.cursor()
        cur.execute("select count(*) from videos;")
        count = cur.fetchone()[0]
        con.close()
        return str(count)
    except Exception as e:
        current_app.logger.error("API Image Failed: %s"%e)