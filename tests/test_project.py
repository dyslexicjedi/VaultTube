import pytest,os,json,mariadb

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
        response = client.get("/api/sub_status/%s"%id)
        assert response.text == '0'
        response = client.get("/api/subscribe/channel/%s"%id)
        assert response.text == "True"
        response = client.get("/api/sub_status/%s"%id)
        assert response.text == '1'
        response = client.get("/api/unsubscribe/channel/%s"%id)
        assert response.text == "True"
        response = client.get("/api/sub_status/%s"%id)
        assert response.text == '0'
    else:
        assert False == True

def test_watched(client):
    response = client.get('/api/unwatched/PublishedAt/0')
    data = json.loads(response.get_data(as_text=True))
    if(len(data) > 0):
        id = data[0]['id']
        response = client.get("/api/watch_status/%s"%id)
        assert response.text == "0"
        response = client.get("/api/video/%s"%id)
        data = json.loads(response.get_data(as_text=True))
        assert data[0]['timestamp'] == "0"
        reponse = client.get("/api/set_timestamp/%s/%s"%("1515",id))
        assert reponse.text == "True"
        response = client.get("/api/video/%s"%id)
        data = json.loads(response.get_data(as_text=True))
        assert data[0]['timestamp'] == "1515"
        response = client.get("/api/watched/%s"%id)
        assert response.text == "True"
        response = client.get("/api/watch_status/%s"%id)
        assert response.text == "1"
        response = client.get("/api/video/%s"%id)
        data = json.loads(response.get_data(as_text=True))
        assert data[0]['timestamp'] == "0"
        response = client.get("/api/unwatched/%s"%id)
        assert response.text == "True"
        response = client.get("/api/watch_status/%s"%id)
        assert response.text == "0"
    else:
        assert False == True


