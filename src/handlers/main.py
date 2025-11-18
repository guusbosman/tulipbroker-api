# src/handlers/main.py
import json
import logging
from . import health, config, orders, metrics, personas

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    request_context = event.get("requestContext", {})
    http_info = request_context.get("http", {})
    path = event.get("rawPath") or http_info.get("path", "")
    method = http_info.get("method", "UNKNOWN")
    request_id = request_context.get("requestId") or getattr(context, "aws_request_id", "unknown")

    logger.info(
        json.dumps(
            {
                "event": "RequestReceived",
                "path": path,
                "method": method,
                "requestId": request_id,
            }
        )
    )
    if path == "/health":
        return health.handler(event, context)
    elif path == "/api/config":
        return config.handler(event, context)
    elif path == "/api/orders":
        return orders.handler(event, context)
    elif path == "/api/metrics/pulse":
        return metrics.handler(event, context)
    elif path == "/api/personas" or path.startswith("/api/personas/"):
        return personas.handler(event, context)
    response = {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps({"error": "Not found", "path": path}),
    }
    logger.info(
        json.dumps(
            {
                "event": "RequestNotFound",
                "path": path,
                "method": method,
                "requestId": request_id,
            }
        )
    )
    return response
