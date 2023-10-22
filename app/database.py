import mariadb,requests,json,os

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
            cur.execute("create table playlists (`playlist` varchar(100),`videoid` varchar(100),PRIMARY KEY(`playlist`,`videoid`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Channels
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'channels' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Channels Table not created, creating...")
            cur.execute("create table channels (`channelid` varchar(100),`channelname` varchar(100),`json` longtext,`subscribed` int(11) DEFAULT 0,PRIMARY KEY(`channelid`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
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
                PRIMARY KEY (`id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
                        """)
        con.close()
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
        cur.execute("Select * FROM videos where id = '%s'"%id)
        if(cur.fetchone()):
            test = True
        check.close()
        return test
    except Exception as e:
        logger.error("Error during check_db_video: %s"%e)

def save_video(id,ret,img,logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        #Save Video Data
        sql = "Insert into videos(id,youtuber,json,filepath,PublishedAt,channelId,length) values(%s,%s,%s,%s,%s,%s,%s);"
        cur.execute(sql,(id,ret["Youtuber"],json.dumps(ret["Json"]),ret["Filepath"].replace(os.environ['VAULTTUBE_VAULTDIR'],""),ret['PublishedAt'],ret['channelId'],ret['length']))
        #Save Thumbnail
        sql = "Insert Ignore into images(id,image) values(%s,%s)"
        cur.execute(sql,(id,img))
        con.commit()
        con.close()
    except Exception as e:
        logger.error("Error during save_video: %s"%e)

def check_db_channel(id,logger):
    logger.debug("Checking db for channel id: %s",id)
    test = False
    try:
        check = get_connection(logger)
        cur = check.cursor()
        cur.execute("Select * FROM channels where channelid = '%s'"%id)
        if(not cur.fetchone()):
            test = False
        else:
            test = True
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
        con.close()
    except Exception as e:
        logger.error("Error during save_channel: %s"%e)

def get_active_subscriptions(logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("select channelid from channels where subscribed = 1;")
        rv = cur.fetchall()
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
        cur.execute("Select length FROM videos where id = '%s'"%id)
        data = cur.fetchone()[0]
        if(not data == "0"):
            test = True
        check.close()
        return test
    except Exception as e:
        logger.error("Error during check_db_video_length: %s"%e)

def update_length(id,length,logger):
    try:
        con = get_connection(logger)
        cur = con.cursor()
        cur.execute("Update videos set length = '%s' where id='%s';"%(length,id))
        con.commit()
        con.close()
    except Exception as e:
        logger.error("Error duing update_length: %s"%e)
