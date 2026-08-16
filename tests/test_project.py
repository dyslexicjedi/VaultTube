import pytest,os,json,mariadb,time,shutil,subprocess
import threading
from providers.base import set_status, update_status, del_status, get_status_copy


def _db_connect():
    return mariadb.connect(
        host=os.environ['VAULTTUBE_DBHOST'],
        user=os.environ['VAULTTUBE_DBUSER'],
        password=os.environ['VAULTTUBE_DBPASS'],
        database=os.environ['VAULTTUBE_DBNAME'],
        autocommit=True,
        port=int(os.environ['VAULTTUBE_DBPORT'])
    )


def _insert_chvids():
    """Insert the ChVids1 channel + 3 videos used by several channel-video tests."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute("REPLACE INTO channels(channelid, channelname, json, subscribed) VALUES('ChVids1','Ch Vids','{}',0);")
    for n, (title, watched) in enumerate([('A', 0), ('B', 1), ('C', 0)], start=1):
        cur.execute(
            "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched) "
            "VALUES(%s, 'Ch Vids', 'ChVids1', '{}', %s, %s, %s, %s);",
            ('ChVidsVid%d' % n, '/videos/%d.mp4' % n, '2024-01-0%d 10:00:00' % n, title, watched)
        )
    con.commit()
    con.close()


def test_home(client):
    response = client.get("/")
    assert response.status_code == 200


def _insert_home_content_filter_videos():
    con = _db_connect()
    cur = con.cursor()
    cur.execute("DELETE FROM videos WHERE channelId = %s", ("HomeContentFilter",))
    cur.execute(
        "REPLACE INTO channels(channelid, channelname, json, subscribed) "
        "VALUES('HomeContentFilter', 'Home Content Filter', '{}', 0)"
    )
    rows = [
        (
            "HomeFilterSafe",
            "Classic woodworking workshop",
            None,
            "2099-01-01 10:00:00",
            120,
        ),
        (
            "HomeFilterTitleAdult",
            "A PORN documentary",
            "Archive overview",
            "2099-01-02 10:00:00",
            121,
        ),
        (
            "HomeFilterDescriptionAdult",
            "Behind the scenes",
            "Marked nSfW for adults",
            "2099-01-03 10:00:00",
            122,
        ),
        (
            "HomeFilterBoundary",
            "How to choose an XXXLarge shirt",
            "Sizing advice",
            "2099-01-04 10:00:00",
            123,
        ),
        (
            "HomeFilterClassBoundary",
            "A classic passage about class design",
            "Boundary matching example",
            "2099-01-05 10:00:00",
            124,
        ),
        (
            "HomeFilterStandaloneAss",
            "Standalone ASS collection",
            "Boundary matching example",
            "2099-01-06 10:00:00",
            125,
        ),
        (
            "HomeFilterSexScenes",
            "Movie sex scenes explained",
            "Explicit scene catalog",
            "2099-01-07 10:00:00",
            126,
        ),
        (
            "HomeFilterErotica",
            "Archive showcase",
            "An EROTICA collection",
            "2099-01-08 10:00:00",
            127,
        ),
        (
            "HomeFilterPornhub",
            "Pornhub archive",
            "Creator history",
            "2099-01-09 10:00:00",
            128,
        ),
        (
            "HomeFilterPornstar",
            "Meet the pornstars",
            "Creator profiles",
            "2099-01-10 10:00:00",
            129,
        ),
        (
            "HomeFilterOnlyFansModel",
            "OnlyFansModel interview",
            "Creator profile",
            "2099-01-11 10:00:00",
            130,
        ),
        (
            "HomeFilterSexEducation",
            "Sex education policy",
            "A classroom discussion",
            "2099-01-12 10:00:00",
            131,
        ),
        (
            "HomeFilterSexDifferences",
            "Research roundup",
            "Research into sex differences",
            "2099-01-13 10:00:00",
            132,
        ),
        (
            "HomeFilterNudeShootHashtag",
            "Studio notes #nudeshoot",
            "New portfolio work",
            "2099-01-14 10:00:00",
            133,
        ),
        (
            "HomeFilterArtNudeHashtag",
            "Gallery notes",
            "Tags: #artnude",
            "2099-01-15 10:00:00",
            134,
        ),
        (
            "HomeFilterDenudedBoundary",
            "Denuded landscape",
            "A denudement study",
            "2099-01-16 10:00:00",
            135,
        ),
        (
            "HomeFilterNudeHashtagExtended",
            "Follow #nudeshooter updates",
            "An extended nude hashtag",
            "2099-01-17 10:00:00",
            136,
        ),
        (
            "HomeFilterPhotoArtNudeHashtag",
            "Portfolio tags",
            "Includes #photoartnude",
            "2099-01-18 10:00:00",
            137,
        ),
    ]
    for video_id, title, description, published_at, timestamp in rows:
        cur.execute(
            "REPLACE INTO videos("
            "id, channel_name, channelId, json, filepath, PublishedAt, title, "
            "description, watched, timestamp"
            ") VALUES(%s, 'Home Content Filter', 'HomeContentFilter', '{}', "
            "%s, %s, %s, %s, 0, %s)",
            (
                video_id,
                "/videos/HomeContentFilter/%s.mp4" % video_id,
                published_at,
                title,
                description,
                timestamp,
            ),
        )
    con.commit()
    con.close()


def test_home_content_selector_markup_and_api_wiring(client):
    body = client.get("/").get_data(as_text=True)
    topbar_start = body.index('<header class="vt-topbar">')
    topbar_end = body.index("</header>", topbar_start)
    topbar = body[topbar_start:topbar_end]
    content = body[body.index('<main class="vt-content">'):]

    assert 'id="home-content-sfw" value="sfw" checked' in body
    assert 'id="home-content-nsfw" value="nsfw"' in body
    assert '<fieldset class="vt-home-content-filter">' in topbar
    assert '<legend class="vt-sr-only">Home content filter</legend>' in topbar
    assert topbar.index('class="vt-search"') < topbar.index("vt-home-content-filter")
    assert topbar.index("vt-home-content-filter") < topbar.index('id="add-open"')
    assert "vt-home-content-filter" not in content
    assert 'id="home-content-sfw"' not in client.get("/browse.html").get_data(as_text=True)
    assert "loadHome('sfw')" in body
    assert "withContent('/api/list/resume/', mode)" in body
    assert "withContent('/api/getvids/unwatched/AddedAt/desc/0', mode)" in body
    assert "withContent('/api/channels/0?order=activity', mode)" in body
    assert "loadSequence" in body
    assert "activeController.abort()" in body
    assert "channels.slice(0, 4)" in body
    assert "CHANNEL_BATCH_SIZE" not in body
    assert "processCandidates" not in body
    assert "channelOffset" not in body
    assert 'id="home-retry"' in body
    assert "result.reason.name !== 'AbortError'" in body
    assert "loadHome(currentMode)" in body


def test_getvids_home_content_modes_and_boundaries(client):
    _insert_home_content_filter_videos()
    base = (
        "/api/getvids/unwatched/PublishedAt/desc/0"
        "?channel_ids[]=HomeContentFilter"
    )

    unfiltered = {
        row["id"] for row in json.loads(client.get(base).get_data(as_text=True))
    }
    invalid = {
        row["id"] for row in json.loads(client.get(base + "&content=unknown").get_data(as_text=True))
    }
    sfw = {
        row["id"] for row in json.loads(client.get(base + "&content=sfw").get_data(as_text=True))
    }
    nsfw = {
        row["id"] for row in json.loads(client.get(base + "&content=nsfw").get_data(as_text=True))
    }

    expected_all = {
        "HomeFilterSafe",
        "HomeFilterTitleAdult",
        "HomeFilterDescriptionAdult",
        "HomeFilterBoundary",
        "HomeFilterClassBoundary",
        "HomeFilterStandaloneAss",
        "HomeFilterSexScenes",
        "HomeFilterErotica",
        "HomeFilterPornhub",
        "HomeFilterPornstar",
        "HomeFilterOnlyFansModel",
        "HomeFilterSexEducation",
        "HomeFilterSexDifferences",
        "HomeFilterNudeShootHashtag",
        "HomeFilterArtNudeHashtag",
        "HomeFilterDenudedBoundary",
        "HomeFilterNudeHashtagExtended",
        "HomeFilterPhotoArtNudeHashtag",
    }
    assert unfiltered == expected_all
    assert invalid == expected_all
    assert sfw == {
        "HomeFilterSafe",
        "HomeFilterBoundary",
        "HomeFilterClassBoundary",
        "HomeFilterSexEducation",
        "HomeFilterSexDifferences",
        "HomeFilterDenudedBoundary",
    }
    assert nsfw == {
        "HomeFilterTitleAdult",
        "HomeFilterDescriptionAdult",
        "HomeFilterStandaloneAss",
        "HomeFilterSexScenes",
        "HomeFilterErotica",
        "HomeFilterPornhub",
        "HomeFilterPornstar",
        "HomeFilterOnlyFansModel",
        "HomeFilterNudeShootHashtag",
        "HomeFilterArtNudeHashtag",
        "HomeFilterNudeHashtagExtended",
        "HomeFilterPhotoArtNudeHashtag",
    }


def test_resume_home_content_modes(client):
    _insert_home_content_filter_videos()

    sfw = {
        row["id"] for row in json.loads(client.get("/api/list/resume/?content=sfw").get_data(as_text=True))
    }
    nsfw = {
        row["id"] for row in json.loads(client.get("/api/list/resume/?content=nsfw").get_data(as_text=True))
    }
    unfiltered = {
        row["id"] for row in json.loads(client.get("/api/list/resume/").get_data(as_text=True))
    }

    assert {
        "HomeFilterSafe",
        "HomeFilterBoundary",
        "HomeFilterClassBoundary",
        "HomeFilterSexEducation",
        "HomeFilterSexDifferences",
        "HomeFilterDenudedBoundary",
    } <= sfw
    assert "HomeFilterTitleAdult" not in sfw
    assert "HomeFilterDescriptionAdult" not in sfw
    assert "HomeFilterStandaloneAss" not in sfw
    assert "HomeFilterSexScenes" not in sfw
    assert "HomeFilterErotica" not in sfw
    assert "HomeFilterPornhub" not in sfw
    assert "HomeFilterPornstar" not in sfw
    assert "HomeFilterOnlyFansModel" not in sfw
    assert "HomeFilterNudeShootHashtag" not in sfw
    assert "HomeFilterArtNudeHashtag" not in sfw
    assert "HomeFilterNudeHashtagExtended" not in sfw
    assert "HomeFilterPhotoArtNudeHashtag" not in sfw
    assert {
        "HomeFilterTitleAdult",
        "HomeFilterDescriptionAdult",
        "HomeFilterStandaloneAss",
        "HomeFilterSexScenes",
        "HomeFilterErotica",
        "HomeFilterPornhub",
        "HomeFilterPornstar",
        "HomeFilterOnlyFansModel",
        "HomeFilterNudeShootHashtag",
        "HomeFilterArtNudeHashtag",
        "HomeFilterNudeHashtagExtended",
        "HomeFilterPhotoArtNudeHashtag",
    } <= nsfw
    assert "HomeFilterSafe" not in nsfw
    assert "HomeFilterBoundary" not in nsfw
    assert "HomeFilterClassBoundary" not in nsfw
    assert "HomeFilterSexEducation" not in nsfw
    assert "HomeFilterSexDifferences" not in nsfw
    assert "HomeFilterDenudedBoundary" not in nsfw
    assert {
        "HomeFilterSafe",
        "HomeFilterTitleAdult",
        "HomeFilterDescriptionAdult",
        "HomeFilterBoundary",
        "HomeFilterClassBoundary",
        "HomeFilterStandaloneAss",
        "HomeFilterSexScenes",
        "HomeFilterErotica",
        "HomeFilterPornhub",
        "HomeFilterPornstar",
        "HomeFilterOnlyFansModel",
        "HomeFilterSexEducation",
        "HomeFilterSexDifferences",
        "HomeFilterNudeShootHashtag",
        "HomeFilterArtNudeHashtag",
        "HomeFilterDenudedBoundary",
        "HomeFilterNudeHashtagExtended",
        "HomeFilterPhotoArtNudeHashtag",
    } <= unfiltered


def test_channels_home_content_filter_aggregates_and_composes(client):
    con = _db_connect()
    cur = con.cursor()
    channels = [
        ("UCCFAdult", "Adult Channel"),
        ("UCCFSafe", "Safe Channel"),
        ("ContentMixed", "Mixed Channel"),
        ("ContentTieA", "Tie A"),
        ("ContentTieB", "Tie B"),
        ("ContentEmpty", "Empty Channel"),
    ]
    for channel_id, channel_name in channels:
        cur.execute(
            "REPLACE INTO channels(channelid, channelname, json, subscribed) "
            "VALUES(%s, %s, '{}', 0)",
            (channel_id, channel_name),
        )

    videos = [
        ("CFAdult1", "UCCFAdult", "NSFW archive", "2099-04-06 10:00:00", 0, "youtube"),
        ("CFAdult2", "UCCFAdult", "Pornstar profile", "2099-04-02 10:00:00", 0, "youtube"),
        ("CFAdultSafeWatched", "UCCFAdult", "Safe documentary", "2099-04-11 10:00:00", 1, "youtube"),
        ("CFSafe1", "UCCFSafe", "Safe workshop", "2099-04-05 10:00:00", 0, "youtube"),
        ("CFSafe2", "UCCFSafe", "Sex education", "2099-04-03 10:00:00", 0, "youtube"),
        ("CFSafeAdultWatched", "UCCFSafe", "Erotica archive", "2099-04-10 10:00:00", 1, "youtube"),
        ("CFMixedSafe", "ContentMixed", "Travel guide", "2099-04-04 10:00:00", 0, "reddit"),
        ("CFMixedAdult", "ContentMixed", "OnlyFansModel profile", "2099-04-07 10:00:00", 0, "reddit"),
        ("CFTieA", "ContentTieA", "Safe tie A", "2099-04-01 10:00:00", 0, "reddit"),
        ("CFTieB", "ContentTieB", "Safe tie B", "2099-04-01 10:00:00", 0, "reddit"),
    ]
    for video_id, channel_id, title, published_at, watched, source in videos:
        cur.execute(
            "REPLACE INTO videos("
            "id, channel_name, channelId, json, filepath, PublishedAt, title, "
            "watched, source"
            ") VALUES(%s, %s, %s, '{}', %s, %s, %s, %s, %s)",
            (
                video_id,
                channel_id,
                channel_id,
                "/videos/%s/%s.mp4" % (channel_id, video_id),
                published_at,
                title,
                watched,
                source,
            ),
        )
    con.commit()
    con.close()

    def channel_id(row):
        return row.get("channelid") or row.get("channelId")

    sfw_rows = json.loads(
        client.get("/api/channels/0?order=activity&content=sfw").get_data(as_text=True)
    )
    nsfw_rows = json.loads(
        client.get("/api/channels/0?order=activity&content=nsfw").get_data(as_text=True)
    )
    absent_rows = json.loads(
        client.get("/api/channels/0?order=activity").get_data(as_text=True)
    )
    invalid_rows = json.loads(
        client.get("/api/channels/0?order=activity&content=unknown").get_data(as_text=True)
    )

    sfw = {channel_id(row): row for row in sfw_rows}
    nsfw = {channel_id(row): row for row in nsfw_rows}
    assert "UCCFAdult" not in sfw
    assert "ContentEmpty" not in sfw
    assert int(sfw["UCCFSafe"]["unwatched"]) == 2
    assert int(sfw["UCCFSafe"]["vidcount"]) == 2
    assert sfw["UCCFSafe"]["lastvidtime"] == "2099-04-05 10:00:00"
    assert "UCCFSafe" not in nsfw
    assert "ContentEmpty" not in nsfw
    assert int(nsfw["UCCFAdult"]["unwatched"]) == 2
    assert int(nsfw["UCCFAdult"]["vidcount"]) == 2
    assert nsfw["UCCFAdult"]["lastvidtime"] == "2099-04-06 10:00:00"

    sfw_ids = [channel_id(row) for row in sfw_rows]
    assert sfw_ids.index("ContentTieA") < sfw_ids.index("ContentTieB")

    absent_ids = {channel_id(row) for row in absent_rows}
    invalid_ids = {channel_id(row) for row in invalid_rows}
    assert absent_ids == invalid_ids
    assert {channel_id for channel_id, _ in channels} <= absent_ids

    youtube_nsfw = json.loads(
        client.get(
            "/api/channels/0?order=activity&content=nsfw&source=youtube"
        ).get_data(as_text=True)
    )
    youtube_nsfw_ids = {channel_id(row) for row in youtube_nsfw}
    assert "UCCFAdult" in youtube_nsfw_ids
    assert "ContentMixed" not in youtube_nsfw_ids
    assert "UCCFSafe" not in youtube_nsfw_ids


def test_getvids_content_filter_is_applied_before_limit(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("DELETE FROM videos WHERE channelId = %s", ("HomeFilterLimit",))
    cur.execute(
        "REPLACE INTO channels(channelid, channelname, json, subscribed) "
        "VALUES('HomeFilterLimit', 'Home Filter Limit', '{}', 0)"
    )
    cur.execute(
        "REPLACE INTO videos("
        "id, channel_name, channelId, json, filepath, PublishedAt, title, watched"
        ") VALUES('HomeFilterLimitSafe', 'Home Filter Limit', "
        "'HomeFilterLimit', '{}', '/videos/HomeFilterLimit/safe.mp4', "
        "'2090-01-01 10:00:00', 'A safe older video', 0)"
    )
    for index in range(40):
        video_id = "HomeFilterLimitAdult%02d" % index
        cur.execute(
            "REPLACE INTO videos("
            "id, channel_name, channelId, json, filepath, PublishedAt, title, watched"
            ") VALUES(%s, 'Home Filter Limit', 'HomeFilterLimit', '{}', %s, %s, "
            "'NSFW newer video', 0)",
            (
                video_id,
                "/videos/HomeFilterLimit/%s.mp4" % video_id,
                "2090-01-02 10:%02d:00" % index,
            ),
        )
    con.commit()
    con.close()

    response = client.get(
        "/api/getvids/unwatched/PublishedAt/desc/0"
        "?channel_ids[]=HomeFilterLimit&content=sfw"
    )
    data = json.loads(response.get_data(as_text=True))
    assert [row["id"] for row in data] == ["HomeFilterLimitSafe"]


def test_populate_db(client):
    try:
        response = client.get("/api/checkdb")
        assert response.text == "True"
        con = _db_connect()
        cur = con.cursor()
        cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('Test123','Test123','Test123',0);")
        con.commit()
        cur.execute("Select * from channels limit 1;")
        assert 1 == cur.rowcount
        cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp) values('Test123','Test123','Test123','TestJSON','/videos/1','2023-10-21 15:15:15',0,0);")
        con.commit()
        cur.execute("Select * from videos limit 1;")
        assert 1 == cur.rowcount
        con.close()
    except Exception as e:
        print(e)
        assert True == False


def test_subscribe(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute("REPLACE INTO channels(channelid,channelname,json,subscribed) values('SubTest123','SubTest123','{}',0);")
    con.commit()
    # Query the DB directly for the test channel's ID
    cur.execute("SELECT channelid FROM channels WHERE channelid = %s;", ("SubTest123",))
    ch_row = cur.fetchone()
    assert ch_row is not None, "Test channel should exist in DB"
    ch_id = ch_row[0]
    con.close()

    response = client.get("/api/sub_status/channel/%s" % ch_id)
    assert response.text == '0'

    response = client.get("/api/subscribe/channel/%s" % ch_id)
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/channel/%s" % ch_id)
    assert response.text == '1'

    response = client.get("/api/unsubscribe/channel/%s" % ch_id)
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/channel/%s" % ch_id)
    assert response.text == '0'


def test_search_fulltext(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, description) "
        "VALUES('SearchFT1', 'TestCreator', 'TestChannel1', '{}', '/videos/search1.mp4', "
        "'2023-10-21 15:15:15', 'Python Tutorial Advanced', 'Learn advanced Python programming techniques for vaulttubefulltextkw');"
    )
    con.commit()
    con.close()

    # Search for a unique keyword that only exists in our test data - guarantees top rank
    response = client.get("/api/search/vaulttubefulltextkw/0")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert 'SearchFT1' in ids, "FULLTEXT search should find SearchFT1 by description with unique keyword"

    # Search for a common word - verify search returns results containing the term
    response = client.get("/api/search/advanced/0")
    data = json.loads(response.get_data(as_text=True))
    assert len(data) > 0, "Search for 'advanced' should return results"
    found_advanced = any('advanced' in (v.get('title') or '').lower() or
                         'advanced' in (v.get('description') or '').lower()
                         for v in data)
    assert found_advanced, "At least one result should contain 'advanced'"

    response = client.get("/api/search/xyznonexistent/0")
    data = json.loads(response.get_data(as_text=True))
    assert data == [], "Search for nonexistent term should return empty list"


def test_search_short_query(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, description) "
        "VALUES('SearchLIKE1', 'TestCreator', 'TestChannel1', '{}', '/videos/search2.mp4', "
        "'2023-10-21 15:15:15', 'Zynced Workflow Tool', 'A unique workflow tool');"
    )
    con.commit()
    con.close()

    response = client.get("/api/search/Zy/0")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert 'SearchLIKE1' in ids, "Short query LIKE fallback should find SearchLIKE1 by title prefix"


def test_get_video(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO channels(channelid,channelname,json,subscribed) "
        "VALUES('GetVidCh1', 'GetVidYoutuber', '{}', 0);"
    )
    con.commit()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, description, watched) "
        "VALUES('GetVid1', 'GetVidYoutuber', 'GetVidCh1', '{}', '/videos/getvid1.mp4', "
        "'2024-01-01 10:00:00', 'Get Video Test', 'Testing get video API', 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/video/GetVid1")
    data = json.loads(response.get_data(as_text=True))
    assert len(data) > 0
    assert data[0]['id'] == 'GetVid1'
    assert data[0]['channel_name'] == 'GetVidYoutuber'
    assert data[0]['title'] == 'Get Video Test'


def test_get_video_mp4_suffix(client):
    """Test that video lookup strips .mp4 from the id."""
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched) "
        "VALUES('GetVidMp4', 'TestYt', 'Ch1', '{}', '/videos/GetVidMp4.mp4', "
        "'2024-01-01 10:00:00', 'Mp4 Test', 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/video/GetVidMp4.mp4")
    data = json.loads(response.get_data(as_text=True))
    assert len(data) > 0
    assert data[0]['id'] == 'GetVidMp4'


@pytest.mark.parametrize(
    ("video_id", "stored_path"),
    [
        ("PathRelative", "PathChannel/video.mp4"),
        ("PathLeadingSlash", "/PathChannel/video.mp4"),
        ("PathPublicPrefix", "/videos/PathChannel/video.mp4"),
    ],
)
def test_get_video_normalizes_public_filepath(client, video_id, stored_path):
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title) "
        "VALUES(%s, 'Path Test', 'PathChannel', '{}', %s, '2024-01-01 10:00:00', 'Path Test')",
        (video_id, stored_path),
    )
    con.commit()
    con.close()

    response = client.get("/api/video/" + video_id)
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert data[0]["filepath"] == "/videos/PathChannel/video.mp4"


def test_vault_paths_normalize_and_reject_traversal():
    from vault_paths import public_video_path, resolve_vault_path, vault_relative_path

    vault = os.environ["VAULTTUBE_VAULTDIR"]
    absolute = os.path.join(vault, "Channel", "video.mp4")
    assert vault_relative_path(absolute) == os.path.join("Channel", "video.mp4")
    assert vault_relative_path("/Channel/video.mp4") == os.path.join("Channel", "video.mp4")
    assert public_video_path("/videos/Channel/video.mp4") == "/videos/Channel/video.mp4"
    assert resolve_vault_path("/Channel/video.mp4") == os.path.realpath(absolute)
    with pytest.raises(ValueError):
        resolve_vault_path("../outside.mp4")


def test_mark_watched(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched, timestamp) "
        "VALUES('WatchVid1', 'TestYt', 'Ch1', '{}', '/videos/watchvid1.mp4', "
        "'2024-01-01 10:00:00', 'Watch Test', 0, 0);"
    )
    con.commit()
    con.close()

    # Verify not watched
    response = client.get("/api/watch_status/WatchVid1")
    assert response.text == "0"

    # Mark watched
    response = client.get("/api/watched/WatchVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    # Verify watched
    response = client.get("/api/watch_status/WatchVid1")
    assert response.text == "1"


def test_mark_unwatched(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched, timestamp) "
        "VALUES('UnwatchVid1', 'TestYt', 'Ch1', '{}', '/videos/unwatchvid1.mp4', "
        "'2024-01-01 10:00:00', 'Unwatch Test', 1, 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/watch_status/UnwatchVid1")
    assert response.text == "1"

    response = client.get("/api/unwatched/UnwatchVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/watch_status/UnwatchVid1")
    assert response.text == "0"


def test_set_timestamp(client):
    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched, timestamp) "
        "VALUES('TsVid1', 'TestYt', 'Ch1', '{}', '/videos/tsvid1.mp4', "
        "'2024-01-01 10:00:00', 'Timestamp Test', 0, 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/set_timestamp/1515/TsVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/video/TsVid1")
    data = json.loads(response.get_data(as_text=True))
    assert data[0]['timestamp'] == "1515"


def test_download_queue(client, monkeypatch):
    """Test that downloading a URL enqueues a job and returns success."""
    import queue as _queue

    # Ensure queue is initialized for test client
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = _queue.Queue()
        q = client.application.config['queue']
    # Ensure queue is empty before test
    while not q.empty():
        try:
            q.get_nowait()
        except _queue.Empty:
            break

    response = client.post("/api/download/single", json={"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    with client.application.app_context():
        assert q.qsize() == 1


def test_download_queue_missing_url(client):
    """Test that downloading without a URL returns an error."""
    response = client.post("/api/download/single", json={})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is False


def test_video_count(client):
    response = client.get("/api/stats/video/count")
    assert response.status_code == 200
    # Should return a non-negative integer string
    count = int(response.text)
    assert count >= 0


def test_video_unwatched_count(client):
    response = client.get("/api/stats/video/unwatched")
    assert response.status_code == 200
    count = int(response.text)
    assert count >= 0


def test_stats(client):
    response = client.get("/api/stats")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, dict)
    totals = data['totals']
    for key in ('videos', 'watched', 'unwatched', 'total_seconds', 'avg_seconds', 'channels', 'playlists'):
        assert isinstance(totals[key], int)
    assert totals['watched'] + totals['unwatched'] == totals['videos']
    assert isinstance(data['by_source'], list)
    assert isinstance(data['top_channels'], list)
    assert len(data['top_channels']) <= 10
    assert len(data['added_per_week']) == 26
    assert len(data['duration_buckets']) == 5
    assert 'deleted' in data['deleted'] and 'youtube_total' in data['deleted']


def test_random_video(client):
    response = client.get("/api/random")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_random_video_include_reddit(client):
    response = client.get("/api/random?include_reddit=1")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_channels_page(client):
    response = client.get("/api/channels/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    for ch in data:
        assert 'unwatched' in ch
        assert 'vidcount' in ch
        assert 'source' in ch


def test_channels_order_activity(client):
    response = client.get("/api/channels/0?order=activity")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_channels_source_filter(client):
    def displayed_source(ch):
        if ch['source']:
            return ch['source']
        cid = ch.get('channelId') or ch.get('channelid')
        if cid.startswith('UC'):
            return 'youtube'
        return 'patreon' if cid.isdigit() else 'reddit'

    for src in ('youtube', 'patreon', 'reddit'):
        response = client.get("/api/channels/0?source=" + src)
        data = json.loads(response.get_data(as_text=True))
        assert isinstance(data, list)
        for ch in data:
            assert displayed_source(ch) == src

    # unknown values are ignored, not an error
    response = client.get("/api/channels/0?source=bogus")
    assert isinstance(json.loads(response.get_data(as_text=True)), list)


def test_browse_page(client):
    response = client.get("/browse.html")
    assert response.status_code == 200
    assert b'browse-grid' in response.data


def test_up_next(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('UpNextCh1','UpNextCh1','{}',0);")
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp) values('UpNext1','UpNextCh1','UpNextCh1','{}','/videos/1','2023-01-02 12:00:00',0,0);")
    # Published after the current video and unwatched -> must be first in the list
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp) values('UpNext2','UpNextCh1','UpNextCh1','{}','/videos/2','2023-01-03 12:00:00',0,0);")
    # Watched -> must never appear
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp) values('UpNext3','UpNextCh1','UpNextCh1','{}','/videos/3','2023-01-01 12:00:00',1,0);")
    con.close()

    response = client.get("/api/up_next/UpNext1?limit=5")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]['id'] == 'UpNext2'
    ids = [v['id'] for v in data]
    assert 'UpNext1' not in ids   # never suggest the current video
    assert 'UpNext3' not in ids   # never suggest watched videos
    assert len(data) <= 5


def test_up_next_unknown_video(client):
    response = client.get("/api/up_next/NonexistentVid123")
    data = json.loads(response.get_data(as_text=True))
    assert data == []


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def close(self):
        pass


def _playlist_page(video_ids, next_token=None, total_results=None):
    page = {'items': [{'contentDetails': {'videoId': v}} for v in video_ids]}
    if next_token:
        page['nextPageToken'] = next_token
    if total_results is not None:
        page['pageInfo'] = {'totalResults': total_results}
    return page


def _ensure_queue(client):
    import queue as _queue
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = _queue.Queue()
        q = client.application.config['queue']
    while not q.empty():
        try:
            q.get_nowait()
        except _queue.Empty:
            break
    return q


def test_uploads_playlist_id():
    from scanner import uploads_playlist_id
    assert uploads_playlist_id('UCVtTestChannel1') == 'UUVtTestChannel1'


def test_parse_response_closes_connection_on_empty():
    from api import parse_response

    class FakeCur:
        rowcount = 0
        closed = False
        def close(self):
            self.closed = True

    class FakeCon:
        closed = False
        def close(self):
            self.closed = True

    cur, con = FakeCur(), FakeCon()
    assert parse_response(cur, con) == "[]"
    assert cur.closed and con.closed


def test_db_checks_fail_closed(monkeypatch):
    """A DB error must read as 'already have it', never as 'missing' —
    otherwise a DB blip makes scanners re-enqueue everything they see."""
    import logging, database

    def boom(logger):
        raise RuntimeError("db down")

    monkeypatch.setattr(database, 'get_connection', boom)
    log = logging.getLogger('test')
    assert database.check_db_video('AnyVid') is True
    assert database.check_db_channel('AnyChan') is True
    assert database.check_pl2vid_info('AnyPl', 'AnyVid') is True
    assert database.check_db_video_length('AnyVid') is True
    assert database.get_video_index() is None


def test_get_video_index(client):
    import logging, database
    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp,length) values('GetVid1','X','GetVidCh1','{}','/videos/1','2024-01-01 10:00:00',0,0,'0:10:00');")
    cur.execute("Insert ignore into IgnoreVid(id) values('TombVid1');")
    con.close()

    lengths, ignored = database.get_video_index()
    assert lengths.get('GetVid1') == '0:10:00'
    assert 'TombVid1' in ignored


def test_connection_pool_reuse(client):
    import logging, database
    log = logging.getLogger('test')
    for _ in range(3):
        con = database.get_connection(log)
        cur = con.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        con.close()   # returns to the pool; next call must hand out a working one


def test_find_next_previous_series(client):
    """Series detection keys on channelId + title column, so it works for
    Patreon/Reddit rows too (their legacy channel_name column is empty)."""
    con = _db_connect()
    cur = con.cursor()
    # channel_name deliberately empty, like Patreon rows
    for n in (1, 2, 3):
        cur.execute(
            "Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp,title) "
            "values(%s,'','UpNextCh1','{}','/videos/x',%s,0,0,%s);",
            ('FnpVid%d' % n, '2024-01-0%d 10:00:00' % n, 'My Series Episode %d' % n))
    con.close()

    response = client.get("/api/find_next_previous/FnpVid2")
    data = json.loads(response.get_data(as_text=True))
    assert data['NextID'] == 'FnpVid3'
    assert data['NextTitle'] == 'My Series Episode 3'
    assert data['PreviousID'] == 'FnpVid1'
    assert data['PreviousTitle'] == 'My Series Episode 1'

    # Unknown video: empty dict, not an error
    response = client.get("/api/find_next_previous/NoSuchVidXyz")
    assert json.loads(response.get_data(as_text=True)) == {}


def test_patreon_db_info_json(client):
    """Non-YouTube rows store honest metadata, not a fake YouTube API blob."""
    import logging, datetime
    from providers.patreon import patreon_db_info

    assert patreon_db_info('PatVid1', '11752268', datetime.datetime(2024, 1, 1), 'Pat Title') is True

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select json, title, source from videos where id = 'PatVid1'")
    jdata, title, source = cur.fetchone()
    con.close()
    j = json.loads(jdata)
    assert j['webpage_url'] == 'https://www.patreon.com/posts/PatVid1'
    assert j['title'] == 'Pat Title'
    assert 'items' not in j          # the fake YouTube shape is gone
    assert title == 'Pat Title' and source == 'patreon'


def test_run_deleted_check_batched(client, monkeypatch):
    """One API call per 50 IDs; two independent negative scans confirm an
    unavailable video, while a positive result restores one immediately."""
    import logging, requests, backend

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp,isDeleted,source) values('DelVid1','X','GetVidCh1','{}','/videos/1','2024-01-01 10:00:00',0,0,0,'youtube');")
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp,isDeleted,source) values('DelVid2','X','GetVidCh1','{}','/videos/2','2024-01-02 10:00:00',0,0,1,'youtube');")
    con.close()

    calls = []
    def fake_get(url, **kw):
        calls.append(url)
        # Only DelVid2 still exists at the source
        return _FakeResp({'items': [{'id': 'DelVid2'}]})
    monkeypatch.setattr(requests, 'get', fake_get)

    with client.application.app_context():
        backend.run_deleted_check(rows=[('DelVid1', 0), ('DelVid2', 1)])
        # A second completed scan is required to confirm DelVid1 unavailable.
        backend.run_deleted_check(rows=[('DelVid1', 0)])

    assert len(calls) == 2
    assert 'DelVid1' in calls[0] and 'DelVid2' in calls[0]

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select id, isDeleted from videos where id in ('DelVid1','DelVid2') order by id")
    result = dict(cur.fetchall())
    con.close()
    assert result['DelVid1'] == 1   # vanished from the source
    assert result['DelVid2'] == 0   # back/still up: flag cleared

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT entity_id, event_type FROM sentinel_events "
        "WHERE entity_id IN ('DelVid1','DelVid2') ORDER BY entity_id"
    )
    assert cur.fetchall() == [
        ('DelVid1', 'source_unavailable'),
        ('DelVid2', 'source_restored'),
    ]
    cur.close()
    con.close()


def test_partial_download_detection():
    from backend import is_partial_download
    # yt-dlp working files in all their shapes
    assert is_partial_download('abc12345678.mp4.part')
    assert is_partial_download('abc12345678.mp4.part-Frag42')
    assert is_partial_download('abc12345678.mp4.ytdl')
    assert is_partial_download('abc12345678.f137.mp4')   # pre-merge video stream
    assert is_partial_download('abc12345678.f140.m4a')   # pre-merge audio stream
    # finished files
    assert not is_partial_download('abc12345678.mp4')
    assert not is_partial_download('abc12345678.webm')
    assert not is_partial_download('partytime.mp4')


def test_looks_like_youtube():
    from backend import looks_like_youtube
    assert looks_like_youtube('/videos/UCabc123/dQw4w9WgXcQ.mp4')
    assert not looks_like_youtube('/videos/11752268/152769940.mp4')        # patreon
    assert not looks_like_youtube('/videos/SomeRedditUser/abc123.mp4')     # reddit
    assert not looks_like_youtube('/videos/UCabc123/152769940.mp4')        # wrong id length


def test_error_type_classification():
    from downloader import get_error_type
    # Throttling responses must be transient (retried), not permanent failures
    assert get_error_type('HTTP Error 429: Too Many Requests') == 'Network Error'
    assert get_error_type('Download throttled by server') == 'Network Error'
    assert get_error_type('Connection reset by peer') == 'Network Error'
    assert get_error_type('Video not found') == 'Content Not Found'
    assert get_error_type('HTTP Error 403: Forbidden') == 'Authentication Error'
    assert get_error_type('something exploded') == 'Provider Error'


def test_channel_scan_paginates(client, monkeypatch):
    """The scan must walk every playlistItems page, not just the first."""
    import requests, scanner
    q = _ensure_queue(client)

    pages = [
        _playlist_page(['VtScanVid1', 'VtScanVid2'], next_token='p2'),
        _playlist_page(['VtScanVid3']),
    ]
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResp(pages[len(calls) - 1])

    monkeypatch.setattr(requests, 'get', fake_get)

    with client.application.app_context():
        scanner.get_channel_video_list(('UCVtTestChannel1',))

    assert len(calls) == 2
    assert calls[0][0] == 'https://www.googleapis.com/youtube/v3/playlistItems'
    assert calls[0][1]['params']['playlistId'] == 'UUVtTestChannel1'
    assert calls[0][1]['params']['maxResults'] == 50
    assert calls[1][1]['params']['pageToken'] == 'p2'
    assert q.qsize() == 3
    urls = sorted(qo.url for qo in list(q.queue))
    assert urls == [
        'https://www.youtube.com/watch?v=VtScanVid1',
        'https://www.youtube.com/watch?v=VtScanVid2',
        'https://www.youtube.com/watch?v=VtScanVid3',
    ]


def test_channel_scan_stops_when_caught_up(client, monkeypatch):
    """A page with nothing new means everything older is known: stop paging."""
    import requests, scanner
    q = _ensure_queue(client)

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp) values('VtScanVid1','UCVtTestChannel1','UCVtTestChannel1','{}','/videos/1','2024-01-01 10:00:00',0,0);")
    con.close()

    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **kw: (calls.append(url), _FakeResp(_playlist_page(['VtScanVid1'], next_token='p2')))[1])

    with client.application.app_context():
        scanner.get_channel_video_list(('UCVtTestChannel1',))

    assert len(calls) == 1   # did not fetch page 2
    assert q.qsize() == 0


def test_channel_scan_mixed_page_takes_new_only(client, monkeypatch):
    """New uploads on a page with known videos are enqueued, but paging stops
    there — a subscription must not backfill deep history."""
    import requests, scanner
    q = _ensure_queue(client)

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp) values('VtScanVid1','UCVtTestChannel1','UCVtTestChannel1','{}','/videos/1','2024-01-01 10:00:00',0,0);")
    con.close()

    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **kw: (calls.append(url), _FakeResp(_playlist_page(['VtScanVid3', 'VtScanVid1'], next_token='p2')))[1])

    with client.application.app_context():
        scanner.get_channel_video_list(('UCVtTestChannel1',))

    assert len(calls) == 1
    assert q.qsize() == 1
    assert list(q.queue)[0].url == 'https://www.youtube.com/watch?v=VtScanVid3'


def test_youtube_playlist_pager_normal_multipage_and_params(monkeypatch):
    import requests
    import providers.youtube as yt

    pages = [
        _playlist_page(['PagerVid1', 'PagerVid2'], next_token='p2'),
        _playlist_page(['PagerVid3']),
    ]
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResp(pages[len(calls) - 1])

    monkeypatch.setattr(requests, 'get', fake_get)

    assert list(yt.iter_playlist_pages('PLPagerTest')) == [
        ['PagerVid1', 'PagerVid2'],
        ['PagerVid3'],
    ]
    assert len(calls) == 2
    assert calls[0][0] == yt._PLAYLIST_ITEMS_ENDPOINT
    assert '?' not in calls[0][0]
    assert calls[0][1]['params'] == {
        'part': 'contentDetails',
        'playlistId': 'PLPagerTest',
        'maxResults': 50,
        'key': os.environ['VAULTTUBE_YTKEY'],
    }
    assert calls[1][1]['params']['pageToken'] == 'p2'
    assert calls[0][1]['timeout'] == 30


def test_youtube_playlist_pager_stops_on_repeated_token(monkeypatch):
    import requests
    import providers.youtube as yt

    pages = [
        _playlist_page(['TokenVid1'], next_token='same'),
        _playlist_page(['TokenVid2'], next_token='same'),
    ]
    calls = []
    monkeypatch.setattr(
        requests, 'get',
        lambda url, **kwargs: (
            calls.append(kwargs['params'].get('pageToken')),
            _FakeResp(pages[len(calls) - 1]),
        )[1],
    )

    with pytest.raises(yt.YouTubePlaylistTruncated):
        list(yt.iter_playlist_pages('PLRepeatedToken'))
    assert calls == [None, 'same']


def test_youtube_playlist_pager_stops_on_token_cycle(monkeypatch):
    import requests
    import providers.youtube as yt

    pages = [
        _playlist_page(['CycleVid1'], next_token='A'),
        _playlist_page(['CycleVid2'], next_token='B'),
        _playlist_page(['CycleVid3'], next_token='A'),
    ]
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs['params'].get('pageToken'))
        return _FakeResp(pages[len(calls) - 1])

    monkeypatch.setattr(requests, 'get', fake_get)

    with pytest.raises(yt.YouTubePlaylistTruncated):
        list(yt.iter_playlist_pages('PLTokenCycle'))
    assert calls == [None, 'A', 'B']


def test_youtube_playlist_pager_allows_identical_pages(monkeypatch):
    import requests
    import providers.youtube as yt

    pages = [
        _playlist_page(['RepeatedVid1'], next_token='A'),
        _playlist_page(['RepeatedVid1']),
    ]
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs['params'].get('pageToken'))
        return _FakeResp(pages[len(calls) - 1])

    monkeypatch.setattr(requests, 'get', fake_get)

    assert list(yt.iter_playlist_pages('PLRepeatedData')) == [
        ['RepeatedVid1'],
        ['RepeatedVid1'],
    ]
    assert calls == [None, 'A']


def test_youtube_playlist_pager_honors_total_results(monkeypatch):
    import requests
    import providers.youtube as yt

    pages = [
        _playlist_page(
            ['TotalVid1', 'TotalVid2', 'TotalVid3', 'TotalVid4', 'TotalVid5'],
            next_token='p2', total_results=7,
        ),
        _playlist_page(
            ['TotalVid6', 'TotalVid7'], next_token='misleading',
            total_results=7,
        ),
    ]
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs['params'].get('pageToken'))
        return _FakeResp(pages[len(calls) - 1])

    monkeypatch.setattr(requests, 'get', fake_get)

    assert [vid for page in yt.iter_playlist_pages('PLTotalBoundary')
            for vid in page] == [
        'TotalVid1', 'TotalVid2', 'TotalVid3', 'TotalVid4',
        'TotalVid5', 'TotalVid6', 'TotalVid7',
    ]
    assert calls == [None, 'p2']


def test_youtube_playlist_pager_hard_page_cap(monkeypatch):
    import requests
    import providers.youtube as yt

    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs['params'].get('pageToken'))
        number = len(calls)
        return _FakeResp(
            _playlist_page(
                ['CapVid%d' % number], next_token='p%d' % number
            )
        )

    monkeypatch.setattr(requests, 'get', fake_get)

    with pytest.raises(yt.YouTubePlaylistTruncated):
        list(yt.iter_playlist_pages('PLPageCap', max_pages=2))
    assert calls == [None, 'p1']


def test_youtube_playlist_pager_raises_quota_reason(monkeypatch):
    import requests
    import providers.youtube as yt

    payload = {
        'error': {
            'code': 403,
            'errors': [
                {'domain': 'youtube.quota', 'reason': 'quotaExceeded'},
            ],
            'message': 'The request cannot be completed because quota is gone.',
        },
    }
    monkeypatch.setattr(
        requests, 'get', lambda url, **kwargs: _FakeResp(payload)
    )

    with pytest.raises(yt.YouTubeQuotaExceeded):
        list(yt.iter_playlist_pages('PLQuotaReason'))


@pytest.mark.parametrize('error_payload', [
    {
        'code': 403,
        'errors': [{'reason': 'dailyLimitExceeded'}],
        'message': 'Daily limit exhausted.',
    },
    {
        'code': 403,
        'status': 'RESOURCE_EXHAUSTED',
        'message': 'Resource exhausted.',
    },
])
def test_youtube_playlist_pager_raises_alternate_quota_values(
        monkeypatch, error_payload):
    import requests
    import providers.youtube as yt

    monkeypatch.setattr(
        requests, 'get',
        lambda url, **kwargs: _FakeResp({'error': error_payload}),
    )

    with pytest.raises(yt.YouTubeQuotaExceeded):
        list(yt.iter_playlist_pages('PLResourceExhausted'))


def test_youtube_playlist_pager_treats_http_429_as_quota(monkeypatch):
    import requests
    import providers.youtube as yt

    monkeypatch.setattr(
        requests, 'get',
        lambda url, **kwargs: _FakeResp(
            {'error': {'message': 'Too many requests'}}, status_code=429
        ),
    )

    with pytest.raises(yt.YouTubeQuotaExceeded):
        list(yt.iter_playlist_pages('PLHttp429'))


def test_youtube_playlist_pager_normal_api_error_is_truncation(monkeypatch):
    import requests
    import providers.youtube as yt

    monkeypatch.setattr(
        requests, 'get',
        lambda url, **kwargs: _FakeResp({
            'error': {
                'code': 404,
                'errors': [{'reason': 'playlistNotFound'}],
            },
        }),
    )

    with pytest.raises(yt.YouTubePlaylistTruncated):
        list(yt.iter_playlist_pages('PLNotFound'))


def test_youtube_playlist_pager_logs_consumer_stopped_early(
        monkeypatch, caplog):
    import logging
    import requests
    import providers.youtube as yt

    monkeypatch.setattr(
        requests, 'get',
        lambda url, **kwargs: _FakeResp(
            _playlist_page(['EarlyStopVid'], next_token='p2')
        ),
    )
    caplog.set_level(logging.INFO, logger='youtube')

    pages = yt.iter_playlist_pages('PLEarlyStop')
    assert next(pages) == ['EarlyStopVid']
    pages.close()

    assert any(
        'playlistItems scan PLEarlyStop' in record.getMessage()
        and 'stop=consumer stopped early' in record.getMessage()
        for record in caplog.records
    )


def test_youtube_quota_exception_propagates_from_scanner(monkeypatch):
    import scanner
    import providers.youtube as yt

    def quota_pages(playlist_id):
        raise yt.YouTubeQuotaExceeded('quota exhausted')
        yield

    monkeypatch.setattr(scanner, 'iter_playlist_pages', quota_pages)

    with pytest.raises(yt.YouTubeQuotaExceeded):
        scanner.get_channel_video_list(('UCQuotaPropagation',))


def test_scanner_truncation_logs_and_continues_to_next_subscription(
        client, monkeypatch, caplog):
    import logging
    import scanner
    import providers.youtube as yt

    _ensure_queue(client)
    monkeypatch.setattr(
        scanner, 'get_active_subscriptions',
        lambda: [('UCTruncatedFirst',), ('UCServicedSecond',)],
    )
    monkeypatch.setattr(scanner, 'get_active_playlist_subs', lambda: [])
    processed = []

    def fake_pages(playlist_id, request_budget=None):
        if playlist_id == scanner.uploads_playlist_id('UCTruncatedFirst'):
            raise yt.YouTubePlaylistTruncated('test truncation')
        yield ['AfterTruncationVid']

    monkeypatch.setattr(scanner, 'iter_playlist_pages', fake_pages)
    monkeypatch.setattr(scanner, 'check_db_video', lambda video_id: False)
    monkeypatch.setattr(
        scanner, 'enqueue',
        lambda queue_object, queue: processed.append(queue_object.url),
    )
    monkeypatch.setattr(scanner, 'cleanup_old_errors', lambda days: None)
    monkeypatch.setattr(scanner, 'cleanup_old_queue_rows', lambda days: None)
    client.application.config[scanner._YOUTUBE_SCAN_CURSOR_CONFIG] = 0
    client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] = {}
    caplog.set_level(logging.ERROR, logger='scanner')

    scanner.scan_once(client.application)

    assert processed == [
        'https://www.youtube.com/watch?v=AfterTruncationVid',
    ]
    assert any(
        'Scanning Channel truncated on ChannelID UCTruncatedFirst'
        in record.getMessage()
        for record in caplog.records
    )


def test_scanner_quota_circuit_breaker_keeps_patreon_work(client, monkeypatch):
    import scanner
    import providers.youtube as yt

    youtube_calls = []
    patreon_calls = []
    playlist_calls = []
    monkeypatch.setattr(
        scanner, 'get_active_subscriptions',
        lambda: [('UCQuotaFirst',), ('12345',), ('UCQuotaSkipped',)],
    )
    monkeypatch.setattr(
        scanner, 'get_active_playlist_subs', lambda: [('PLQuotaSkipped',)]
    )

    def fake_channel(channel_id, request_budget):
        youtube_calls.append(channel_id[0])
        raise yt.YouTubeQuotaExceeded('quota exhausted')

    monkeypatch.setattr(scanner, 'get_channel_video_list', fake_channel)
    monkeypatch.setattr(
        scanner, 'get_playlist_video_list',
        lambda playlist_id, request_budget: playlist_calls.append(playlist_id[0]),
    )
    monkeypatch.setattr(
        scanner, 'scan_campaign',
        lambda campaign_id: patreon_calls.append(campaign_id),
    )
    monkeypatch.setattr(scanner, 'cleanup_old_errors', lambda days: None)
    monkeypatch.setattr(scanner, 'cleanup_old_queue_rows', lambda days: None)
    client.application.config[scanner._YOUTUBE_SCAN_CURSOR_CONFIG] = 0
    client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] = {}

    scanner.scan_once(client.application)

    assert youtube_calls == ['UCQuotaFirst']
    assert playlist_calls == []
    assert patreon_calls == ['12345']


def test_scanner_pass_uses_one_shared_request_budget(client, monkeypatch):
    import scanner

    monkeypatch.setenv('VAULTTUBE_YT_SCAN_BUDGET', '2')
    monkeypatch.setattr(
        scanner, 'get_active_subscriptions',
        lambda: [('UCBudget1',), ('UCBudget2',)],
    )
    monkeypatch.setattr(
        scanner, 'get_active_playlist_subs',
        lambda: [('PLBudget3',), ('PLBudgetSkipped',)],
    )
    seen_budgets = []
    calls = []

    def consume(kind, item_id, request_budget):
        seen_budgets.append(request_budget)
        calls.append((kind, item_id))
        request_budget.consume()

    monkeypatch.setattr(
        scanner, 'get_channel_video_list',
        lambda item, budget: consume('channel', item[0], budget),
    )
    monkeypatch.setattr(
        scanner, 'get_playlist_video_list',
        lambda item, budget: consume('playlist', item[0], budget),
    )
    monkeypatch.setattr(scanner, 'cleanup_old_errors', lambda days: None)
    monkeypatch.setattr(scanner, 'cleanup_old_queue_rows', lambda days: None)
    client.application.config[scanner._YOUTUBE_SCAN_CURSOR_CONFIG] = 0
    client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] = {}

    scanner.scan_once(client.application)

    assert calls == [
        ('channel', 'UCBudget1'),
        ('channel', 'UCBudget2'),
        ('playlist', 'PLBudget3'),
    ]
    assert len({id(budget) for budget in seen_budgets}) == 1
    assert seen_budgets[0].used == 2


def test_scanner_budget_rotates_work_across_passes(client, monkeypatch):
    import scanner

    monkeypatch.setenv('VAULTTUBE_YT_SCAN_BUDGET', '1')
    monkeypatch.setattr(
        scanner, 'get_active_subscriptions',
        lambda: [('UCFair1',), ('24680',), ('UCFair2',)],
    )
    monkeypatch.setattr(
        scanner, 'get_active_playlist_subs',
        lambda: [('PLFair3',), ('PLFair4',)],
    )
    serviced = []
    patreon_calls = []

    def service(kind, item, budget):
        budget.consume()
        serviced.append((kind, item[0]))

    monkeypatch.setattr(
        scanner, 'get_channel_video_list',
        lambda item, budget: service('channel', item, budget),
    )
    monkeypatch.setattr(
        scanner, 'get_playlist_video_list',
        lambda item, budget: service('playlist', item, budget),
    )
    monkeypatch.setattr(
        scanner, 'scan_campaign',
        lambda campaign_id: patreon_calls.append(campaign_id),
    )
    monkeypatch.setattr(scanner, 'cleanup_old_errors', lambda days: None)
    monkeypatch.setattr(scanner, 'cleanup_old_queue_rows', lambda days: None)
    client.application.config[scanner._YOUTUBE_SCAN_CURSOR_CONFIG] = 0
    client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] = {}

    for _ in range(4):
        scanner.scan_once(client.application)

    assert serviced == [
        ('channel', 'UCFair1'),
        ('channel', 'UCFair2'),
        ('playlist', 'PLFair3'),
        ('playlist', 'PLFair4'),
    ]
    assert patreon_calls == ['24680'] * 4


def test_scanner_budget_resumes_playlist_page_and_services_other_work(
        client, monkeypatch):
    import requests
    import scanner

    monkeypatch.setenv('VAULTTUBE_YT_SCAN_BUDGET', '1')
    monkeypatch.setattr(scanner, 'get_active_subscriptions', lambda: [])
    monkeypatch.setattr(
        scanner, 'get_active_playlist_subs',
        lambda: [('PLResumeMulti',), ('PLResumeOther',)],
    )
    calls = []
    processed = []

    def fake_get(url, **kwargs):
        params = kwargs['params']
        playlist_id = params['playlistId']
        page_token = params.get('pageToken')
        calls.append((playlist_id, page_token))
        if playlist_id == 'PLResumeMulti' and page_token is None:
            return _FakeResp(
                _playlist_page(['ResumeVid1'], next_token='page-two')
            )
        if playlist_id == 'PLResumeMulti' and page_token == 'page-two':
            return _FakeResp(_playlist_page(['ResumeVid2']))
        if playlist_id == 'PLResumeOther' and page_token is None:
            return _FakeResp(_playlist_page(['OtherVid1']))
        raise AssertionError(
            "Unexpected playlist request: %s %s" %
            (playlist_id, page_token)
        )

    _ensure_queue(client)
    monkeypatch.setattr(requests, 'get', fake_get)
    monkeypatch.setattr(scanner, 'check_db_video', lambda video_id: False)
    monkeypatch.setattr(
        scanner, 'enqueue',
        lambda queue_object, queue: processed.append(queue_object.url),
    )
    monkeypatch.setattr(
        scanner, 'insert_pl2vid_info',
        lambda playlist_id, video_id: None,
    )
    monkeypatch.setattr(scanner, 'cleanup_old_errors', lambda days: None)
    monkeypatch.setattr(scanner, 'cleanup_old_queue_rows', lambda days: None)
    client.application.config[scanner._YOUTUBE_SCAN_CURSOR_CONFIG] = 0
    client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] = {}

    for _ in range(3):
        scanner.scan_once(client.application)

    assert calls == [
        ('PLResumeMulti', None),
        ('PLResumeOther', None),
        ('PLResumeMulti', 'page-two'),
    ]
    assert processed == [
        'https://www.youtube.com/watch?v=ResumeVid1',
        'https://www.youtube.com/watch?v=OtherVid1',
        'https://www.youtube.com/watch?v=ResumeVid2',
    ]
    assert client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] == {}


def test_scanner_quota_resumes_failed_page_without_refetching_page_one(
        client, monkeypatch):
    import requests
    import scanner

    monkeypatch.setattr(scanner, 'get_active_subscriptions', lambda: [])
    monkeypatch.setattr(
        scanner, 'get_active_playlist_subs',
        lambda: [('PLQuotaResume',)],
    )
    calls = []
    processed = []
    page_two_attempts = 0

    def fake_get(url, **kwargs):
        nonlocal page_two_attempts
        params = kwargs['params']
        page_token = params.get('pageToken')
        calls.append(page_token)
        if page_token is None:
            return _FakeResp(
                _playlist_page(['QuotaResumeVid1'], next_token='page-two')
            )
        if page_token == 'page-two':
            page_two_attempts += 1
            if page_two_attempts == 1:
                return _FakeResp(
                    {'error': {'message': 'Too many requests'}},
                    status_code=429,
                )
            return _FakeResp(_playlist_page(['QuotaResumeVid2']))
        raise AssertionError("Unexpected page token: %s" % page_token)

    _ensure_queue(client)
    monkeypatch.setattr(requests, 'get', fake_get)
    monkeypatch.setattr(scanner, 'check_db_video', lambda video_id: False)
    monkeypatch.setattr(
        scanner, 'enqueue',
        lambda queue_object, queue: processed.append(queue_object.url),
    )
    monkeypatch.setattr(
        scanner, 'insert_pl2vid_info',
        lambda playlist_id, video_id: None,
    )
    monkeypatch.setattr(scanner, 'cleanup_old_errors', lambda days: None)
    monkeypatch.setattr(scanner, 'cleanup_old_queue_rows', lambda days: None)
    client.application.config[scanner._YOUTUBE_SCAN_CURSOR_CONFIG] = 0
    client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] = {}

    scanner.scan_once(client.application)
    scanner.scan_once(client.application)

    assert calls == [None, 'page-two', 'page-two']
    assert processed == [
        'https://www.youtube.com/watch?v=QuotaResumeVid1',
        'https://www.youtube.com/watch?v=QuotaResumeVid2',
    ]
    assert client.application.config[
        scanner._YOUTUBE_SCAN_CONTINUATIONS_CONFIG
    ] == {}


def test_download_playlist_fails_on_token_cycle_without_partial_queue(
        client, monkeypatch):
    import requests
    import providers.youtube as yt
    from QueueObject import QueueObject

    q = _ensure_queue(client)
    pages = [
        _playlist_page(['PartialVid1'], next_token='same'),
        _playlist_page(['PartialVid2'], next_token='same'),
    ]
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs['params'].get('pageToken'))
        return _FakeResp(pages[len(calls) - 1])

    monkeypatch.setattr(requests, 'get', fake_get)

    with client.application.app_context():
        result = yt.download_playlist(
            QueueObject('PLPartialCycle', '', 'youtube', 0, '')
        )

    assert result is False
    assert q.qsize() == 0
    assert calls == [None, 'same']


def test_download_playlist_fails_on_invalid_api_response(client, monkeypatch):
    import requests
    import providers.youtube as yt
    from QueueObject import QueueObject

    q = _ensure_queue(client)
    monkeypatch.setattr(
        requests, 'get',
        lambda url, **kwargs: _FakeResp({
            'error': {
                'code': 404,
                'errors': [{'reason': 'playlistNotFound'}],
            },
        }),
    )

    with client.application.app_context():
        result = yt.download_playlist(
            QueueObject('PLInvalidExpansion', '', 'youtube', 0, '')
        )

    assert result is False
    assert q.qsize() == 0


def test_download_playlist_writes_queue_rows(client, monkeypatch):
    """Playlist expansion must go through enqueue() so queue rows persist."""
    import requests
    import providers.youtube as yt
    q = _ensure_queue(client)

    monkeypatch.setattr(requests, 'get', lambda url, **kw: _FakeResp(_playlist_page(['VtPlVid1', 'VtPlVid2'])))

    from QueueObject import QueueObject
    with client.application.app_context():
        result = yt.download_playlist(QueueObject('PLVtTest123', '', 'youtube', 0, ''))

    assert result is True
    assert q.qsize() == 2

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select status from queue where url = 'https://www.youtube.com/watch?v=VtPlVid1'")
    row = cur.fetchone()
    assert row is not None and row[0] == 'pending'
    cur.execute("Select count(*) from pl2vid where playlistId = 'PLVtTest123' and videoId = 'VtPlVid1'")
    assert cur.fetchone()[0] == 1
    cur.execute("Select count(*) from pl2vid where playlistId = 'PLVtTest123' and videoId = 'VtPlVid2'")
    assert cur.fetchone()[0] == 1
    con.close()


def test_channel_source_url(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('UCVtTestChannel1','YT Test','{}',0);")
    cur.execute("""Insert into channels(channelid,channelname,json,subscribed) values('987654321099','Patreon Test','{"data":{"attributes":{"name":"Patreon Test","url":"https://www.patreon.com/vttest"}}}',0);""")
    cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('VtTestRedditUser','VtTestRedditUser','{}',0);")
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp) values('RedVid1','VtTestRedditUser','VtTestRedditUser','{}','/videos/1','2023-03-01 12:00:00',0,0);")
    cur.execute("Update videos set source='reddit' where id='RedVid1';")
    con.close()

    cases = {
        'UCVtTestChannel1': 'https://www.youtube.com/channel/UCVtTestChannel1',
        '987654321099': 'https://www.patreon.com/vttest',
        'VtTestRedditUser': 'https://www.reddit.com/user/VtTestRedditUser',
    }
    for channelid, expected in cases.items():
        response = client.get("/api/channel/" + channelid)
        data = json.loads(response.get_data(as_text=True))
        assert data['source_url'] == expected, channelid


def test_channel_source_url_unknown(client):
    response = client.get("/api/channel/NoSuchChannelXyz")
    data = json.loads(response.get_data(as_text=True))
    assert data['source_url'] is None
    assert data['channelname'] is None


def test_channel_info_enriched(client):
    """/api/channel now returns description, thumbnail_url, and video_count."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO channels(channelid, channelname, json, subscribed) "
        "VALUES('ChInfo1', 'Ch Info Name', '{\"items\":[{\"snippet\":{\"description\":\"Test description\",\"thumbnails\":{\"medium\":{\"url\":\"https://example.com/thumb.jpg\"}}}}]}', 1);"
    )
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched) "
        "VALUES('ChInfoVid1', 'Ch Info Name', 'ChInfo1', '{}', '/videos/1.mp4', '2024-01-01 10:00:00', 'Ch Info Video', 0);"
    )
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched) "
        "VALUES('ChInfoVid2', 'Ch Info Name', 'ChInfo1', '{}', '/videos/2.mp4', '2024-01-02 10:00:00', 'Ch Info Video 2', 1);"
    )
    con.commit()
    con.close()

    response = client.get("/api/channel/ChInfo1")
    data = json.loads(response.get_data(as_text=True))
    assert data['channelname'] == 'Ch Info Name'
    assert data['subscribed'] == 1
    assert data['description'] == 'Test description'
    assert data['thumbnail_url'] == 'https://example.com/thumb.jpg'
    assert data['video_count'] == 2


