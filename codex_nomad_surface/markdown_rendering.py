from __future__ import annotations

import re


def markdown_with_soft_line_breaks(text: object) -> str:
    source = str(text or "")
    if "\n" not in source:
        return source

    lines = source.splitlines(keepends=True)
    rendered: list[str] = []
    in_fence = False
    fence_char = ""
    fence_length = 0
    in_html_block = False

    for index, line in enumerate(lines):
        body, newline = split_line_ending(line)
        fence = markdown_fence_start_marker(body)
        if fence and not in_fence:
            fence_char, fence_length = fence
            in_fence = True
            rendered.append(line)
            continue
        if in_fence:
            rendered.append(line)
            if markdown_fence_close_marker(body, fence_char, fence_length):
                in_fence = False
                fence_char = ""
                fence_length = 0
            continue

        if markdown_html_block_start(body):
            in_html_block = True

        if (
            in_html_block
            or not newline
            or not markdown_line_can_receive_hard_break(body)
        ):
            rendered.append(line)
            if in_html_block and markdown_html_block_end(body):
                in_html_block = False
            continue

        next_body = ""
        if index + 1 < len(lines):
            next_body, _ = split_line_ending(lines[index + 1])
        if markdown_line_can_follow_hard_break(next_body):
            rendered.append(f"{body.rstrip(' \t')}  {newline}")
        else:
            rendered.append(line)

    return "".join(rendered)


def split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def markdown_fence_start_marker(line: str) -> tuple[str, int] | None:
    match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
    if not match:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def markdown_fence_close_marker(line: str, fence_char: str, fence_length: int) -> bool:
    if fence_char not in {"`", "~"} or fence_length < 3:
        return False
    escaped_char = re.escape(fence_char)
    return bool(re.match(rf"^ {{0,3}}{escaped_char}{{{fence_length},}}[ \t]*$", line))


def markdown_line_can_receive_hard_break(line: str) -> bool:
    if not line.strip() or line.endswith(("  ", "\\")):
        return False
    return not markdown_line_is_structural(line)


def markdown_line_can_follow_hard_break(line: str) -> bool:
    return bool(line.strip()) and not markdown_line_is_structural(line, allow_list=True)


def markdown_line_is_structural(line: str, allow_list: bool = False) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if markdown_fence_start_marker(line):
        return True
    if line.startswith(("    ", "\t")):
        return True
    if re.match(r"^ {0,3}#{1,6}(?:\s|$)", line):
        return True
    if re.match(r"^ {0,3}(?:[-*_]\s*){3,}$", line):
        return True
    if re.match(r"^ {0,3}(?:=+|-+)\s*$", line):
        return True
    if markdown_table_delimiter_line(line) or markdown_table_row(line):
        return True
    if not allow_list and re.match(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])\s+", line):
        return True
    if markdown_html_block_start(line) or markdown_html_block_end(line):
        return True
    return False


def markdown_table_row(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and not stripped.startswith((">", "- ", "* ", "+ "))


def markdown_table_delimiter_line(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 2:
        return False
    return all(re.match(r"^:?-{3,}:?$", cell) for cell in cells)


def markdown_html_block_start(line: str) -> bool:
    stripped = line.lstrip()
    return bool(
        re.match(r"^<!--", stripped)
        or re.match(r"^<(?i:pre|script|style|textarea)(?:\s|>|$)", stripped)
    )


def markdown_html_block_end(line: str) -> bool:
    stripped = line.rstrip()
    return bool(
        stripped.endswith("-->")
        or re.search(r"</(?i:pre|script|style|textarea)>\s*$", stripped)
    )
