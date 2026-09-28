"""Project-owned Markdown prompts; substitution never evaluates expressions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

TEMPLATES_DIR = Path("ops/prompts")
MARKER = re.compile(r"(?P<escape>\\)?\{\{input\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}\}")
RESERVED = re.compile(r"\{\{\s*input(?:\.|\s|\}\}|$)")


class TemplateError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate settings instead of silently accepting the last one."""


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise TemplateError("Metadata keys must be unique strings.")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class Input:
    name: str
    label: str
    type: str = "text"
    required: bool = True
    default: str = ""
    options: tuple[str, ...] = ()
    help: str = ""


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    title: str
    description: str
    body: str
    inputs: tuple[Input, ...]

    def expand(self, values: dict[str, str]) -> str:
        resolved = {}
        for field in self.inputs:
            value = values.get(field.name, field.default)
            if not isinstance(value, str):
                raise TemplateError(f"{field.label}: enter text.")
            if field.required and not value.strip():
                raise TemplateError(f"Please fill: {field.label}")
            if field.type == "select" and value not in field.options:
                if value or field.required:
                    raise TemplateError(f"{field.label}: choose an available option.")
            resolved[field.name] = value
        return MARKER.sub(
            lambda match: match[0][1:] if match["escape"] else resolved[match["name"]],
            self.body,
        )


def _text(settings: dict, key: str, default: str = "") -> str:
    value = settings.get(key, default)
    if not isinstance(value, str):
        raise TemplateError(f"{key} must be text (quote numeric values).")
    return value


def parse_template(source: str, template_id: str) -> PromptTemplate:
    metadata = {}
    body = source
    lines = source.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
        if closing is None:
            raise TemplateError("Front matter needs a closing --- line.")
        try:
            metadata = yaml.load("".join(lines[1:closing]), Loader=UniqueLoader)
        except yaml.YAMLError as exc:
            raise TemplateError("Invalid YAML front matter.") from exc
        if not isinstance(metadata, dict):
            raise TemplateError("Front matter must be a mapping.")
        body = "".join(lines[closing + 1:])
    if not body.strip():
        raise TemplateError("Prompt body is empty.")
    if set(metadata) - {"title", "description", "inputs"}:
        raise TemplateError("Unknown front matter setting.")
    if RESERVED.search(MARKER.sub("", body)):
        raise TemplateError("Invalid input marker; use {{input.name}}.")
    names = list(dict.fromkeys(m["name"] for m in MARKER.finditer(body) if not m["escape"]))
    settings = metadata.get("inputs", {})
    if not isinstance(settings, dict):
        raise TemplateError("inputs must be a mapping.")
    unknown = set(settings) - set(names)
    if unknown:
        raise TemplateError(f"Inputs absent from body: {', '.join(sorted(unknown))}")
    fields = []
    for name in names:
        spec = settings.get(name, {})
        if not isinstance(spec, dict):
            raise TemplateError(f"{name}: input settings must be a mapping.")
        if set(spec) - {"label", "type", "required", "default", "options", "help"}:
            raise TemplateError(f"{name}: unknown input setting.")
        kind = _text(spec, "type", "text")
        if kind not in {"text", "textarea", "select"}:
            raise TemplateError(f"{name}: unsupported input type.")
        required = spec.get("required", True)
        if not isinstance(required, bool):
            raise TemplateError(f"{name}: required must be true or false.")
        options = spec.get("options", [])
        if not isinstance(options, list) or any(not isinstance(v, str) for v in options):
            raise TemplateError(f"{name}: options must be a list of strings.")
        if len(options) != len(set(options)):
            raise TemplateError(f"{name}: duplicate options.")
        if (kind == "select" and not options) or (kind != "select" and options):
            raise TemplateError(f"{name}: options are required only for select inputs.")
        default = _text(spec, "default")
        if kind == "select" and "default" in spec and default not in options:
            raise TemplateError(f"{name}: default must be an available option.")
        fields.append(Input(
            name=name,
            label=_text(spec, "label", name),
            type=kind,
            required=required,
            default=default,
            options=tuple(options),
            help=_text(spec, "help"),
        ))
    return PromptTemplate(
        id=template_id,
        title=_text(metadata, "title", Path(template_id).stem),
        description=_text(metadata, "description"),
        body=body,
        inputs=tuple(fields),
    )


def load_templates(project_path: str) -> tuple[list[PromptTemplate], list[str]]:
    """Read current files on each interaction; never mix projects or shared roots."""
    templates: list[PromptTemplate] = []
    errors: list[str] = []
    root = Path(project_path).expanduser() / TEMPLATES_DIR
    if not project_path or not root.is_dir():
        return templates, errors
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        ancestors = [root, path, *path.parents[:len(relative.parts) - 1]]
        if any(part.is_symlink() for part in ancestors):
            continue
        try:
            with path.open(encoding="utf-8", newline="") as file:
                templates.append(parse_template(file.read(), relative.as_posix()))
        except (OSError, UnicodeError, TemplateError) as exc:
            errors.append(f"{relative.as_posix()}: {exc}")
    return templates, errors
