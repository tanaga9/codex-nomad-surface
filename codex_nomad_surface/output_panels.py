"""Stateful output panels that close once when their turn finishes."""
import streamlit as st


def turn_expander(label: str, key: str, active: bool | None, *, initially_open: bool = True):
    phases = st.session_state.setdefault("output_panel_phases", {})
    previous = phases.get(key)
    if active is not None and previous is not None and active != previous:
        st.session_state[key] = active
    if key not in st.session_state:
        st.session_state[key] = initially_open if active is None else active
    phases[key] = active
    # Keep the default constant: changing it changes the expander's identity.
    return st.expander(label, key=key, expanded=False, on_change="rerun")
