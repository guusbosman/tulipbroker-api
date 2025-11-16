import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import datetime
from decimal import Decimal
import pytest

# Ensure handlers package (under src/) is importable when running pytest
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *_args, **_kwargs: SimpleNamespace()
boto3_stub.client = lambda *_args, **_kwargs: SimpleNamespace()
sys.modules.setdefault("boto3", boto3_stub)

dynamodb_stub = types.ModuleType("boto3.dynamodb")
conditions_stub = types.ModuleType("boto3.dynamodb.conditions")


class Key:
    def __init__(self, name):
        self.name = name

    def eq(self, value):
        return ("eq", self.name, value)


conditions_stub.Key = Key
sys.modules.setdefault("boto3.dynamodb", dynamodb_stub)
sys.modules.setdefault("boto3.dynamodb.conditions", conditions_stub)

botocore_stub = types.ModuleType("botocore")
exceptions_stub = types.ModuleType("botocore.exceptions")


class ClientError(Exception):
    def __init__(self, error_response, operation_name):
        super().__init__(error_response)
        self.response = error_response
        self.operation_name = operation_name


exceptions_stub.ClientError = ClientError
sys.modules.setdefault("botocore", botocore_stub)
sys.modules.setdefault("botocore.exceptions", exceptions_stub)

from handlers import orders  # noqa: E402  (import after sys.path tweak)


