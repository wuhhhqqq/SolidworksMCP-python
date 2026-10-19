"""Remove invocation credentials from application logs."""

from typing import Any


def redact_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in {
                "api_key", "authorization", "password", "token", "api_keys",
            } else redact_credentials(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    return value
