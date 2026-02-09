import datetime
from types import SimpleNamespace

import pytest

from handlers import orders_yugabyte


class FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _row(accepted_at):
    return (
        "6e2f2b78-4b9e-4a35-9b74-9c6b24f8d7b1",
        "demo-ui",
        "hash",
        "clusius",
        "BUY",
        123.45,
        10,
        "GTC",
        "ACCEPTED",
        accepted_at,
        "us-east-2",
        "us-east-2a",
        12,
        "tulip",
        "qa",
        "0.1.0",
    )


def test_get_by_idempotency_formats_timestamp(monkeypatch):
    accepted_at = datetime.datetime(2026, 2, 8, 12, 0, tzinfo=datetime.timezone.utc)
    cursor = FakeCursor(fetchone_result=_row(accepted_at))
    monkeypatch.setattr(orders_yugabyte.psycopg, "connect", lambda *_a, **_k: FakeConn(cursor))

    item = orders_yugabyte.get_by_idempotency("demo-ui", "hash")

    assert item["acceptedAt"].endswith("Z")
    assert item["orderId"] == "6e2f2b78-4b9e-4a35-9b74-9c6b24f8d7b1"
    assert item["price"] == 123.45


def test_fetch_recent_orders(monkeypatch):
    cursor = FakeCursor(fetchall_result=[_row(datetime.datetime.now(datetime.timezone.utc))])
    monkeypatch.setattr(orders_yugabyte.psycopg, "connect", lambda *_a, **_k: FakeConn(cursor))

    items = orders_yugabyte.fetch_recent_orders(5)

    assert len(items) == 1
    assert items[0]["clientId"] == "demo-ui"
    assert cursor.executed[-1][1] == (5,)


def test_insert_order_falls_back_to_idempotency(monkeypatch):
    cursor = FakeCursor(fetchone_result=None)
    monkeypatch.setattr(orders_yugabyte.psycopg, "connect", lambda *_a, **_k: FakeConn(cursor))
    monkeypatch.setattr(
        orders_yugabyte,
        "get_by_idempotency",
        lambda *_a, **_k: {"orderId": "existing"},
    )

    item = orders_yugabyte.insert_order(
        {
            "orderId": "new",
            "clientId": "demo-ui",
            "idempotencyKey": "hash",
            "userId": "clusius",
            "side": "BUY",
            "price": 123.45,
            "quantity": 1,
            "timeInForce": "GTC",
            "status": "ACCEPTED",
            "acceptedAt": "2026-02-08T12:00:00Z",
            "region": "us-east-2",
            "acceptedAz": "us-east-2a",
            "processingMs": None,
            "market": "tulip",
            "env": "qa",
            "version": "0.1.0",
        }
    )

    assert item["orderId"] == "existing"


def test_update_and_delete(monkeypatch):
    cursor = FakeCursor()
    monkeypatch.setattr(orders_yugabyte.psycopg, "connect", lambda *_a, **_k: FakeConn(cursor))

    orders_yugabyte.update_processing_ms("order-1", 42)
    orders_yugabyte.delete_order("order-1")

    assert len(cursor.executed) == 2
