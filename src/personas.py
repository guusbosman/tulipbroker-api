import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError

PERSONAS_PATH = Path(__file__).resolve().parent.parent / "personas" / "personas.json"
PERSONAS_TABLE = os.getenv("PERSONAS_TABLE")

logger = logging.getLogger(__name__)
dynamodb = boto3.resource("dynamodb") if PERSONAS_TABLE else None

_CACHE_TTL_SECONDS = 30
_PERSONA_REGISTRY: Dict[str, dict] = {}
_CACHE_LOADED_AT = 0.0


def _load_seed_personas() -> Dict[str, dict]:
    if not PERSONAS_PATH.exists():
        return {}
    with PERSONAS_PATH.open("r", encoding="utf-8") as handle:
        personas = json.load(handle)
    return {persona["userId"]: persona for persona in personas}


def _load_personas() -> Dict[str, dict]:
    global _PERSONA_REGISTRY, _CACHE_LOADED_AT
    now = time.monotonic()
    if _PERSONA_REGISTRY and now - _CACHE_LOADED_AT < _CACHE_TTL_SECONDS:
        return _PERSONA_REGISTRY

    if not PERSONAS_TABLE or not dynamodb:
        _PERSONA_REGISTRY = _load_seed_personas()
        _CACHE_LOADED_AT = now
        return _PERSONA_REGISTRY

    table = dynamodb.Table(PERSONAS_TABLE)
    items = []
    start_key = None
    try:
        while True:
            scan_kwargs = {}
            if start_key:
                scan_kwargs["ExclusiveStartKey"] = start_key
            result = table.scan(**scan_kwargs)
            items.extend(result.get("Items", []))
            start_key = result.get("LastEvaluatedKey")
            if not start_key:
                break
    except ClientError:
        logger.exception("Failed to scan personas table, falling back to seed data")
        _PERSONA_REGISTRY = _load_seed_personas()
        _CACHE_LOADED_AT = now
        return _PERSONA_REGISTRY

    if not items:
        _PERSONA_REGISTRY = _load_seed_personas()
    else:
        _PERSONA_REGISTRY = {item["userId"]: item for item in items}
    _CACHE_LOADED_AT = now
    return _PERSONA_REGISTRY


UNKNOWN_PERSONA = {
    "userId": "unknown",
    "userName": "Unknown User",
    "avatarUrl": "",
    "bio": "",
}


def get_persona(user_id: Optional[str]) -> dict:
    registry = _load_personas()
    if not user_id:
        return UNKNOWN_PERSONA
    return registry.get(user_id, {**UNKNOWN_PERSONA, "userId": user_id})


def personas() -> Dict[str, dict]:
    return dict(_load_personas())
