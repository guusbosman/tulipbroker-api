import os
import datetime
from typing import Any

import psycopg


def _dsn() -> str:
    dsn = os.getenv("YB_DSN")
    if dsn:
        return dsn
    host = os.getenv("YB_HOST", "127.0.0.1")
    port = os.getenv("YB_PORT", "5433")
    dbname = os.getenv("YB_DB", "tulipbroker")
    user = os.getenv("YB_USER", "yugabyte")
    password = os.getenv("YB_PASSWORD", "yugabyte")
    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


def _row_to_item(row: tuple[Any, ...]) -> dict:
    (
        order_id,
        client_id,
        idempotency_key_hash,
        user_id,
        side,
        price,
        quantity,
        time_in_force,
        status,
        accepted_at,
        region,
        accepted_az,
        processing_ms,
        market,
        env,
        version,
    ) = row

    if isinstance(accepted_at, datetime.datetime):
        accepted_at = accepted_at.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "orderId": str(order_id),
        "clientId": client_id,
        "idempotencyKey": idempotency_key_hash,
        "userId": user_id,
        "side": side,
        "price": float(price),
        "quantity": float(quantity),
        "timeInForce": time_in_force,
        "status": status,
        "acceptedAt": accepted_at,
        "region": region,
        "acceptedAz": accepted_az,
        "processingMs": processing_ms,
        "market": market,
        "env": env,
        "version": version,
    }


def get_by_idempotency(client_id: str, idempotency_hash: str) -> dict | None:
    sql = """
        SELECT order_id, client_id, idempotency_key_hash, user_id, side, price, quantity,
               time_in_force, status, accepted_at, region, accepted_az, processing_ms,
               market, env, version
          FROM orders
         WHERE client_id = %s AND idempotency_key_hash = %s
         LIMIT 1
    """
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, (client_id, idempotency_hash))
        row = cur.fetchone()
        return _row_to_item(row) if row else None


def insert_order(item: dict) -> dict:
    sql = """
        INSERT INTO orders (
            order_id, client_id, idempotency_key_hash, user_id, side, price, quantity,
            time_in_force, status, accepted_at, region, accepted_az, processing_ms,
            market, env, version
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (client_id, idempotency_key_hash) DO NOTHING
        RETURNING order_id, client_id, idempotency_key_hash, user_id, side, price, quantity,
                  time_in_force, status, accepted_at, region, accepted_az, processing_ms,
                  market, env, version
    """
    values = (
        item["orderId"],
        item["clientId"],
        item["idempotencyKey"],
        item["userId"],
        item["side"],
        item["price"],
        item["quantity"],
        item["timeInForce"],
        item["status"],
        item["acceptedAt"].replace("Z", "+00:00"),
        item.get("region"),
        item.get("acceptedAz"),
        item.get("processingMs"),
        item.get("market"),
        item.get("env"),
        item.get("version"),
    )
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, values)
        row = cur.fetchone()
        if not row:
            existing = get_by_idempotency(item["clientId"], item["idempotencyKey"])
            if existing:
                return existing
        return _row_to_item(row) if row else item


def update_processing_ms(order_id: str, processing_ms: int) -> None:
    sql = "UPDATE orders SET processing_ms = %s WHERE order_id = %s"
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, (processing_ms, order_id))


def delete_order(order_id: str) -> None:
    sql = "DELETE FROM orders WHERE order_id = %s"
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, (order_id,))


def fetch_recent_orders(limit: int) -> list[dict]:
    sql = """
        SELECT order_id, client_id, idempotency_key_hash, user_id, side, price, quantity,
               time_in_force, status, accepted_at, region, accepted_az, processing_ms,
               market, env, version
          FROM orders
         ORDER BY accepted_at DESC
         LIMIT %s
    """
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        return [_row_to_item(row) for row in rows]