class FrozenDateTime(datetime.datetime):
    """Deterministic datetime subclass so utcnow() returns a fixed instant."""

    @classmethod
    def utcnow(cls):  # noqa: N802  (matching datetime API)
        return datetime.datetime(2024, 1, 1, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        base = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        if tz:
            return base.astimezone(tz)
        return base.replace(tzinfo=None)


class FakeTable:
    def __init__(self):
        self.stored_item = None
        self.deleted_keys = []
        self.updated_items = []

    def query(self, **kwargs):
        return {"Items": []}

    def put_item(self, **kwargs):
        self.stored_item = kwargs

    def delete_item(self, **kwargs):
        self.deleted_keys.append(kwargs)

    def update_item(self, **kwargs):
        self.updated_items.append(kwargs)
        if self.stored_item and "Item" in self.stored_item:
            value = kwargs.get("ExpressionAttributeValues", {}).get(":value")
            self.stored_item["Item"]["processingMs"] = int(value) if value is not None else None


class FakeDynamoResource:
    def __init__(self, table):
        self._table = table

    def Table(self, name):  # noqa: N802 (match boto3 API)
        assert name == "orders-table"
        return self._table


class FakeSQSClient:
    def __init__(self):
        self.sent_messages = []

    def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ORDERS_TABLE", "orders-table")
    monkeypatch.setenv("EVENTS_FIFO_URL", "https://sqs.test/orders.fifo")
    monkeypatch.setenv("MARKET_SYMBOL", "tulip")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_AVAILABILITY_ZONE", "us-east-1c")
    monkeypatch.setattr(orders, "ORDERS_TABLE", "orders-table")
    monkeypatch.setattr(orders, "EVENTS_FIFO_URL", "https://sqs.test/orders.fifo")
    monkeypatch.setattr(orders, "MARKET_SYMBOL", "tulip")


def test_post_order_happy_path(monkeypatch):
    table = FakeTable()
    fake_dynamo = FakeDynamoResource(table)
    fake_sqs = FakeSQSClient()
    logged_events = []

    monkeypatch.setattr(orders, "dynamodb", fake_dynamo)
    monkeypatch.setattr(orders, "sqs", fake_sqs)
    monkeypatch.setattr(orders, "_query_order_by_idempotency", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(orders.datetime, "datetime", FrozenDateTime, raising=False)
    monkeypatch.setattr(
        orders.uuid, "uuid4", lambda: UUID("12345678-1234-5678-1234-567812345678")
    )
    monkeypatch.setattr(
        orders.logger,
        "info",
        lambda message: logged_events.append(json.loads(message)),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(
            {
                "clientId": "unit-test",
                "userId": "clusius",
                "side": "BUY",
                "price": 10.5,
                "quantity": 2,
                "timeInForce": "GTC",
                "idempotencyKey": "abc123",
            }
        ),
    }

    response = orders.handler(
        event, SimpleNamespace(aws_request_id="ctx-123", availability_zone="us-east-1c")
    )

    assert response["statusCode"] == 201
    payload = json.loads(response["body"])
    assert payload["orderId"] == "12345678-1234-5678-1234-567812345678"
    assert payload["status"] == "ACCEPTED"
    assert payload["market"] == "tulip"
    assert payload["acceptedAt"] == "2024-01-01T12:00:00Z"
    assert payload["processingMs"] >= 0
    assert payload["userId"] == "clusius"
    assert payload["userName"]

    assert table.stored_item is not None
    assert table.stored_item["ConditionExpression"] == "attribute_not_exists(pk)"
    assert table.stored_item["Item"]["status"] == "ACCEPTED"
    assert table.stored_item["Item"]["processingMs"] is not None
    assert table.updated_items, "processing metric should update Dynamo record"

    assert fake_sqs.sent_messages, "order acceptance should enqueue SQS event"
    sent_message = fake_sqs.sent_messages[0]
    assert sent_message["QueueUrl"].endswith("orders.fifo")
    message_body = json.loads(sent_message["MessageBody"])
    assert message_body["orderId"] == payload["orderId"]
    assert message_body["side"] == "BUY"
    assert message_body["quantity"] == 2.0
    assert message_body["userId"] == "clusius"

    assert not table.deleted_keys, "successful path should not delete the record"
    accepted_event = next((evt for evt in logged_events if evt.get("event") == "OrderAccepted"), None)
    assert accepted_event is not None
    assert accepted_event["processingMs"] >= 0


def test_post_order_requires_user(monkeypatch):
    table = FakeTable()
    fake_dynamo = FakeDynamoResource(table)

    monkeypatch.setattr(orders, "dynamodb", fake_dynamo)
    monkeypatch.setattr(orders, "_query_order_by_idempotency", lambda *args, **kwargs: (None, None))

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(
            {
                "clientId": "unit-test",
                "side": "BUY",
                "price": 10.5,
                "quantity": 2,
                "timeInForce": "GTC",
                "idempotencyKey": "abc123",
            }
        ),
    }

    response = orders.handler(
        event, SimpleNamespace(aws_request_id="ctx-123", availability_zone="us-east-1c")
    )

    assert response["statusCode"] == 400
    details = json.loads(response["body"]).get("details", [])
    assert "userId is required" in details


def test_post_order_rolls_back_when_sqs_fails(monkeypatch):
    table = FakeTable()
    fake_dynamo = FakeDynamoResource(table)

    def _failing_send(**kwargs):
        raise orders.ClientError(
            {"Error": {"Code": "InternalError", "Message": "oops"}}, "SendMessage"
        )

    monkeypatch.setattr(orders, "dynamodb", fake_dynamo)
    monkeypatch.setattr(orders, "sqs", SimpleNamespace(send_message=_failing_send))
    monkeypatch.setattr(orders, "_query_order_by_idempotency", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(
        orders.uuid, "uuid4", lambda: UUID("12345678-1234-5678-1234-567812345678")
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(
            {
                "clientId": "unit-test",
                "userId": "clusius",
                "side": "BUY",
                "price": 10.5,
                "quantity": 2,
                "timeInForce": "GTC",
                "idempotencyKey": "abc123",
            }
        ),
    }

    response = orders.handler(
        event, SimpleNamespace(aws_request_id="ctx-123", availability_zone="us-east-1c")
    )

    assert response["statusCode"] == 502
    body = json.loads(response["body"])
    assert body["error"] == "Failed to enqueue order event"
    assert table.deleted_keys, "failed enqueue should delete the stored order"
    deleted_key = table.deleted_keys[0]["Key"]
    assert deleted_key["pk"] == "ORDER#12345678-1234-5678-1234-567812345678"
    assert deleted_key["sk"] == "ORDER#12345678-1234-5678-1234-567812345678"


def test_post_order_succeeds_when_idempotency_index_missing(monkeypatch):
    class MissingIndexTable(FakeTable):
        def query(self, **kwargs):
            raise orders.ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": "Requested resource not found",
                    }
                },
                "Query",
            )

    table = MissingIndexTable()
    fake_dynamo = FakeDynamoResource(table)
    fake_sqs = FakeSQSClient()

    monkeypatch.setattr(orders, "dynamodb", fake_dynamo)
    monkeypatch.setattr(orders, "sqs", fake_sqs)
    monkeypatch.setattr(orders.datetime, "datetime", FrozenDateTime, raising=False)
    monkeypatch.setattr(
        orders.uuid, "uuid4", lambda: UUID("12345678-1234-5678-1234-567812345678")
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(
            {
                "clientId": "unit-test",
                "userId": "clusius",
                "side": "BUY",
                "price": 10.5,
                "quantity": 2,
                "timeInForce": "GTC",
                "idempotencyKey": "abc123",
            }
        ),
    }

    response = orders.handler(
        event, SimpleNamespace(aws_request_id="ctx-123", availability_zone="us-east-1c")
    )

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["status"] == "ACCEPTED"
    assert table.stored_item is not None
    assert fake_sqs.sent_messages, "order acceptance should enqueue SQS event"


