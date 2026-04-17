import pytest,os,json,mariadb
import threading
from providers.base import set_status, update_status, del_status, get_status_copy

def test_home(client):
    response = client.get("/")
    assert response.status_code == 200

def test_populate_db(client):
    try:
        response = client.get("/api/checkdb")
        assert response.text == "True"
        con = mariadb.connect(host=os.environ['VAULTTUBE_DBHOST'],user=os.environ['VAULTTUBE_DBUSER'],password=os.environ['VAULTTUBE_DBPASS'],database=os.environ['VAULTTUBE_DBNAME'],autocommit=True,port=int(os.environ['VAULTTUBE_DBPORT']))
        cur = con.cursor()
        #Populate Channels Table
        cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('Test123','Test123','Test123',0);")
        con.commit()
        cur.execute("Select * from channels limit 1;")
        assert 1 == cur.rowcount
        #Populate Video Table
        cur.execute("Insert into videos(id,youtuber,channelId,json,filepath,PublishedAt,watched,timestamp) values('Test123','Test123','Test123','TestJSON','/videos/1','2023-10-21 15:15:15',0,0);")
        con.commit()
        cur.execute("Select * from videos limit 1;")
        assert 1 == cur.rowcount
        con.close()
    except Exception as e:
        print(e)
        assert True == False


def test_subscribe(client):
    response = client.get("/api/channels/0")
    data = json.loads(response.get_data(as_text=True))
    if(len(data) > 0):
        id = data[0]['channelid']
        response = client.get("/api/sub_status/channel/%s"%id)
        assert response.text == '0'
        response = client.get("/api/subscribe/channel/%s"%id)
        data = json.loads(response.get_data(as_text=True))
        assert data['success'] == True
        response = client.get("/api/sub_status/channel/%s"%id)
        assert response.text == '1'
        response = client.get("/api/unsubscribe/channel/%s"%id)
        data = json.loads(response.get_data(as_text=True))
        assert data['success'] == True
        response = client.get("/api/sub_status/channel/%s"%id)
        assert response.text == '0'
    else:
        assert False == True

# Need to Rework this based on new sorting API
# def test_watched(client):
#     response = client.get('/api/unwatched/PublishedAt/0')
#     data = json.loads(response.get_data(as_text=True))
#     if(len(data) > 0):
#         id = data[0]['id']
#         response = client.get("/api/watch_status/%s"%id)
#         assert response.text == "0"
#         response = client.get("/api/video/%s"%id)
#         data = json.loads(response.get_data(as_text=True))
#         assert data[0]['timestamp'] == "0"
#         reponse = client.get("/api/set_timestamp/%s/%s"%("1515",id))
#         assert reponse.text == "True"
#         response = client.get("/api/video/%s"%id)
#         data = json.loads(response.get_data(as_text=True))
#         assert data[0]['timestamp'] == "1515"
#         response = client.get("/api/watched/%s"%id)
#         assert response.text == "True"
#         response = client.get("/api/watch_status/%s"%id)
#         assert response.text == "1"
#         response = client.get("/api/video/%s"%id)
#         data = json.loads(response.get_data(as_text=True))
#         assert data[0]['timestamp'] == "0"
#         response = client.get("/api/unwatched/%s"%id)
#         assert response.text == "True"
#         response = client.get("/api/watch_status/%s"%id)
#         assert response.text == "0"
#     else:
#         assert False == True


def test_dl_status_map_thread_safety():
    """Test that dl_status_map operations are thread-safe under concurrent access."""
    iterations = 100
    num_threads = 10
    errors = []

    def worker(thread_id):
        try:
            for i in range(iterations):
                video_id = f"vid_{thread_id}_{i}"
                set_status(video_id, {'progress': '0%', 'title': f'Title {i}', 'type': 'test'})
                update_status(video_id, {'progress': '50%'})
                status = get_status_copy()
                assert video_id in status
                del_status(video_id)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread safety errors: {errors}"

    final_status = get_status_copy()
    assert len(final_status) == 0


