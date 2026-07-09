"""Typed-argument gate (FR-032, SEC-06) — the tool-calling analogue of the SQL
safety gate. Every argument set is validated against the tool's cached
JSON-Schema *before* invocation. Deny-by-default: unknown properties are
rejected (no smuggling extra fields to a connector), types must match (with a
narrow, safe string→number/bool coercion), required fields must be present, and
enum/bounds are enforced. The returned dict is the *sanitised* args — only
schema-declared, correctly-typed values reach the tool.

We support the subset of JSON-Schema an MCP tool realistically declares (object
with typed properties, required, enum, min/max, minLength/maxLength, items). An
unrecognised construct never silently passes — it is validated conservatively.
"""

from __future__ import annotations

from typing import Any

from eadip.mcp.models import ToolValidationError

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate + sanitise `arguments` against a JSON-Schema `schema`.

    Returns a new dict containing only declared properties, coerced to their
    declared types. Raises `ToolValidationError` on any violation. An empty/absent
    schema means the tool declared no arguments, so a non-empty arg set is denied
    (deny-by-default) rather than passed through unchecked.
    """
    if not isinstance(arguments, dict):
        raise ToolValidationError("arguments must be an object", {"type": type(arguments).__name__})

    stype = schema.get("type", "object")
    if stype != "object":
        raise ToolValidationError("tool argument schema must be an object", {"type": str(stype)})

    props: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = list(schema.get("required", []) or [])
    allow_extra = bool(schema.get("additionalProperties", False))

    extra = set(arguments) - set(props)
    if extra and not allow_extra:
        raise ToolValidationError("unknown argument", {"fields": ",".join(sorted(extra))})

    for name in required:
        if name not in arguments:
            raise ToolValidationError("missing required argument", {"field": name})

    out: dict[str, Any] = {}
    for name, value in arguments.items():
        subschema = props.get(name)
        if subschema is None:  # only reachable when additionalProperties is true
            out[name] = value
            continue
        out[name] = _validate_value(name, value, subschema)
    return out


def _validate_value(field: str, value: Any, subschema: dict[str, Any]) -> Any:
    jtype = subschema.get("type")
    if jtype is not None:
        value = _coerce_type(field, value, jtype)

    if "enum" in subschema and value not in subschema["enum"]:
        raise ToolValidationError(
            "value not in enum", {"field": field, "allowed": ",".join(map(str, subschema["enum"]))}
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _check_bounds(field, value, subschema)
    if isinstance(value, str):
        _check_length(field, value, subschema)
    if isinstance(value, list):
        item_schema = subschema.get("items")
        if isinstance(item_schema, dict):
            value = [_validate_value(f"{field}[]", v, item_schema) for v in value]
    return value


def _coerce_type(field: str, value: Any, jtype: str) -> Any:
    expected = _JSON_TYPES.get(jtype)
    if expected is None:
        raise ToolValidationError("unsupported argument type", {"field": field, "type": str(jtype)})

    # bool is a subclass of int — guard so a boolean is never accepted as a number.
    if jtype in ("integer", "number") and isinstance(value, bool):
        raise ToolValidationError("expected number, got boolean", {"field": field})
    if isinstance(value, expected):
        return value

    # Narrow, explicit coercion from strings only (JSON over the wire is stringy).
    if isinstance(value, str):
        try:
            if jtype == "integer":
                return int(value)
            if jtype == "number":
                return float(value)
            if jtype == "boolean":
                low = value.strip().lower()
                if low in ("true", "false"):
                    return low == "true"
        except ValueError:
            pass
    raise ToolValidationError(
        "argument has the wrong type",
        {"field": field, "expected": jtype, "got": type(value).__name__},
    )


def _check_bounds(field: str, value: float, subschema: dict[str, Any]) -> None:
    if "minimum" in subschema and value < subschema["minimum"]:
        raise ToolValidationError(
            "below minimum", {"field": field, "min": str(subschema["minimum"])}
        )
    if "maximum" in subschema and value > subschema["maximum"]:
        raise ToolValidationError(
            "above maximum", {"field": field, "max": str(subschema["maximum"])}
        )


def _check_length(field: str, value: str, subschema: dict[str, Any]) -> None:
    if "minLength" in subschema and len(value) < subschema["minLength"]:
        raise ToolValidationError("too short", {"field": field, "min": str(subschema["minLength"])})
    if "maxLength" in subschema and len(value) > subschema["maxLength"]:
        raise ToolValidationError("too long", {"field": field, "max": str(subschema["maxLength"])})
