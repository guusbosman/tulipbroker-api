import json
from types import SimpleNamespace

import pytest

from handlers import personas as personas_handler
from personas import personas as seed_personas


class FakePersonaTable:
    def __init__(self):
        self.items = {
            "clusius": {
                "userId": "clusius",
                "userName": "Carolus Clusius",
                "avatarUrl": "/avatars/clusius.png",
                "bio": "Botanist",
                "createdAt": 1,
                "updatedAt": 1,
            }
        }

    def scan(self, **kwargs):
        return {"Items": list(self.items.values())}

    def get_item(self, Key):
        return {"Item": self.items.get(Key["userId"])}

    def put_item(self, Item, ConditionExpression=None):
        user_id = Item["userId"]
        if user_id in self.items:
            raise personas_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
            )
        self.items[user_id] = Item

    def update_item(
        self,
        Key,
        UpdateExpression=None,
        ExpressionAttributeValues=None,
        ConditionExpression=None,
        ReturnValues=None,
    ):
        user_id = Key["userId"]
        if user_id not in self.items:
            raise personas_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
            )
        for key, value in ExpressionAttributeValues.items():
            field = key.lstrip(":")
            if field != "updatedAt":
                self.items[user_id][field] = value
        self.items[user_id]["updatedAt"] = ExpressionAttributeValues[":updatedAt"]
        return {"Attributes": self.items[user_id]}

    def delete_item(self, Key, ConditionExpression=None):
        user_id = Key["userId"]
        if user_id not in self.items:
            raise personas_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "DeleteItem"
            )
        del self.items[user_id]


@pytest.fixture(autouse=True)
def _personas_env(monkeypatch):
    fake_table = FakePersonaTable()
    monkeypatch.setattr(personas_handler, "PERSONAS_TABLE", "personas-table")
    monkeypatch.setattr(
        personas_handler,
        "dynamodb",
        SimpleNamespace(Table=lambda _name: fake_table),
    )
    personas_handler._cache_data.clear()
    personas_handler._cache_loaded_at = 0
    yield


def _event(path, method="GET", body=None):
    return {
        "rawPath": path,
        "body": json.dumps(body) if body else None,
        "requestContext": {"http": {"method": method, "path": path}},
    }


def test_list_personas_returns_items():
    response = personas_handler.handler(_event("/api/personas"), None)
    payload = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert payload["items"][0]["userId"] == "clusius"


def test_create_persona(monkeypatch):
    event = _event(
        "/api/personas",
        method="POST",
        body={"userName": "New User", "avatarUrl": "/avatars/new.png"},
    )
    response = personas_handler.handler(event, None)
    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["userName"] == "New User"


def test_update_persona():
    event = _event(
        "/api/personas/clusius",
        method="PUT",
        body={"userName": "Professor Clusius"},
    )
    response = personas_handler.handler(event, None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["userName"] == "Professor Clusius"


def test_delete_persona():
    event = _event("/api/personas/clusius", method="DELETE")
    response = personas_handler.handler(event, None)
    assert response["statusCode"] == 204


def test_get_persona_by_id():
    response = personas_handler.handler(_event("/api/personas/clusius"), None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["userId"] == "clusius"


def test_get_persona_not_found(monkeypatch):
    event = _event("/api/personas/unknown", method="GET")
    response = personas_handler.handler(event, None)
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"] == "Persona not found"


def test_list_personas_falls_back_to_seed_data(monkeypatch):
    monkeypatch.setattr(personas_handler, "PERSONAS_TABLE", None)
    monkeypatch.setattr(personas_handler, "dynamodb", None)
    personas_handler._cache_data.clear()
    personas_handler._cache_loaded_at = 0

    response = personas_handler.handler(_event("/api/personas"), None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert len(body["items"]) == len(seed_personas())
