from __future__ import annotations

import json
import re
from typing import Any


_ATX_HEADING = re.compile(r"^[ ]{0,3}(#{1,6})(?:[ \t]+|$)(.*)$")
_SETEXT_UNDERLINE = re.compile(r"^[ ]{0,3}(=+|-+)[ \t]*$")
_FENCE_OPEN = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$")
_BLOCK_PREFIX = re.compile(
    r"^(?:[ ]{4}|[ ]{0,3}(?:>|[*+-][ \t]+|\d{1,9}[.)][ \t]+|<|\[[^]]+\]:))"
)
_THEMATIC_BREAK = re.compile(
    r"^[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$"
)


def _document_lines(content: str) -> list[str]:
    lines = content.splitlines(keepends=True)
    if not lines or content.endswith(("\n", "\r")):
        lines.append("")
    return lines


def _line_text(line: str) -> str:
    return line.rstrip("\r\n")


def _selection_scope(selection: dict[str, Any]) -> dict[str, Any]:
    kind = selection.get("selection_kind")
    if kind not in {"caret", "range"}:
        raise ValueError("selection_invalid")

    def point(name: str) -> dict[str, int]:
        value = selection.get(name)
        if not isinstance(value, dict):
            raise ValueError("selection_invalid")
        line = value.get("line")
        column = value.get("column")
        if type(line) is not int or type(column) is not int or line < 1 or column < 1:
            raise ValueError("selection_invalid")
        return {"line": line, "column": column}

    from_line = selection.get("from_line")
    to_line = selection.get("to_line")
    if (
        type(from_line) is not int
        or type(to_line) is not int
        or from_line < 1
        or to_line < from_line
    ):
        raise ValueError("selection_invalid")
    return {
        "selection_kind": kind,
        "column_unit": "unicode_code_point",
        "anchor": point("anchor"),
        "head": point("head"),
        "from_line": from_line,
        "to_line": to_line,
    }


def markdown_headings(content: str) -> list[dict[str, Any]]:
    """Return ATX and Setext headings while ignoring fenced code blocks."""
    lines = _document_lines(content)
    headings: list[dict[str, Any]] = []
    fence: tuple[str, int] | None = None
    setext_candidate: tuple[int, str] | None = None

    for index, raw_line in enumerate(lines):
        line = _line_text(raw_line)
        if fence:
            setext_candidate = None
            character, minimum = fence
            closing_fence = rf"[ ]{{0,3}}{re.escape(character)}{{{minimum},}}[ \t]*"
            if re.fullmatch(closing_fence, line):
                fence = None
            continue

        opener = _FENCE_OPEN.match(line)
        if opener:
            setext_candidate = None
            marker = opener.group(1)
            if marker[0] == "`" and "`" in opener.group(2):
                continue
            fence = (marker[0], len(marker))
            continue

        atx = _ATX_HEADING.match(line)
        if atx:
            setext_candidate = None
            title = re.sub(r"[ \t]+#+[ \t]*$", "", atx.group(2)).strip()
            headings.append(
                {
                    "text": title,
                    "level": len(atx.group(1)),
                    "line": index + 1,
                    "marker_line": index + 1,
                    "style": "atx",
                }
            )
            continue

        setext = _SETEXT_UNDERLINE.match(line)
        if setext:
            if setext_candidate and setext_candidate[0] == index - 1:
                title = setext_candidate[1]
                headings.append(
                    {
                        "text": title,
                        "level": 1 if setext.group(1).startswith("=") else 2,
                        "line": index,
                        "marker_line": index + 1,
                        "style": "setext",
                    }
                )
            setext_candidate = None
            continue

        title = line.strip()
        setext_candidate = (
            (index, title)
            if title
            and not _BLOCK_PREFIX.match(line)
            and not _THEMATIC_BREAK.match(line)
            else None
        )

    return headings


def scope_text_snapshot(
    *,
    content: str,
    arguments: dict[str, Any],
    editor_kind: str,
    selection: dict[str, Any] | None = None,
    max_chars: int,
) -> tuple[dict[str, Any], str]:
    scope = str(arguments.get("scope") or "all")
    scope_result: dict[str, Any] = {"type": scope}
    result_text = content
    lines = _document_lines(content)

    if scope == "selection":
        if not isinstance(selection, dict) or not isinstance(
            selection.get("text"), str
        ):
            raise RuntimeError("selection_unavailable")
        result_text = selection["text"]
        scope_result = {"type": scope, **_selection_scope(selection)}
    elif scope == "lines":
        start = max(1, int(arguments.get("from_line") or 1))
        end = max(start, int(arguments.get("to_line") or start))
        if start > len(lines):
            raise ValueError("line_out_of_range")
        end = min(end, len(lines))
        result_text = "".join(lines[start - 1 : end])
        scope_result.update({"from_line": start, "to_line": end})
    elif scope in {"outline", "section"}:
        if editor_kind != "document":
            raise ValueError("scope_requires_markdown")
        headings = markdown_headings(content)
        if scope == "outline":
            outline_lines: list[str] = []
            bounded_headings: list[dict[str, Any]] = []
            # Count both the rendered outline and the serialized heading array.
            used_chars = 2
            for heading in headings:
                outline_line = f"{'#' * heading['level']} {heading['text']}"
                metadata_chars = len(
                    json.dumps(heading, ensure_ascii=False, separators=(",", ":"))
                )
                item_chars = len(outline_line) + metadata_chars
                if outline_lines:
                    item_chars += 2
                if used_chars + item_chars > max_chars:
                    scope_result["truncated"] = True
                    scope_result["headings_truncated"] = True
                    break
                outline_lines.append(outline_line)
                bounded_headings.append(heading)
                used_chars += item_chars
            result_text = "\n".join(outline_lines)
            scope_result["headings"] = bounded_headings
        else:
            requested = str(arguments.get("heading") or "").strip()
            requested = re.sub(r"^#{1,6}[ \t]+", "", requested).strip()
            matches = [heading for heading in headings if heading["text"] == requested]
            if not matches:
                raise ValueError("section_not_found")
            if len(matches) > 1:
                raise ValueError("section_ambiguous")
            heading = matches[0]
            start_index = int(heading["line"]) - 1
            next_heading = next(
                (
                    candidate
                    for candidate in headings
                    if int(candidate["line"]) > int(heading["line"])
                    and int(candidate["level"]) <= int(heading["level"])
                ),
                None,
            )
            end_index = int(next_heading["line"]) - 1 if next_heading else len(lines)
            result_text = "".join(lines[start_index:end_index])
            scope_result.update(
                {
                    "heading": requested,
                    "level": heading["level"],
                    "from_line": start_index + 1,
                    "to_line": end_index,
                }
            )
    elif scope != "all":
        raise ValueError("unsupported_scope")

    if len(result_text) > max_chars:
        result_text = result_text[:max_chars]
        scope_result["truncated"] = True
    return scope_result, result_text
