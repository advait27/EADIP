"""Typed-argument gate (Phase 8, FR-032): deny-by-default validation + coercion
of tool arguments against the cached JSON-Schema."""

from __future__ import annotations

import pytest

from eadip.mcp.models import ToolValidationError
from eadip.mcp.validate import validate_arguments

_SCHEMA = {
    "type": "object",
    "properties": {
        "currency": {"type": "string", "enum": ["EUR", "USD"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "active": {"type": "boolean"},
    },
    "required": ["currency"],
    "additionalProperties": False,
}


def test_valid_arguments_pass_and_are_sanitised() -> None:
    out = validate_arguments(_SCHEMA, {"currency": "EUR", "limit": 5})
    assert out == {"currency": "EUR", "limit": 5}


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ToolValidationError) as e:
        validate_arguments(_SCHEMA, {"currency": "EUR", "evil": "x"})
    assert "unknown argument" in e.value.reason


def test_missing_required_is_rejected() -> None:
    with pytest.raises(ToolValidationError) as e:
        validate_arguments(_SCHEMA, {"limit": 5})
    assert e.value.detail["field"] == "currency"


def test_enum_is_enforced() -> None:
    with pytest.raises(ToolValidationError) as e:
        validate_arguments(_SCHEMA, {"currency": "XXX"})
    assert "enum" in e.value.reason


def test_bounds_are_enforced() -> None:
    with pytest.raises(ToolValidationError):
        validate_arguments(_SCHEMA, {"currency": "EUR", "limit": 0})
    with pytest.raises(ToolValidationError):
        validate_arguments(_SCHEMA, {"currency": "EUR", "limit": 1000})


def test_string_to_number_coercion() -> None:
    out = validate_arguments(_SCHEMA, {"currency": "USD", "limit": "7"})
    assert out["limit"] == 7 and isinstance(out["limit"], int)


def test_boolean_is_not_accepted_as_number() -> None:
    with pytest.raises(ToolValidationError):
        validate_arguments(_SCHEMA, {"currency": "USD", "limit": True})


def test_wrong_type_is_rejected() -> None:
    with pytest.raises(ToolValidationError):
        validate_arguments(_SCHEMA, {"currency": 5})


def test_empty_schema_denies_extra_args() -> None:
    # A tool that declared no arguments must not receive any (deny-by-default).
    with pytest.raises(ToolValidationError):
        validate_arguments({}, {"anything": 1})
    assert validate_arguments({}, {}) == {}


def test_additional_properties_allowed_when_declared() -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": True}
    assert validate_arguments(schema, {"free": "form"}) == {"free": "form"}