def test_channel_videos_paged(client):
    """/api/channel/<id>/<page> returns that channel's videos in getvids shape."""
    _insert_chvids()

    response = client.get("/api/channel/ChVids1/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    assert len(data) == 3
    ids = [v['id'] for v in data]
    assert 'ChVidsVid1' in ids
    assert 'ChVidsVid2' in ids
    assert 'ChVidsVid3' in ids
    assert all('channel_name' in v and 'title' in v for v in data)


def test_channel_videos_status_filter(client):
    """/api/channel/<id>/<page>?status=unwatched filters watched state."""
    _insert_chvids()
    response = client.get("/api/channel/ChVids1/0?status=unwatched")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert 'ChVidsVid1' in ids
    assert 'ChVidsVid3' in ids
    assert 'ChVidsVid2' not in ids

    response = client.get("/api/channel/ChVids1/0?status=watched")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert ids == ['ChVidsVid2']


def test_channel_videos_sort_direction(client):
    """/api/channel/<id>/<page>?sort=...&direction=... orders results."""
    _insert_chvids()
    response = client.get("/api/channel/ChVids1/0?sort=title&direction=asc")
    data = json.loads(response.get_data(as_text=True))
    titles = [v['title'] for v in data]
    assert titles == ['A', 'B', 'C']

    response = client.get("/api/channel/ChVids1/0?sort=title&direction=desc")
    data = json.loads(response.get_data(as_text=True))
    titles = [v['title'] for v in data]
    assert titles == ['C', 'B', 'A']


def test_getvids_channel_id_query_filter(client):
    """getvids accepts a single channelId query parameter."""
    _insert_chvids()
    response = client.get("/api/getvids/all/PublishedAt/desc/0?channelId=ChVids1")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert all(v['channelId'] == 'ChVids1' for v in data)
    assert 'ChVidsVid1' in ids


def test_insert_not_found_goes_to_ignorevid(client):
    import logging
    from database import insert_not_found
    insert_not_found('TombVid2')

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select count(*) from IgnoreVid where id = 'TombVid2'")
    assert cur.fetchone()[0] == 1
    cur.execute("Select count(*) from videos where id = 'TombVid2'")
    assert cur.fetchone()[0] == 0
    con.close()


def test_tombstone_migration(client):
    con = _db_connect()
    cur = con.cursor()
    # A legacy-style tombstone row, as old insert_not_found wrote them
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,watched,timestamp,length) values('TombVid1','404','404','404','404',1,0,'0');")
    con.close()

    response = client.get("/api/checkdb")
    assert response.text == "True"

    con = _db_connect()
    cur = con.cursor()
    cur.execute("Select count(*) from videos where id = 'TombVid1'")
    assert cur.fetchone()[0] == 0
    cur.execute("Select count(*) from IgnoreVid where id = 'TombVid1'")
    assert cur.fetchone()[0] == 1
    con.close()


