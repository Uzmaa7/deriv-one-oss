"""Lab vanilla scaffold — Python helpers for /run."""

APP_ID = "{{APP_ID}}"


def hello() -> dict:
    return {"app_id": APP_ID, "message": "Hello from {{APP_NAME}}"}
