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


def _escape_text(value: object) -> str:
    return html.escape(str(value))


def _render_help_text(field: dict) -> str:
    help_text = str(field.get("help") or "").strip()
    if not help_text:
        return ""
    return f'<div class="promptform-help">{_escape_text(help_text)}</div>'


def _wrap_field(field: dict, control_html: str) -> str:
    return (
        '<label class="promptform-field">'
        f'<span class="promptform-label">{_escape_text(field["label"])}</span>'
        f"{control_html}"
        f"{_render_help_text(field)}"
        "</label>"
    )


def _render_text_like_field(field: dict, multiline: bool = False) -> str:
    placeholder = str(field.get("placeholder") or "")
    required_attr = " required" if field.get("required") else ""
    placeholder_attr = (
        f' placeholder="{_escape_attr(placeholder)}"' if placeholder else ""
    )
    field_id = _escape_attr(field["id"])
    default_value = str(field.get("default") or "")
    if multiline:
        control = (
            f'<textarea data-promptform-field="{field_id}"'
            f"{placeholder_attr}{required_attr}>"
            f"{_escape_text(default_value)}</textarea>"
        )
    else:
        control = (
            f'<input type="text" data-promptform-field="{field_id}"'
            f' value="{_escape_attr(default_value)}"'
            f"{placeholder_attr}{required_attr}>"
        )
    return _wrap_field(field, control)


def _render_checkbox_field(field: dict) -> str:
    checked_attr = " checked" if field.get("default") else ""
    return (
        '<div class="promptform-checkbox-wrap">'
        '<label class="promptform-checkbox">'
        f'<input type="checkbox" data-promptform-field="{_escape_attr(field["id"])}"{checked_attr}>'
        f"<span>{_escape_text(field['label'])}</span>"
        "</label>"
        f"{_render_help_text(field)}"
        "</div>"
    )


def _render_select_field(field: dict) -> str:
    default_value = str(field.get("default") or "")
    options_html = []
    for option in field.get("options", []):
        selected_attr = " selected" if option["value"] == default_value else ""
        options_html.append(
            f'<option value="{_escape_attr(option["value"])}"{selected_attr}>'
            f"{_escape_text(option['label'])}</option>"
        )
    control = (
        f'<select data-promptform-field="{_escape_attr(field["id"])}">'
        f"{''.join(options_html)}"
        "</select>"
    )
    return _wrap_field(field, control)


def _render_radio_field(field: dict, instance_key: str) -> str:
    default_value = str(field.get("default") or "")
    input_name = f"promptform-{instance_key}-{field['id']}"
    options_html = []
    for option in field.get("options", []):
        checked_attr = " checked" if option["value"] == default_value else ""
        options_html.append(
            '<label class="promptform-option">'
            f'<input type="radio" name="{_escape_attr(input_name)}"'
            f' data-promptform-field="{_escape_attr(field["id"])}"'
            f' value="{_escape_attr(option["value"])}"{checked_attr}>'
            f"<span>{_escape_text(option['label'])}</span>"
            "</label>"
        )
    return (
        '<fieldset class="promptform-fieldset">'
        f'<legend class="promptform-label">{_escape_text(field["label"])}</legend>'
        f'<div class="promptform-options">{"".join(options_html)}</div>'
        f"{_render_help_text(field)}"
        "</fieldset>"
    )


def _render_field(field: dict, instance_key: str) -> str:
    field_type = field["type"]
    if field_type == "text":
        return _render_text_like_field(field)
    if field_type == "textarea":
        return _render_text_like_field(field, multiline=True)
    if field_type == "checkbox":
        return _render_checkbox_field(field)
    if field_type == "select":
        return _render_select_field(field)
    return _render_radio_field(field, instance_key)


def render_promptform(form: dict, instance_key: str) -> None:
    dom_id = re.sub(r"[^a-zA-Z0-9_-]", "-", f"promptform-{instance_key}")
    fields_html = "".join(
        _render_field(field, instance_key) for field in form.get("fields", [])
    )
    purpose_html = (
        f'<div class="promptform-description">{_escape_text(form["purpose"])}</div>'
        if form.get("purpose")
        else ""
    )
    usage_html = (
        f'<div class="promptform-help">{_escape_text(form["usage"])}</div>'
        if form.get("usage")
        else ""
    )
    example_html = (
        '<div class="promptform-example">'
        f'Example reply: {_escape_text(form["response_example"])}'
        "</div>"
        if form.get("response_example")
        else ""
    )
    css = load_asset_text("promptform.css")
    js = load_asset_text("promptform.js")
    schema_json = json.dumps(form)

    st.html(
        f"""
        <div id="{_escape_attr(dom_id)}" class="promptform-mount">
          <style>{css}</style>
          <form class="promptform-root" data-promptform-root>
            <div class="promptform-title">{_escape_text(form.get("title") or "Prompt Form")}</div>
            {purpose_html}
            {usage_html}
            {example_html}
            {fields_html}
            <div class="promptform-actions">
              <button type="submit" class="promptform-submit">
                {_escape_text(form.get("submit_label") or "Insert into chat")}
              </button>
              <span class="promptform-status" data-promptform-status></span>
            </div>
          </form>
        </div>
        <script>{js}</script>
        <script>
        (() => {{
          const root = document.getElementById({json.dumps(dom_id)});
          if (!(root instanceof HTMLElement) || !window.PromptForm?.mount) {{
            return;
          }}
          window.PromptForm.mount(root, {schema_json});
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


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
        """,
        unsafe_allow_javascript=True,
    )


def inject_selectbox_mobile_guard() -> None:
    st.html(
        f"""
        <div id="selectbox-mobile-guard" style="display:none"></div>
        <script>{load_asset_text("selectbox_mobile_guard.js")}</script>
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
