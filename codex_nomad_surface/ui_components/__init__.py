from __future__ import annotations

import html
import json
import re
from pathlib import Path

import streamlit as st


ASSETS_DIR = Path(__file__).parent / "assets"


@st.cache_data(show_spinner=False)
def load_asset_text(name: str) -> str:
    return (ASSETS_DIR / name).read_text(encoding="utf-8")


def _escape_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def inject_chat_input_ime_guard() -> None:
    st.html(
        f"""
        <div id="chat-input-ime-guard" style="display:none"></div>
        <script>{load_asset_text("chat_input_ime_guard.js")}</script>
        """,
        unsafe_allow_javascript=True,
    )


def inject_chat_input_bridge() -> None:
    st.html(
        f"""
        <div id="chat-input-bridge" style="display:none"></div>
        <script>{load_asset_text("chat_input_bridge.js")}</script>
        <script>{load_asset_text("chat_input_drag_guard.js")}</script>
        """,
        unsafe_allow_javascript=True,
    )


def inject_chat_input_outbox(scope: object) -> None:
    st.html(
        f"""
        <div id="chat-input-outbox" style="display:none"></div>
        <script>{load_asset_text("chat_input_outbox.js")}</script>
        <script>window.codexNomadSurface?.setChatOutboxScope?.({json.dumps(str(scope or ""))});</script>
        """,
        unsafe_allow_javascript=True,
    )


def clear_chat_input_outbox(text: object, scope: object) -> None:
    st.html(
        f"""
        <script>
        window.codexNomadSurface?.clearPendingChatMessage?.({json.dumps(str(text or ""))}, {json.dumps(str(scope or ""))});
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def inject_responsive_input_style() -> None:
    st.html(
        """
        <style>
        [data-testid="stBottomBlockContainer"] {
          padding-top: 0.4rem !important;
          padding-bottom: max(0.4rem, env(safe-area-inset-bottom)) !important;
        }

        [data-testid="stChatInput"] {
          padding-top: 0.4rem !important;
          padding-bottom: max(0.4rem, env(safe-area-inset-bottom)) !important;
        }

        @media (max-width: 640px) {
          .stApp input,
          .stApp textarea,
          .stApp select,
          .stApp [contenteditable="true"],
          .stApp [role="combobox"] {
            font-size: 16px !important;
          }
        }
        </style>
        """
    )


def render_copy_text_button(text: object, button_id: str) -> None:
    """Render a browser-local clipboard button without sending text to Python."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", button_id)
    st.html(
        f"""
        <button
          id="{_escape_attr(safe_id)}"
          type="button"
          title="Copy message"
          aria-label="Copy message"
          style="width:100%; min-width:2.5rem; padding:0.35rem;"
        >Copy</button>
        <script>
        (() => {{
          const button = document.getElementById({json.dumps(safe_id)});
          if (!(button instanceof HTMLButtonElement)) return;
          const text = {json.dumps(str(text))};
          button.addEventListener("click", async () => {{
            try {{
              await navigator.clipboard.writeText(text);
            }} catch (_) {{
              const area = document.createElement("textarea");
              area.value = text;
              area.style.position = "fixed";
              area.style.opacity = "0";
              document.body.appendChild(area);
              area.select();
              document.execCommand("copy");
              area.remove();
            }}
            const label = button.textContent;
            button.textContent = "Copied";
            window.setTimeout(() => {{ button.textContent = label; }}, 1200);
          }});
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def render_add_starter_button(starter: str, disabled: bool) -> None:
    starter = starter.strip()
    disabled_attr = "disabled" if disabled else ""
    js = load_asset_text("add_starter_button.js").replace(
        "__STARTER_JSON__", json.dumps(starter)
    )
    st.html(
        f"""
        <button
          type="button"
          data-codex-add-starter="true"
          {disabled_attr}
          style="
            width: 100%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            box-sizing: border-box;
          "
        >
          Add starter
        </button>
        <script>{js}</script>
        """,
        unsafe_allow_javascript=True,
    )
