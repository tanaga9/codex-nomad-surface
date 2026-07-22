from codex_nomad_surface.app import append_once_chat_input_html
from codex_nomad_surface.ui_components import load_asset_text


def test_append_once_chat_input_html_tracks_sanitized_token() -> None:
    html = append_once_chat_input_html("skill append/chat-1 nonce", "$example", "line")

    assert "codexNomadChatInputAppendTokens" in html
    assert "skill-append-chat-1-nonce" in html
    assert ".has(token)" in html
    assert ".add(token)" in html
    assert '"$example"' in html
    assert '"line"' in html


def test_append_once_chat_input_html_adds_token_only_after_successful_append() -> None:
    html = append_once_chat_input_html("restore-input-1", "draft text", "paragraph")

    append_call = 'appendToChatInput(text, { spacing: "paragraph" })'
    assert f"if ({append_call})" in html
    assert html.index(append_call) < html.index(".add(token)")


def test_chat_input_outbox_saves_before_submit_and_clears_after_delivery() -> None:
    script = load_asset_text("chat_input_outbox.js")

    assert 'sessionStorage.setItem(storageKey("pending")' in script
    assert 'document.addEventListener("keydown"' in script
    assert "clearPendingChatMessage" in script
    assert "setChatOutboxScope" in script
    assert "RECOVERY_DELAY_MS = 5000" in script
    assert "opaqueAppBackground" in script
    assert "Unconfirmed message" in script
    assert "Minimize recovery dialog" in script
    assert "Restore to input" in script
    assert 'storageKey("draft")' not in script
