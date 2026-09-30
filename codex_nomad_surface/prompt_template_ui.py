"""Local chat picker controls and acknowledged template delivery."""
from __future__ import annotations

import hashlib
import uuid

import streamlit as st

from codex_nomad_surface.ui_components import load_asset_text

from codex_nomad_surface.prompt_templates import (
    PromptTemplate,
    TemplateError,
)


def template_state(metadata: dict) -> dict:
    picker_id = metadata.setdefault("picker_id", uuid.uuid4().hex)
    return metadata.setdefault("template_state", {"id": picker_id})


def acknowledge_delivery(state: dict, token: str) -> bool:
    pending = state.get("pending_addition")
    if not pending or pending["token"] != token:
        return False
    state.pop("pending_addition")
    return True


def render_template_delivery(metadata: dict, *, disabled: bool = False) -> None:
    state = template_state(metadata)
    pending = state.get("pending_addition")
    if not pending:
        return
    delivery = st.components.v2.component(
        "nomad_template_delivery",
        js=load_asset_text("template_delivery.js"),
    )
    result = delivery(
        data={**pending, "enabled": not disabled},
        key=f"template-delivery-{pending['token']}",
        on_ack_change=lambda: None,
    )
    if acknowledge_delivery(state, result.ack):
        state["delivered_token"] = pending["token"]
        st.rerun()
    with st.expander("Pending prompt"):
        st.code(pending["text"], language="markdown", wrap_lines=True)
    if st.button("Cancel insertion", key=f"template-cancel-{state['id']}"):
        state.pop("pending_addition", None)
        # Input values remain available when the user cancels delivery.
        st.rerun()


def render_template_composer(
    metadata: dict, templates: list[PromptTemplate], *, disabled: bool = False
) -> None:
    state = template_state(metadata)
    scope = state["id"]
    disabled = disabled or bool(state.get("pending_addition"))
    if not templates:
        st.caption("No templates found in this project’s ops/prompts/ folder.")
        return
    st.caption("Prompt Templates")

    picker_id = state.setdefault("picker_id", uuid.uuid4().hex)
    picker_key = f"template-picker-{scope}-{picker_id}"
    options = [template.id for template in templates]
    titles = {template.id: f"{template.title} — {template.id}" for template in templates}
    if picker_key not in st.session_state:
        selected_id = state.get("selected")
        st.session_state[picker_key] = selected_id if selected_id in options else None
    elif st.session_state[picker_key] not in options:
        st.session_state[picker_key] = None

    def select_template():
        state["selected"] = st.session_state[picker_key]

    st.selectbox(
        "Prompt Template", options, index=None, key=picker_key,
        format_func=titles.get, placeholder="Search and choose a template",
        on_change=select_template, disabled=disabled,
    )
    selected = next((t for t in templates if t.id == state.get("selected")), None)
    if selected is None:
        state.pop("selected", None)
        return
    revision = hashlib.sha256(repr(selected).encode()).hexdigest()
    draft = state.setdefault("drafts", {}).get(selected.id)
    if draft is None or draft["revision"] != revision:
        if draft is not None:
            st.info("The template changed. Inputs have been reset to its current defaults.")
        draft = {"revision": revision, "values": {}, "nonce": uuid.uuid4().hex}
        state["drafts"][selected.id] = draft
    with st.container(border=True):
        st.write(selected.title)
        if selected.description:
            st.caption(selected.description)
        for field in selected.inputs:
            key = f"template-input-{scope}-{draft['nonce']}-{field.name}"
            if key not in st.session_state:
                value = draft["values"].get(field.name, field.default)
                st.session_state[key] = (
                    value if field.type != "select" or value in field.options else None
                )

            def remember(name=field.name, widget_key=key, values=draft["values"]):
                values[name] = st.session_state[widget_key] or ""

            kwargs = dict(
                label=field.label + (" *" if field.required else ""),
                key=key,
                help=field.help or None,
                disabled=disabled,
                on_change=remember,
            )
            if field.type == "select":
                st.selectbox(options=field.options, index=None, **kwargs)
            elif field.type == "textarea":
                st.text_area(**kwargs)
            else:
                st.text_input(**kwargs)
        try:
            expanded = selected.expand(draft["values"])
            error = ""
        except TemplateError as exc:
            expanded, error = None, str(exc)
        if expanded is not None:
            with st.expander("Preview"):
                st.code(expanded, language="markdown", wrap_lines=True)
        elif error:
            st.caption(error)
        if st.button(
            "Add to draft",
            key=f"template-insert-{scope}",
            disabled=disabled or expanded is None,
        ):
            state["pending_addition"] = {
                "token": uuid.uuid4().hex, "text": expanded,
                "template_id": selected.id,
            }
            st.rerun()
