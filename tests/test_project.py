import pytest
from app import main


@pytest.fixture()
def client():
    client = main.app.test_client()
    return client

def test_home(client):
    response = client.get("/")
    assert response.status_code == 200

