import json
import os
import datetime
import logging

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
logger = logging.getLogger()
logger.setLevel(logging.INFO)

ORDERS_TABLE = os.getenv("ORDERS_TABLE")
PULSE_SAMPLE_LIMIT = int(os.getenv("PULSE_SAMPLE_LIMIT", "200"))


def _response(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def _parse_ts(value: str):
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def handler(event, context):
    if not ORDERS_TABLE:
        return _response(500, {"error": "Orders table not configured"})

    table = dynamodb.Table(ORDERS_TABLE)
    try:
        result = table.scan(Limit=PULSE_SAMPLE_LIMIT)
    except ClientError:
        logger.exception("Failed to scan orders for market pulse")
        return _response(500, {"error": "Unable to compute pulse"})

    items = result.get("Items", [])
    minutes = {}
    latest_item = None
    total_buys = total_sells = 0

    for item in items:
        ts = _parse_ts(item.get("acceptedAt"))
        if not ts:
            continue
        minute = ts.replace(second=0, microsecond=0, tzinfo=datetime.timezone.utc)
        bucket = minutes.setdefault(minute, {"prices": [], "buys": 0, "sells": 0})
        price = float(item.get("price", 0))
        bucket["prices"].append(price)
        if item.get("side") == "BUY":
            bucket["buys"] += 1
            total_buys += 1
        else:
            bucket["sells"] += 1
            total_sells += 1
        if latest_item is None or ts > _parse_ts(latest_item.get("acceptedAt")):
            latest_item = item

    points = []
    for minute, bucket in sorted(minutes.items()):
        avg_price = (
            sum(bucket["prices"]) / len(bucket["prices"]) if bucket["prices"] else 0
        )
        points.append(
            {
                "ts": minute.isoformat().replace("+00:00", "Z"),
                "avgPrice": round(avg_price, 4),
                "buyOrders": bucket["buys"],
                "sellOrders": bucket["sells"],
            }
        )

    latest_price = float(latest_item.get("price", 0)) if latest_item else 0
    total_orders = total_buys + total_sells
    buy_share = total_buys / total_orders if total_orders else 0

    payload = {
        "points": points[-60:],
        "stats": {
            "lastPrice": latest_price,
            "buyShare": buy_share,
            "sellShare": 1 - buy_share if total_orders else 0,
            "ordersSampled": total_orders,
        },
    }

    return _response(200, payload)
