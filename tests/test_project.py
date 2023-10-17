import pytest
import json

def test_home(client):
    response = client.get("/")
    assert response.status_code == 200

def test_subscribe(client):
    response = client.get("/api/channels/0")
    data = json.loads(response.get_data(as_text=True))
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
