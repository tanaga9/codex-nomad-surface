import streamlit as st

out = st.components.v2.component(
    "nomad-canvas.nomad_canvas",
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
    """Mount the tldraw editor for one Nomad Surface canvas.

    Parameters
    ----------
    canvas_id: str
        Stable path-safe identifier for the canvas.
    initial_document: dict or None
        Previously saved tldraw document snapshot.
    websocket_url: str
        Same-origin Canvas Runtime WebSocket URL.
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
        },
        width="stretch",
        height=height,
    )