def test_getvids_deleted_filter(client):
    con = _db_connect()
    cur = con.cursor()
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp,isDeleted) values('DelVid1','GetVidCh1','GetVidCh1','{}','/videos/1','2023-02-01 12:00:00',0,0,1);")
    cur.execute("Insert into videos(id,channel_name,channelId,json,filepath,PublishedAt,watched,timestamp,isDeleted) values('DelVid2','GetVidCh1','GetVidCh1','{}','/videos/2','2023-02-02 12:00:00',0,0,0);")
    con.close()

    response = client.get("/api/getvids/all/PublishedAt/desc/0?deleted=1&channel_ids[]=GetVidCh1")
    data = json.loads(response.get_data(as_text=True))
    ids = [v['id'] for v in data]
    assert 'DelVid1' in ids
    assert 'DelVid2' not in ids


def test_queue_page(client):
    response = client.get("/queue.html")
    assert response.status_code == 200
    assert b'active-list' in response.data


def test_download_upload_redirect_to_queue(client):
    for path in ("/download.html", "/upload.html"):
        response = client.get(path)
        assert response.status_code == 301
        assert response.headers['Location'].endswith('/queue.html')


def test_retry_download(client):
    import queue as _queue
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = _queue.Queue()
        q = client.application.config['queue']
    while not q.empty():
        try:
            q.get_nowait()
        except _queue.Empty:
            break

    # URL is registered in conftest's _TEST_QUEUE_URLS for DB cleanup
    response = client.post("/api/downloads/retry", json={"url": "https://example.com/vt-test-queue-row"})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True
    assert data['data']['enqueued'] is True
    assert q.qsize() == 1

    # Same URL again: still pending, so it must be skipped
    response = client.post("/api/downloads/retry", json={"url": "https://example.com/vt-test-queue-row"})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True
    assert data['data']['enqueued'] is False
    assert q.qsize() == 1


