import json
import os
import uuid
import hashlib
import datetime
import logging
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

ORDERS_TABLE = os.getenv("ORDERS_TABLE")
EVENTS_FIFO_URL = os.getenv("EVENTS_FIFO_URL")
MARKET_SYMBOL = os.getenv("MARKET_SYMBOL", "tulip")


def _response(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def _hash_idempotency(client_id: str, idempotency_key: str) -> str:
    seed = f"{client_id}:{idempotency_key}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def handler(event, context):
    request_context = event.get("requestContext", {})
    http_info = request_context.get("http", {})
    method = http_info.get("method")
    path = http_info.get("path")
    request_id = request_context.get("requestId") or getattr(context, "aws_request_id", "unknown")

    logger.debug(
        json.dumps(
            {
                "event": "OrdersRequest",
                "method": method,
                "path": path,
                "requestId": request_id,
            }
        )
    )
    if method == "POST":
        return _handle_post(event)
    if method == "GET":
        return _handle_get(event)
    return _response(405, {"error": "Method not allowed"})


def _handle_get(event):
    if not ORDERS_TABLE:
        return _response(500, {"error": "Orders infrastructure not configured"})

    params = event.get("queryStringParameters") or {}
    limit_param = params.get("limit") if isinstance(params, dict) else None
    try:
        limit = min(int(limit_param), 50) if limit_param else 20
    except ValueError:
        return _response(400, {"error": "limit must be numeric"})

    table = dynamodb.Table(ORDERS_TABLE)
    # naive scan for now (Phase 1); we can switch to GSI later
    try:
        result = table.scan(Limit=limit)
    except ClientError:
        logger.exception("Failed to read orders")
        return _response(500, {"error": "Failed to load orders"})
    items = sorted(result.get("Items", []), key=lambda x: x.get("acceptedAt", ""), reverse=True)
    # convert Decimals to floats for JSON
    normalized = []
    for item in items:
        normalized.append(
            {
                "orderId": item.get("orderId"),
                "side": item.get("side"),
                "price": float(item.get("price", 0)),
                "quantity": float(item.get("quantity", 0)),
                "timeInForce": item.get("timeInForce"),
                "status": item.get("status"),
                "acceptedAt": item.get("acceptedAt"),
                "clientId": item.get("clientId"),
                "region": item.get("region"),
                "acceptedAz": item.get("acceptedAz"),
            }
        )

    logger.info(
        json.dumps(
            {
                "event": "OrdersFetched",
                "count": len(normalized),
            }
        )
    )
    return _response(200, {"items": normalized})


def _handle_post(event):
    if not ORDERS_TABLE or not EVENTS_FIFO_URL:
        return _response(500, {"error": "Orders infrastructure not configured"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON payload"})

    errors = []
    side = body.get("side")
    if side not in ("BUY", "SELL"):
        errors.append("side must be BUY or SELL")

    price = body.get("price")
    quantity = body.get("quantity")

    try:
        price_decimal = Decimal(str(price))
        if price_decimal <= 0:
            raise ValueError
    except Exception:
        errors.append("price must be a positive number")

    try:
        quantity_decimal = Decimal(str(quantity))
        if quantity_decimal <= 0:
            raise ValueError
    except Exception:
        errors.append("quantity must be a positive number")

    idempotency_key = body.get("idempotencyKey")
    if not idempotency_key or not isinstance(idempotency_key, str):
        errors.append("idempotencyKey is required")

    client_id = body.get("clientId") or "demo-ui"
    time_in_force = body.get("timeInForce", "GTC")

    if errors:
        return _response(400, {"error": "Validation failed", "details": errors})

    order_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow().isoformat() + "Z"
    pk = f"ORDER#{order_id}"
    idempotency_hash = _hash_idempotency(client_id, idempotency_key)

    table = dynamodb.Table(ORDERS_TABLE)
    item = {
        "pk": pk,
        "sk": pk,
        "orderId": order_id,
        "clientId": client_id,
        "side": side,
        "price": price_decimal,
        "quantity": quantity_decimal,
        "timeInForce": time_in_force,
        "status": "ACCEPTED",
        "acceptedAt": now,
        "region": os.getenv("AWS_REGION", "unknown"),
        "acceptedAz": os.getenv("AWS_REGION", "unknown"),
        "idempotencyKey": idempotency_hash,
        "simulationSeed": idempotency_hash,
        "env": os.getenv("APP_ENV", "qa"),
        "version": os.getenv("APP_VERSION", "0.0.0"),
    }

    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.warning(
                json.dumps(
                    {"event": "OrderDuplicate", "orderId": order_id, "clientId": client_id}
                )
            )
            return _response(409, {"error": "Order already exists"})
        logger.exception("Failed to persist order %s", order_id)
        return _response(500, {"error": "Failed to store order"})

    message = {
        "type": "OrderAccepted",
        "orderId": order_id,
        "clientId": client_id,
        "side": side,
        "price": float(price_decimal),
        "quantity": int(quantity_decimal),
        "timeInForce": time_in_force,
        "acceptedAt": now,
        "market": MARKET_SYMBOL,
        "env": os.getenv("APP_ENV", "qa"),
    }

    try:
        sqs.send_message(
            QueueUrl=EVENTS_FIFO_URL,
            MessageGroupId=f"market-{MARKET_SYMBOL}",
            MessageDeduplicationId=idempotency_hash,
            MessageBody=json.dumps(message),
        )
    except ClientError:
        logger.exception("Failed to enqueue order %s", order_id)
        return _response(502, {"error": "Failed to enqueue order event"})

    logger.info(
        json.dumps(
            {
                "event": "OrderAccepted",
                "orderId": order_id,
                "clientId": client_id,
                "side": side,
                "qty": float(quantity_decimal),
                "price": float(price_decimal),
                "timeInForce": time_in_force,
                "idempotency": idempotency_hash,
                "market": MARKET_SYMBOL,
                "acceptedAt": now,
            }
        )
    )

    return _response(
        201,
        {
            "orderId": order_id,
            "status": "ACCEPTED",
            "acceptedAt": now,
            "market": MARKET_SYMBOL,
        },
    )
