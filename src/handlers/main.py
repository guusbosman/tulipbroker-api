# src/handlers/main.py
import json
from . import health, config

def handler(event, context):
    # HTTP API v2 event
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path", "")
    if path == "/health":
        return health.handler(event, context)
    elif path == "/api/config":
        return config.handler(event, context)
    # default 404
    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps({"error": "Not found", "path": path}),
    }
