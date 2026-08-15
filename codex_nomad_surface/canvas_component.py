from __future__ import annotations

import streamlit as st


_component = st.components.v2.component(
    "codex-nomad-surface.nomad_canvas",
    js="index-*.js",
    css="index-*.css",
    html='<div class="react-root"></div>',
)


def nomad_canvas(
    canvas_id: str,
    *,
    initial_document: dict | None,
    websocket_url: str,
    key: str | None = None,
    height: int = 640,
):
    """Mount the tldraw CCv2 component for one thread canvas."""
    return _component(
        key=key,
        data={
            "canvasId": canvas_id,
            "initialDocument": initial_document,
            "websocketUrl": websocket_url,
        },
        default={
            "connection_state": "connecting",
            "document": initial_document,
            "preview_svg": "",
            "saved_at": "",
            "shape_count": 0,
        },
        on_connection_state_change=lambda: None,
        on_document_change=lambda: None,
        on_preview_svg_change=lambda: None,
        on_saved_at_change=lambda: None,
        on_shape_count_change=lambda: None,
        width="stretch",
        height=height,
    )
