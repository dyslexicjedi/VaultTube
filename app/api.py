from flask import Blueprint,current_app,send_file,Response
import mariadb,json,io

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