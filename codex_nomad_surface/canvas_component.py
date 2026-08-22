from __future__ import annotations

import uuid
from typing import Literal

import streamlit as st


_component = st.components.v2.component(
    "codex-nomad-surface.nomad_canvas",
    js="index-*.js",
    css="index-*.css",
    html='<div class="react-root"></div>',
)


def _obsidian_uuid(canvas_id: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"codex-nomad-surface://canvas/{canvas_id}",
        )
    )


def nomad_canvas(
    canvas_id: str,
    *,
    initial_document: dict | None,
    websocket_url: str,
    export_request: dict | None = None,
    key: str | None = None,
    height: int | Literal["content", "stretch"] = 640,
):
    """Mount the tldraw CCv2 component for one thread canvas."""
    return _component(
        key=key,
        data={
            "canvasId": canvas_id,
            "initialDocument": initial_document,
            "websocketUrl": websocket_url,
            "obsidianUuid": _obsidian_uuid(canvas_id),
            "exportRequest": export_request,
        },
        width="stretch",
        height=height,
    )
