import pytest,os,json,mariadb

def test_home(client):
    response = client.get("/")
    assert response.status_code == 200

def test_empty_home(client):
    try:
        response = client.get("/api/checkdb")
        assert response.text == "True"
        con = mariadb.connect(host=os.environ['VAULTTUBE_DBHOST'],user=os.environ['VAULTTUBE_DBUSER'],password=os.environ['VAULTTUBE_DBPASS'],database=os.environ['VAULTTUBE_DBNAME'],autocommit=True,port=int(os.environ['VAULTTUBE_DBPORT']))
        cur = con.cursor()
        cur.execute("Insert into channels(channelid,channelname,json,subscribed) values('Test123','Test123','Test123',0);")
        con.commit()
        cur.execute("Select * from channels limit 1;")
        assert 1 == cur.rowcount
        con.close()
    except Exception as e:
        print(e)
        assert True == False


def test_subscribe(client):
    response = client.get("/api/channels/0")
    print(response.text)
    data = json.loads(response.get_data(as_text=True))
    if(len(data) > 0):
        id = data[0]['channelid']
        response = client.get("/api/sub_status/%s"%id)
        assert response.text == '0'
        response = client.get("/api/subscribe/%s"%id)
        assert response.text == "True"
        response = client.get("/api/sub_status/%s"%id)
        assert response.text == '1'
        response = client.get("/api/unsubscribe/%s"%id)
        assert response.text == "True"
        response = client.get("/api/sub_status/%s"%id)
        assert response.text == '0'
    else:
        assert False == True