def test_retry_download_missing_url(client):
    response = client.post("/api/downloads/retry", json={})
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is False


@pytest.mark.parametrize("path,marker", [
    ("/", b"vt-hero"),
    ("/browse.html", b"browse-grid"),
    ("/player.html", b"upnext-list"),
    ("/queue.html", b"active-list"),
    ("/channels.html", b"channels-grid"),
    ("/creator.html", b"creator-grid"),
    ("/search.html", b"search-grid"),
    ("/storage.html", b"channelChart"),
    ("/playlists.html", b"playlists-grid"),
    ("/playlist.html", b"playlist-grid"),
    ("/random.html", b"random-grid"),
])
def test_page_renders(client, path, marker):
    response = client.get(path)
    assert response.status_code == 200
    assert marker in response.data
    assert b"vt-sidebar" in response.data  # every page carries the app shell


def test_playlists_page(client):
    response = client.get("/api/playlists/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_creator_count(client):
    response = client.get("/api/creator/count/TestCreator")
    data = json.loads(response.get_data(as_text=True))
    assert 'count' in data
    assert isinstance(data['count'], int)


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True
    assert 'revision' in data['data']


def test_health_reports_deployed_revision(client, monkeypatch):
    monkeypatch.setenv("VAULTTUBE_REVISION", "test-commit-sha")
    response = client.get("/api/health")
    data = response.get_json()
    assert data["data"]["revision"] == "test-commit-sha"


def test_jellyfin_phase1_status_is_optional(client, monkeypatch):
    monkeypatch.delenv("VAULTTUBE_JELLYFIN_URL", raising=False)
    monkeypatch.delenv("VAULTTUBE_JELLYFIN_TOKEN", raising=False)
    response = client.get("/api/jellyfin/phase1/status")
    assert response.status_code == 200
    assert response.get_json()["data"]["configured"] is False


def test_jellyfin_phase1_rewrites_manifest_without_exposing_token(client, monkeypatch):
    import api

    monkeypatch.setenv("VAULTTUBE_JELLYFIN_URL", "http://jellyfin:8096")
    monkeypatch.setenv("VAULTTUBE_JELLYFIN_TOKEN", "super-secret-token")

    class FakeResponse:
        status_code = 200
        url = "http://jellyfin:8096/Videos/Item123/master.m3u8"
        headers = {"Content-Type": "application/vnd.apple.mpegurl"}
        text = (
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
            "/Videos/Item123/main.m3u8?api_key=super-secret-token&foo=bar\n"
        )

        def close(self):
            pass

    calls = []

    def fake_get(config, url, **kwargs):
        calls.append((config, url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(api, "upstream_get", fake_get)
    response = client.get("/api/jellyfin/phase1/Item123/manifest.m3u8")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/api/jellyfin/phase1/Item123/asset/" in body
    assert "super-secret-token" not in body
    assert calls[0][0]["token"] == "super-secret-token"
    assert calls[0][2]["params"]["VideoCodec"] == "h264"


def test_jellyfin_phase1_asset_forwards_range(client, monkeypatch):
    import api
    import jellyfin

    monkeypatch.setenv("VAULTTUBE_JELLYFIN_URL", "http://jellyfin:8096")
    monkeypatch.setenv("VAULTTUBE_JELLYFIN_TOKEN", "test-token")
    config = jellyfin.get_config()
    encoded = jellyfin.encode_asset_url(
        config, "Item123", "http://jellyfin:8096/Videos/Item123/0.ts")

    class FakeResponse:
        status_code = 206
        url = "http://jellyfin:8096/Videos/Item123/0.ts"
        headers = {
            "Content-Type": "video/MP2T",
            "Content-Length": "5",
            "Content-Range": "bytes 0-4/5",
            "Accept-Ranges": "bytes",
        }

        def iter_content(self, chunk_size):
            yield b"abcde"

        def close(self):
            pass

    calls = []

    def fake_get(config, url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(api, "upstream_get", fake_get)
    response = client.get(
        "/api/jellyfin/phase1/Item123/asset/" + encoded,
        headers={"Range": "bytes=0-4"},
    )

    assert response.status_code == 206
    assert response.data == b"abcde"
    assert response.headers["Content-Range"] == "bytes 0-4/5"
    assert calls[0][1]["range_header"] == "bytes=0-4"


def test_jellyfin_asset_rejects_other_hosts(monkeypatch):
    import jellyfin

    config = {
        "base_url": "http://jellyfin:8096",
        "token": "test-token",
        "user_id": "",
        "verify_tls": True,
    }
    with pytest.raises(jellyfin.JellyfinProxyError):
        jellyfin.encode_asset_url(
            config, "Item123", "https://attacker.example/Videos/Item123/0.ts")


def test_composite_session_is_deterministic_and_bounded(monkeypatch, tmp_path):
    import composite

    source = tmp_path / "reaction.mp4"
    source.write_bytes(b"test")
    monkeypatch.setenv("VAULTTUBE_TRANSCODE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(composite, "source_path_for", lambda video_id: str(source))
    monkeypatch.setattr(composite, "get_duration", lambda path: 100.0)
    monkeypatch.setattr(composite, "get_config", lambda: {"token": "secret"})
    monkeypatch.setattr(
        composite, "item_info",
        lambda config, item_id: {"id": item_id, "name": "Episode", "duration": 80.0},
    )

    first_id, first = composite.create_session("Reaction1", "Item123", 10, 5)
    second_id, second = composite.create_session("Reaction1", "Item123", 10, 5)

    assert first_id == second_id
    assert first["duration"] == 75.0
    assert second["width"] == 1280
    assert composite.load_session(first_id)["companion_start"] == 5.0


def test_composite_ffmpeg_command_builds_side_by_side_mixed_audio(monkeypatch, tmp_path):
    import composite

    source = tmp_path / "reaction.mp4"
    source.write_bytes(b"test")
    monkeypatch.setattr(composite, "source_path_for", lambda video_id: str(source))
    monkeypatch.setattr(composite, "get_config", lambda: {
        "base_url": "http://jellyfin:8096", "token": "server-token"
    })
    metadata = {
        "reaction_id": "Reaction1", "item_id": "Item123",
        "reaction_start": 12.5, "companion_start": 3.0,
        "duration": 60.0, "width": 1280, "height": 360,
    }

    command = composite._ffmpeg_command(metadata, str(tmp_path))
    filter_graph = command[command.index("-filter_complex") + 1]

    assert "hstack=inputs=2" in filter_graph
    assert "amix=inputs=2" in filter_graph
    assert "amix=inputs=2:duration=shortest:normalize=0" in filter_graph
    assert "scale=640:360" in filter_graph
    assert "volume=0.8" not in filter_graph
    assert "channel_layouts=stereo" in filter_graph
    assert "volume=12.0dB[a0]" in filter_graph
    assert "volume=-8.0dB[a1]" in filter_graph
    assert "loudnorm=I=-16.0:LRA=11.0:TP=-1.5:linear=false" in filter_graph
    assert "aresample=48000" in filter_graph
    assert "api_key" not in " ".join(command)
    assert any(part.startswith("X-Emby-Token: server-token") for part in command)


def test_composite_audio_targets_change_the_cache_key(monkeypatch, tmp_path):
    import composite

    source = tmp_path / "reaction.mp4"
    source.write_bytes(b"test")
    monkeypatch.setenv("VAULTTUBE_TRANSCODE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(composite, "source_path_for", lambda video_id: str(source))
    monkeypatch.setattr(composite, "get_duration", lambda path: 100.0)
    monkeypatch.setattr(composite, "get_config", lambda: {"token": "secret"})
    monkeypatch.setattr(
        composite, "item_info",
        lambda config, item_id: {"id": item_id, "name": "Episode", "duration": 80.0},
    )

    original_id, original = composite.create_session("Reaction1", "Item123", 10, 5)
    monkeypatch.setenv("VAULTTUBE_COMPOSITE_LOUDNESS", "-14")
    louder_id, louder = composite.create_session("Reaction1", "Item123", 10, 5)

    assert original["pipeline_version"] == 2
    assert original["audio_loudness_i"] == -16.0
    assert original["reaction_gain_db"] == 12.0
    assert original["companion_gain_db"] == -8.0
    assert louder["audio_loudness_i"] == -14.0
    assert louder_id != original_id


def test_composite_rejects_invalid_audio_target(monkeypatch):
    import composite

    monkeypatch.setenv("VAULTTUBE_COMPOSITE_TRUE_PEAK", "2")
    with pytest.raises(composite.CompositeError, match="TRUE_PEAK is out of range"):
        composite._session_payload("Reaction1", "Item123", 10, 5)


def test_composite_source_gains_change_the_cache_key(monkeypatch, tmp_path):
    import composite

    source = tmp_path / "reaction.mp4"
    source.write_bytes(b"test")
    monkeypatch.setenv("VAULTTUBE_TRANSCODE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(composite, "source_path_for", lambda video_id: str(source))
    monkeypatch.setattr(composite, "get_duration", lambda path: 100.0)
    monkeypatch.setattr(composite, "get_config", lambda: {"token": "secret"})
    monkeypatch.setattr(
        composite, "item_info",
        lambda config, item_id: {"id": item_id, "name": "Episode", "duration": 80.0},
    )

    original_id, _ = composite.create_session("Reaction1", "Item123", 10, 5)
    monkeypatch.setenv("VAULTTUBE_COMPOSITE_REACTION_GAIN_DB", "3")
    adjusted_id, adjusted = composite.create_session("Reaction1", "Item123", 10, 5)

    assert adjusted["reaction_gain_db"] == 3.0
    assert adjusted_id != original_id


def test_composite_create_api_returns_playlist(client, monkeypatch):
    import api

    monkeypatch.setattr(
        api, "create_composite_session",
        lambda *args: ("a" * 24, {"duration": 100.0, "width": 1280, "height": 360}),
    )
    monkeypatch.setattr(api, "ensure_composite_running", lambda session_id: None)
    response = client.post("/api/companion/composite", json={
        "reaction_id": "Reaction1",
        "jellyfin_item_id": "Item123",
        "reaction_start": 12.5,
        "companion_start": 0,
    })

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["session_id"] == "a" * 24
    assert data["playlist"].endswith("/playlist.m3u8")


def _insert_companion_reaction(video_id="Reaction1"):
    import database

    con = database.get_connection()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO videos (id, title, filepath, length) VALUES (%s, %s, %s, %s)",
        (video_id, "Reaction", "test/reaction.mp4", "3600"),
    )
    cur.close()
    con.close()


def test_companion_link_database_round_trip_and_delete():
    import database

    _insert_companion_reaction()
    saved = database.save_companion_link(
        "Reaction1", "Item123", -12.25, 345.5, True
    )

    assert saved["reaction_id"] == "Reaction1"
    assert saved["jellyfin_item_id"] == "Item123"
    assert saved["sync_offset"] == -12.25
    assert saved["reaction_position"] == 345.5
    assert saved["synced"] is True
    assert database.get_companion_link("Reaction1") == saved
    assert database.delete_companion_link("Reaction1") is True
    assert database.get_companion_link("Reaction1") is None


def test_companion_state_api_validates_once_and_coalesces_progress(client, monkeypatch):
    import api

    _insert_companion_reaction()
    validated = []
    monkeypatch.setattr(api, "get_config", lambda: {"token": "test"})
    monkeypatch.setattr(
        api, "item_info",
        lambda config, item_id: validated.append(item_id) or {"id": item_id},
    )

    created = client.post("/api/companion/state/Reaction1", json={
        "jellyfin_item_id": "Item123",
        "sync_offset": -4.5,
        "reaction_position": 120.25,
        "synced": True,
    })
    progressed = client.post("/api/companion/state/Reaction1", json={
        "reaction_position": 140.75,
    })
    loaded = client.get("/api/companion/state/Reaction1")

    assert created.status_code == 200
    assert progressed.status_code == 200
    assert validated == ["Item123"]
    state = loaded.get_json()["data"]
    assert state["sync_offset"] == -4.5
    assert state["reaction_position"] == 140.75

    removed = client.delete("/api/companion/state/Reaction1")
    assert removed.get_json()["data"]["deleted"] is True
    assert client.get("/api/companion/state/Reaction1").get_json()["data"] is None


def test_companion_state_api_rejects_invalid_progress(client, monkeypatch):
    import api

    _insert_companion_reaction()
    monkeypatch.setattr(api, "get_config", lambda: {"token": "test"})
    monkeypatch.setattr(api, "item_info", lambda config, item_id: {"id": item_id})

    response = client.post("/api/companion/state/Reaction1", json={
        "jellyfin_item_id": "Item123",
        "sync_offset": 0,
        "reaction_position": -1,
        "synced": True,
    })

    assert response.status_code == 400
    assert "reaction_position" in response.get_json()["error"]


def test_player_contains_phase1_companion_controls(client):
    response = client.get("/player.html")
    assert response.status_code == 200
    assert b'id="companion-player"' in response.data
    assert b'id="companion-sync"' in response.data
    assert b'playsinline' in response.data
    assert b'var companionSynced = false' in response.data
    assert b'Unsynced \xe2\x80\x94 play the reaction to its sync point' in response.data
    assert b"classList.add('companion-mode')" in response.data
    assert b"video.canPlayType" in response.data
    assert b"'/api/transcode/'" in response.data
    assert b"navigator.maxTouchPoints > 1" in response.data
    assert b"'/api/companion/composite'" in response.data
    assert b"Play composite" in response.data
    assert b"/api/companion/state/" in response.data
    assert b"canonicalReactionPosition" in response.data
    assert b"restoreSavedCompanion" in response.data
    assert b"keepalive: !!force" in response.data
    assert b"window.addEventListener('pagehide'" in response.data
    assert b'id="companion-remove"' in response.data


def test_companion_players_use_equal_letterboxed_viewports():
    css_path = os.path.join(os.path.dirname(__file__), "..", "app", "static", "css", "theme.css")
    with open(css_path, encoding="utf-8") as css_file:
        css = css_file.read()
    assert ".vt-companion-stage.active .vt-player-frame" in css
    assert ".vt-companion-stage.active .vt-player-frame::before" in css
    assert "padding-top: 56.25%" in css
    assert "position: absolute" in css
    assert "object-fit: contain" in css
    assert ".vt-player-layout.companion-mode > aside" in css
    assert ".vt-companion-stage.active.composite-playing" in css
    assert "padding-top: 28.125%" in css


def test_video_getvids_unwatched(client):
    response = client.get("/api/getvids/unwatched/PublishedAt/desc/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_video_getvids_watched(client):
    response = client.get("/api/getvids/watched/PublishedAt/desc/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_video_getvids_invalid_sort(client):
    """Test that invalid sort column falls back to default."""
    response = client.get("/api/getvids/unwatched/invalid_col/desc/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_search_empty_result(client):
    response = client.get("/api/search/zzzznonexistent12345/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    # Should be empty or not contain test-specific IDs
    for v in data:
        assert v.get('id') not in ('SearchFT1', 'SearchLIKE1')


def test_api_checkdb(client):
    response = client.get("/api/checkdb")
    assert response.status_code == 200
    assert response.text == "True"


def test_queue_status(client):
    # Initialize the queue for test client
    with client.application.app_context():
        if 'queue' not in client.application.config:
            client.application.config['queue'] = __import__('queue').Queue()

    response = client.get("/api/status/queue/")
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert 'dl_status' in data
    assert 'queue_size' in data
    assert 'queue_value' in data
    assert 'cur_id' in data
    assert 'cur_title' in data
    assert 'active' in data


def test_subscribe_playlist(client):
    """Test subscribing to a playlist (existing playlist in DB)."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO playlists(playlistId,playlistName,channelId,json,subscribed) "
        "VALUES('PLTest123', 'Test Playlist', 'TestCh1', '{}', 0);"
    )
    con.commit()
    con.close()

    response = client.get("/api/subscribe/playlist/PLTest123")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/playlist/PLTest123")
    assert response.text == '1'

    response = client.get("/api/unsubscribe/playlist/PLTest123")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/sub_status/playlist/PLTest123")
    assert response.text == '0'


def test_unsubscribe_nonexistent(client):
    """Test unsubscribing from a channel that doesn't exists (should not error)."""
    response = client.get("/api/unsubscribe/channel/NonExistentChannel12345")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True


def test_getvids_with_channel_filter(client):
    """Test getvids endpoint with channel_ids filter."""
    response = client.get("/api/getvids/unwatched/PublishedAt/desc/0?channel_ids[]=TestChannel1")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)


def test_download_error_entry(client):
    """Test that download errors can be logged and retrieved."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO download_errors(url, error_type, error_message) VALUES(%s, %s, %s);",
        ("https://example.com/bad", "TestError", "Test error message")
    )
    con.commit()
    con.close()

    response = client.get("/api/downloads/errors/")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(e['url'] == 'https://example.com/bad' for e in data)


def test_clear_download_errors(client):
    """Test clearing download errors."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute("INSERT INTO download_errors(url, error_type, error_message) VALUES(%s, %s, %s);",
                ("https://example.com/clear", "TestErr", "To clear"))
    con.commit()
    con.close()

    response = client.delete("/api/downloads/errors/")
    data = json.loads(response.get_data(as_text=True))
    assert data['success'] is True

    response = client.get("/api/downloads/errors/")
    data = json.loads(response.get_data(as_text=True))
    assert not any(e['url'] == 'https://example.com/clear' for e in data)


def test_dl_status_set_update_get():
    """Test individual dl_status_map operations."""
    video_id = "dl_test_vid_001"
    set_status(video_id, {'progress': '0%', 'title': 'Test Title', 'type': 'youtube'})
    status = get_status_copy()
    assert video_id in status
    assert status[video_id]['progress'] == '0%'
    assert status[video_id]['title'] == 'Test Title'

    update_status(video_id, {'progress': '50%', 'title': 'Updated Title'})
    status = get_status_copy()
    assert status[video_id]['progress'] == '50%'
    assert status[video_id]['title'] == 'Updated Title'

    del_status(video_id)
    status = get_status_copy()
    assert video_id not in status


def test_dl_status_overwrite(client):
    """Test that set_status overwrites existing entries."""
    video_id = "dl_test_vid_002"
    set_status(video_id, {'progress': '10%', 'title': 'First'})
    set_status(video_id, {'progress': '90%', 'title': 'Second'})
    status = get_status_copy()
    assert status[video_id]['progress'] == '90%'
    assert status[video_id]['title'] == 'Second'
    del_status(video_id)


def test_getvids_sort_options(client):
    """Test getvids with various sort options."""
    for sort_col in ['PublishedAt', 'AddedAt', 'title', 'watched']:
        response = client.get(f"/api/getvids/unwatched/{sort_col}/desc/0")
        data = json.loads(response.get_data(as_text=True))
        assert isinstance(data, list), f"Sort column '{sort_col}' should return a list"


def test_getvids_direction_options(client):
    """Test getvids with sort directions."""
    for direction in ['asc', 'desc']:
        response = client.get(f"/api/getvids/unwatched/PublishedAt/{direction}/0")
        data = json.loads(response.get_data(as_text=True))
        assert isinstance(data, list), f"Direction '{direction}' should return a list"


def test_getvids_invalid_direction(client):
    """Test that invalid direction falls back to default."""
    response = client.get("/api/getvids/unwatched/PublishedAt/invalid/0")
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, list)




def test_patreon_normalize_url():
    """Creator-prefixed Patreon URLs must be normalized for yt-dlp's extractor."""
    from providers.patreon import _normalize_url
    assert _normalize_url("https://www.patreon.com/TeeReacts/posts/foo-123") == "https://www.patreon.com/posts/foo-123"
    assert _normalize_url("https://www.patreon.com/posts/foo-123") == "https://www.patreon.com/posts/foo-123"
    assert _normalize_url("https://www.youtube.com/watch?v=abc") == "https://www.youtube.com/watch?v=abc"


def test_patreon_scan_campaign_filters(client, monkeypatch):
    """scan_campaign enqueues only new, viewable video posts."""
    import queue as _queue
    import providers.patreon as patreon

    posts = {'data': [
        {'id': '1', 'attributes': {'post_type': 'text_only', 'current_user_can_view': True, 'title': 'announcement'}},
        {'id': '2', 'attributes': {'post_type': 'video_external_file', 'current_user_can_view': False, 'title': 'locked'}},
        {'id': '3', 'attributes': {'post_type': 'video_external_file', 'current_user_can_view': True, 'title': 'already have'}},
        {'id': '4', 'attributes': {'post_type': 'video_external_file', 'current_user_can_view': True, 'title': 'new video'}},
    ]}
    monkeypatch.setattr(patreon, '_api_get', lambda url: posts)
    monkeypatch.setattr(patreon, 'check_db_video', lambda id: id == '3')
    # bypass DB persistence so the fake post URL never lands in the real queue table
    monkeypatch.setattr(patreon, 'enqueue', lambda qo, q: q.put(qo))
    monkeypatch.setenv('VAULTTUBE_PATREONCOOKIE', '/tmp/fake-cookie')

    client.application.config['queue'] = _queue.Queue()
    with client.application.app_context():
        patreon.scan_campaign('11752268')

    q = client.application.config['queue']
    assert q.qsize() == 1
    qo = q.get()
    assert qo.url == 'https://www.patreon.com/posts/4'
    assert qo.source == 'patreon'


def test_queue_persistence_roundtrip(client):
    """Queue rows are inserted as pending, resumable, and excluded once done."""
    from database import insert_queue_item, update_queue_status, get_resumable_queue_items
    from QueueObject import QueueObject

    logger = client.application.logger
    qo = QueueObject("https://example.com/vt-test-queue-row", "", "youtube", 0, "")
    rowid = qo.row_id = insert_queue_item(qo)
    assert rowid is not None
    try:
        assert any(r[0] == rowid for r in get_resumable_queue_items())
        update_queue_status(rowid, "done")
        assert not any(r[0] == rowid for r in get_resumable_queue_items())
    finally:
        con = _db_connect()
        cur = con.cursor()
        cur.execute("DELETE FROM queue WHERE id = %s", (rowid,))
        con.commit()
        con.close()


def test_download_single_persists_queue_row(client):
    """POST /api/download/single writes a queue table row."""
    import queue as _queue
    client.application.config['queue'] = _queue.Queue()
    url = "https://www.youtube.com/watch?v=vt-test-1"
    response = client.post("/api/download/single", json={"url": url})
    assert response.status_code == 200
    con = _db_connect()
    cur = con.cursor()
    try:
        cur.execute("SELECT status FROM queue WHERE url = %s", (url,))
        row = cur.fetchone()
        assert row is not None and row[0] == 'pending'
    finally:
        cur.execute("DELETE FROM queue WHERE url = %s", (url,))
        con.commit()
        con.close()


def test_handle_failure_retries_transient(client, monkeypatch):
    """Network errors are requeued with attempt tracking; permanent errors are not."""
    import downloader
    from QueueObject import QueueObject

    updates = []
    errors = []
    timers = []
    monkeypatch.setattr(downloader, 'update_queue_status',
                        lambda rowid, status, error=None, attempts=None: updates.append((status, attempts)))
    monkeypatch.setattr(downloader, 'insert_download_error',
                        lambda url, et, em: errors.append(et))

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            timers.append((delay, fn, args))
        def start(self):
            pass
    monkeypatch.setattr(downloader.threading, 'Timer', FakeTimer)

    logger = client.application.logger

    class FakeQueue:
        def put(self, item):
            pass
    q = FakeQueue()

    # Transient: requeued, no download_errors row
    qo = QueueObject("https://example.com/a")
    downloader.handle_failure(qo, q, 'Network Error', 'Connection timed out')
    assert updates == [('pending', 1)] and timers and not errors

    # Exhausted attempts: marked failed and recorded
    qo2 = QueueObject("https://example.com/b")
    qo2.attempts = downloader.MAX_ATTEMPTS - 1
    downloader.handle_failure(qo2, q, 'Network Error', 'Connection timed out')
    assert updates[-1] == ('failed', downloader.MAX_ATTEMPTS) and errors == ['Network Error']

    # Permanent: marked failed immediately
    qo3 = QueueObject("https://example.com/c")
    downloader.handle_failure(qo3, q, 'Provider Error', 'No supported media')
    assert updates[-1] == ('failed', 1) and errors[-1] == 'Provider Error'


def test_enqueue_dedups_pending_urls(client):
    """enqueue skips URLs already pending in the queue table."""
    import queue as _queue
    from queue_utils import enqueue
    from QueueObject import QueueObject

    logger = client.application.logger
    q = _queue.Queue()
    url = "https://example.com/vt-test-queue-row"
    try:
        assert enqueue(QueueObject(url), q) is True
        assert enqueue(QueueObject(url), q) is False
        assert q.qsize() == 1
    finally:
        con = _db_connect()
        cur = con.cursor()
        cur.execute("DELETE FROM queue WHERE url = %s", (url,))
        con.commit()
        con.close()


def test_patreon_inline_video_detection():
    """Video blocks in block-editor post bodies are detected; plain text is not."""
    from providers.patreon import _inline_video_media_ids, _post_has_video

    cjs_video = '{"type":"doc","content":[{"type":"video","attrs":{"fallback_strategy":"fallback","media_id":"645278519"}},{"type":"paragraph","content":[]}]}'
    cjs_text = '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"hi"}]}]}'

    assert _inline_video_media_ids(cjs_video) == ['645278519']
    assert _inline_video_media_ids(cjs_text) == []
    assert _inline_video_media_ids(None) == []
    assert _inline_video_media_ids('not json') == []

    assert _post_has_video({'post_type': 'video_external_file'}) is True
    assert _post_has_video({'post_type': 'text_only', 'content_json_string': cjs_video}) is True
    assert _post_has_video({'post_type': 'text_only', 'content_json_string': cjs_text}) is False
    assert _post_has_video({'post_type': 'text_only'}) is False


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg not available')
def test_video_codec_fields_and_hls(client):
    """End-to-end HLS transcode for a non-Apple video file."""
    import logging
    from transcoder import get_transcode_cache_dir

    vault = os.environ['VAULTTUBE_VAULTDIR']
    channel_dir = os.path.join(vault, 'HlsTestChannel')
    os.makedirs(channel_dir, exist_ok=True)
    source_path = os.path.join(channel_dir, 'HlsVid1.webm')

    # Generate a tiny VP9+Opus webm (definitely not Apple-direct)
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc=duration=5:size=320x240:rate=10',
        '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=5',
        '-c:v', 'libvpx-vp9', '-b:v', '100k',
        '-c:a', 'libopus', '-b:a', '32k',
        source_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        con = _db_connect()
        cur = con.cursor()
        cur.execute(
            "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, watched, timestamp, source) "
            "VALUES('HlsVid1', 'HlsTestChannel', 'HlsTestChannel', '{}', 'HlsTestChannel/HlsVid1.webm', "
            "'2024-01-01 10:00:00', 'HLS Test', 0, 0, 'youtube');"
        )
        con.commit()
        con.close()

        response = client.get("/api/video/HlsVid1")
        data = json.loads(response.get_data(as_text=True))
        v = data[0]
        assert v['id'] == 'HlsVid1'
        assert v['vcodec'] is not None
        assert v['acodec'] is not None
        assert v['container'] == 'webm'

        # HLS playlist request should launch transcode and return m3u8
        response = client.get("/api/transcode/HlsVid1/playlist.m3u8")
        assert response.status_code in (200, 404)  # 404 if first segment not ready yet
        if response.status_code == 200:
            assert response.content_type == 'application/vnd.apple.mpegurl'
            body = response.get_data(as_text=True)
            assert body.startswith('#EXTM3U')
            assert 'seg_' in body
        else:
            # Poll briefly for the playlist to appear
            for _ in range(20):
                time.sleep(0.5)
                response = client.get("/api/transcode/HlsVid1/playlist.m3u8")
                if response.status_code == 200:
                    break
            assert response.status_code == 200
            assert response.content_type == 'application/vnd.apple.mpegurl'
            body = response.get_data(as_text=True)
            assert body.startswith('#EXTM3U')
            assert 'seg_' in body

        # First segment should be TS
        response = client.get("/api/transcode/HlsVid1/seg_00000.ts")
        assert response.status_code == 200
        assert response.content_type == 'video/MP2T'

        # Non-numeric segment must be rejected by the segment route (404)
        response = client.get("/api/transcode/HlsVid1/seg_abc.ts")
        assert response.status_code == 404

        # Paths with slashes fall through to the legacy (gone) route
        response = client.get("/api/transcode/HlsVid1/seg_../../../etc/passwd.ts")
        assert response.status_code == 410

        # Legacy path-based endpoint is gone
        response = client.get("/api/transcode/foo/bar.mp4")
        assert response.status_code == 410
    finally:
        _delete_test_file(source_path)
        cache_dir = get_transcode_cache_dir('HlsVid1')
        shutil.rmtree(os.path.dirname(cache_dir), ignore_errors=True)


def test_hls_playlist_route_is_registered_and_returns_vod(client, monkeypatch, tmp_path):
    """Route contract must not depend on FFmpeg startup timing."""
    import api

    source = tmp_path / "source.webm"
    source.write_bytes(b"test source")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    monkeypatch.setattr(api, "source_path_for", lambda video_id: str(source))
    monkeypatch.setattr(
        api, "generate_hls",
        lambda video_id, source_path=None: str(cache_dir),
    )
    monkeypatch.setattr(api, "touch_cache_access", lambda video_id: None)
    monkeypatch.setattr(api, "get_duration", lambda source_path: 12.0)

    response = client.get("/api/transcode/RouteContract/playlist.m3u8")

    assert response.status_code == 200
    assert response.content_type == "application/vnd.apple.mpegurl"
    body = response.get_data(as_text=True)
    assert body.startswith("#EXTM3U")
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in body
    assert "#EXT-X-ENDLIST" in body


def test_build_vod_playlist_is_stable_vod():
    """Synthesized playlist is a complete VOD list with ENDLIST (issue #24)."""
    from transcoder import build_vod_playlist, segment_count_for_duration, SEGMENT_SECONDS

    # 20s @ 6s segments -> 4 segments (6, 6, 6, 2)
    assert segment_count_for_duration(20.0) == 4
    body = build_vod_playlist(20.0)
    lines = body.splitlines()

    assert lines[0] == '#EXTM3U'
    assert '#EXT-X-PLAYLIST-TYPE:VOD' in lines
    assert '#EXT-X-TARGETDURATION:%d' % SEGMENT_SECONDS in lines
    assert lines[-1] == '#EXT-X-ENDLIST'

    seg_lines = [l for l in lines if l.startswith('seg_')]
    assert seg_lines == ['seg_00000.ts', 'seg_00001.ts', 'seg_00002.ts', 'seg_00003.ts']

    # Last segment carries the remainder duration, not a full SEGMENT_SECONDS.
    inf = [l for l in lines if l.startswith('#EXTINF:')]
    assert inf[0] == '#EXTINF:6.000000,'
    assert inf[-1] == '#EXTINF:2.000000,'


def test_build_vod_playlist_unknown_duration():
    """Unknown/zero duration returns None so the caller can fall back."""
    from transcoder import build_vod_playlist, segment_count_for_duration

    assert build_vod_playlist(None) is None
    assert build_vod_playlist(0) is None
    assert segment_count_for_duration(None) == 0


def test_wait_for_segment_blocks_then_returns(tmp_path):
    """wait_for_segment blocks until the file lands, instead of 404-ing."""
    import threading
    from transcoder import wait_for_segment, _active, _active_lock

    cache_dir = str(tmp_path)
    key = ('WaitSegVid', 'deadbeef')
    seg_path = os.path.join(cache_dir, 'seg_00002.ts')

    # Register a fake running encode so wait_for_segment keeps polling.
    class _FakeProc:
        def poll(self):
            return None
    with _active_lock:
        _active[key] = {'process': _FakeProc(), 'last_request': time.time(), 'dir': cache_dir}

    def _write_later():
        time.sleep(0.5)
        with open(seg_path, 'wb') as f:
            f.write(b'\x47' * 188)  # one TS packet

    try:
        t = threading.Thread(target=_write_later)
        t.start()
        result = wait_for_segment(cache_dir, 2, key, timeout=5.0, interval=0.1)
        t.join()
        assert result == seg_path
    finally:
        with _active_lock:
            _active.pop(key, None)


def test_wait_for_segment_gives_up_when_encode_dead(tmp_path):
    """If the encode has exited and the file is absent, return None (-> 404)."""
    from transcoder import wait_for_segment

    # No entry in _active means _is_running() is False -> immediate give-up.
    result = wait_for_segment(str(tmp_path), 0, ('NoSuchVid', 'cafe'),
                              timeout=5.0, interval=0.1)
    assert result is None


def test_apple_direct_routing_detection():
    """is_apple_direct recognizes H264+AAC+mp4 and rejects everything else."""
    from transcoder import is_apple_direct

    assert is_apple_direct('h264', 'aac', 'mp4') is True
    assert is_apple_direct('avc1', 'mp4a', 'mov') is True
    assert is_apple_direct('h264', 'aac', 'webm') is False
    assert is_apple_direct('vp9', 'opus', 'webm') is False
    assert is_apple_direct(None, 'aac', 'mp4') is False


def _delete_test_file(path):
    try:
        os.remove(path)
    except Exception:
        pass


# ---------------- Chapter parsing (issue #27) ----------------

def _ch(desc):
    from chapters import parse_chapters
    return parse_chapters(desc)


def test_parse_chapters_valid():
    desc = ("In this video we build a thing.\n\n"
            "0:00 Intro\n"
            "2:15 Setup\n"
            "10:42 Demo\n"
            "45:00 Outro\n")
    ch = _ch(desc)
    assert [c['start'] for c in ch] == [0, 135, 642, 2700]
    assert ch[0]['title'] == 'Intro'
    assert ch[2]['title'] == 'Demo'


def test_parse_chapters_hours_and_separators():
    desc = ("0:00:00 - Intro\n"
            "01:30:00 | Main Event\n")
    ch = _ch(desc)
    assert [c['start'] for c in ch] == [0, 5400]
    assert ch[1]['title'] == 'Main Event'


def test_parse_chapters_first_not_zero_rejected():
    desc = "0:05 Intro\n2:15 Setup\n10:42 Demo\n"
    assert _ch(desc) == []


def test_parse_chapters_single_timestamp_rejected():
    assert _ch("0:00 Only one\n") == []


def test_parse_chapters_non_monotonic_rejected():
    desc = "0:00 Intro\n10:00 Middle\n5:00 Backwards\n"
    assert _ch(desc) == []


def test_parse_chapters_empty_and_none():
    assert _ch(None) == []
    assert _ch("") == []
    assert _ch("No timestamps here at all.\nJust text.") == []


def test_parse_chapters_ignores_inline_timestamps():
    # A timestamp in the middle of a sentence is not a chapter line.
    desc = "We meet at 2:30 for lunch.\n0:00 Start\n5:00 End\n"
    ch = _ch(desc)
    assert [c['start'] for c in ch] == [0, 300]


def test_chapters_endpoint(client):
    con = _db_connect()
    cur = con.cursor()
    desc = "0:00 Intro\n2:15 Setup\n10:42 Demo\n"
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, description) "
        "VALUES('ChapEP1', 'ChapCreator', 'ChapCh1', '{}', '/videos/chap1.mp4', "
        "'2024-01-01 10:00:00', 'Chapter Test', %s);",
        (desc,)
    )
    # Chapter-less video → empty list
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, description) "
        "VALUES('ChapEP2', 'ChapCreator', 'ChapCh1', '{}', '/videos/chap2.mp4', "
        "'2024-01-02 10:00:00', 'No Chapters', 'Just a plain description with no timestamps.');"
    )
    con.commit()
    con.close()

    data = json.loads(client.get("/api/chapters/ChapEP1").get_data(as_text=True))
    assert [c['start'] for c in data['chapters']] == [0, 135, 642]
    assert data['chapters'][1]['title'] == 'Setup'

    data = json.loads(client.get("/api/chapters/ChapEP2").get_data(as_text=True))
    assert data['chapters'] == []

    # Missing video → graceful empty list, not an error
    data = json.loads(client.get("/api/chapters/DoesNotExist").get_data(as_text=True))
    assert data['chapters'] == []


# ---------------------------------------------------------------------------
# Storage: filesize column, lazy backfill, /api/storage endpoint
# ---------------------------------------------------------------------------

def test_filesize_column_exists():
    """The filesize column is created by checkdb() (idempotent migration)."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'videos' AND column_name = 'filesize'",
        (os.environ['VAULTTUBE_DBNAME'],)
    )
    assert cur.fetchone()[0] == 1
    cur.close()
    con.close()


def test_update_video_filesize():
    """update_video_filesize writes the byte count and reads back correctly."""
    import logging
    from database import update_video_filesize

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title) "
        "VALUES('SizeVid1', 'TestCreator', 'TestCh1', '{}', '/videos/size1.mp4', "
        "'2024-01-01 10:00:00', 'Size Test');"
    )
    con.commit()
    con.close()

    update_video_filesize('SizeVid1', 1048576)

    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT filesize FROM videos WHERE id = 'SizeVid1'")
    row = cur.fetchone()
    cur.close()
    con.close()
    assert row is not None
    assert int(row[0]) == 1048576


def test_maybe_update_filesize_backfills_from_null(tmp_path):
    """_maybe_update_filesize populates NULL filesize from the real file size,
    and is idempotent (a second call does not re-probe or overwrite)."""
    import logging
    from backend import _maybe_update_filesize

    # Real file on disk with a known size
    fpath = str(tmp_path / "BackfillVid1.mkv")
    with open(fpath, 'wb') as f:
        f.write(b'x' * 2048)

    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title) "
        "VALUES('BackfillVid1', 'TestCreator', 'TestCh1', '{}', %s, "
        "'2024-01-01 10:00:00', 'Backfill Test');",
        (fpath,)
    )
    con.commit()
    con.close()

    log = logging.getLogger('test')
    _maybe_update_filesize('BackfillVid1', fpath)

    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT filesize FROM videos WHERE id = 'BackfillVid1'")
    row = cur.fetchone()
    cur.close()
    con.close()
    assert row is not None
    assert int(row[0]) == 2048

    # Second call must be a no-op (filesize already non-NULL → no probe)
    _maybe_update_filesize('BackfillVid1', fpath)

    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT filesize FROM videos WHERE id = 'BackfillVid1'")
    row = cur.fetchone()
    cur.close()
    con.close()
    assert int(row[0]) == 2048  # unchanged


def test_api_storage(client):
    """/api/storage returns byte totals, per-source, top channels, weekly
    growth, and a transcode-cache snapshot."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "REPLACE INTO channels(channelid, channelname, json, subscribed) "
        "VALUES('StorCh1', 'Stor Channel', '{}', 0);"
    )
    # Two YouTube videos with known sizes + one Patreon video with NULL filesize
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, source, filesize) "
        "VALUES('StorV1', 'Stor Channel', 'StorCh1', '{}', '/videos/s1.mkv', "
        "'2024-01-01 10:00:00', 'V1', 'youtube', 1000000);"
    )
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, source, filesize) "
        "VALUES('StorV2', 'Stor Channel', 'StorCh1', '{}', '/videos/s2.mkv', "
        "'2024-01-02 10:00:00', 'V2', 'youtube', 2500000);"
    )
    cur.execute(
        "REPLACE INTO videos(id, channel_name, channelId, json, filepath, PublishedAt, title, source, filesize) "
        "VALUES('StorV3', 'Patreon Creator', 'PatCh1', '{}', '/videos/s3.mp4', "
        "'2024-01-03 10:00:00', 'V3', 'patreon', NULL);"
    )
    con.commit()
    con.close()

    response = client.get("/api/storage")
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert isinstance(data, dict)

    # Totals: 3 videos, 3.5M bytes (NULL filesize excluded from sum)
    assert data['totals']['videos'] == 3
    assert data['totals']['bytes'] == 3500000

    # Per-source: youtube leads (3.5M), patreon present with 0 bytes (NULL sums to 0)
    sources = {row[0]: row for row in data['by_source']}
    assert 'youtube' in sources
    assert sources['youtube'][1] == 3500000
    assert sources['youtube'][2] == 2
    assert 'patreon' in sources
    assert sources['patreon'][1] == 0
    assert sources['patreon'][2] == 1

    # Top channel: StorCh1 with 3.5M; 4th element is channelId for creator links
    assert data['top_channels'][0][0] == 'Stor Channel'
    assert data['top_channels'][0][1] == 3500000
    assert data['top_channels'][0][2] == 2
    assert data['top_channels'][0][3] == 'StorCh1'
    assert len(data['top_channels']) <= 10

    # Weekly growth: 26 zero-filled buckets
    assert len(data['added_per_week_bytes']) == 26

    # Cache snapshot structure
    cache = data['cache']
    for key in ('bytes', 'entries', 'oldest', 'oldest_path', 'cap_bytes',
                'ttl_seconds', 'cache_dir', 'active_transcodes', 'cap_gb'):
        assert key in cache
    assert isinstance(cache['bytes'], int)
    assert isinstance(cache['entries'], int)
    assert isinstance(cache['cap_bytes'], int)
    assert cache['cap_bytes'] > 0


