import mariadb,requests,json

#Perform database checks on startup
def checkdb(config,logger):
    logger.info("Startup Database Checks")
    #Create Database if doesn't exist
    try:
        dbcheck = mariadb.connect(**config._sections['Database'])
        dbcur = dbcheck.cursor()
        dbcur.execute("CREATE DATABASE %s;"%(config['Database']['database']))
    except Exception as e:
        pass
    #Create Tables if doesn't exist
    try:
        con = mariadb.connect(**config._sections['Database'])
        cur = con.cursor()
        #Images Table
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'images' LIMIT 1;"%(config['Database']['database']))
        if(not cur.fetchone()):
            logger.info("Images Table not created, creating...")
            cur.execute("CREATE TABLE `images` (`id` varchar(50) NOT NULL,`image` longblob DEFAULT NULL,PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        #Tag Table
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'tags' LIMIT 1;"%(config['Database']['database']))
        if(not cur.fetchone()):
            logger.info("Tags Table not created, creating...")
            cur.execute("CREATE TABLE `tags` (`id` varchar(25) NOT NULL,`tag` varchar(250) NOT NULL,PRIMARY KEY (`id`,`tag`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'playlists' LIMIT 1;"%(config['Database']['database']))
        if(not cur.fetchone()):
            logger.info("Playlists Table not created, creating...")
            cur.execute("create table playlists (`playlist` varchar(100),`videoid` varchar(100),PRIMARY KEY(`playlist`,`videoid`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'videos' LIMIT 1;"%(config['Database']['database']))
        if(not cur.fetchone()):
            logger.info("Tags Videos not created, creating...")
            cur.execute("""CREATE TABLE `videos` (
                `id` varchar(50) COLLATE utf8mb4_bin NOT NULL,
                `youtuber` varchar(255) COLLATE utf8mb4_bin DEFAULT NULL,
                `json` longtext COLLATE utf8mb4_bin DEFAULT NULL,
                `filepath` varchar(2000) COLLATE utf8mb4_bin DEFAULT NULL,
                `AddedAt` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                `PublishedAt` TIMESTAMP DEFAULT NULL,
                `watched` int(11) DEFAULT 0,
                PRIMARY KEY (`id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
                        """)
        con.close()
        return True
    except Exception as e:
        logger.error("Failed during table create: %s",e)
        return False

def check_db_video(id,config,logger):
    logger.debug("Checking db for id: %s",id)
    test = False
    try:
        check = mariadb.connect(**config._sections['Database'])
        cur = check.cursor()
        cur.execute("Select * FROM videos where id = '%s'"%id)
        if(not cur.fetchone()):
            test = False
        else:
            test = True
        check.close()
        return test
    except Exception as e:
        logger.error("Error during check_db_video: %s"%e)

def save_video(id,ret,img,config,logger):
    try:
        con = mariadb.connect(**config._sections['Database'])
        cur = con.cursor()
        #Save Video Data
        sql = "Insert into videos(id,youtuber,json,filepath,PublishedAt) values(%s,%s,%s,%s,%s);"
        cur.execute(sql,(id,ret["Youtuber"],json.dumps(ret["Json"]),ret["Filepath"].replace("/vault/Media/VaultTube",""),ret['PublishedAt']))
        #Save Thumbnail
        sql = "Insert Ignore into images(id,image) values(%s,%s)"
        cur.execute(sql,(id,img))
        con.commit()
        con.close()
    except Exception as e:
        logger.error("Error during save_video: %s"%e)