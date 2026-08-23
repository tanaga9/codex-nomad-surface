from __future__ import annotations

from typing import Literal

import streamlit as st


_component = st.components.v2.component(
    "codex-nomad-surface.nomad_text",
    js="index-*.js",
    css="index-*.css",
    html='<div class="nomad-text-root"></div>',
)


def nomad_text(
    text_id: str,
    *,
    initial_text: str,
    text_format: Literal["plain", "markdown"],
    editor_kind: Literal["text", "document"],
    presentation: Literal["raw", "assisted"],
    revision: int,
    websocket_url: str,
    placeholder: str = "Start writing…",
    key: str | None = None,
    height: int | Literal["content", "stretch"] = 640,
):
    """Mount the shared CodeMirror engine for a Text or Document editor."""
    return _component(
        key=key,
        data={
            "textId": text_id,
            "initialText": initial_text,
            "format": text_format,
            "editorKind": editor_kind,
            "presentation": presentation,
            "revision": revision,
            "websocketUrl": websocket_url,
            "placeholder": placeholder,
        },
        width="stretch",
        height=height,
    )