def test_cache_stats_reads_dir(tmp_path, monkeypatch):
    """cache_stats() walks the cache dir and reports bytes/entries/oldest."""
    import logging
    from transcoder import cache_stats

    # Point the cache root at a temp dir and lay out two cache entries
    base = str(tmp_path)
    monkeypatch.setenv('VAULTTUBE_TRANSCODE_CACHE_DIR', base)
    monkeypatch.setenv('VAULTTUBE_TRANSCODE_MAX_CACHE_GB', '50')

    vid_a = os.path.join(base, 'vidA', 'abcd0123456789ef')
    vid_b = os.path.join(base, 'vidB', '0123456789abcdef')
    os.makedirs(vid_a)
    os.makedirs(vid_b)
    with open(os.path.join(vid_a, 'playlist.m3u8'), 'w') as f:
        f.write('#EXTM3U\n')
    with open(os.path.join(vid_a, 'seg_00000.ts'), 'wb') as f:
        f.write(b'x' * 1024)
    with open(os.path.join(vid_b, 'seg_00000.ts'), 'wb') as f:
        f.write(b'y' * 2048)

    stats = cache_stats()
    assert stats['entries'] == 2
    assert stats['bytes'] == 1024 + len('#EXTM3U\n') + 2048
    assert stats['cap_bytes'] == 50 * 1024 * 1024 * 1024
    assert stats['oldest'] is not None
    assert stats['oldest_path'] is not None


