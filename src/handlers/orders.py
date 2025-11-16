import json
import os
import uuid
import hashlib
import datetime
import logging
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from personas import get_persona

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)

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


def _query_order_by_idempotency(table, idempotency_hash: str):
    try:
        result = table.query(
            IndexName="IdempotencyKeyIndex",
            KeyConditionExpression=Key("idempotencyKey").eq(idempotency_hash),
            Limit=1,
        )
    except ClientError as exc:
        error = exc.response.get("Error", {}) if isinstance(exc.response, dict) else {}
        error_code = error.get("Code")
        if error_code in {"ResourceNotFoundException", "ValidationException"}:
            logger.warning(
                json.dumps(
                    {
                        "event": "IdempotencyIndexUnavailable",
                        "code": error_code,
                        "message": error.get("Message"),
                    }
                )
            )
            return None, None
        logger.exception("Failed to query orders by idempotency key")
        return None, "Failed to query orders"
    items = result.get("Items", [])
    return (items[0], None) if items else (None, None)


def _order_response_payload(order_item: dict) -> dict:
    persona = get_persona(order_item.get("userId"))
    return {
        "orderId": order_item.get("orderId"),
        "status": order_item.get("status", "ACCEPTED"),
        "acceptedAt": order_item.get("acceptedAt"),
        "market": order_item.get("market", MARKET_SYMBOL),
        "processingMs": order_item.get("processingMs"),
        "userId": persona.get("userId"),
        "userName": persona.get("userName"),
        "avatarUrl": persona.get("avatarUrl"),
        "bio": persona.get("bio"),
    }


def _resolve_region_and_az(context=None) -> tuple[str, str]:
    region = os.getenv("AWS_REGION", "unknown")
    az = os.getenv("AWS_AVAILABILITY_ZONE")
    if not az and context:
        az = getattr(context, "availability_zone", None)
    if not az:
        az = region
    return region, az


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
        return _handle_post(event, context)
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
        processing_ms = item.get("processingMs")
        persona = get_persona(item.get("userId"))
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
                "userId": persona.get("userId"),
                "userName": persona.get("userName"),
                "avatarUrl": persona.get("avatarUrl"),
                "bio": persona.get("bio"),
                "region": item.get("region"),
                "acceptedAz": item.get("acceptedAz"),
                "processingMs": float(processing_ms) if processing_ms is not None else None,
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


def _handle_post(event, context=None):
    request_started = time.perf_counter()
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
    user_id = body.get("userId")
    if not user_id or not isinstance(user_id, str):
        errors.append("userId is required")
    time_in_force = body.get("timeInForce", "GTC")

    if errors:
        return _response(400, {"error": "Validation failed", "details": errors})

    order_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    pk = f"ORDER#{order_id}"
    idempotency_hash = _hash_idempotency(client_id, idempotency_key)
    region, accepted_az = _resolve_region_and_az(context)

    table = dynamodb.Table(ORDERS_TABLE)
    existing_order, query_error = _query_order_by_idempotency(table, idempotency_hash)
    if query_error:
        return _response(500, {"error": query_error})
    if existing_order:
        logger.info(
            json.dumps(
                {
                    "event": "OrderReplay",
                    "clientId": client_id,
                    "idempotency": idempotency_hash,
                    "existingOrderId": existing_order.get("orderId"),
                }
            )
        )
        return _response(200, _order_response_payload(existing_order))

    item = {
        "pk": pk,
        "sk": pk,
        "orderId": order_id,
        "clientId": client_id,
        "userId": user_id,
        "side": side,
        "price": price_decimal,
        "quantity": quantity_decimal,
        "timeInForce": time_in_force,
        "status": "ACCEPTED",
        "acceptedAt": now,
        "region": region,
        "acceptedAz": accepted_az,
        "idempotencyKey": idempotency_hash,
        "simulationSeed": idempotency_hash,
        "env": os.getenv("APP_ENV", "qa"),
        "version": os.getenv("APP_VERSION", "0.0.0"),
        "market": MARKET_SYMBOL,
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
        "userId": user_id,
        "side": side,
        "price": float(price_decimal),
        "quantity": float(quantity_decimal),
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
        try:
            table.delete_item(Key={"pk": pk, "sk": pk})
        except ClientError:
            logger.exception("Rollback delete failed for order %s", order_id)
        return _response(502, {"error": "Failed to enqueue order event"})

    processing_ms = int((time.perf_counter() - request_started) * 1000)
    item["processingMs"] = processing_ms
    persisted_processing_metric = False
    try:
        table.update_item(
            Key={"pk": pk},
            UpdateExpression="SET processingMs = :value",
            ExpressionAttributeValues={":value": Decimal(processing_ms)},
        )
        persisted_processing_metric = True
    except ClientError as exc:
        logger.warning(
            json.dumps(
                {
                    "event": "ProcessingMetricPersistFailed",
                    "orderId": order_id,
                    "error": exc.response.get("Error", {}),
                }
            )
        )

    logger.info(
        json.dumps(
            {
                "event": "ProcessingMetricPersisted",
                "orderId": order_id,
                "processingMs": processing_ms,
                "persisted": persisted_processing_metric,
            }
        )
    )

    logger.info(
        json.dumps(
            {
                "event": "OrderAccepted",
                "orderId": order_id,
                "clientId": client_id,
                "userId": user_id,
                "side": side,
                "qty": float(quantity_decimal),
                "price": float(price_decimal),
                "timeInForce": time_in_force,
                "idempotency": idempotency_hash,
                "market": MARKET_SYMBOL,
                "acceptedAt": now,
                "processingMs": processing_ms,
            }
        )
    )

    return _response(
        201,
        _order_response_payload(item),
    )
