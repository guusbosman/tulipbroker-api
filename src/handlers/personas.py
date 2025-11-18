import base64
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List
from urllib.parse import unquote

import boto3
from botocore.exceptions import ClientError

from personas import personas as seed_personas

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
PERSONAS_TABLE = os.getenv("PERSONAS_TABLE")

_CACHE_TTL_SECONDS = 30
_cache_data: Dict[str, Dict[str, Any]] = {}
_cache_loaded_at = 0.0

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def handler(event, context):
    request_context = event.get("requestContext", {})
    http_info = request_context.get("http", {})
    method = http_info.get("method", "GET")
    raw_path = event.get("rawPath") or http_info.get("path", "")

    if raw_path == "/api/personas":
        if method == "GET":
            personas = _list_personas()
            return _response(200, {"items": personas})
        if method == "POST":
            return _create_persona(_parse_body(event))
        return _response(405, {"error": "Method not allowed"})

    if raw_path.startswith("/api/personas/"):
        user_id = unquote(raw_path.split("/api/personas/", 1)[1]).strip()
        if not user_id:
            return _response(400, {"error": "userId is required"})
        if method == "GET":
            persona = _get_persona(user_id)
            if not persona:
                return _response(404, {"error": "Persona not found"})
            return _response(200, persona)
        if method == "PUT":
            return _update_persona(user_id, _parse_body(event))
        if method == "DELETE":
            return _delete_persona(user_id)
        return _response(405, {"error": "Method not allowed"})

    return _response(404, {"error": "Not found"})


def _table():
    if not PERSONAS_TABLE:
        return None
    return dynamodb.Table(PERSONAS_TABLE)


def _list_personas() -> List[dict]:
    table = _table()
    if not table:
        logger.warning("PERSONAS_TABLE not configured; returning seed personas")
        return list(seed_personas().values())

    global _cache_loaded_at, _cache_data
    now = time.monotonic()
    if now - _cache_loaded_at < _CACHE_TTL_SECONDS and _cache_data:
        return _sorted_personas(list(_cache_data.values()))

    items: List[dict] = []
    start_key = None
    while True:
        scan_kwargs: Dict[str, Any] = {}
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key
        result = table.scan(**scan_kwargs)
        items.extend(result.get("Items", []))
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            break

    if not items:
        seed = list(seed_personas().values())
        _cache_data = {persona["userId"]: persona for persona in seed}
        _cache_loaded_at = now
        return seed

    _cache_data = {item["userId"]: item for item in items}
    _cache_loaded_at = now
    return _sorted_personas(items)


def _sorted_personas(items: List[dict]) -> List[dict]:
    return sorted(items, key=lambda persona: persona.get("userName", "").lower())


def _get_persona(user_id: str) -> Dict[str, Any]:
    table = _table()
    if not table:
        return seed_personas().get(user_id)
    try:
        result = table.get_item(Key={"userId": user_id})
    except ClientError:
        logger.exception("Failed to load persona %s", user_id)
        return None
    item = result.get("Item")
    if item:
        _cache_data[item["userId"]] = item
    return item


def _create_persona(payload: Dict[str, Any]):
    table = _table()
    if not table:
        return _response(500, {"error": "Personas store not configured"})

    user_name = (payload.get("userName") or "").strip()
    avatar_url = (payload.get("avatarUrl") or "").strip()
    bio = (payload.get("bio") or "").strip()
    requested_user_id = (payload.get("userId") or "").strip()

    if not user_name:
        return _response(400, {"error": "userName is required"})

    user_id = requested_user_id or _slugify(user_name)
    if not user_id:
        user_id = f"user-{uuid.uuid4().hex[:6]}"

    item = {
        "userId": user_id,
        "userName": user_name,
        "avatarUrl": avatar_url,
        "bio": bio,
        "createdAt": int(time.time()),
        "updatedAt": int(time.time()),
    }

    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(userId)")
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _response(409, {"error": "userId already exists"})
        logger.exception("Failed to create persona")
        return _response(500, {"error": "Failed to create persona"})

    _cache_data[item["userId"]] = item
    return _response(201, item)


def _update_persona(user_id: str, payload: Dict[str, Any]):
    table = _table()
    if not table:
        return _response(500, {"error": "Personas store not configured"})

    updates = []
    values = {}
    set_fields = {"userName": "userName", "avatarUrl": "avatarUrl", "bio": "bio"}
    for key, attr in set_fields.items():
        if key in payload:
            value = payload.get(key)
            updates.append(f"{attr} = :{attr}")
            values[f":{attr}"] = value.strip() if isinstance(value, str) else value

    if not updates:
        return _response(400, {"error": "No updatable fields provided"})

    updates.append("updatedAt = :updatedAt")
    values[":updatedAt"] = int(time.time())

    update_expr = "SET " + ", ".join(updates)

    try:
        result = table.update_item(
            Key={"userId": user_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(userId)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _response(404, {"error": "Persona not found"})
        logger.exception("Failed to update persona")
        return _response(500, {"error": "Failed to update persona"})

    item = result.get("Attributes")
    _cache_data[user_id] = item
    return _response(200, item)


def _delete_persona(user_id: str):
    table = _table()
    if not table:
        return _response(500, {"error": "Personas store not configured"})
    try:
        table.delete_item(Key={"userId": user_id}, ConditionExpression="attribute_exists(userId)")
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _response(404, {"error": "Persona not found"})
        logger.exception("Failed to delete persona")
        return _response(500, {"error": "Failed to delete persona"})

    _cache_data.pop(user_id, None)
    return _response(204, None)


def _parse_body(event) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body)
    try:
        if isinstance(body, (bytes, bytearray)):
            body = body.decode()
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def _response(status: int, payload: Any):
    body = "" if payload is None else json.dumps(payload)
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    return {"statusCode": status, "headers": headers, "body": body}


def _slugify(name: str) -> str:
    slug = _SLUG_PATTERN.sub("-", name.lower()).strip("-")
    return slug[:48]
