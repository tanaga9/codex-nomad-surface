import pytest

from codex_nomad_surface.text_authoring import markdown_headings, scope_text_snapshot


def test_markdown_headings_support_atx_setext_and_ignore_fences():
    content = """# Top

Setext title
------------

````python
# not a heading
```
still fenced
````

~~~
## also not a heading
~~~

  ### Nested ###
"""

    assert markdown_headings(content) == [
        {"text": "Top", "level": 1, "line": 1, "marker_line": 1, "style": "atx"},
        {
            "text": "Setext title",
            "level": 2,
            "line": 3,
            "marker_line": 4,
            "style": "setext",
        },
        {
            "text": "Nested",
            "level": 3,
            "line": 16,
            "marker_line": 16,
            "style": "atx",
        },
    ]


def test_setext_does_not_reuse_fence_closer_or_other_block_lines():
    content = """## Before
text
```
code
```
---
after
## Next

- list item
---

***
---
"""

    assert markdown_headings(content) == [
        {
            "text": "Before",
            "level": 2,
            "line": 1,
            "marker_line": 1,
            "style": "atx",
        },
        {
            "text": "Next",
            "level": 2,
            "line": 8,
            "marker_line": 8,
            "style": "atx",
        },
    ]

    scope, text = scope_text_snapshot(
        content=content,
        arguments={"scope": "section", "heading": "Before"},
        editor_kind="document",
        max_chars=80_000,
    )
    assert scope["to_line"] == 7
    assert text == "## Before\ntext\n```\ncode\n```\n---\nafter\n"


def test_section_scope_uses_heading_levels_and_preserves_source():
    content = "# One\nintro\n## Detail\nbody\n# Two\nend\n"

    scope, text = scope_text_snapshot(
        content=content,
        arguments={"scope": "section", "heading": "One"},
        editor_kind="document",
        max_chars=80_000,
    )

    assert text == "# One\nintro\n## Detail\nbody\n"
    assert scope == {
        "type": "section",
        "heading": "One",
        "level": 1,
        "from_line": 1,
        "to_line": 4,
    }


def test_section_scope_fails_closed_for_duplicate_headings():
    with pytest.raises(ValueError, match="section_ambiguous"):
        scope_text_snapshot(
            content="# Same\nfirst\n# Same\nsecond",
            arguments={"scope": "section", "heading": "Same"},
            editor_kind="document",
            max_chars=80_000,
        )


def test_plain_text_rejects_markdown_only_scopes():
    with pytest.raises(ValueError, match="scope_requires_markdown"):
        scope_text_snapshot(
            content="# Looks like Markdown",
            arguments={"scope": "outline"},
            editor_kind="text",
            max_chars=80_000,
        )


def test_outline_bounds_text_and_heading_metadata_together():
    scope, text = scope_text_snapshot(
        content="# One\n## Two\n### Three\n",
        arguments={"scope": "outline"},
        editor_kind="document",
        max_chars=80,
    )

    assert text == "# One"
    assert scope["headings"] == [
        {
            "text": "One",
            "level": 1,
            "line": 1,
            "marker_line": 1,
            "style": "atx",
        }
    ]
    assert scope["truncated"] is True
    assert scope["headings_truncated"] is True


def test_selection_scope_preserves_direction_and_columns():
    selection = {
        "selection_kind": "range",
        "column_unit": "unicode_code_point",
        "anchor": {"line": 2, "column": 5},
        "head": {"line": 1, "column": 2},
        "from_line": 1,
        "to_line": 2,
        "text": "selected",
    }

    scope, text = scope_text_snapshot(
        content="ignored",
        arguments={"scope": "selection"},
        editor_kind="text",
        selection=selection,
        max_chars=80_000,
    )

    assert text == "selected"
    assert scope == {
        "type": "selection",
        **{key: value for key, value in selection.items() if key != "text"},
    }
