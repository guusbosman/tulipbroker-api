import json, os, datetime

def handler(event, context):
    payload = {
        "version": os.getenv("APP_VERSION", "0.0.0"),
        "env": os.getenv("APP_ENV", "qa"),
        "region": os.getenv("AWS_REGION", "unknown"),
        "commit": os.getenv("GIT_SHA", ""),
        "buildTime": os.getenv("BUILD_TIME", datetime.datetime.utcnow().isoformat() + "Z"),
    }
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "x-api-region": payload["region"],
        },
        "body": json.dumps(payload)
    }

