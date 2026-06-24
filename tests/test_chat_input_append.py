from codex_nomad_surface.app import append_once_chat_input_html


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
