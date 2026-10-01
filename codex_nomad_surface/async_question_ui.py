"""Non-blocking question controls that prepare the next ordinary chat input."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import streamlit as st

from codex_nomad_surface.async_questions import answers_prompt, normalize_questions
from codex_nomad_surface.prompt_template_ui import render_template_delivery, template_state
from codex_nomad_surface.output_panels import turn_expander


def render_async_questions(
    parts: dict[str, Any], scope: str, *, active: bool | None = None,
) -> set[str]:
    rendered = set()
    states = st.session_state.setdefault("async_question_drafts", {})
    for segment in parts.get("segments", []):
        questions = normalize_questions(segment.get("metadata", {}).get("questions"))
        if not questions:
            continue
        item_id = str(segment.get("item_id") or "")
        question_scope = segment.get("metadata", {}).get("thread_id") or scope
        reference = hashlib.sha256(json.dumps(
            [question_scope, item_id, questions], ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()
        state = states.setdefault(reference, {"choices": {}, "custom": {}})
        delivery = template_state(state)
        prepared = bool(state.get("requested_token")) and (
            delivery.get("delivered_token") == state["requested_token"]
        )
        pending = bool(delivery.get("pending_addition"))
        panel_key = f"async-question-panel-{reference}"
        if prepared and state.get("collapsed_after_insertion_token") != state["requested_token"]:
            # Close only after the draft confirms receipt, allowing manual reopening.
            st.session_state[panel_key] = False
            state["collapsed_after_insertion_token"] = state["requested_token"]
        with turn_expander(
            "Questions", panel_key, active,
        ):
            st.caption("You can answer while Codex continues working.")
            answers = []
            for index, question in enumerate(questions):
                # Keep the widget identity when live output moves into history;
                # text still being edited may not have reached Python yet.
                key = f"async-question-{reference}-{index}"
                options = question["options"]
                choice = None
                if options:
                    if key not in st.session_state:
                        st.session_state[key] = state["choices"].get(index)

                    def remember_choice(i=index, widget_key=key, target=state):
                        target["choices"][i] = st.session_state[widget_key]

                    choice = st.radio(
                        question["title"], list(range(len(options) + 1)),
                        format_func=lambda value, labels=options: (
                            labels[value] if value < len(labels) else "Write an answer"
                        ),
                        index=None, key=key, on_change=remember_choice,
                        disabled=pending or prepared,
                    )
                if not options or choice == len(options):
                    text_key = f"{key}-text"
                    if text_key not in st.session_state:
                        st.session_state[text_key] = state["custom"].get(index, "")

                    def remember_text(i=index, widget_key=text_key, target=state):
                        target["custom"][i] = st.session_state[widget_key]

                    answer = st.text_area(
                        "Your answer" if options else question["title"],
                        key=text_key, on_change=remember_text,
                        disabled=pending or prepared,
                    )
                else:
                    answer = options[choice] if choice is not None else ""
                answers.append(answer)
            if st.button(
                "Add answers to draft", key=f"async-question-add-{reference}",
                disabled=pending or prepared or not all(answer.strip() for answer in answers),
                help="Review and send the answers from the main chat input.",
            ):
                token = uuid.uuid4().hex
                state["requested_token"] = token
                delivery["pending_addition"] = {
                    "token": token, "text": answers_prompt(questions, answers),
                }
                st.rerun()
            if prepared:
                st.caption("Answers added to your draft. Review and send them.")
            render_template_delivery(state)
        if item_id:
            rendered.add(item_id)
    return rendered
