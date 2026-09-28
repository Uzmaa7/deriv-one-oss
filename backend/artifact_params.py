"""Validate call_artifact params against a JSON Schema subset."""

from __future__ import annotations

import ipaddress
import re
from typing import Any


class ParamsValidationError(ValueError):
    pass


def validate_params(params: Any, schema: dict | None) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ParamsValidationError("params must be an object")
    if not schema:
        return dict(params)

    if schema.get("type") == "object":
        if schema.get("additionalProperties") is False:
            allowed = set((schema.get("properties") or {}).keys())
            extra = set(params.keys()) - allowed
            if extra:
                raise ParamsValidationError(f"unexpected params: {sorted(extra)}")

        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in params:
                raise ParamsValidationError(f"missing required param: {key}")

        out: dict[str, Any] = {}
        for key, value in params.items():
            prop = properties.get(key)
            if prop is None:
                out[key] = value
                continue
            out[key] = _validate_value(key, value, prop)
        return out

    raise ParamsValidationError("only object schemas are supported")


def _validate_value(key: str, value: Any, prop: dict) -> Any:
    expected = prop.get("type")
    if expected == "string":
        if not isinstance(value, str):
            raise ParamsValidationError(f"{key} must be a string")
        pattern = prop.get("pattern")
        if pattern and not re.match(pattern, value):
            raise ParamsValidationError(f"{key} failed pattern validation")
        min_len = prop.get("minLength")
        max_len = prop.get("maxLength")
        if min_len is not None and len(value) < min_len:
            raise ParamsValidationError(f"{key} too short")
        if max_len is not None and len(value) > max_len:
            raise ParamsValidationError(f"{key} too long")
        enum = prop.get("enum")
        if enum and value not in enum:
            raise ParamsValidationError(f"{key} not in allowed values")
        return value
    if expected == "boolean":
        if not isinstance(value, bool):
            raise ParamsValidationError(f"{key} must be a boolean")
        return value
    if expected == "object":
        if not isinstance(value, dict):
            raise ParamsValidationError(f"{key} must be an object")
        return value
    if expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ParamsValidationError(f"{key} must be an integer")
        return value
    if key == "source_ip" and isinstance(value, str):
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise ParamsValidationError("source_ip is not a valid IP") from exc
        return value
    return value