def test_storage_page_loads(client):
    """/storage.html renders the merged dashboard (stats + storage)."""
    response = client.get("/storage.html")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # Storage KPIs
    assert 'id="kpi-total"' in body
    assert 'id="kpi-cache"' in body
    # Stats KPIs merged in
    assert 'id="kpi-runtime"' in body
    assert 'id="kpi-unwatched"' in body
    # Charts from both
    assert 'id="channelChart"' in body
    assert 'id="durationChart"' in body
    assert 'id="cache-grid"' in body


def test_stats_html_redirects_to_storage(client):
    """/stats.html 301-redirects to /storage.html (merged page)."""
    response = client.get("/stats.html")
    assert response.status_code == 301
    assert response.headers['Location'].endswith('/storage.html')


def test_get_video_missing_id_returns_404(client):
    response = client.get("/api/video/does-not-exist-zzz")
    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


def test_save_video_persists_filesize(tmp_path):
    """save_video's INSERT carries the filesize column end-to-end (guards
    against column/placeholder count drift introduced by the storage work)."""
    import logging
    import datetime as _dt
    from database import save_video

    # Minimal ret dict matching the shape process_new_video builds
    ret = {
        'channel_name': 'SaveVidCreator',
        'Json': {'id': 'SaveVid1', 'title': 'Save Test'},
        'Filepath': str(tmp_path / 'save1.mkv'),
        'PublishedAt': _dt.datetime(2024, 1, 1, 10, 0, 0),
        'channelId': 'SaveCh1',
        'length': _dt.timedelta(seconds=120),
        'title': 'Save Test',
        'description': '',
        'vcodec': 'h264',
        'acodec': 'aac',
        'container': 'mkv',
        'filesize': 4096,
    }

    log = logging.getLogger('test')
    save_video('SaveVid1', ret, None, source='youtube')

    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT filesize, vcodec, title FROM videos WHERE id = 'SaveVid1'")
    row = cur.fetchone()
    cur.close()
    con.close()
    assert row is not None
    assert int(row[0]) == 4096
    assert row[1] == 'h264'
    assert row[2] == 'Save Test'


