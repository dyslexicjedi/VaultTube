import mariadb,requests,json,os
from difflib import SequenceMatcher

#Perform database checks on startup
def checkdb(logger):
    logger.info("Startup Database Checks")
    try:
        logger.info("Testing connection to database")
        dbcheck = get_connection(logger)
        dbcheck.close()
    except Exception as e:
        logger.error("Unable to connect to database: %s"%e)

    #Create Database if doesn't exist
    try:
        dbcheck = get_connection(logger)
        dbcur = dbcheck.cursor()
        dbcur.execute("CREATE DATABASE %s;"%(os.environ['VAULTTUBE_DBNAME']))
        dbcur.close()
    except Exception as e:
        logger.debug("Database already exists")
        pass
    #Create Tables if doesn't exist
    try:
        con = get_connection(logger)
        cur = con.cursor()
        #Images Table
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'images' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Images Table not created, creating...")
            cur.execute("CREATE TABLE `images` (`id` varchar(50) NOT NULL,`image` longblob DEFAULT NULL,PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Tag Table
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'tags' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Tags Table not created, creating...")
            cur.execute("CREATE TABLE `tags` (`id` varchar(25) NOT NULL,`tag` varchar(250) NOT NULL,PRIMARY KEY (`id`,`tag`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Playlists
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'playlists' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Playlists Table not created, creating...")
            cur.execute("create table playlists (`playlistId` varchar(100),`playlistName` varchar(100),`channelId` varchar(100),`json` longtext,`subscribed` int(11),PRIMARY KEY(`playlistId`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Channels
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'channels' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Channels Table not created, creating...")
            cur.execute("create table channels (`channelid` varchar(100),`channelname` varchar(100),`json` longtext,`subscribed` int(11) DEFAULT 0,PRIMARY KEY(`channelid`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Pl2VID
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'pl2vid' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("pl2vid Table not created, creating...")
            cur.execute("create table pl2vid (`playlistId` varchar(100),`videoId` varchar(100),PRIMARY KEY(`playlistId`,`videoId`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Videos
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'videos' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Tags Videos not created, creating...")
            cur.execute("""CREATE TABLE `videos` (
                `id` varchar(50) COLLATE utf8mb4_bin NOT NULL,
                `youtuber` varchar(255) COLLATE utf8mb4_bin DEFAULT NULL,
                `channelId` varchar(255) DEFAULT NULL,
                `json` longtext COLLATE utf8mb4_bin DEFAULT NULL,
                `filepath` varchar(2000) COLLATE utf8mb4_bin DEFAULT NULL,
                `AddedAt` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                `PublishedAt` TIMESTAMP DEFAULT NULL,
                `watched` int(11) DEFAULT 0,
                `timestamp` varchar(50) DEFAULT 0,
                `length` varchar(50) DEFAULT 0,
                `lastScanned` datetime DEFAULT CURRENT_TIMESTAMP,
                `isDeleted` int(11) DEFAULT 0,
                `source` varchar(100) DEFAULT 'youtube',
                `title` varchar(2000) DEFAULT NULL,
                `description` text DEFAULT NULL,
                PRIMARY KEY (`id`),
                FULLTEXT KEY `ft_search` (`title`, `description`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
                        """)
        # Migrations for existing videos table
        # Add description column if missing
        cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = 'videos' AND column_name = 'description'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding description column to videos table...")
            cur.execute("ALTER TABLE videos ADD COLUMN `description` text DEFAULT NULL;")
        # Add FULLTEXT index if missing
        cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = %s AND table_name = 'videos' AND index_name = 'ft_search'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding FULLTEXT index ft_search to videos table...")
            cur.execute("ALTER TABLE videos ADD FULLTEXT KEY `ft_search` (`title`, `description`);")
        # Backfill description from json blob for existing YouTube records
        cur.execute("UPDATE videos SET description = JSON_UNQUOTE(JSON_EXTRACT(json, '$.items[0].snippet.description')) WHERE description IS NULL AND source = 'youtube' AND JSON_VALID(json) AND JSON_EXTRACT(json, '$.items[0].snippet.description') IS NOT NULL;")
        # Add indexes for advanced filtering
        cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = %s AND table_name = 'videos' AND index_name = 'idx_channelId'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding index idx_channelId to videos table...")
            cur.execute("ALTER TABLE videos ADD INDEX `idx_channelId` (`channelId`);")
        cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = %s AND table_name = 'videos' AND index_name = 'idx_PublishedAt'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding index idx_PublishedAt to videos table...")
            cur.execute("ALTER TABLE videos ADD INDEX `idx_PublishedAt` (`PublishedAt`);")
        cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = %s AND table_name = 'videos' AND index_name = 'idx_AddedAt'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding index idx_AddedAt to videos table...")
            cur.execute("ALTER TABLE videos ADD INDEX `idx_AddedAt` (`AddedAt`);")
        #Ignore
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'IgnoreVid' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("IgnoreVid Table not created, creating...")
            cur.execute("create table IgnoreVid (`id` varchar(50) COLLATE utf8mb4_bin NOT NULL,PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Queue
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'queue' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Queue Table not created, creating...")
            cur.execute("""CREATE TABLE `queue` (
                `id` int(11) NOT NULL AUTO_INCREMENT,
                `url` varchar(500) NOT NULL,
                `source` varchar(50) DEFAULT 'youtube',
                `channel_id` varchar(100) DEFAULT '',
                `unsave` tinyint(1) DEFAULT 0,
                `status` varchar(20) DEFAULT 'pending',
                `attempts` int(11) DEFAULT 0,
                `last_error` text DEFAULT NULL,
                `created_at` timestamp DEFAULT CURRENT_TIMESTAMP,
                `updated_at` timestamp DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`),
                INDEX `idx_status` (`status`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        #Download Errors
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'download_errors' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Download Errors Table not created, creating...")
            cur.execute("""CREATE TABLE `download_errors` (
                `id` int(11) NOT NULL AUTO_INCREMENT,
                `url` varchar(500) DEFAULT NULL,
                `error_type` varchar(50) DEFAULT NULL,
                `error_message` text DEFAULT NULL,
                `created_at` timestamp DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
            logger.info("Download Errors table created")
        cur.close()
        con.close()
        cleanup_old_errors(logger, 7)
        cleanup_old_queue_rows(logger, 7)
        return True
    except Exception as e:
        logger.error("Failed during table create: %s",e)
        return False

def check_db_video(id,logger):
    logger.debug("Checking db for video id: %s",id)
    test = False
    try:
        check = get_connection(logger)
        cur = check.cursor()
        cur.execute("Select * FROM videos where id = ?", (id,))
        if(cur.fetchone()):
            test = True
        cur.execute("Select * FROM IgnoreVid where id = ?",(id,))
        if(cur.fetchone()):
            test = True
        cur.close()
        check.close()
        return test
    except Exception as e:
        logger.error("Error during check_db_video: %s"%e)

def save_video(id,ret,img,logger,source='youtube'):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        #Save Video Data
        sql = "Insert Ignore into videos(id,youtuber,json,filepath,PublishedAt,channelId,length,source,title,description) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);"
        cur.execute(sql,(id,ret["Youtuber"],json.dumps(ret["Json"]),ret["Filepath"].replace(os.environ['VAULTTUBE_VAULTDIR'],""),ret['PublishedAt'],ret['channelId'],ret['length'],source,ret['title'],ret.get('description','')))
        #Save Thumbnail
        sql = "Insert Ignore into images(id,image) values(%s,%s)"
        cur.execute(sql,(id,img))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during save_video: %s"%e)

def check_db_channel(id,logger):
    logger.debug("Checking db for channel id: %s",id)
    test = False
    try:
        check = get_connection(logger)
        cur = check.cursor()
        cur.execute("Select * FROM channels where channelid = %s",(id,))
        if(not cur.fetchone()):
            test = False
        else:
            test = True
        cur.close()
        check.close()
        return test
    except Exception as e:
        logger.error("Error during check_db_channel: %s"%e)

def save_channel(channelid,channelname,jdata,logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        #Save Video Data
        sql = "Insert into channels(channelid,channelname,json) values(%s,%s,%s);"
        cur.execute(sql,(channelid,channelname,json.dumps(jdata)))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during save_channel: %s"%e)

def get_active_subscriptions(logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("select channelid from channels where subscribed = 1;")
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during subscription poll")

def get_connection(logger):
    try:
        con = mariadb.connect(host=os.environ['VAULTTUBE_DBHOST'],user=os.environ['VAULTTUBE_DBUSER'],password=os.environ['VAULTTUBE_DBPASS'],database=os.environ['VAULTTUBE_DBNAME'],autocommit=True,port=int(os.environ['VAULTTUBE_DBPORT']))
        return con
    except Exception as e:
        logger.error("Unable to get connection: %s"%e)

def check_db_video_length(id,logger):
    logger.debug("Checking db for video length: %s",id)
    test = False
    try:
        check = get_connection(logger)
        cur = check.cursor()
        cur.execute("Select length FROM videos where id = %s",(id,))
        data = cur.fetchone()[0]
        if(not data == "0"):
            test = True
        cur.close()
        check.close()
        return test
    except Exception as e:
        logger.error("Error during check_db_video_length: %s"%e)

def update_length(id,length,logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("Update videos set length = %s where id=%s;",(length,id))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error duing update_length: %s"%e)

def insert_playlist(plinfo,logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        #Save Video Data
        sql = "Insert into playlists(playlistId,playlistName,channelId,json,subscribed) values(%s,%s,%s,%s,%s);"
        cur.execute(sql,(plinfo['items'][0]['id'],plinfo['items'][0]['snippet']['title'],plinfo['items'][0]['snippet']['channelId'],json.dumps(plinfo),0))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during insert_playlist: %s"%e)

def get_active_playlist_subs(logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("select playlistId from playlists where subscribed = 1;")
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during playlist subscription poll")

def check_pl2vid_info(pl,vid,logger):
    logger.debug("Checking pl2vid for playlists %s and video %s"%(pl,vid))
    test = False
    try:
        check = get_connection(logger)
        cur = check.cursor()
        cur.execute("Select * FROM pl2vid where playlistId = %s and videoId = %s",(pl,vid))
        if(cur.fetchone()):
            test = True
        cur.close()
        check.close()
        return test
    except Exception as e:
        logger.error("Error during check_pl2vid_info: %s"%e)

def insert_pl2vid_info(pl,vid,logger):
    con = None
    cur = None
    try:
        con = get_connection(logger)
        if con is None:
            logger.error("Unable to get connection for insert_pl2vid_info")
            return
        cur = con.cursor()
        sql = "Insert into pl2vid(playlistId,videoId) values(%s,%s);"
        cur.execute(sql,(pl,vid))
        con.commit()
    except Exception as e:
        logger.error("Error during insert_pl2vid_info: %s"%e)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

def find_next_previous(vid,logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        #Get Video
        sql = "Select youtuber,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos where id = %s;"
        cur.execute(sql,(vid,))
        cur_data = cur.fetchone()
        creator = cur_data[0]
        title = cur_data[1]
        #Get Other Videos by Same Creator
        sql = "Select id,JSON_EXTRACT(json,'$.items[0].snippet.title') as title from videos where youtuber = %s order by PublishedAt desc;"
        cur.execute(sql,(creator,))
        np_data = cur.fetchall()
        l = []
        ret = {}
        for index,row in enumerate(np_data):
            np_title = row[1]
            s = SequenceMatcher(None,title,np_title)
            if(s.ratio() > 0.9):
                l.append(row)
        for index,row in enumerate(l):
            np_title = row[1]
            if(title == np_title):
                if(len(l) > index+1):
                    ret['PreviousID'] = l[index+1][0]
                    ret['PreviousTitle'] = l[index+1][1].replace('"','')
                if(index-1 > -1):
                    ret['NextID'] = l[index-1][0]
                    ret['NextTitle'] = l[index-1][1].replace('"','')
        con.commit()
        cur.close()
        con.close()
        return ret
    except Exception as e:
        logger.error("Error during find_next_previous: %s"%e)

def insert_not_found(vid,logger):
    con = get_connection(logger)
    cur = con.cursor()
    sql = "insert into videos(id,youtuber,channelId,json,filepath,watched,timestamp,length) values(%s,'404','404','404','404',1,0,'0');"
    cur.execute(sql,(vid,))
    con.commit()
    cur.close()
    con.close()

def get_oldest_video_check(logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("Select * from videos where source = 'youtube' order by lastScanned asc limit 1000;")
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during oldest video check")

def update_video_deleted(vid,isDeleted,logger):
    con = get_connection(logger)
    cur = con.cursor()
    sql = "update videos set isDeleted=%s,lastScanned=now()  where id=%s;"
    cur.execute(sql,(isDeleted,vid))
    con.commit()
    cur.close()
    con.close()
    logger.info("Updated video deleted status %s for vid %s",isDeleted,vid)

def insert_download_error(url, error_type, error_msg, logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        sql = "INSERT INTO download_errors(url, error_type, error_message) VALUES(%s, %s, %s)"
        cur.execute(sql, (url, error_type, error_msg))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during insert_download_error: %s", e)

def get_download_errors(logger, limit=50):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        sql = "SELECT id, url, error_type, error_message, created_at FROM download_errors ORDER BY created_at DESC LIMIT %s"
        cur.execute(sql, (limit,))
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during get_download_errors: %s", e)
        return []

def clear_download_errors(logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("DELETE FROM download_errors")
        con.commit()
        cur.close()
        con.close()
        logger.info("Download errors cleared")
    except Exception as e:
        logger.error("Error during clear_download_errors: %s", e)

def delete_download_error(error_id, logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("DELETE FROM download_errors WHERE id = %s", (error_id,))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during delete_download_error: %s", e)

def insert_queue_item(qo, logger):
    """Persist a queued download. Returns the row id, or None on failure."""
    try:
        con = get_connection(logger)
        cur = con.cursor()
        sql = "INSERT INTO queue(url, source, channel_id, unsave, status, attempts) VALUES(%s, %s, %s, %s, 'pending', %s)"
        cur.execute(sql, (qo.url, qo.source, qo.channel_id, 1 if qo.unsave else 0, qo.attempts))
        rowid = cur.lastrowid
        con.commit()
        cur.close()
        con.close()
        return rowid
    except Exception as e:
        logger.error("Error during insert_queue_item: %s" % e)
        return None

def update_queue_status(rowid, status, logger, error=None, attempts=None):
    if rowid is None:
        return
    try:
        con = get_connection(logger)
        cur = con.cursor()
        if attempts is not None:
            cur.execute("UPDATE queue SET status=%s, last_error=%s, attempts=%s WHERE id=%s", (status, error, attempts, rowid))
        else:
            cur.execute("UPDATE queue SET status=%s, last_error=%s WHERE id=%s", (status, error, rowid))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during update_queue_status: %s" % e)

def queue_has_url(url, logger):
    """True if the URL is already queued or downloading."""
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("SELECT 1 FROM queue WHERE url = %s AND status IN ('pending','downloading') LIMIT 1", (url,))
        rv = cur.fetchone() is not None
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during queue_has_url: %s" % e)
        return False

def get_resumable_queue_items(logger):
    """Rows that were pending or mid-download when the app last stopped."""
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("SELECT id, url, source, channel_id, unsave, attempts FROM queue WHERE status IN ('pending','downloading') ORDER BY id")
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during get_resumable_queue_items: %s" % e)
        return []

def cleanup_old_queue_rows(logger, days=7):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("DELETE FROM queue WHERE status IN ('done','failed') AND updated_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during cleanup_old_queue_rows: %s" % e)

def cleanup_old_errors(logger, days=7):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("DELETE FROM download_errors WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during cleanup_old_errors: %s", e)