def test_get_orders_enriches_persona(monkeypatch):
    class ScanningTable(FakeTable):
        def scan(self, **kwargs):
            return {
                "Items": [
                    {
                        "orderId": "abc",
                        "userId": "oosterwijck",
                        "side": "BUY",
                        "price": Decimal("5"),
                        "quantity": Decimal("2"),
                        "status": "ACCEPTED",
                        "acceptedAt": "2024-01-01T12:00:00Z",
                        "clientId": "unit-test",
                        "region": "us-east-1",
                        "acceptedAz": "us-east-1c",
                        "processingMs": Decimal("10"),
                    },
                    {
                        "orderId": "def",
                        "userId": "unknown-user",
                        "side": "SELL",
                        "price": Decimal("7"),
                        "quantity": Decimal("1"),
                        "status": "ACCEPTED",
                        "acceptedAt": "2024-01-01T11:00:00Z",
                        "clientId": "unit-test",
                        "region": "us-east-1",
                        "acceptedAz": "us-east-1c",
                    },
                ]
            }

    table = ScanningTable()
    fake_dynamo = FakeDynamoResource(table)
    monkeypatch.setattr(orders, "dynamodb", fake_dynamo)

    response = orders.handler({"requestContext": {"http": {"method": "GET"}}}, None)

    assert response["statusCode"] == 200
    items = json.loads(response["body"]).get("items", [])
    assert items[0]["userName"] == "Maria van Oosterwijck"
    assert items[0]["avatarUrl"]
    assert items[1]["userName"] == "Unknown User"


def test_get_orders_returns_most_recent_first(monkeypatch):
    class PaginatedTable(FakeTable):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.pages = [
                {
                    "Items": [
                        {
                            "orderId": "old",
                            "userId": "clusius",
                            "side": "BUY",
                            "price": Decimal("1"),
                            "quantity": Decimal("1"),
                            "acceptedAt": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "LastEvaluatedKey": {"pk": "ORDER#old"},
                },
                {
                    "Items": [
                        {
                            "orderId": "new",
                            "userId": "oosterwijck",
                            "side": "SELL",
                            "price": Decimal("2"),
                            "quantity": Decimal("3"),
                            "acceptedAt": "2024-01-02T00:00:00Z",
                        }
                    ],
                },
            ]

        def scan(self, **kwargs):
            page = self.pages[self.calls]
            self.calls += 1
            response = {"Items": page["Items"]}
            if "LastEvaluatedKey" in page:
                response["LastEvaluatedKey"] = page["LastEvaluatedKey"]
            return response

    table = PaginatedTable()
    fake_dynamo = FakeDynamoResource(table)
    monkeypatch.setattr(orders, "dynamodb", fake_dynamo)

    event = {
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": {"limit": "2"},
    }

    response = orders.handler(event, None)
    assert response["statusCode"] == 200
    items = json.loads(response["body"]).get("items", [])
    assert [item["orderId"] for item in items] == ["new", "old"]