# ---------------------------------------------------------------------------
# Logger naming regression (issue #33)
# ---------------------------------------------------------------------------
# Each module must log under its own component-specific logger name so log
# lines identify their source (replacing the generic 'main' logger).

def _capture_logger_records(name):
    """Attach a memory handler to logger <name>, return (logger, records, handler).

    Temporarily lowers the logger level to DEBUG so debug-level records (which
    most code paths emit) are captured regardless of the root logger's level."""
    import logging

    log = logging.getLogger(name)
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    log.addHandler(handler)
    prev_level = log.level
    log.setLevel(logging.DEBUG)
    return log, records, handler, prev_level


def _detach(log, handler, prev_level):
    log.removeHandler(handler)
    log.setLevel(prev_level)


def test_module_loggers_have_component_names():
    """Each module exposes a module-level logger with the expected short name."""
    import backend, scanner, downloader, transcoder, database, api
    import providers.youtube as youtube
    import providers.patreon as patreon
    import providers.reddit as reddit
    import queue_utils

    expected = {
        backend: 'backend',
        scanner: 'scanner',
        downloader: 'downloader',
        transcoder: 'transcoder',
        database: 'database',
        api: 'api',
        youtube: 'youtube',
        patreon: 'patreon',
        reddit: 'reddit',
        queue_utils: 'queue',
    }
    for mod, name in expected.items():
        assert mod.logger.name == name, \
            "%r.logger.name=%r, expected %r" % (mod.__name__, mod.logger.name, name)


