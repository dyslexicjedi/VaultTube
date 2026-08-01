import mariadb,requests,json,os,threading,logging
from difflib import SequenceMatcher
from vault_paths import vault_relative_path

logger = logging.getLogger('database')

#Perform database checks on startup
def checkdb():
    logger.info("Startup Database Checks")
    try:
        logger.info("Testing connection to database")
        dbcheck = get_connection()
        dbcheck.close()
    except Exception as e:
        logger.error("Unable to connect to database: %s"%e)

    #Create Database if doesn't exist
    try:
        dbcheck = get_connection()
        dbcur = dbcheck.cursor()
        dbcur.execute("CREATE DATABASE %s;"%(os.environ['VAULTTUBE_DBNAME']))
        dbcur.close()
    except Exception as e:
        logger.debug("Database already exists")
        pass
    #Create Tables if doesn't exist
    try:
        con = get_connection()
        cur = con.cursor()
        #Images Table
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = '%s' AND table_name = 'images' LIMIT 1;"%(os.environ['VAULTTUBE_DBNAME']))
        if(not cur.fetchone()):
            logger.info("Images Table not created, creating...")
            cur.execute("CREATE TABLE `images` (`id` varchar(50) NOT NULL,`image` longblob DEFAULT NULL,PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
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
                `channel_name` varchar(255) COLLATE utf8mb4_bin DEFAULT NULL,
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
        # Codec/container metadata for direct-play vs transcode routing
        cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = 'videos' AND column_name = 'vcodec'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding vcodec column to videos table...")
            cur.execute("ALTER TABLE videos ADD COLUMN `vcodec` varchar(50) DEFAULT NULL;")
        cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = 'videos' AND column_name = 'acodec'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding acodec column to videos table...")
            cur.execute("ALTER TABLE videos ADD COLUMN `acodec` varchar(50) DEFAULT NULL;")
        cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = 'videos' AND column_name = 'container'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding container column to videos table...")
            cur.execute("ALTER TABLE videos ADD COLUMN `container` varchar(50) DEFAULT NULL;")
        # File size in bytes; backfilled lazily during vault scans, set on
        # insert for new downloads/uploads. NULL = not yet measured.
        cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = 'videos' AND column_name = 'filesize'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] == 0:
            logger.info("Adding filesize column to videos table...")
            cur.execute("ALTER TABLE videos ADD COLUMN `filesize` bigint DEFAULT NULL;")
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
        # Rename legacy `youtuber` column to `channel_name`. The name is
        # YouTube-specific and doesn't fit Patreon/Reddit content. Guarded so
        # it runs once on existing databases and is a no-op on fresh installs
        # (where CREATE TABLE already uses the new name). Must run before the
        # tombstone block below so both see a consistent column name.
        cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = 'videos' AND column_name = 'youtuber'", (os.environ['VAULTTUBE_DBNAME'],))
        if cur.fetchone()[0] > 0:
            logger.info("Renaming videos.youtuber column to channel_name...")
            cur.execute("ALTER TABLE videos RENAME COLUMN `youtuber` TO `channel_name`")
        # Migrate legacy not-found tombstones (channel_name='404' placeholder rows)
        # into IgnoreVid; the videos table holds only real content
        cur.execute("SELECT COUNT(*) FROM videos WHERE channel_name='404'")
        tombstones = cur.fetchone()[0]
        if tombstones:
            logger.info("Migrating %s not-found tombstone rows from videos to IgnoreVid...", tombstones)
            cur.execute("INSERT IGNORE INTO IgnoreVid(id) SELECT id FROM videos WHERE channel_name='404'")
            cur.execute("DELETE images FROM images JOIN videos ON images.id = videos.id WHERE videos.channel_name='404'")
            cur.execute("DELETE FROM videos WHERE channel_name='404'")
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
        queue_columns = {
            'priority': "int NOT NULL DEFAULT 0",
            'origin': "varchar(30) NOT NULL DEFAULT 'ordinary'",
            'rescue_session_id': "char(36) DEFAULT NULL",
            'target_item_id': "varchar(255) COLLATE utf8mb4_bin DEFAULT NULL",
        }
        for column, definition in queue_columns.items():
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='queue' AND column_name=%s",
                (os.environ['VAULTTUBE_DBNAME'], column),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "ALTER TABLE queue ADD COLUMN `%s` %s" % (column, definition)
                )
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema=%s AND table_name='queue' "
            "AND index_name='idx_queue_priority'",
            (os.environ['VAULTTUBE_DBNAME'],),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE queue ADD INDEX `idx_queue_priority` "
                "(`status`,`priority`,`id`)"
            )
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
        # Sentinel Phase 1: durable scan evidence, current availability state,
        # and an append-only event ledger.
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = %s AND table_name = 'sentinel_scan_runs' LIMIT 1;", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel scan runs table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_scan_runs` (
                `id` bigint NOT NULL AUTO_INCREMENT,
                `provider` varchar(50) NOT NULL,
                `scan_type` varchar(50) NOT NULL,
                `source_type` varchar(50) NOT NULL,
                `source_id` varchar(255) NOT NULL,
                `status` varchar(20) NOT NULL DEFAULT 'running',
                `started_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `completed_at` timestamp NULL DEFAULT NULL,
                `items_seen` int NOT NULL DEFAULT 0,
                `requests_made` int NOT NULL DEFAULT 0,
                `error_message` text DEFAULT NULL,
                PRIMARY KEY (`id`),
                INDEX `idx_sentinel_scan_status` (`status`),
                INDEX `idx_sentinel_scan_source` (`provider`,`source_type`,`source_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = %s AND table_name = 'sentinel_video_state' LIMIT 1;", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel video state table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_video_state` (
                `video_id` varchar(50) COLLATE utf8mb4_bin NOT NULL,
                `provider` varchar(50) NOT NULL DEFAULT 'youtube',
                `state` varchar(40) NOT NULL DEFAULT 'available',
                `consecutive_negative_checks` int NOT NULL DEFAULT 0,
                `last_scan_id` bigint DEFAULT NULL,
                `last_checked_at` timestamp NULL DEFAULT NULL,
                `last_positive_at` timestamp NULL DEFAULT NULL,
                `last_negative_at` timestamp NULL DEFAULT NULL,
                PRIMARY KEY (`video_id`),
                INDEX `idx_sentinel_video_state` (`state`),
                CONSTRAINT `fk_sentinel_state_scan` FOREIGN KEY (`last_scan_id`)
                    REFERENCES `sentinel_scan_runs` (`id`) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema = %s AND table_name = 'sentinel_events' LIMIT 1;", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel events table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_events` (
                `id` bigint NOT NULL AUTO_INCREMENT,
                `provider` varchar(50) NOT NULL,
                `entity_type` varchar(50) NOT NULL,
                `entity_id` varchar(255) COLLATE utf8mb4_bin NOT NULL,
                `event_type` varchar(80) NOT NULL,
                `from_state` varchar(40) DEFAULT NULL,
                `to_state` varchar(40) DEFAULT NULL,
                `observed_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `scan_run_id` bigint DEFAULT NULL,
                `evidence_json` longtext DEFAULT NULL,
                `dedupe_key` varchar(255) COLLATE utf8mb4_bin NOT NULL,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uq_sentinel_event_dedupe` (`dedupe_key`),
                INDEX `idx_sentinel_event_entity` (`provider`,`entity_type`,`entity_id`),
                INDEX `idx_sentinel_event_observed` (`observed_at`),
                CONSTRAINT `fk_sentinel_event_scan` FOREIGN KEY (`scan_run_id`)
                    REFERENCES `sentinel_scan_runs` (`id`) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        # Phase 3 inventory events must retain their source even when the
        # remote video has never been downloaded into videos.
        for column, definition in [
            ('source_type', "varchar(50) DEFAULT NULL"),
            ('source_id', "varchar(255) DEFAULT NULL"),
        ]:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='sentinel_events' "
                "AND column_name=%s",
                (os.environ['VAULTTUBE_DBNAME'], column),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "ALTER TABLE sentinel_events ADD COLUMN `%s` %s" %
                    (column, definition)
                )
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema=%s AND table_name='sentinel_events' "
            "AND index_name='idx_sentinel_event_source'",
            (os.environ['VAULTTUBE_DBNAME'],),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE sentinel_events ADD INDEX "
                "`idx_sentinel_event_source` (`provider`,`source_type`,`source_id`)"
            )
        cur.execute(
            "UPDATE sentinel_events e JOIN videos v ON v.id=e.entity_id "
            "SET e.source_type='channel', e.source_id=v.channelId "
            "WHERE e.source_id IS NULL AND v.channelId IS NOT NULL"
        )
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema=%s AND table_name='sentinel_inventory_runs' LIMIT 1", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel inventory runs table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_inventory_runs` (
                `id` bigint NOT NULL AUTO_INCREMENT,
                `provider` varchar(50) NOT NULL DEFAULT 'youtube',
                `source_type` varchar(50) NOT NULL,
                `source_id` varchar(255) NOT NULL,
                `remote_collection_id` varchar(255) NOT NULL,
                `status` varchar(20) NOT NULL DEFAULT 'running',
                `continuation_token` varchar(500) DEFAULT NULL,
                `continuation_history_json` longtext DEFAULT NULL,
                `pages_fetched` int NOT NULL DEFAULT 0,
                `items_seen` int NOT NULL DEFAULT 0,
                `requests_made` int NOT NULL DEFAULT 0,
                `started_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `completed_at` timestamp NULL DEFAULT NULL,
                `error_message` text DEFAULT NULL,
                `failure_class` varchar(40) DEFAULT NULL,
                PRIMARY KEY (`id`),
                INDEX `idx_inventory_run_source` (`provider`,`source_type`,`source_id`,`status`),
                INDEX `idx_inventory_run_completed` (`completed_at`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='sentinel_inventory_runs' "
            "AND column_name='failure_class'",
            (os.environ['VAULTTUBE_DBNAME'],),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE sentinel_inventory_runs ADD COLUMN "
                "`failure_class` varchar(40) DEFAULT NULL"
            )
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema=%s AND table_name='sentinel_inventory' LIMIT 1", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel inventory table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_inventory` (
                `scan_run_id` bigint NOT NULL,
                `provider` varchar(50) NOT NULL DEFAULT 'youtube',
                `source_type` varchar(50) NOT NULL,
                `source_id` varchar(255) NOT NULL,
                `entity_id` varchar(255) COLLATE utf8mb4_bin NOT NULL,
                `position` int DEFAULT NULL,
                `remote_published_at` timestamp NULL DEFAULT NULL,
                `observed_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`scan_run_id`,`entity_id`),
                INDEX `idx_inventory_source_entity` (`provider`,`source_type`,`source_id`,`entity_id`),
                CONSTRAINT `fk_inventory_run` FOREIGN KEY (`scan_run_id`)
                    REFERENCES `sentinel_inventory_runs` (`id`) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='sentinel_inventory' "
            "AND column_name='remote_published_at'",
            (os.environ['VAULTTUBE_DBNAME'],),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE sentinel_inventory ADD COLUMN "
                "`remote_published_at` timestamp NULL DEFAULT NULL"
            )
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema=%s AND table_name='sentinel_sources' LIMIT 1", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel sources table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_sources` (
                `provider` varchar(50) NOT NULL DEFAULT 'youtube',
                `source_type` varchar(50) NOT NULL,
                `source_id` varchar(255) NOT NULL,
                `availability_state` varchar(40) NOT NULL DEFAULT 'unknown',
                `consecutive_terminal_checks` int NOT NULL DEFAULT 0,
                `last_observation_scan_id` bigint DEFAULT NULL,
                `last_observed_at` timestamp NULL DEFAULT NULL,
                `risk_score` int NOT NULL DEFAULT 0,
                `risk_level` varchar(20) NOT NULL DEFAULT 'low',
                `risk_reasons_json` longtext DEFAULT NULL,
                `risk_facts_json` longtext DEFAULT NULL,
                `risk_calculated_at` timestamp NULL DEFAULT NULL,
                PRIMARY KEY (`provider`,`source_type`,`source_id`),
                INDEX `idx_sentinel_source_risk` (`risk_score`,`risk_level`),
                CONSTRAINT `fk_sentinel_source_observation_scan`
                    FOREIGN KEY (`last_observation_scan_id`)
                    REFERENCES `sentinel_scan_runs` (`id`) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema=%s AND table_name='sentinel_rescue_previews' LIMIT 1", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel rescue previews table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_rescue_previews` (
                `id` char(36) NOT NULL,
                `provider` varchar(50) NOT NULL DEFAULT 'youtube',
                `source_type` varchar(50) NOT NULL,
                `source_id` varchar(255) NOT NULL,
                `inventory_run_id` bigint NOT NULL,
                `request_json` longtext NOT NULL,
                `summary_json` longtext NOT NULL,
                `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`),
                INDEX `idx_rescue_preview_source` (`provider`,`source_type`,`source_id`,`created_at`),
                CONSTRAINT `fk_rescue_preview_inventory_run`
                    FOREIGN KEY (`inventory_run_id`)
                    REFERENCES `sentinel_inventory_runs` (`id`) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema=%s AND table_name='sentinel_rescue_preview_items' LIMIT 1", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel rescue preview items table not created, creating...")
            cur.execute("""CREATE TABLE `sentinel_rescue_preview_items` (
                `preview_id` char(36) NOT NULL,
                `entity_id` varchar(255) COLLATE utf8mb4_bin NOT NULL,
                `rank_order` int NOT NULL,
                `remote_published_at` timestamp NULL DEFAULT NULL,
                `estimated_duration_seconds` int DEFAULT NULL,
                `estimated_bytes_low` bigint NOT NULL,
                `estimated_bytes_high` bigint NOT NULL,
                `estimate_basis` varchar(80) NOT NULL,
                PRIMARY KEY (`preview_id`,`entity_id`),
                INDEX `idx_rescue_preview_rank` (`preview_id`,`rank_order`),
                CONSTRAINT `fk_rescue_preview_item_preview`
                    FOREIGN KEY (`preview_id`)
                    REFERENCES `sentinel_rescue_previews` (`id`) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema=%s AND table_name='rescue_sessions' LIMIT 1", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel rescue sessions table not created, creating...")
            cur.execute("""CREATE TABLE `rescue_sessions` (
                `id` char(36) NOT NULL,
                `preview_id` char(36) NOT NULL,
                `provider` varchar(50) NOT NULL DEFAULT 'youtube',
                `source_type` varchar(50) NOT NULL,
                `source_id` varchar(255) NOT NULL,
                `status` varchar(20) NOT NULL DEFAULT 'active',
                `max_videos` int NOT NULL,
                `max_bytes` bigint DEFAULT NULL,
                `selected_count` int NOT NULL,
                `estimated_bytes_low` bigint NOT NULL,
                `estimated_bytes_high` bigint NOT NULL,
                `download_delay_seconds` int NOT NULL DEFAULT 30,
                `stop_failure_threshold` int NOT NULL DEFAULT 3,
                `consecutive_blocking_failures` int NOT NULL DEFAULT 0,
                `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `started_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `completed_at` timestamp NULL DEFAULT NULL,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uq_rescue_session_preview` (`preview_id`),
                INDEX `idx_rescue_session_status` (`status`,`created_at`),
                CONSTRAINT `fk_rescue_session_preview` FOREIGN KEY (`preview_id`)
                    REFERENCES `sentinel_rescue_previews` (`id`) ON DELETE RESTRICT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        cur.execute("SELECT * FROM information_schema.tables WHERE table_schema=%s AND table_name='rescue_items' LIMIT 1", (os.environ['VAULTTUBE_DBNAME'],))
        if not cur.fetchone():
            logger.info("Sentinel rescue items table not created, creating...")
            cur.execute("""CREATE TABLE `rescue_items` (
                `session_id` char(36) NOT NULL,
                `entity_id` varchar(255) COLLATE utf8mb4_bin NOT NULL,
                `rank_order` int NOT NULL,
                `queue_id` int DEFAULT NULL,
                `status` varchar(20) NOT NULL DEFAULT 'pending',
                `estimated_bytes_low` bigint NOT NULL,
                `estimated_bytes_high` bigint NOT NULL,
                `last_error` text DEFAULT NULL,
                `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`session_id`,`entity_id`),
                INDEX `idx_rescue_item_status` (`session_id`,`status`,`rank_order`),
                INDEX `idx_rescue_item_queue` (`queue_id`),
                CONSTRAINT `fk_rescue_item_session` FOREIGN KEY (`session_id`)
                    REFERENCES `rescue_sessions` (`id`) ON DELETE CASCADE,
                CONSTRAINT `fk_rescue_item_queue` FOREIGN KEY (`queue_id`)
                    REFERENCES `queue` (`id`) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        # Import only legacy unavailable rows. Available rows are initialized
        # lazily on their next successful check. The evidence explicitly says
        # that observed_at is the import time, not the disappearance time.
        cur.execute("""INSERT IGNORE INTO sentinel_video_state
            (video_id, provider, state, consecutive_negative_checks,
             last_checked_at, last_negative_at)
            SELECT id, 'youtube', 'unavailable', 2, lastScanned, lastScanned
            FROM videos WHERE source='youtube' AND isDeleted=1""")
        cur.execute("""INSERT IGNORE INTO sentinel_events
            (provider, entity_type, entity_id, event_type, from_state, to_state,
             evidence_json, dedupe_key, source_type, source_id)
            SELECT 'youtube', 'video', id, 'imported_existing_state', NULL,
                   'unavailable',
                   '{"source":"videos.isDeleted","observed_at_known":false}',
                   CONCAT('imported:youtube:video:', id, ':unavailable'),
                   'channel', channelId
            FROM videos WHERE source='youtube' AND isDeleted=1""")
        cur.close()
        con.close()
        cleanup_old_errors(7)
        cleanup_old_queue_rows(7)
        return True
    except Exception as e:
        logger.error("Failed during table create: %s",e)
        return False

def check_db_video(id):
    logger.debug("Checking db for video id: %s",id)
    test = False
    try:
        check = get_connection()
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
        # Fail closed: a DB hiccup must read as "already have it", or a scan
        # pass during an outage would re-enqueue everything it sees
        logger.error("Error during check_db_video: %s"%e)
        return True

def get_video_index():
    """The whole dedupe index in two queries: {video_id: length} plus the
    ignored-ID set. Used by the vault sweep instead of two queries per file.
    Returns None on DB failure so callers can skip the pass entirely."""
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("Select id, length FROM videos")
        lengths = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute("Select id FROM IgnoreVid")
        ignored = {row[0] for row in cur.fetchall()}
        cur.close()
        con.close()
        return lengths, ignored
    except Exception as e:
        logger.error("Error during get_video_index: %s"%e)
        return None

def save_video(id,ret,img,source='youtube'):
    try:
        con = get_connection()
        cur = con.cursor()
        #Save Video Data
        sql = "Insert Ignore into videos(id,channel_name,json,filepath,PublishedAt,channelId,length,source,title,description,vcodec,acodec,container,filesize) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);"
        cur.execute(sql,(id,ret["channel_name"],json.dumps(ret["Json"]),vault_relative_path(ret["Filepath"]),ret['PublishedAt'],ret['channelId'],ret['length'],source,ret['title'],ret.get('description',''),ret.get('vcodec'),ret.get('acodec'),ret.get('container'),ret.get('filesize')))
        #Save Thumbnail
        sql = "Insert Ignore into images(id,image) values(%s,%s)"
        cur.execute(sql,(id,img))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during save_video: %s"%e)

def check_db_channel(id):
    logger.debug("Checking db for channel id: %s",id)
    test = False
    try:
        check = get_connection()
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
        # Fail closed so a DB hiccup doesn't trigger channel re-creation
        logger.error("Error during check_db_channel: %s"%e)
        return True

def save_channel(channelid,channelname,jdata):
    try:
        con = get_connection()
        cur = con.cursor()
        #Save Video Data
        sql = "Insert into channels(channelid,channelname,json) values(%s,%s,%s);"
        cur.execute(sql,(channelid,channelname,json.dumps(jdata)))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during save_channel: %s"%e)

def get_active_subscriptions():
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("select channelid from channels where subscribed = 1;")
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        # Returning None here crashes the scanner's `for id in data:` loop and
        # kills the subscription thread for the life of the process.
        logger.error("Error during subscription poll: %s" % e)
        return []

_pool = None
_pool_lock = threading.Lock()

def _conn_kwargs():
    return dict(
        host=os.environ['VAULTTUBE_DBHOST'],
        user=os.environ['VAULTTUBE_DBUSER'],
        password=os.environ['VAULTTUBE_DBPASS'],
        database=os.environ['VAULTTUBE_DBNAME'],
        autocommit=True,
        port=int(os.environ['VAULTTUBE_DBPORT']),
    )

def get_connection(logger=None):
    """Hand out a pooled connection (close() returns it to the pool). Falls
    back to a one-off direct connection if the pool is exhausted, so bursts
    degrade instead of failing. The logger arg is kept for test-monkeypatch
    signature compatibility; the module logger is used internally."""
    global _pool
    try:
        if _pool is None:
            with _pool_lock:
                if _pool is None:
                    _pool = mariadb.ConnectionPool(
                        pool_name='vaulttube',
                        pool_size=int(os.environ.get('VAULTTUBE_DBPOOL', '8')),
                        pool_validation_interval=500,
                        **_conn_kwargs())
        try:
            return _pool.get_connection()
        except mariadb.PoolError:
            return mariadb.connect(**_conn_kwargs())
    except Exception as e:
        logger.error("Unable to get connection: %s"%e)

def check_db_video_length(id):
    logger.debug("Checking db for video length: %s",id)
    test = False
    try:
        check = get_connection()
        cur = check.cursor()
        cur.execute("Select length FROM videos where id = %s",(id,))
        row = cur.fetchone()
        if(row and not row[0] == "0"):
            test = True
        cur.close()
        check.close()
        return test
    except Exception as e:
        # Fail closed ("length is known") so errors don't trigger cv2 work
        logger.error("Error during check_db_video_length: %s"%e)
        return True

def update_video_codec_info(id, vcodec, acodec, container):
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute(
            "UPDATE videos SET vcodec=%s, acodec=%s, container=%s WHERE id=%s",
            (vcodec, acodec, container, id)
        )
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during update_video_codec_info: %s" % e)


def update_video_filesize(id, filesize):
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("UPDATE videos SET filesize=%s WHERE id=%s", (filesize, id))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during update_video_filesize: %s" % e)


def update_length(id,length):
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("Update videos set length = %s where id=%s;",(length,id))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error duing update_length: %s"%e)

def insert_playlist(plinfo):
    try:
        con = get_connection()
        cur = con.cursor()
        #Save Video Data
        sql = "Insert into playlists(playlistId,playlistName,channelId,json,subscribed) values(%s,%s,%s,%s,%s);"
        cur.execute(sql,(plinfo['items'][0]['id'],plinfo['items'][0]['snippet']['title'],plinfo['items'][0]['snippet']['channelId'],json.dumps(plinfo),0))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during insert_playlist: %s"%e)

def get_active_playlist_subs():
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("select playlistId from playlists where subscribed = 1;")
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during playlist subscription poll: %s" % e)
        return []

def check_pl2vid_info(pl,vid):
    logger.debug("Checking pl2vid for playlists %s and video %s"%(pl,vid))
    test = False
    try:
        check = get_connection()
        cur = check.cursor()
        cur.execute("Select * FROM pl2vid where playlistId = %s and videoId = %s",(pl,vid))
        if(cur.fetchone()):
            test = True
        cur.close()
        check.close()
        return test
    except Exception as e:
        # Fail closed so a DB hiccup doesn't trigger duplicate-insert attempts
        logger.error("Error during check_pl2vid_info: %s"%e)
        return True

def insert_pl2vid_info(pl,vid):
    con = None
    cur = None
    try:
        con = get_connection()
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

def find_next_previous(vid):
    """Neighbouring episodes of the same series: same channel, fuzzy title
    match (>0.9), ordered by PublishedAt. Keys on channelId and the title
    column — the legacy channel_name column is empty for Patreon/Reddit rows and
    JSON_EXTRACT over every blob made this a full-table parse per player load."""
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("Select channelId, title from videos where id = %s;",(vid,))
        row = cur.fetchone()
        if not row or not row[0] or not row[1]:
            cur.close()
            con.close()
            return {}
        channel_id, title = row
        cur.execute("Select id, title from videos where channelId = %s and title is not null order by PublishedAt desc;",(channel_id,))
        series = [r for r in cur.fetchall() if SequenceMatcher(None, title, r[1]).ratio() > 0.9]
        cur.close()
        con.close()
        ret = {}
        for index, (rid, rtitle) in enumerate(series):
            if rid == vid:
                # Newest-first: the next episode is the row above, previous below
                if index + 1 < len(series):
                    ret['PreviousID'] = series[index+1][0]
                    ret['PreviousTitle'] = series[index+1][1]
                if index > 0:
                    ret['NextID'] = series[index-1][0]
                    ret['NextTitle'] = series[index-1][1]
                break
        return ret
    except Exception as e:
        logger.error("Error during find_next_previous: %s"%e)
        return {}

def insert_not_found(vid):
    """Mark an ID the source says doesn't exist so scanners never retry it.
    A manual single-URL download still bypasses this (like deleted videos)."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("Insert ignore into IgnoreVid(id) values(%s);",(vid,))
    con.commit()
    cur.close()
    con.close()

def get_oldest_video_check():
    """(id, isDeleted) for the 1000 least-recently-checked YouTube videos."""
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("Select id, isDeleted from videos where source = 'youtube' order by lastScanned asc limit 1000;")
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during oldest video check: %s" % e)
        return []

def update_video_deleted(vid,isDeleted):
    con = get_connection()
    cur = con.cursor()
    sql = "update videos set isDeleted=%s,lastScanned=now()  where id=%s;"
    cur.execute(sql,(isDeleted,vid))
    con.commit()
    cur.close()
    con.close()
    logger.debug("Updated video deleted status %s for vid %s",isDeleted,vid)

def insert_download_error(url, error_type, error_msg):
    try:
        con = get_connection()
        cur = con.cursor()
        sql = "INSERT INTO download_errors(url, error_type, error_message) VALUES(%s, %s, %s)"
        cur.execute(sql, (url, error_type, error_msg))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during insert_download_error: %s", e)

def get_download_errors(limit=50):
    try:
        con = get_connection()
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

def clear_download_errors():
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("DELETE FROM download_errors")
        con.commit()
        cur.close()
        con.close()
        logger.info("Download errors cleared")
    except Exception as e:
        logger.error("Error during clear_download_errors: %s", e)

def delete_download_error(error_id):
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("DELETE FROM download_errors WHERE id = %s", (error_id,))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during delete_download_error: %s", e)

def insert_queue_item(qo):
    """Persist a queued download. Returns the row id, or None on failure."""
    try:
        con = get_connection()
        cur = con.cursor()
        sql = ("INSERT INTO queue(url, source, channel_id, unsave, status, "
               "attempts, priority, origin, rescue_session_id, target_item_id) "
               "VALUES(%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)")
        cur.execute(sql, (
            qo.url, qo.source, qo.channel_id, 1 if qo.unsave else 0,
            qo.attempts, getattr(qo, 'priority', 0),
            getattr(qo, 'origin', 'ordinary'),
            getattr(qo, 'rescue_session_id', None),
            getattr(qo, 'target_item_id', None),
        ))
        rowid = cur.lastrowid
        con.commit()
        cur.close()
        con.close()
        return rowid
    except Exception as e:
        logger.error("Error during insert_queue_item: %s" % e)
        return None

def update_queue_status(rowid, status, error=None, attempts=None):
    if rowid is None:
        return
    try:
        con = get_connection()
        cur = con.cursor()
        if attempts is not None:
            cur.execute("UPDATE queue SET status=%s, last_error=%s, attempts=%s WHERE id=%s", (status, error, attempts, rowid))
        else:
            cur.execute("UPDATE queue SET status=%s, last_error=%s WHERE id=%s", (status, error, rowid))
        cur.execute(
            "SELECT rescue_session_id,target_item_id FROM queue WHERE id=%s",
            (rowid,),
        )
        rescue = cur.fetchone()
        if rescue and rescue[0]:
            session_id, entity_id = rescue
            item_status = {
                'pending': 'queued', 'downloading': 'downloading',
                'done': 'preserved', 'failed': 'failed',
                'cancelled': 'cancelled',
            }.get(status)
            if item_status:
                cur.execute(
                    "UPDATE rescue_items SET status=%s,last_error=%s "
                    "WHERE session_id=%s AND entity_id=%s",
                    (item_status, error, session_id, entity_id),
                )
            if status == 'done':
                cur.execute(
                    "UPDATE rescue_sessions SET consecutive_blocking_failures=0 "
                    "WHERE id=%s",
                    (session_id,),
                )
            elif status == 'failed':
                message = (error or '').lower()
                blocking = any(token in message for token in (
                    'cookie', 'auth', '403', '429', 'too many requests',
                    'throttl',
                ))
                if blocking:
                    cur.execute(
                        "UPDATE rescue_sessions SET "
                        "consecutive_blocking_failures="
                        "consecutive_blocking_failures+1 WHERE id=%s",
                        (session_id,),
                    )
                    cur.execute(
                        "UPDATE rescue_sessions SET status='paused' "
                        "WHERE id=%s AND status='active' AND "
                        "consecutive_blocking_failures>=stop_failure_threshold",
                        (session_id,),
                    )
                else:
                    cur.execute(
                        "UPDATE rescue_sessions SET consecutive_blocking_failures=0 "
                        "WHERE id=%s",
                        (session_id,),
                    )
            cur.execute(
                "UPDATE rescue_sessions s SET s.status='completed', "
                "s.completed_at=NOW() WHERE s.id=%s AND s.status<>'cancelled' "
                "AND NOT EXISTS (SELECT 1 FROM rescue_items i "
                "WHERE i.session_id=s.id AND i.status IN "
                "('pending','queued','downloading'))",
                (session_id,),
            )
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during update_queue_status: %s" % e)

def queue_has_url(url):
    """True if the URL is already queued or downloading."""
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT 1 FROM queue WHERE url = %s AND status IN ('pending','downloading') LIMIT 1", (url,))
        rv = cur.fetchone() is not None
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during queue_has_url: %s" % e)
        return False

def get_resumable_queue_items():
    """Rows that were pending or mid-download when the app last stopped."""
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute(
            "SELECT q.id, q.url, q.source, q.channel_id, q.unsave, q.attempts, "
            "q.priority, q.origin, q.rescue_session_id, q.target_item_id, "
            "s.download_delay_seconds FROM queue q "
            "LEFT JOIN rescue_sessions s ON s.id=q.rescue_session_id "
            "WHERE q.status IN ('pending','downloading') "
            "ORDER BY q.priority, q.id"
        )
        rv = cur.fetchall()
        cur.close()
        con.close()
        return rv
    except Exception as e:
        logger.error("Error during get_resumable_queue_items: %s" % e)
        return []

def cleanup_old_queue_rows(days=7):
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("DELETE FROM queue WHERE status IN ('done','failed') AND updated_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during cleanup_old_queue_rows: %s" % e)

def cleanup_old_errors(days=7):
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("DELETE FROM download_errors WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        logger.error("Error during cleanup_old_errors: %s", e)


# --- Export helpers ---

def export_video_rows(include_json=False):
    """Generator yielding one catalog dict per video for the export endpoint."""
    try:
        con = get_connection()
        cur = con.cursor()
        json_sel = ", v.json" if include_json else ""
        cur.execute(
            "SELECT v.id, v.title, v.channelId, c.channelname, v.source, "
            "v.AddedAt, v.PublishedAt, v.watched, v.`timestamp`, v.`length`, "
            "v.filepath, v.vcodec, v.acodec, v.container, v.filesize, "
            "v.isDeleted, v.description" + json_sel + " "
            "FROM videos v LEFT JOIN channels c ON v.channelId = c.channelid "
            "ORDER BY v.AddedAt"
        )
        cols = ['id', 'title', 'channelId', 'channelName', 'source',
                'AddedAt', 'PublishedAt', 'watched', 'timestamp', 'length',
                'filepath', 'vcodec', 'acodec', 'container', 'filesize',
                'isDeleted', 'description']
        if include_json:
            cols.append('json')
        for row in cur:
            d = dict(zip(cols, row))
            for k in ('AddedAt', 'PublishedAt'):
                if d[k] is not None:
                    d[k] = d[k].isoformat()
            if d['length'] is not None:
                d['length'] = str(d['length'])
            if include_json and d.get('json'):
                try:
                    d['json'] = json.loads(d['json'])
                except Exception:
                    pass
            yield d
        cur.close()
        con.close()
    except Exception as e:
        logger.error("export_video_rows failed: %s", e)


def export_subscribed_channels():
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT channelid, channelname, json FROM channels WHERE subscribed = 1")
        rows = []
        for row in cur:
            d = {'channelId': row[0], 'channelName': row[1]}
            if row[2]:
                try:
                    d['metadata'] = json.loads(row[2])
                except Exception:
                    pass
            rows.append(d)
        cur.close()
        con.close()
        return rows
    except Exception as e:
        logger.error("export_subscribed_channels failed: %s", e)
        return []


def export_subscribed_playlists():
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT playlistId, playlistName, channelId, json FROM playlists WHERE subscribed = 1")
        rows = []
        for row in cur:
            d = {'playlistId': row[0], 'playlistName': row[1], 'channelId': row[2]}
            if row[3]:
                try:
                    d['metadata'] = json.loads(row[3])
                except Exception:
                    pass
            rows.append(d)
        cur.close()
        con.close()
        return rows
    except Exception as e:
        logger.error("export_subscribed_playlists failed: %s", e)
        return []


def export_pl2vid():
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT playlistId, videoId FROM pl2vid ORDER BY playlistId")
        rows = [{'playlistId': r[0], 'videoId': r[1]} for r in cur]
        cur.close()
        con.close()
        return rows
    except Exception as e:
        logger.error("export_pl2vid failed: %s", e)
        return []


def export_tombstones():
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT id FROM IgnoreVid")
        ids = [r[0] for r in cur]
        cur.close()
        con.close()
        return ids
    except Exception as e:
        logger.error("export_tombstones failed: %s", e)
        return []


def export_row_counts():
    try:
        con = get_connection()
        cur = con.cursor()
        counts = {}
        for name, sql in [
            ('videos', "SELECT COUNT(*) FROM videos"),
            ('subscribed_channels', "SELECT COUNT(*) FROM channels WHERE subscribed = 1"),
            ('subscribed_playlists', "SELECT COUNT(*) FROM playlists WHERE subscribed = 1"),
            ('mappings', "SELECT COUNT(*) FROM pl2vid"),
            ('tombstones', "SELECT COUNT(*) FROM IgnoreVid"),
            ('sentinel_events', "SELECT COUNT(*) FROM sentinel_events"),
            ('sentinel_video_states', "SELECT COUNT(*) FROM sentinel_video_state"),
            ('sentinel_scan_runs', "SELECT COUNT(*) FROM sentinel_scan_runs"),
            ('sentinel_inventory_runs', "SELECT COUNT(*) FROM sentinel_inventory_runs"),
            ('sentinel_inventory_items', "SELECT COUNT(*) FROM sentinel_inventory"),
            ('sentinel_sources', "SELECT COUNT(*) FROM sentinel_sources"),
            ('sentinel_rescue_previews', "SELECT COUNT(*) FROM sentinel_rescue_previews"),
            ('sentinel_rescue_preview_items', "SELECT COUNT(*) FROM sentinel_rescue_preview_items"),
            ('rescue_sessions', "SELECT COUNT(*) FROM rescue_sessions"),
            ('rescue_items', "SELECT COUNT(*) FROM rescue_items"),
        ]:
            cur.execute(sql)
            counts[name] = cur.fetchone()[0]
        cur.close()
        con.close()
        return counts
    except Exception as e:
        logger.error("export_row_counts failed: %s", e)
        return {}
