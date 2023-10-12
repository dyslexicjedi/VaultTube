import mariadb

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
                `uploaded` datetime DEFAULT NULL,
                `title` varchar(255) COLLATE utf8mb4_bin DEFAULT NULL,
                `length` varchar(45) COLLATE utf8mb4_bin DEFAULT NULL,
                `quality` varchar(45) COLLATE utf8mb4_bin DEFAULT NULL,
                `filesize` varchar(45) COLLATE utf8mb4_bin DEFAULT NULL,
                `tags` varchar(255) COLLATE utf8mb4_bin DEFAULT NULL,
                `description` mediumtext COLLATE utf8mb4_bin DEFAULT NULL,
                `filepath` varchar(2000) COLLATE utf8mb4_bin DEFAULT NULL,
                `watched` int(11) DEFAULT 0,
                PRIMARY KEY (`id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
                        """)
        con.close()
    except Exception as e:
        logger.error("Failed during table create: %s",e)