def test_database_checkdb_logs_under_database_logger():
    """checkdb log records carry the 'database' logger name, not 'main'."""
    log, records, handler, prev_level = _capture_logger_records('database')
    try:
        import database
        database.checkdb()
    finally:
        _detach(log, handler, prev_level)

    assert records, "checkdb produced no log records"
    assert all(r.name == 'database' for r in records), \
        "found records not under 'database': %r" % {r.name for r in records}
    assert not any(r.name == 'main' for r in records), \
        "checkdb still logging under 'main'"


def test_backend_scan_vault_logs_under_backend_logger(client, monkeypatch):
    """scan_vault log records carry the 'backend' logger name."""
    log, records, handler, prev_level = _capture_logger_records('backend')
    try:
        import backend
        # Avoid hitting the real vault dir / DB queries; make get_video_index
        # return None so scan_vault exits immediately after logging the skip.
        monkeypatch.setattr(backend, 'get_video_index', lambda: None)
        with client.application.app_context():
            backend.scan_vault()
    finally:
        _detach(log, handler, prev_level)

    names = {r.name for r in records}
    assert 'backend' in names, "scan_vault did not log under 'backend' (got %r)" % names
    assert 'main' not in names
    # The "could not load video index" line is the documented skip message
    assert any('could not load video index' in r.getMessage() for r in records)


def test_downloader_handle_failure_logs_under_downloader_logger(client, monkeypatch):
    """handle_failure retry/failed log lines carry the 'downloader' logger name."""
    import downloader
    from QueueObject import QueueObject

    monkeypatch.setattr(downloader, 'update_queue_status',
                        lambda rowid, status, error=None, attempts=None: None)
    monkeypatch.setattr(downloader, 'insert_download_error',
                        lambda url, et, em: None)
    monkeypatch.setattr(downloader.threading, 'Timer',
                        lambda delay, fn, args=(): type('T', (), {'start': staticmethod(lambda: None)})())

    log, records, handler, prev_level = _capture_logger_records('downloader')
    try:
        # Transient Network Error → handle_failure logs the "Retrying" line
        qo = QueueObject("https://example.com/x")
        downloader.handle_failure(qo, type('Q', (), {'put': lambda self, item: None})(),
                                 'Network Error', 'Connection timed out')
    finally:
        _detach(log, handler, prev_level)

    names = {r.name for r in records}
    assert 'downloader' in names, "got %r" % names
    assert 'main' not in names
    assert any('Retrying' in r.getMessage() for r in records)


def test_youtube_download_logs_under_youtube_logger(monkeypatch):
    """yt-dlp download path logs under the 'youtube' logger name."""
    import providers.youtube as youtube

    # Force the cookie path so download_video reads a fake cookie file.
    import tempfile, os
    cookie = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    cookie.write('cookies')
    cookie.close()
    monkeypatch.setenv('VAULTTUBE_YTCOOKIE', cookie.name)

    def _fail(url, ydl_opts, cookies_contents, label=''):
        raise RuntimeError('stop')

    monkeypatch.setattr(youtube, '_download_attempt', _fail)

    log, records, handler, prev_level = _capture_logger_records('youtube')
    try:
        try:
            youtube.download_video('https://www.youtube.com/watch?v=abcdefghijk')
        except RuntimeError:
            pass
    finally:
        _detach(log, handler, prev_level)
        os.unlink(cookie.name)

    names = {r.name for r in records}
    assert 'youtube' in names, "got %r" % names
    assert 'main' not in names
    # The "Starting Download" debug line is emitted before the attempt
    assert any('Starting Download' in r.getMessage() for r in records)


def test_patreon_scan_logs_under_patreon_logger(client, monkeypatch):
    """Patreon scan_campaign log lines carry the 'patreon' logger name."""
    import queue as _queue
    import providers.patreon as patreon

    posts = {'data': [
        {'id': '4', 'attributes': {'post_type': 'video_external_file',
                                    'current_user_can_view': True, 'title': 'new video'}},
    ]}
    monkeypatch.setattr(patreon, '_api_get', lambda url: posts)
    monkeypatch.setattr(patreon, 'check_db_video', lambda id: False)
    monkeypatch.setattr(patreon, 'enqueue', lambda qo, q: q.put(qo))
    monkeypatch.setenv('VAULTTUBE_PATREONCOOKIE', '/tmp/fake-cookie')

    client.application.config['queue'] = _queue.Queue()
    log, records, handler, prev_level = _capture_logger_records('patreon')
    try:
        with client.application.app_context():
            patreon.scan_campaign('11752268')
    finally:
        _detach(log, handler, prev_level)

    names = {r.name for r in records}
    assert 'patreon' in names, "got %r" % names
    assert 'main' not in names
    # "Processing Patreon post" is the documented info line for a new post
    assert any('Processing Patreon post' in r.getMessage() for r in records)


def test_api_routes_log_under_api_logger(client):
    """API route handlers log under the 'api' logger name, not 'main'."""
    log, records, handler, prev_level = _capture_logger_records('api')
    try:
        # /api/getvids emits a debug "Called Latest" line on entry
        client.get('/api/getvids/all/PublishedAt/desc/0')
    finally:
        _detach(log, handler, prev_level)

    names = {r.name for r in records}
    assert 'api' in names, "got %r" % names
    assert 'main' not in names


def test_transcoder_logs_under_transcoder_logger(tmp_path):
    """get_codec_info failure path logs under the 'transcoder' logger name."""
    import transcoder

    log, records, handler, prev_level = _capture_logger_records('transcoder')
    try:
        # Nonexistent file → ffprobe fails → logs the error under 'transcoder'
        transcoder.get_codec_info(str(tmp_path / 'does-not-exist.mkv'))
    finally:
        _detach(log, handler, prev_level)

    names = {r.name for r in records}
    assert 'transcoder' in names, "got %r" % names
    assert 'main' not in names
    assert any('ffprobe failed' in r.getMessage() for r in records)


def test_scanner_logs_under_scanner_logger(client):
    """scanner log lines carry the 'scanner' logger name (not 'main')."""
    import scanner

    def fake_iter(playlist_id):
        # Yield one page of one unknown ID → scanner logs "Processing" then enqueues it
        yield ['ScannerProbeVid1']

    log, records, handler, prev_level = _capture_logger_records('scanner')
    try:
        orig = scanner.iter_playlist_pages
        scanner.iter_playlist_pages = fake_iter
        # check_db_video returns False so the scan enqueues instead of stopping
        import database
        orig_check = database.check_db_video
        database.check_db_video = lambda vid: False
        try:
            with client.application.app_context():
                scanner.get_channel_video_list(('UCScannerTest',))
        finally:
            scanner.iter_playlist_pages = orig
            database.check_db_video = orig_check
    finally:
        _detach(log, handler, prev_level)

    names = {r.name for r in records}
    assert 'scanner' in names, "got %r" % names
    assert 'main' not in names
    assert any('Processing' in r.getMessage() for r in records)


def test_root_logger_has_handlers_configured():
    """main.py attaches handlers to the root logger so all component loggers
    propagate to them (otherwise component log lines would be dropped)."""
    import logging
    root = logging.getLogger()
    assert root.handlers, "root logger has no handlers — component logs would be lost"


def test_no_logger_argument_in_function_signatures():
    """Provider/download/db entry-point functions no longer take a `logger`
    parameter — this guards against re-introducing the old convention."""
    import inspect
    import database, backend, scanner, downloader, queue_utils
    import providers.youtube as youtube
    import providers.patreon as patreon
    import providers.reddit as reddit

    checks = [
        (database.checkdb,           []),
        (database.save_video,        ['id', 'ret', 'img', 'source']),
        # get_connection keeps logger=None for test-monkeypatch compat; skip it
        (backend.scan_vault,         []),
        (backend.process_new_video,  ['id', 'fpath']),
        (scanner.start_scanner,     ['app']),
        (downloader.start_dl_queue, ['app']),
        (downloader.handle_failure,  ['qo', 'q', 'error_type', 'error_msg']),
        (queue_utils.enqueue,        ['qo', 'q']),
        (youtube.download,           ['qo']),
        (youtube.download_video,     ['url', 'cookies']),
        (patreon.download,           ['q']),
        (patreon.scan_campaign,      ['campaign_id']),
        (reddit.download,            ['q']),
    ]
    for fn, expected_args in checks:
        sig = inspect.signature(fn)
        actual = list(sig.parameters)
        assert 'logger' not in actual, \
            "%s still takes a 'logger' parameter: %r" % (fn.__qualname__, actual)
        if expected_args:
            assert actual == expected_args, \
                "%s signature %r != expected %r" % (fn.__qualname__, actual)


# ---------------------------------------------------------------------------
# Sticky alerts (issue: cookies-expired banner)
# ---------------------------------------------------------------------------

def _clear_all_alerts():
    from providers.base import _alerts, _alerts_lock
    with _alerts_lock:
        _alerts.clear()


def test_alerts_raise_clear_get():
    """raise_alert registers, get_alerts returns a snapshot, clear_alert removes."""
    from providers.base import raise_alert, clear_alert, get_alerts
    _clear_all_alerts()
    try:
        raise_alert('test_1', 'Title One', 'message one')
        raise_alert('test_2', 'Title Two', 'message two', kind='warning')
        alerts = {a['id']: a for a in get_alerts()}
        assert set(alerts) == {'test_1', 'test_2'}
        assert alerts['test_1']['title'] == 'Title One'
        assert alerts['test_1']['message'] == 'message one'
        assert alerts['test_1']['kind'] == 'error'
        assert alerts['test_2']['kind'] == 'warning'

        clear_alert('test_1')
        ids = {a['id'] for a in get_alerts()}
        assert ids == {'test_2'}
    finally:
        _clear_all_alerts()


def test_alerts_get_returns_copy():
    """Mutating the returned list/dicts must not affect the registry."""
    from providers.base import raise_alert, get_alerts
    _clear_all_alerts()
    try:
        raise_alert('test_copy', 'T', 'm')
        snapshot = get_alerts()
        snapshot.clear()
        assert any(a['id'] == 'test_copy' for a in get_alerts())
    finally:
        _clear_all_alerts()


def test_status_alerts_endpoint(client):
    """/api/status/alerts returns the current alerts as {success, data}."""
    from providers.base import raise_alert
    _clear_all_alerts()
    try:
        raise_alert('endpoint_test', 'Cookies Bad', 're-export cookies.txt')
        response = client.get('/api/status/alerts')
        assert response.status_code == 200
        body = json.loads(response.get_data(as_text=True))
        assert body['success'] is True
        ids = {a['id'] for a in body['data']}
        assert 'endpoint_test' in ids
    finally:
        _clear_all_alerts()


def test_download_attempt_cookie_invalid_raises_alert(monkeypatch):
    """_download_attempt raises the alert when yt-dlp reports invalid cookies."""
    import providers.youtube as yt
    from yt_dlp.utils import DownloadError
    from providers.base import get_alerts
    _clear_all_alerts()
    try:
        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                raise DownloadError("The provided YouTube account cookies are no longer valid.")
            def download(self, url): pass

        monkeypatch.setattr(yt.yt_dlp, 'YoutubeDL', lambda opts: FakeYDL(opts))
        monkeypatch.setenv('VAULTTUBE_VAULTDIR', '/tmp/vt_test_vault')

        raised = False
        try:
            yt._download_attempt('https://www.youtube.com/watch?v=abcdefghijk', {}, None)
        except DownloadError:
            raised = True
        assert raised, "DownloadError should still propagate after raising the alert"

        alerts = {a['id']: a for a in get_alerts()}
        assert 'youtube_cookies_invalid' in alerts
    finally:
        _clear_all_alerts()


def test_download_attempt_cookie_warning_raises_alert(monkeypatch):
    """A successful download that still emitted the cookie-invalid WARNING
    raises the alert (the common case: yt-dlp falls back to android player)."""
    import logging
    import providers.youtube as yt
    from providers.base import get_alerts
    _clear_all_alerts()
    try:
        class FakeYDL:
            def __init__(self, opts):
                self._logger = opts.get('logger')
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                if self._logger:
                    self._logger.warning("The provided YouTube account cookies are "
                                         "no longer valid.")
                return {
                    'channel_id': 'FakeCh1', 'id': 'WarnDlVid1', 'title': 'T',
                }
            def download(self, url): pass

        monkeypatch.setattr(yt.yt_dlp, 'YoutubeDL', lambda opts: FakeYDL(opts))
        monkeypatch.setenv('VAULTTUBE_VAULTDIR', '/tmp/vt_test_vault')
        # save_video_from_ytdlp would hit the DB; stub it out
        monkeypatch.setattr(yt, 'save_video_from_ytdlp', lambda *a, **k: None)

        result = yt._download_attempt('https://www.youtube.com/watch?v=WarnDlVid1', {}, None)
        assert result is True
        alerts = {a['id']: a for a in get_alerts()}
        assert 'youtube_cookies_invalid' in alerts
    finally:
        _clear_all_alerts()


def test_base_page_includes_alerts_container(client):
    """Every page carries the #vt-alerts container for the banner."""
    response = client.get('/')
    assert b'id="vt-alerts"' in response.data


def test_download_attempt_warning_then_exception_raises_alert(monkeypatch):
    """Same production bug for the download path: warning emitted, then a
    different DownloadError raised. The warning capture in finally must
    still fire the alert."""
    import providers.youtube as yt
    from yt_dlp.utils import DownloadError
    from providers.base import get_alerts
    _clear_all_alerts()
    try:
        class FakeYDL:
            def __init__(self, opts):
                self._logger = opts.get('logger')
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                if self._logger:
                    self._logger.warning("The provided YouTube account cookies are "
                                         "no longer valid.")
                raise DownloadError("Sign in to confirm you're not a bot.")
            def download(self, url): pass

        monkeypatch.setattr(yt.yt_dlp, 'YoutubeDL', lambda opts: FakeYDL(opts))
        monkeypatch.setenv('VAULTTUBE_VAULTDIR', '/tmp/vt_test_vault')

        raised = False
        try:
            yt._download_attempt('https://www.youtube.com/watch?v=abcdefghijk', {}, None)
        except DownloadError:
            raised = True
        assert raised, "DownloadError should still propagate"
        alerts = {a['id']: a for a in get_alerts()}
        assert 'youtube_cookies_invalid' in alerts, \
            "alert must fire from the WARNING even when a different DownloadError follows"
    finally:
        _clear_all_alerts()
