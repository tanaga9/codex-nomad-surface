"""Flat MCP elicitation forms and their protocol-shaped responses."""

from __future__ import annotations

import math
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError


def enum_options(field: dict[str, Any]) -> list[tuple[str, str]]:
    values = field.get("enum")
    if isinstance(values, list) and all(isinstance(value, str) for value in values):
        titles = field.get("enumNames") or []
        if not isinstance(titles, list):
            titles = []
        return [
            (value, str(titles[index]) if index < len(titles) else value)
            for index, value in enumerate(values)
        ]
    choices = field.get("oneOf") or field.get("anyOf") or []
    if isinstance(choices, list) and choices and all(
        isinstance(choice, dict) and isinstance(choice.get("const"), str)
        for choice in choices
    ):
        return [
            (choice["const"], str(choice.get("title") or choice["const"]))
            for choice in choices
        ]
    return []


def form_schema(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("type") != "object":
        raise ValueError("This form must describe an object.")

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                raise ValueError("This form uses unsupported schema references.")
            return {key: normalize(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    schema = normalize(raw)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("This form is missing its fields.")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("This form has an invalid schema.") from exc
    for name, field in properties.items():
        if not isinstance(field, dict):
            raise ValueError(f"Unsupported field: {name}.")
        kind = field.get("type")
        if not isinstance(kind, str):
            raise ValueError(f"Unsupported field: {name}.")
        if kind == "array":
            if not isinstance(field.get("items"), dict) or not enum_options(field["items"]):
                raise ValueError(f"Unsupported field: {name}.")
        elif kind not in {"string", "integer", "number", "boolean"}:
            raise ValueError(f"Unsupported field: {name}.")
        if (
            kind == "string"
            and any(key in field for key in ("enum", "oneOf", "anyOf"))
            and not enum_options(field)
        ):
            raise ValueError(f"Unsupported choices: {name}.")
    return schema


def validate_content(schema: dict[str, Any], content: Any) -> None:
    if not isinstance(content, dict):
        raise ValueError("Form responses must contain an object.")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    error = next(validator.iter_errors(content), None)
    if error:
        path = ".".join(str(part) for part in error.absolute_path)
        raise ValueError(f"{path}: {error.message}" if path else error.message)


def form_content(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    content: dict[str, Any] = {}
    required = schema.get("required", [])
    for name, field in schema["properties"].items():
        value = values.get(name)
        kind = field["type"]
        if value is None or (
            name not in required
            and (value == "" or (kind == "array" and value == []))
        ):
            continue
        if kind in {"integer", "number"}:
            try:
                value = int(value) if kind == "integer" else float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name}: Enter a valid {kind}.") from exc
            if kind == "number" and not math.isfinite(value):
                raise ValueError(f"{name}: Enter a finite number.")
        content[name] = value
    validate_content(schema, content)
    return content
