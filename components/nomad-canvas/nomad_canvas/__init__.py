import uuid

import streamlit as st

out = st.components.v2.component(
    "nomad-canvas.nomad_canvas",
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
    height: int = 640,
):
    """Mount the tldraw editor for one Nomad Surface canvas.

    Parameters
    ----------
    canvas_id: str
        Stable path-safe identifier for the canvas.
    initial_document: dict or None
        Previously saved tldraw document snapshot.
    websocket_url: str
        Same-origin Canvas Runtime WebSocket URL.
    export_request: dict or None
        A low-frequency browser-side export request.
    key: str or None
        An optional key that uniquely identifies this component.

    Returns
    -------
    ComponentResult
        The mounted component result. Canvas persistence is handled directly
        over the WebSocket and does not emit Streamlit state while editing.

    """
    return out(
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
