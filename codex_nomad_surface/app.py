from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import streamlit as st
from starlette.middleware import Middleware
from streamlit.starlette import App

from codex_nomad_surface.chat_store import ChatMessage, ChatSession
from codex_nomad_surface.codex_client import (
    CodexClient,
    CodexThread,
    CodexThreadMessages,
    ConnectionStatus,
)
from codex_nomad_surface.http_gate import (
    FileContentMiddleware,
    auth_required,
    cookie_auth_is_valid,
    sync_file_content_route_setting,
)
from codex_nomad_surface.promptform_defs import (
    PromptFormDef,
    load_promptform_defs,
    promptform_def_by_id,
)
from codex_nomad_surface.settings import (
    AppSettings,
    Project,
    load_settings,
    save_settings,
)
from codex_nomad_surface.skill_defs import (
    SkillDef,
    skill_def_by_id,
    skill_defs_from_app_server,
)
from codex_nomad_surface.ui_components import (
    inject_chat_input_bridge,
    inject_chat_input_ime_guard,
    render_promptform,
)

st.set_page_config(
    page_title="Codex Nomad Surface",
    page_icon="▣",
    layout="centered",
    initial_sidebar_state="collapsed",
)


APP_SERVER_STARTUP_CHECK_SECONDS = 1.5
DISCONNECTED_STATUS_POLL_INTERVAL_SECONDS = 2
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
LOGO_PATH = Path(__file__).parent / "ui_components" / "assets" / "logo.svg"
PROMPTFORM_BLOCK_PATTERN = re.compile(
    r"""
    ^[ \t]*```promptform[ \t]*\r?\n
    (?P<body>.*?)
    ^[ \t]*```[ \t]*$
    """,
    re.DOTALL | re.MULTILINE | re.VERBOSE,
)


def init_state() -> None:
    st.session_state.setdefault("authenticated", False)
    st.session_state.setdefault("selected_chat_id", "")
    st.session_state.setdefault("draft_chat", None)
    st.session_state.setdefault("selected_project_key", "")
    st.session_state.setdefault("last_query_chat_id", None)
    st.session_state.setdefault("chat_select_version", 0)
    st.session_state.setdefault("last_rendered_chat_id", "")
    st.session_state.setdefault("loaded_thread_ids", set())
    st.session_state.setdefault("thread_history_cursors", {})
    st.session_state.setdefault("thread_history_has_older", {})
    st.session_state.setdefault("approval_action_in_progress", "")
    st.session_state.setdefault("app_server_launch_in_progress", False)
    st.session_state.setdefault("app_server_launch_failure_returncode", None)
    st.session_state.setdefault("managed_app_server_process", None)
    st.session_state.setdefault("chat_history_autoscroll", False)
    st.session_state.setdefault("codex_run_controls_by_chat", {})


def settings_state() -> AppSettings:
    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()
    return st.session_state.settings


def persist() -> None:
    save_settings(st.session_state.settings)
    sync_file_content_route_setting(st.session_state.settings)


def chats_state() -> list[ChatSession]:
    if "chats" not in st.session_state:
        st.session_state.chats = []
    return st.session_state.chats


def render_surface_logo() -> None:
    st.logo(str(LOGO_PATH), size="large")


def draft_chat_for_project(project: Project | None) -> ChatSession | None:
    draft = st.session_state.get("draft_chat")
    if not project or not isinstance(draft, ChatSession):
        return None
    if draft.project_name != project.name:
        return None
    return draft


def clear_draft_chat(chat: ChatSession | None = None) -> None:
    draft = st.session_state.get("draft_chat")
    if not isinstance(draft, ChatSession):
        return
    if chat is None or draft.id == chat.id:
        st.session_state.draft_chat = None


def discard_draft_chat(project: Project | None = None) -> None:
    draft = st.session_state.get("draft_chat")
    if not isinstance(draft, ChatSession):
        return
    if project and draft.project_name != project.name:
        return
    run_controls_state().pop(draft.id, None)
    st.session_state.draft_chat = None


def format_thread_time(value: int) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return ""


def server_threads_state(client: CodexClient) -> list[CodexThread]:
    try:
        return client.list_threads()
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=60)
def archived_project_paths(session_root: str) -> list[str]:
    root = Path(session_root).expanduser()
    if not root.is_dir():
        return []

    projects: dict[str, float] = {}
    for session_path in root.rglob("*.jsonl"):
        cwd = archived_project_path_from_session(session_path)
        if not cwd:
            continue
        try:
            modified_at = session_path.stat().st_mtime
        except OSError:
            modified_at = 0.0
        previous = projects.get(cwd)
        if previous is None or modified_at > previous:
            projects[cwd] = modified_at

    return [
        path
        for path, _ in sorted(
            projects.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def archived_project_path_from_session(session_path: Path) -> str:
    try:
        with session_path.open(encoding="utf-8") as handle:
            first_line = handle.readline()
    except OSError:
        return ""

    if not first_line:
        return ""

    try:
        record = json.loads(first_line)
    except json.JSONDecodeError:
        return ""

    payload = record.get("payload") if isinstance(record, dict) else None
    if not isinstance(payload, dict):
        return ""
    cwd = payload.get("cwd")
    return str(cwd).strip() if cwd else ""


def available_project_paths(server_threads: list[CodexThread]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    for thread in server_threads:
        if not thread.cwd or thread.cwd in seen:
            continue
        paths.append(thread.cwd)
        seen.add(thread.cwd)

    for path in archived_project_paths(str(CODEX_SESSIONS_DIR)):
        if path in seen:
            continue
        paths.append(path)
        seen.add(path)

    return paths


def unique_project_name(path: str, used_names: set[str]) -> str:
    base_name = Path(path).name or path
    if base_name not in used_names:
        return base_name
    parent = Path(path).parent.name
    candidate = f"{parent}/{base_name}" if parent else base_name
    if candidate not in used_names:
        return candidate
    suffix = 2
    while f"{candidate} ({suffix})" in used_names:
        suffix += 1
    return f"{candidate} ({suffix})"


def project_options(server_threads: list[CodexThread]) -> list[Project]:
    projects: list[Project] = []
    names: set[str] = set()
    for path in available_project_paths(server_threads):
        name = unique_project_name(path, names)
        projects.append(Project(name=name, path=path))
        names.add(name)
    return projects


def project_key(project: Project) -> str:
    return f"{project.name}\n{project.path}"


def project_label(project: Project, duplicate_names: set[str]) -> str:
    if project.name in duplicate_names:
        return f"{project.name} · {project.path}"
    return project.name


def selected_project_index(projects: list[Project], selected: str) -> int:
    for index, project in enumerate(projects):
        if selected == project_key(project):
            return index
    for index, project in enumerate(projects):
        if selected == project.name:
            return index
    return 0


def selected_project_key() -> str:
    return str(st.session_state.get("selected_project_key") or "")


def set_selected_project_key(value: str) -> None:
    st.session_state.selected_project_key = value


def auth_screen() -> None:
    if not auth_required():
        st.session_state.authenticated = True
        st.rerun()
    if cookie_auth_is_valid():
        st.session_state.authenticated = True
        st.rerun()

    st.title("Codex Nomad Surface")
    st.caption(
        "Connection details and work content stay hidden until authentication is complete."
    )

    st.info(
        "Authentication is handled before the Streamlit app loads. Open `/_nomad_auth/login` to unlock this surface."
    )

    st.link_button("Open login", "/_nomad_auth/login")


def connection_card(
    client: CodexClient, status: ConnectionStatus | None = None
) -> None:
    status = status or client.status()
    message = f"Codex: {status.label}"
    if status.detail:
        message = f"{message}\n\n{status.detail}"
    if status.ok:
        st.success(message)
    else:
        st.warning(message)


@st.fragment(run_every=DISCONNECTED_STATUS_POLL_INTERVAL_SECONDS)
def disconnected_connection_status(app_server_url: str) -> None:
    client = CodexClient(app_server_url)
    status = client.status()
    connection_card(client, status)
    if status.ok:
        st.rerun()


def app_server_url_host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def can_start_local_app_server(settings: AppSettings, status: ConnectionStatus) -> bool:
    return (
        status.label == "Disconnected"
        and app_server_url_host(settings.app_server_url) == "127.0.0.1"
    )


def terminate_process_at_exit(process: subprocess.Popen[bytes]) -> None:
    def cleanup() -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    atexit.register(cleanup)


def managed_app_server_process() -> subprocess.Popen[bytes] | None:
    process = st.session_state.managed_app_server_process
    if process is None:
        return None
    if process.poll() is not None:
        st.session_state.managed_app_server_process = None
        return None
    return process


def stop_managed_app_server() -> tuple[bool, str]:
    process = managed_app_server_process()
    if process is None:
        return False, "No Codex App Server started by this app is running."
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    st.session_state.managed_app_server_process = None
    return True, "Stopped the Codex App Server started by this app."


def start_local_app_server(
    settings: AppSettings,
) -> tuple[bool, str, subprocess.Popen[bytes] | None]:
    command = ["codex", "app-server", "--listen", settings.app_server_url]
    if shutil.which(command[0]) is None:
        return False, "`codex` command was not found on this host.", None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return False, f"Could not start Codex App Server: {exc}", None
    terminate_process_at_exit(process)
    return True, "Starting Codex App Server...", process


@st.dialog("Codex App Server launch status", width="large")
def app_server_launch_status_dialog(returncode: int | None) -> None:
    st.error(f"Codex App Server exited during startup with code {returncode}.")
    st.caption("Check this web server's logs for stdout and stderr output.")
    if st.button("OK", type="primary"):
        st.session_state.app_server_launch_failure_returncode = None
        st.rerun()


def local_app_server_launcher(settings: AppSettings, status: ConnectionStatus) -> None:
    if not can_start_local_app_server(settings, status):
        st.session_state.app_server_launch_in_progress = False
        st.session_state.app_server_launch_failure_returncode = None
        return

    st.caption("The configured App Server URL points to local host 127.0.0.1.")
    if st.session_state.app_server_launch_in_progress:
        st.info("Starting Codex App Server...")
    if st.button(
        "Start Codex App Server (WebSockets)",
        type="primary",
        disabled=st.session_state.app_server_launch_in_progress,
    ):
        st.session_state.app_server_launch_in_progress = True
        st.rerun()

    if st.session_state.app_server_launch_in_progress:
        ok, message, process = start_local_app_server(settings)
        if not ok:
            st.session_state.app_server_launch_in_progress = False
            st.error(message)
            return
        time.sleep(APP_SERVER_STARTUP_CHECK_SECONDS)
        st.session_state.app_server_launch_in_progress = False
        returncode = process.poll() if process else -1
        if returncode is None:
            st.session_state.managed_app_server_process = process
        else:
            st.session_state.managed_app_server_process = None
            st.session_state.app_server_launch_failure_returncode = returncode
        st.rerun()

    if st.session_state.app_server_launch_failure_returncode is not None:
        app_server_launch_status_dialog(
            st.session_state.app_server_launch_failure_returncode
        )
    st.caption(
        "If this web server is force-killed, the launched Codex App Server process may remain running."
    )


def connection_gate_screen(settings: AppSettings, status: ConnectionStatus) -> None:
    st.title("Codex Nomad Surface")
    disconnected_connection_status(settings.app_server_url)
    st.warning(
        "Codex is not connected, so the operation screen is unavailable. Start App Server and confirm the connection URL."
    )
    st.caption("Chat cannot start until the connection is available.")

    settings_screen(settings)
    local_app_server_launcher(settings, status)


def project_selector(
    server_threads: list[CodexThread], key_prefix: str
) -> Project | None:
    projects = project_options(server_threads)
    if projects:
        duplicate_names = {
            project.name
            for project in projects
            if sum(item.name == project.name for item in projects) > 1
        }
        selected_key = st.selectbox(
            "Project",
            [project_key(project) for project in projects],
            index=selected_project_index(projects, selected_project_key()),
            key=f"{key_prefix}_project",
            format_func=lambda key: project_label(
                next(project for project in projects if project_key(project) == key),
                duplicate_names,
            ),
        )
        if selected_key != selected_project_key():
            discard_draft_chat()
            set_selected_project_key(selected_key)
        return next(
            project for project in projects if project_key(project) == selected_key
        )

    st.warning(
        "No projects or threads were returned by Codex App Server. Start a thread for the target project in Codex first."
    )
    return None


def approval_key(approval: dict, fallback: str = "") -> str:
    return str(
        approval.get("id")
        or approval.get("approval_id")
        or approval.get("detail")
        or approval
        or fallback
    )


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def query_chat_id() -> str:
    value = st.query_params.get("chat", "")
    if isinstance(value, list):
        return str(value[0] if value else "")
    return str(value or "")


def set_query_chat_id(chat_id: str) -> None:
    if chat_id:
        st.query_params["chat"] = chat_id
    elif "chat" in st.query_params:
        del st.query_params["chat"]
    st.session_state.last_query_chat_id = chat_id


def reset_home_view() -> None:
    st.session_state.selected_chat_id = ""
    st.session_state.draft_chat = None
    st.session_state.last_rendered_chat_id = ""
    st.session_state.chat_history_autoscroll = False
    st.session_state.chat_select_version += 1
    st.query_params.clear()
    st.session_state.last_query_chat_id = ""


def sync_chat_selection_from_url(server_threads: list[CodexThread]) -> None:
    chat_id = query_chat_id()
    if chat_id == st.session_state.last_query_chat_id:
        return
    st.session_state.last_query_chat_id = chat_id
    if st.session_state.selected_chat_id != chat_id:
        if chat_id:
            discard_draft_chat()
        st.session_state.selected_chat_id = chat_id
        st.session_state.chat_select_version += 1
    if chat_id:
        select_project_for_chat_id(server_threads, chat_id)


def select_project_for_chat_id(server_threads: list[CodexThread], chat_id: str) -> None:
    projects = project_options(server_threads)
    local_chat = next((chat for chat in chats_state() if chat.id == chat_id), None)
    if local_chat:
        project = next(
            (item for item in projects if item.name == local_chat.project_name), None
        )
        if project:
            set_selected_project_key(project_key(project))
            return

    if not chat_id.startswith("thread:"):
        return
    thread_id = chat_id.removeprefix("thread:")
    thread = next((item for item in server_threads if item.id == thread_id), None)
    if not thread:
        return
    project = next((item for item in projects if item.path == thread.cwd), None)
    if project:
        set_selected_project_key(project_key(project))


def server_thread_chat(project: Project, thread: CodexThread) -> ChatSession:
    created_at = format_thread_time(thread.created_at)
    updated_at = format_thread_time(thread.updated_at)
    title = thread.preview.strip().splitlines()[0][:48] or "New Chat"
    return ChatSession(
        id=f"thread:{thread.id}",
        project_name=project.name,
        title=title,
        thread_id=thread.id,
        created_at=created_at,
        updated_at=updated_at or created_at,
    )


def project_chats(
    project: Project | None, server_threads: list[CodexThread]
) -> list[ChatSession]:
    if not project:
        return []
    local_chats = [chat for chat in chats_state() if chat.project_name == project.name]
    known_thread_ids = {chat.thread_id for chat in local_chats if chat.thread_id}
    known_chat_ids = {chat.id for chat in local_chats}
    server_chats = [
        server_thread_chat(project, thread)
        for thread in server_threads
        if thread.cwd == project.path
        and thread.id not in known_thread_ids
        and f"thread:{thread.id}" not in known_chat_ids
    ]
    return local_chats + server_chats


def create_chat(project: Project) -> ChatSession:
    chat = ChatSession.new(project.name)
    chats_state().insert(0, chat)
    st.session_state.selected_chat_id = chat.id
    set_query_chat_id(chat.id)
    st.session_state.chat_select_version += 1
    return chat


def draft_chat(project: Project) -> ChatSession:
    chat = draft_chat_for_project(project)
    if chat:
        return chat
    chat = ChatSession.new(project.name)
    st.session_state.draft_chat = chat
    return chat


def materialize_chat(project: Project, chat: ChatSession | None) -> ChatSession:
    if chat is None:
        clear_draft_chat()
        return create_chat(project)

    known_chat_ids = {item.id for item in chats_state()}
    if chat.id in known_chat_ids:
        clear_draft_chat(chat)
        return chat

    if chat.project_name != project.name:
        clear_draft_chat(chat)
        return create_chat(project)

    chats_state().insert(0, chat)
    clear_draft_chat(chat)
    st.session_state.selected_chat_id = chat.id
    set_query_chat_id(chat.id)
    st.session_state.chat_select_version += 1
    return chat


def draft_or_selected_chat(project: Project, chat: ChatSession | None) -> ChatSession:
    return chat or draft_chat(project)


def select_chat(
    project: Project | None, server_threads: list[CodexThread]
) -> ChatSession | None:
    if not project:
        st.caption("Select a project.")
        return None

    chats = project_chats(project, server_threads)

    selected_id = st.session_state.selected_chat_id
    if selected_id and selected_id not in {chat.id for chat in chats}:
        selected_id = ""
        st.session_state.selected_chat_id = selected_id
        set_query_chat_id("")
        st.session_state.chat_select_version += 1

    ids = [""] + [chat.id for chat in chats]
    label_by_id = {
        "": "",
        **{
            chat.id: f"{chat.title} · {chat.updated_at or chat.created_at} · {chat.id[:6]}"
            for chat in chats
        },
    }
    selected_index = ids.index(selected_id)
    selected_id = st.selectbox(
        "Chat",
        ids,
        index=selected_index,
        key=f"chat_list_{st.session_state.chat_select_version}",
        format_func=lambda chat_id: label_by_id[chat_id],
    )
    if selected_id != st.session_state.selected_chat_id:
        if selected_id:
            discard_draft_chat(project)
        st.session_state.selected_chat_id = selected_id
        set_query_chat_id(selected_id)
        st.session_state.chat_history_autoscroll = True
        st.rerun()
    if not selected_id:
        return None
    selected = chats[ids.index(selected_id) - 1]
    if selected.id not in {chat.id for chat in chats_state()}:
        chats_state().insert(0, selected)
    return selected


def thread_messages_from_result(result: CodexThreadMessages) -> list[ChatMessage]:
    return [
        ChatMessage(
            role=message["role"],
            content=message["content"],
            metadata=message.get("metadata") or {},
        )
        for message in result.messages
        if message.get("role") in {"user", "assistant"}
        and (message.get("content") or message.get("metadata"))
    ]


def update_thread_history_state(thread_id: str, result: CodexThreadMessages) -> None:
    st.session_state.thread_history_cursors[thread_id] = result.cursor
    st.session_state.thread_history_has_older[thread_id] = result.has_older


def hydrate_thread_chat(client: CodexClient, chat: ChatSession | None) -> None:
    if not chat or not chat.thread_id or chat.messages:
        return
    if chat.thread_id in st.session_state.loaded_thread_ids:
        return

    result = client.read_thread_messages(chat.thread_id, limit=5)
    update_thread_history_state(chat.thread_id, result)
    messages = thread_messages_from_result(result)
    if not messages:
        return

    st.session_state.loaded_thread_ids.add(chat.thread_id)
    chat.messages = messages
    if chat.messages:
        chat.touch()
        st.session_state.chat_history_autoscroll = True


def load_older_history(client: CodexClient, chat: ChatSession | None) -> None:
    if not chat or not chat.thread_id:
        return
    if not st.session_state.thread_history_has_older.get(chat.thread_id):
        return

    if st.button("Load older history", key=f"older_{chat.thread_id}"):
        cursor = st.session_state.thread_history_cursors.get(chat.thread_id)
        result = client.read_thread_messages(chat.thread_id, limit=20, cursor=cursor)
        update_thread_history_state(chat.thread_id, result)
        messages = thread_messages_from_result(result)
        if messages:
            chat.messages = messages + chat.messages
            chat.touch()
        st.session_state.chat_history_autoscroll = False
        st.rerun()


def normalize_codex_output_parts(
    value: object, fallback_output: str = ""
) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    segments = normalize_codex_output_segments(value.get("segments"))
    if not segments:
        legacy_segments = [
            ("final_answer", value.get("output") or fallback_output),
            ("commentary", value.get("commentary")),
            ("plan", value.get("plan")),
            ("reasoning_summary", value.get("reasoning_summary")),
            ("error", value.get("errors")),
        ]
        segments = [
            {
                "kind": kind,
                "text": str(text).strip(),
                "item_id": "",
                "phase": "",
                "metadata": {},
            }
            for kind, text in legacy_segments
            if str(text or "").strip()
        ]
    return {
        "segments": segments,
        "output": codex_output_text_for_kind(segments, "final_answer"),
        "commentary": codex_output_text_for_kind(segments, "commentary"),
        "plan": codex_output_text_for_kind(segments, "plan"),
        "reasoning_summary": codex_output_text_for_kind(segments, "reasoning_summary"),
        "errors": codex_output_text_for_kind(segments, "error"),
        "approval_request": codex_output_text_for_kind(segments, "approval_request"),
    }


def normalize_codex_output_segments(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    segments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        metadata = item.get("metadata")
        segments.append(
            {
                "kind": str(item.get("kind") or "unknown").strip() or "unknown",
                "text": text,
                "item_id": str(item.get("item_id") or ""),
                "phase": str(item.get("phase") or ""),
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        )
    return segments


def codex_output_text_for_kind(segments: list[dict[str, Any]], kind: str) -> str:
    return "\n\n".join(
        str(segment.get("text") or "").strip()
        for segment in segments
        if segment.get("kind") == kind and str(segment.get("text") or "").strip()
    )


def codex_output_has_auxiliary(parts: dict[str, Any]) -> bool:
    return any(
        segment.get("kind") != "final_answer"
        for segment in parts.get("segments", [])
        if isinstance(segment, dict)
    )


def render_codex_output_auxiliary(
    parts: dict[str, Any], expanded_until_final_answer: bool = False
) -> None:
    segments = [
        segment for segment in parts.get("segments", []) if isinstance(segment, dict)
    ]
    progress_segments = [
        segment
        for segment in segments
        if segment.get("kind") in {"commentary", "operation_event"}
        and str(segment.get("text") or "").strip()
    ]
    if progress_segments:
        with st.expander("Progress notes", expanded=expanded_until_final_answer):
            for segment in progress_segments:
                st.markdown(str(segment.get("text") or ""))

    labels = {
        "reasoning_summary": "Reasoning summary",
        "plan": "Plan",
        "approval_request": "Approval",
        "error": "Errors",
    }
    for kind, label in labels.items():
        text = codex_output_text_for_kind(segments, kind)
        if not text:
            continue
        if kind == "error":
            st.error(text)
        else:
            with st.expander(label, expanded=False):
                st.markdown(text)

    known_kinds = set(labels) | {"final_answer"}
    unknown_segments = [
        segment
        for segment in segments
        if segment.get("kind") not in known_kinds
        and segment.get("kind") not in {"commentary", "operation_event"}
    ]
    if unknown_segments:
        with st.expander("Other output", expanded=False):
            for segment in unknown_segments:
                st.markdown(f"**{segment.get('kind') or 'unknown'}**")
                st.markdown(str(segment.get("text") or ""))


def render_codex_stream_output(parts: dict[str, Any]) -> None:
    normalized = normalize_codex_output_parts(parts)
    final_answer_started = bool(normalized["output"])
    render_codex_output_auxiliary(
        normalized, expanded_until_final_answer=not final_answer_started
    )
    if final_answer_started:
        st.markdown(normalized["output"])


def render_chat(
    client: CodexClient,
    project: Project | None,
    chat: ChatSession | None,
    skip_latest_user: bool = False,
) -> None:
    if not chat:
        return
    promptform_defs = load_available_promptform_defs(project.path if project else "")
    skill_defs = load_available_skill_defs(
        client.base_url, project.path if project else ""
    )
    if not chat.messages:
        if chat.thread_id:
            st.caption(
                "An App Server thread is selected. Previous messages have not been loaded yet. The next submission will continue this thread."
            )
            return
        st.caption("No messages yet. Add a prompt form or type a request for Codex.")
        render_codex_run_overrides(
            client.base_url,
            chat,
            key_prefix=f"start_run_overrides_{chat.id}",
            allow_model_provider=True,
            disabled=False,
        )
        return

    messages = (
        chat.messages[:-1]
        if skip_latest_user and chat.messages and chat.messages[-1].role == "user"
        else chat.messages
    )
    for index, message in enumerate(messages):
        if message.role == "promptform_picker":
            picker_id = str(message.metadata.get("picker_id") or f"{chat.id}-{index}")
            with st.chat_message("promptform-picker", avatar="🧩"):
                render_promptform_picker_message(
                    message,
                    promptform_defs,
                    message_key=f"{chat.id}-{picker_id}",
                )
            continue

        if message.role == "skill_picker":
            picker_id = str(message.metadata.get("picker_id") or f"{chat.id}-{index}")
            with st.chat_message("skill-picker", avatar=":material/extension:"):
                render_skill_picker_message(
                    message,
                    skill_defs,
                    message_key=f"{chat.id}-{picker_id}",
                )
            continue

        if message.role == "server_thread_info":
            with st.chat_message("server-thread-info", avatar=":material/info:"):
                render_server_thread_info_message(message)
            continue

        if message.role in {"start_run_overrides", "run_overrides"}:
            message_id = str(message.metadata.get("message_id") or f"{chat.id}-{index}")
            with st.chat_message("run-overrides", avatar=":material/tune:"):
                render_codex_run_overrides(
                    client.base_url,
                    chat,
                    key_prefix=f"run_overrides_{chat.id}_{message_id}",
                    allow_model_provider=message.role == "start_run_overrides",
                    disabled=index < len(chat.messages) - 1,
                    message_metadata=message.metadata,
                )
            continue

        with st.chat_message(message.role):
            content = message.content
            embedded_forms: list[dict] = []
            embedded_form_errors: list[str] = []
            if message.role == "assistant":
                output_parts = normalize_codex_output_parts(
                    message.metadata.get("codex_output"), content
                )
                content = output_parts["output"]
                content, embedded_forms, embedded_form_errors = extract_promptforms(
                    content
                )
            if message.role == "assistant" and codex_output_has_auxiliary(output_parts):
                render_codex_output_auxiliary(output_parts)
            if content:
                st.markdown(content)
            for form_index, form_schema in enumerate(embedded_forms):
                render_promptform(
                    form_schema,
                    instance_key=f"{chat.id if chat else 'chat'}-{index}-{form_index}",
                )
            for error in embedded_form_errors:
                st.warning(f"Prompt Form parse error: {error}", icon="⚠️")


def normalize_embedded_form_option(option: object) -> dict:
    if isinstance(option, str):
        return {"value": option, "label": option}
    if not isinstance(option, dict):
        raise ValueError("Form options must be strings or objects.")

    if "value" not in option:
        raise ValueError("Form option objects must define a value.")
    value = str(option.get("value") or "")
    label = str(option.get("label") or value).strip()
    return {"value": value, "label": label}


def normalize_embedded_form_field(field: object) -> dict:
    if not isinstance(field, dict):
        raise ValueError("Form fields must be objects.")

    field_id = str(field.get("id") or "").strip()
    field_type = str(field.get("type") or "").strip().lower()
    field_label = str(field.get("label") or "").strip()
    if not field_id:
        raise ValueError("Form fields must have an id.")
    if field_type not in {"radio", "select", "text", "textarea", "checkbox"}:
        raise ValueError(f"Unsupported form field type: {field_type}")

    normalized = {
        "id": field_id,
        "type": field_type,
        "label": field_label or field_id.replace("_", " ").replace("-", " ").title(),
        "placeholder": str(field.get("placeholder") or "").strip(),
        "help": str(field.get("help") or "").strip(),
        "required": bool(field.get("required", False)),
        "default": str(field.get("default") or "").strip(),
    }

    if field_type in {"radio", "select"}:
        options = [
            normalize_embedded_form_option(option)
            for option in field.get("options", [])
        ]
        if not options:
            raise ValueError(f"{field_type} fields must provide at least one option.")
        normalized["options"] = options
        if not normalized["default"]:
            normalized["default"] = options[0]["value"]
    elif field_type == "checkbox":
        normalized["default"] = bool(field.get("default", False))
        normalized["checked_value"] = str(field.get("checked_value") or "true")
        normalized["unchecked_value"] = str(field.get("unchecked_value") or "false")

    return normalized


def normalize_promptform(form: object) -> dict:
    if not isinstance(form, dict):
        raise ValueError("Prompt Form must be a JSON object.")

    template = str(form.get("template") or "").strip()
    if not template:
        raise ValueError("Prompt Form must define a template.")

    fields = [normalize_embedded_form_field(field) for field in form.get("fields", [])]
    if not fields:
        raise ValueError("Prompt Form must define at least one field.")

    append_spacing = str(form.get("append_spacing") or "paragraph").strip().lower()
    if append_spacing not in {"none", "line", "paragraph"}:
        append_spacing = "paragraph"

    return {
        "title": str(form.get("title") or "Prompt Form").strip() or "Prompt Form",
        "purpose": str(form.get("purpose") or "").strip(),
        "usage": str(form.get("usage") or "").strip(),
        "response_example": str(form.get("response_example") or "").strip(),
        "submit_label": str(form.get("submit_label") or "Insert into chat").strip()
        or "Insert into chat",
        "template": template,
        "append_spacing": append_spacing,
        "fields": fields,
    }


def extract_promptforms(content: str) -> tuple[str, list[dict], list[str]]:
    forms: list[dict] = []
    errors: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw_json = textwrap.dedent(match.group("body")).strip()
        try:
            forms.append(normalize_promptform(json.loads(raw_json)))
            return ""
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON at line {exc.lineno}, column {exc.colno}")
            return match.group(0)
        except ValueError as exc:
            errors.append(str(exc))
            return match.group(0)

    stripped = PROMPTFORM_BLOCK_PATTERN.sub(replace, content).strip()
    return stripped, forms, errors


def chat_history_panel(
    client: CodexClient, project: Project | None, chat: ChatSession | None
) -> None:
    load_older_history(client, chat)
    autoscroll = bool(st.session_state.chat_history_autoscroll)
    with st.container(
        height="stretch", autoscroll=autoscroll, key="chat-history-panel"
    ):
        render_chat(
            client,
            project,
            chat,
            skip_latest_user=bool(st.session_state.get("pending_turn")),
        )
        if project and chat:
            render_pending_turn(client, project, chat)
    if autoscroll:
        st.session_state.chat_history_autoscroll = False


def render_pending_turn(
    client: CodexClient, project: Project, chat: ChatSession
) -> None:
    pending = st.session_state.get("pending_turn")
    if not pending or pending.get("chat_id") != chat.id:
        return

    with st.chat_message("user"):
        st.markdown(pending["text"])
    with st.chat_message("assistant"):
        result = None
        if not pending.get("runtime") and not pending.get("approval"):
            output_placeholder = st.empty()

            def update_stream(output_parts: dict[str, Any]) -> None:
                pending["output_parts"] = normalize_codex_output_parts(output_parts)
                pending["output"] = pending["output_parts"]["output"]
                with output_placeholder.container():
                    render_codex_stream_output(pending["output_parts"])

            with st.spinner("Sending to Codex..."):
                result = client.start_chat_turn(
                    project.path,
                    pending["text"],
                    chat.thread_id,
                    thread_overrides=pending.get("thread_overrides"),
                    turn_overrides=pending.get("turn_overrides"),
                    approval_policy=pending.get("approval_policy"),
                    output_callback=update_stream,
                )
        elif pending.get("approval"):
            render_inline_approval(client, chat, pending)
            return

        if result:
            handle_turn_result(chat, pending, result)
            return


def render_inline_approval(
    client: CodexClient, chat: ChatSession, pending: dict
) -> None:
    output_placeholder = st.empty()

    def update_stream(output_parts: dict[str, Any]) -> None:
        pending["output_parts"] = normalize_codex_output_parts(output_parts)
        pending["output"] = pending["output_parts"]["output"]
        with output_placeholder.container():
            render_codex_stream_output(pending["output_parts"])

    existing_parts = normalize_codex_output_parts(
        pending.get("output_parts"), str(pending.get("output") or "")
    )
    if any(existing_parts.values()):
        with output_placeholder.container():
            render_codex_stream_output(existing_parts)

    approval = pending["approval"]
    key = approval_key(approval)
    in_progress = st.session_state.approval_action_in_progress == key
    title = (
        approval.get("title")
        or approval.get("command")
        or "Codex is waiting for a user response"
    )
    st.markdown(f"**{title}**")
    st.code(
        str(approval.get("detail") or approval.get("body") or approval), language="text"
    )
    response_options = approval.get("options")
    if approval.get("kind") == "tool_user_input_request" and isinstance(
        approval.get("questions"), list
    ) and not response_options:
        if render_tool_user_input_request(
            client, chat, pending, approval, key, in_progress, update_stream
        ):
            return

    if isinstance(response_options, list) and response_options:
        for index, option in enumerate(response_options):
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            decision = str(option.get("decision") or f"option:{index}")
            if not label:
                continue
            if st.button(
                label,
                key=f"inline-option-{key}-{index}",
                disabled=in_progress,
            ):
                st.session_state.approval_action_in_progress = key
                with st.spinner("Sending response and continuing..."):
                    result = client.respond_chat_turn(
                        pending["runtime"],
                        approval,
                        decision,
                        output_callback=update_stream,
                    )
                handle_turn_result(chat, pending, result)
    else:
        approve_label = (
            "Approve"
            if approval.get("kind") == "approval_request"
            else "Send affirmative response"
        )
        reject_label = (
            "Reject" if approval.get("kind") == "approval_request" else "Decline"
        )
        if st.button(
            approve_label, key=f"inline-approve-{key}", disabled=in_progress
        ):
            st.session_state.approval_action_in_progress = key
            with st.spinner("Sending response and continuing..."):
                result = client.respond_chat_turn(
                    pending["runtime"],
                    approval,
                    "approve",
                    output_callback=update_stream,
                )
            handle_turn_result(chat, pending, result)
        if st.button(
            reject_label, key=f"inline-reject-{key}", disabled=in_progress
        ):
            st.session_state.approval_action_in_progress = key
            with st.spinner("Sending response and continuing..."):
                result = client.respond_chat_turn(
                    pending["runtime"],
                    approval,
                    "reject",
                    output_callback=update_stream,
                )
            handle_turn_result(chat, pending, result)


def render_tool_user_input_request(
    client: CodexClient,
    chat: ChatSession,
    pending: dict,
    approval: dict,
    key: str,
    in_progress: bool,
    update_stream: Any,
) -> bool:
    questions = [
        question for question in approval.get("questions", []) if isinstance(question, dict)
    ]
    if not questions:
        return False

    with st.form(f"tool-user-input-{key}"):
        answers: dict[str, dict[str, list[str]]] = {}
        for index, question in enumerate(questions):
            question_id = str(question.get("id") or index)
            prompt = str(
                question.get("question") or question.get("header") or f"Question {index + 1}"
            )
            options = [
                str(option).strip()
                for option in question.get("options", [])
                if str(option).strip()
            ]
            is_other = bool(question.get("isOther"))
            if options:
                labels = [*options, "Other"] if is_other else options
                selected = st.radio(
                    prompt,
                    labels,
                    key=f"tool-user-input-{key}-{question_id}",
                    disabled=in_progress,
                )
                if selected == "Other":
                    value = st.text_input(
                        "Other",
                        key=f"tool-user-input-other-{key}-{question_id}",
                        disabled=in_progress,
                    )
                else:
                    value = selected
            else:
                value = st.text_input(
                    prompt,
                    key=f"tool-user-input-text-{key}-{question_id}",
                    type="password" if question.get("isSecret") else "default",
                    disabled=in_progress,
                )
            answers[question_id] = {"answers": [str(value)] if str(value).strip() else []}

        submitted = st.form_submit_button("Send response", disabled=in_progress)
    if not submitted:
        return True

    st.session_state.approval_action_in_progress = key
    with st.spinner("Sending response and continuing..."):
        result = client.respond_chat_turn(
            pending["runtime"],
            approval,
            f"answersJson:{compact_json(answers)}",
            output_callback=update_stream,
        )
    handle_turn_result(chat, pending, result)
    return True


def handle_turn_result(chat: ChatSession, pending: dict, result: dict) -> None:
    if result.get("thread_id"):
        chat.thread_id = result["thread_id"]

    if result.get("status") == "approval" and result.get("approval"):
        pending["runtime"] = result["runtime"]
        pending["approval"] = result["approval"]
        pending["output_parts"] = normalize_codex_output_parts(
            result.get("output_parts"), str(result.get("output") or "")
        )
        pending["output"] = pending["output_parts"]["output"]
        st.session_state.approval_action_in_progress = ""
        st.session_state.chat_history_autoscroll = True
        st.rerun()

    output_parts = normalize_codex_output_parts(
        result.get("output_parts"), str(result.get("output") or "")
    )
    if any(output_parts.values()):
        response_text = output_parts["output"]
        metadata = {"codex_output": output_parts}
    else:
        response_text = "The response was empty."
        metadata = {}
    chat.add_message("assistant", response_text, metadata=metadata)
    st.session_state.pending_turn = None
    st.session_state.approval_action_in_progress = ""
    st.session_state.chat_history_autoscroll = True
    st.rerun()


def cancel_pending_turn_if_needed(
    client: CodexClient, chat: ChatSession | None
) -> None:
    pending = st.session_state.get("pending_turn")
    if not pending:
        return
    if chat and pending.get("chat_id") == chat.id:
        return
    runtime = pending.get("runtime")
    if runtime:
        client.close_chat_turn(runtime)
    st.session_state.pending_turn = None
    st.session_state.approval_action_in_progress = ""


def queue_user_turn(project: Project, chat: ChatSession | None, user_text: str) -> None:
    pending = st.session_state.get("pending_turn")
    if pending and pending.get("runtime"):
        return
    starting_new_thread = not chat or not chat.thread_id
    chat = materialize_chat(project, chat)
    controls = chat_run_controls(chat)
    thread_overrides = (
        build_start_thread_overrides(controls)
        if starting_new_thread
        else build_continuation_thread_overrides(controls)
    )
    if starting_new_thread:
        remember_new_chat_run_control_defaults(controls)
        ensure_start_run_overrides_message(chat, controls)
    chat.add_message("user", user_text)
    st.session_state.pending_turn = {
        "chat_id": chat.id,
        "text": user_text,
        "thread_overrides": thread_overrides,
        "turn_overrides": build_turn_overrides(controls),
        "approval_policy": controls.get("approval_policy", "").strip() or None,
    }
    st.session_state.chat_history_autoscroll = True
    st.rerun()


def load_available_promptform_defs(project_path: str = "") -> list[PromptFormDef]:
    defs = load_promptform_defs(project_path)
    if not defs:
        st.error(
            "No Prompt Form definitions found. Add JSON files under `promptform-defs/`."
        )
        st.stop()
    return defs


@st.cache_data(show_spinner=False, ttl=30)
def load_available_skill_defs(base_url: str, project_path: str = "") -> list[SkillDef]:
    if not project_path:
        return []
    return skill_defs_from_app_server(CodexClient(base_url).list_skills(project_path))


@st.cache_data(show_spinner=False, ttl=15)
def load_codex_config(base_url: str) -> dict:
    return CodexClient(base_url).read_config()


@st.cache_data(show_spinner=False, ttl=60)
def load_codex_models(base_url: str) -> list[dict]:
    return CodexClient(base_url).list_models()


@st.cache_data(show_spinner=False, ttl=30)
def load_provider_models(provider_key: str, provider: dict) -> list[dict]:
    base_url = str(provider.get("base_url") or "").strip()
    if not provider_key or not base_url:
        return []

    models_url = f"{base_url.rstrip('/')}/models"
    headers: dict[str, str] = {}
    env_key = str(
        provider.get("env_key")
        or provider.get("envKey")
        or provider.get("api_key_env_var")
        or provider.get("apiKeyEnvVar")
        or ""
    ).strip()
    if env_key:
        api_key = os.environ.get(env_key, "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    request = Request(models_url, headers=headers)
    try:
        with urlopen(request, timeout=3) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []

    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def codex_model_id(model: dict) -> str:
    return str(model.get("id") or model.get("model") or model.get("name") or "")


def codex_model_label(model: dict) -> str:
    model_id = codex_model_id(model)
    display_name = str(model.get("displayName") or model_id)
    description = str(model.get("description") or "").strip()
    return f"{display_name} - {description}" if description else display_name


def option_index(options: list[str], value: str) -> int:
    return options.index(value) if value in options else 0


def optional_selectbox_index(options: list[str], value: str) -> int | None:
    if not value:
        return None
    return options.index(value) if value in options else None


def config_string(config: dict, key: str) -> str:
    value = config.get(key)
    return "" if value is None else str(value)


def selected_model_efforts(models: list[dict], selected_model: str) -> list[str]:
    for model in models:
        if codex_model_id(model) != selected_model:
            continue
        efforts = model.get("supportedReasoningEfforts")
        if not isinstance(efforts, list):
            return []
        values = []
        for effort in efforts:
            if not isinstance(effort, dict):
                continue
            value = str(effort.get("reasoningEffort") or "")
            if value:
                values.append(value)
        return values
    return []


def model_effort_options(
    models: list[dict], selected_model: str, current: str
) -> list[str]:
    options = [""] + selected_model_efforts(models, selected_model)
    if current and current not in options:
        options.insert(0, current)
    return options


def model_provider_options(config: dict, selected: str, current: str) -> list[str]:
    options = ["", "openai"]
    providers = config.get("model_providers")
    if isinstance(providers, dict):
        options.extend(str(name) for name in providers if str(name).strip())
    for value in (current, selected):
        if value and value not in options:
            options.insert(1, value)
    return list(dict.fromkeys(options))


def model_provider_label(config: dict, value: str) -> str:
    if not value:
        return ""
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return value
    provider = providers.get(value)
    if not isinstance(provider, dict):
        return value
    name = str(provider.get("name") or "").strip()
    base_url = str(provider.get("base_url") or "").strip()
    details = " - ".join(part for part in (name, base_url) if part)
    return f"{value} ({details})" if details else value


def configured_model_provider(config: dict, value: str) -> dict:
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return {}
    provider = providers.get(value)
    return provider if isinstance(provider, dict) else {}


def run_controls_state() -> dict[str, dict[str, str]]:
    controls = st.session_state.setdefault("codex_run_controls_by_chat", {})
    return controls if isinstance(controls, dict) else {}


def chat_run_controls(chat: ChatSession | None) -> dict[str, str]:
    if not chat:
        return {}
    controls = run_controls_state().get(chat.id)
    if isinstance(controls, dict):
        return controls.copy()
    if chat.thread_id or chat.messages:
        return {}

    defaults = new_chat_run_control_defaults(settings_state())
    run_controls_state()[chat.id] = defaults.copy()
    return defaults


def save_chat_run_controls(chat: ChatSession, controls: dict[str, str]) -> None:
    run_controls_state()[chat.id] = controls.copy()


def new_chat_run_control_defaults(settings: AppSettings) -> dict[str, str]:
    defaults = {
        "model_provider": settings.new_chat_model_provider.strip(),
        "model": settings.new_chat_model.strip(),
        "reasoning_effort": settings.new_chat_reasoning_effort.strip(),
    }
    return {key: value for key, value in defaults.items() if value}


def remember_new_chat_run_control_defaults(controls: dict[str, str]) -> None:
    settings = settings_state()
    model_provider = controls.get("model_provider", "").strip()
    model = controls.get("model", "").strip()
    reasoning_effort = controls.get("reasoning_effort", "").strip()
    if (
        settings.new_chat_model_provider == model_provider
        and settings.new_chat_model == model
        and settings.new_chat_reasoning_effort == reasoning_effort
    ):
        return
    settings.new_chat_model_provider = model_provider
    settings.new_chat_model = model
    settings.new_chat_reasoning_effort = reasoning_effort
    persist()


def build_turn_overrides(controls: dict[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    mapping = {
        "model": "model",
        "reasoning_effort": "effort",
        "reasoning_summary": "summary",
        "verbosity": "verbosity",
    }
    for source, target in mapping.items():
        value = controls.get(source, "").strip()
        if value:
            overrides[target] = value
    sandbox_policy = controls.get("sandbox_policy_json", "").strip()
    if sandbox_policy:
        try:
            parsed = json.loads(sandbox_policy)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            overrides["sandboxPolicy"] = parsed
    return overrides


def build_thread_overrides(controls: dict[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    service_tier = controls.get("service_tier", "").strip()
    model_provider = controls.get("model_provider", "").strip()
    if model_provider:
        overrides["modelProvider"] = model_provider
    if service_tier:
        overrides["serviceTier"] = service_tier
    return overrides


def build_start_thread_overrides(controls: dict[str, str]) -> dict[str, Any]:
    return build_thread_overrides(controls)


def build_continuation_thread_overrides(controls: dict[str, str]) -> dict[str, Any]:
    overrides = build_thread_overrides(controls)
    overrides.pop("modelProvider", None)
    return overrides


def nested_thread_value(raw: Any, keys: set[str]) -> Any:
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in keys and value not in (None, ""):
                return value
        for value in raw.values():
            found = nested_thread_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(raw, list):
        for item in raw:
            found = nested_thread_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def format_thread_info_value(value: Any) -> str:
    if value in (None, ""):
        return "_Not returned by App Server_"
    if isinstance(value, dict):
        value_type = value.get("type")
        value = value_type if value_type else json.dumps(value, ensure_ascii=False)
    elif isinstance(value, list):
        value = json.dumps(value, ensure_ascii=False)
    else:
        value = str(value)
    return f"`{str(value).replace('`', '\\`')}`"


def server_thread_info_fields(runtime: dict[str, Any]) -> list[tuple[str, Any]]:
    thread = runtime.get("thread") if isinstance(runtime.get("thread"), dict) else {}
    turns = thread.get("turns") if isinstance(thread.get("turns"), list) else []
    latest_turn = turns[-1] if turns and isinstance(turns[-1], dict) else {}
    return [
        ("Thread ID", thread.get("id")),
        ("Status", thread.get("status")),
        ("Model provider", runtime.get("modelProvider") or thread.get("modelProvider")),
        ("Model", runtime.get("model")),
        (
            "Reasoning effort",
            runtime.get("reasoningEffort")
            or nested_thread_value(
                thread, {"reasoningEffort", "reasoning_effort", "effort"}
            ),
        ),
        (
            "Service tier",
            runtime.get("serviceTier")
            or nested_thread_value(thread, {"serviceTier", "service_tier"}),
        ),
        ("Approval policy", runtime.get("approvalPolicy")),
        ("Approvals reviewer", runtime.get("approvalsReviewer")),
        ("Sandbox", runtime.get("sandbox")),
        ("CWD", runtime.get("cwd") or thread.get("cwd")),
        ("CLI version", thread.get("cliVersion")),
        ("Source", thread.get("source")),
        ("Agent role", thread.get("agentRole")),
        ("Agent nickname", thread.get("agentNickname")),
        ("Created at", format_thread_time(int(thread.get("createdAt") or 0))),
        ("Updated at", format_thread_time(int(thread.get("updatedAt") or 0))),
        ("Turn count", len(turns) if turns else None),
        ("Latest turn status", latest_turn.get("status")),
        ("Latest turn duration ms", latest_turn.get("durationMs")),
        ("Latest turn error", latest_turn.get("error")),
    ]


def format_server_thread_info(runtime: dict[str, Any]) -> str:
    fields = server_thread_info_fields(runtime)
    lines = ["### Codex App Server thread info", "", "Source RPC: `thread/resume`", ""]
    for label, value in fields:
        lines.append(f"- **{label}:** {format_thread_info_value(value)}")
    return "\n".join(lines)


def server_thread_info_metadata(runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "fields": [
            {"label": label, "value": format_thread_info_value(value)}
            for label, value in server_thread_info_fields(runtime)
        ]
    }


def render_server_thread_info_message(message: ChatMessage) -> None:
    fields = message.metadata.get("fields")
    if not isinstance(fields, list):
        st.markdown(message.content)
        return

    primary_labels = {
        "Thread ID",
        "Status",
        "Model provider",
        "Model",
        "Reasoning effort",
    }
    primary_lines = [
        "### Codex App Server thread info",
        "",
        "Source RPC: `thread/resume`",
        "",
    ]
    detail_lines: list[str] = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        value = str(item.get("value") or "")
        if not label:
            continue
        line = f"- **{label}:** {value}"
        if label in primary_labels:
            primary_lines.append(line)
        else:
            detail_lines.append(line)

    st.markdown("\n".join(primary_lines))
    if detail_lines:
        with st.expander("Details"):
            st.markdown("\n".join(detail_lines))


def append_server_thread_info_to_chat(
    client: CodexClient, project: Project, chat: ChatSession
) -> None:
    if not chat.thread_id:
        return
    runtime = client.read_thread_runtime_info(chat.thread_id, project.path)
    if runtime:
        content = format_server_thread_info(runtime)
        metadata = server_thread_info_metadata(runtime)
    else:
        content = (
            "### Codex App Server thread info\n\n"
            "`thread/resume` returned no runtime data."
        )
        metadata = {}
    chat.add_message("server_thread_info", content, metadata=metadata)


def append_server_thread_info_message(
    settings: AppSettings, project: Project | None, chat: ChatSession | None
) -> None:
    if not project or not chat or not chat.thread_id:
        return
    target_chat = materialize_chat(project, chat)
    append_server_thread_info_to_chat(
        CodexClient(settings.app_server_url), project, target_chat
    )
    st.session_state.chat_history_autoscroll = True
    st.rerun()


def valid_json_object_or_empty(value: str) -> bool:
    if not value.strip():
        return True
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict)


def run_overrides_message_metadata(controls: dict[str, str]) -> dict[str, Any]:
    return {
        "message_id": str(uuid.uuid4()),
        "controls": controls.copy(),
    }


def ensure_start_run_overrides_message(
    chat: ChatSession, controls: dict[str, str] | None = None
) -> None:
    if chat.thread_id or chat.messages:
        return
    chat.add_message(
        "start_run_overrides",
        "",
        metadata=run_overrides_message_metadata(controls or chat_run_controls(chat)),
    )


def add_draftable_chat_message(
    project: Project,
    chat: ChatSession | None,
    role: str,
    content: str = "",
    metadata: dict | None = None,
) -> ChatSession:
    target_chat = draft_or_selected_chat(project, chat)
    ensure_start_run_overrides_message(target_chat)
    target_chat.add_message(role, content, metadata)
    st.session_state.chat_history_autoscroll = True
    return target_chat


def add_run_overrides_message(project: Project, chat: ChatSession | None) -> None:
    target_chat = materialize_chat(project, chat)
    target_chat.add_message(
        "run_overrides",
        "",
        metadata=run_overrides_message_metadata(chat_run_controls(target_chat)),
    )
    st.session_state.chat_history_autoscroll = True
    st.rerun()


def run_override_snapshot_controls(
    message_metadata: dict[str, Any] | None,
) -> dict[str, str]:
    if not message_metadata or not isinstance(message_metadata.get("controls"), dict):
        return {}
    return {
        str(key): str(value)
        for key, value in message_metadata["controls"].items()
        if value is not None
    }


def format_run_override_snapshot_value(value: str) -> str:
    if not value.strip():
        return "_No override_"
    escaped = value.replace("`", "\\`")
    return f"`{escaped}`"


def render_locked_codex_run_overrides(
    controls: dict[str, str], allow_model_provider: bool
) -> None:
    st.markdown("**Run Overrides**")
    st.caption("Locked because newer chat content follows it.")

    primary_fields = []
    if allow_model_provider:
        primary_fields.append(("Model provider", controls.get("model_provider", "")))
    primary_fields.extend(
        [
            ("Model", controls.get("model", "")),
            ("Reasoning effort", controls.get("reasoning_effort", "")),
        ]
    )
    lines = [
        f"- **{label}:** {format_run_override_snapshot_value(value)}"
        for label, value in primary_fields
    ]
    st.markdown("\n".join(lines))

    advanced_fields = [
        ("Service tier", controls.get("service_tier", "")),
        ("Reasoning summary", controls.get("reasoning_summary", "")),
        ("Verbosity", controls.get("verbosity", "")),
        ("Approval policy", controls.get("approval_policy", "")),
        ("Sandbox policy JSON", controls.get("sandbox_policy_json", "")),
    ]
    with st.expander("Advanced"):
        st.markdown(
            "\n".join(
                f"- **{label}:** {format_run_override_snapshot_value(value)}"
                for label, value in advanced_fields
            )
        )


def sidebar_promptform_actions(
    project: Project | None, chat: ChatSession | None
) -> None:
    disabled = not project or bool(st.session_state.get("pending_turn"))
    if st.button("Add Prompt Form", disabled=disabled, use_container_width=True):
        add_draftable_chat_message(
            project,
            chat,
            "promptform_picker",
            metadata={
                "picker_id": str(uuid.uuid4()),
                "selected_def_id": "",
            },
        )
        st.rerun()


def sidebar_skill_actions(project: Project | None, chat: ChatSession | None) -> None:
    disabled = not project or bool(st.session_state.get("pending_turn"))
    if st.button("Use Skill", disabled=disabled, use_container_width=True):
        add_draftable_chat_message(
            project,
            chat,
            "skill_picker",
            metadata={
                "picker_id": str(uuid.uuid4()),
                "selected_skill_id": "",
            },
        )
        st.rerun()


def render_promptform_picker_message(
    message: ChatMessage, defs: list[PromptFormDef], message_key: str
) -> None:
    selected_def_id = str(message.metadata.get("selected_def_id") or "")
    options = [item.id for item in defs]
    label_by_id = {item.id: promptform_def_option_label(item) for item in defs}
    selected_index = (
        options.index(selected_def_id) if selected_def_id in options else None
    )
    selected_def_id = st.selectbox(
        "Prompt Form",
        options,
        index=selected_index,
        key=f"promptform_picker_{message_key}",
        format_func=lambda item_id: label_by_id[item_id],
        placeholder="Choose a prompt form",
        label_visibility="collapsed",
    )
    message.metadata["selected_def_id"] = selected_def_id
    if not selected_def_id:
        return
    selected_def = promptform_def_by_id(defs, selected_def_id)
    if selected_def is None:
        st.warning("The selected Prompt Form was not found.", icon="⚠️")
        return
    render_promptform(
        normalize_promptform(selected_def.form),
        instance_key=f"{message_key}-{selected_def.id}",
    )


def promptform_def_option_label(item: PromptFormDef) -> str:
    title = str(item.form.get("title") or item.id)
    purpose = str(item.form.get("purpose") or "").strip()
    summary = f"{title} - {purpose}" if purpose else title
    return f"{item.source_label}: {item.path} - {summary}"


def render_skill_picker_message(
    message: ChatMessage, defs: list[SkillDef], message_key: str
) -> None:
    selected_skill_id = str(message.metadata.get("selected_skill_id") or "")
    options = [item.id for item in defs]
    label_by_id = {
        item.id: f"{item.id} - {item.description}" if item.description else item.id
        for item in defs
    }
    selected_index = (
        options.index(selected_skill_id) if selected_skill_id in options else None
    )
    selected_skill_id = st.selectbox(
        "Skill",
        options,
        index=selected_index,
        key=f"skill_picker_{message_key}",
        format_func=lambda item_id: label_by_id[item_id],
        placeholder="Choose a skill",
        label_visibility="collapsed",
    )
    message.metadata["selected_skill_id"] = selected_skill_id
    if not selected_skill_id:
        return
    selected_skill = skill_def_by_id(defs, selected_skill_id)
    if selected_skill is None:
        st.warning("The selected Skill was not found.", icon="⚠️")
        return
    if selected_skill.description:
        st.caption(selected_skill.description)
    if st.button("Add to prompt", key=f"skill_append_{message_key}"):
        st.session_state[f"pending_skill_append_{message_key}"] = {
            "marker": f"${selected_skill.name}",
            "nonce": str(uuid.uuid4()),
        }
        st.rerun()
    append_pending_skill_to_chat_input(message_key)


def append_pending_skill_to_chat_input(message_key: str) -> None:
    pending_key = f"pending_skill_append_{message_key}"
    pending = st.session_state.pop(pending_key, None)
    if not isinstance(pending, dict):
        return
    marker = str(pending.get("marker") or "")
    if not marker:
        return
    nonce = str(pending.get("nonce") or uuid.uuid4())
    dom_id = re.sub(r"[^a-zA-Z0-9_-]", "-", f"skill-append-{message_key}-{nonce}")
    st.html(
        f"""
        <span id="{dom_id}"></span>
        <script>
        (() => {{
          const marker = {json.dumps(marker)};
          const appendToChatInput = window.codexNomadSurface?.appendToChatInput;
          if (typeof appendToChatInput !== "function") {{
            return;
          }}
          appendToChatInput(marker, {{ spacing: "line" }});
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def chat_composer(
    project: Project | None,
    chat: ChatSession | None,
) -> None:
    prompt_disabled = not project or bool(st.session_state.get("pending_turn"))
    prompt = st.chat_input(
        "Message Codex", key="chat_prompt_input", disabled=prompt_disabled
    )
    if prompt and project:
        user_text = prompt.strip()
        if user_text:
            queue_user_turn(project, chat, user_text)

    if not project:
        st.caption("Select a project and enter a message before sending.")


def chat_workspace(
    client: CodexClient,
    project: Project | None,
    chat: ChatSession | None,
) -> None:
    active_chat = chat or (draft_chat(project) if project else None)
    # Keep the native st.chat_input UI, while separating the append bridge
    # from the IME-specific Enter guard.
    inject_chat_input_bridge()
    inject_chat_input_ime_guard()
    chat_history_panel(client, project, active_chat)
    chat_composer(project, active_chat)


def render_sidebar_home_title() -> None:
    with st.container(key="sidebar-home-title"):
        st.button(
            "Codex Nomad Surface",
            key="reset_home_view",
            on_click=reset_home_view,
            type="tertiary",
            width="content",
        )


def surface_sidebar(
    settings: AppSettings, server_threads: list[CodexThread]
) -> tuple[Project | None, ChatSession | None]:
    with st.sidebar:
        render_sidebar_home_title()
        if st.button("Settings", key="open_settings_dialog"):
            settings_dialog(settings)
        project = project_selector(server_threads, "sidebar")
        chat = select_chat(project, server_threads)
        if st.button(
            "Run Overrides",
            key="open_run_overrides_dialog",
            disabled=not project
            or not chat
            or not chat.thread_id
            or bool(st.session_state.get("pending_turn")),
            use_container_width=True,
        ):
            add_run_overrides_message(project, chat)
        if st.button(
            "Show Server Thread Info",
            key="show_server_thread_info",
            disabled=not project
            or not chat
            or not chat.thread_id
            or bool(st.session_state.get("pending_turn")),
            use_container_width=True,
        ):
            append_server_thread_info_message(settings, project, chat)
        st.divider()
        sidebar_promptform_actions(project, chat)
        sidebar_skill_actions(project, chat)
    return project, chat


@st.dialog("Settings", width="large")
def settings_dialog(settings: AppSettings) -> None:
    settings_screen(settings, heading=False)


def render_codex_run_overrides(
    app_server_url: str,
    chat: ChatSession,
    key_prefix: str,
    allow_model_provider: bool,
    disabled: bool,
    message_metadata: dict[str, Any] | None = None,
) -> None:
    if disabled:
        render_locked_codex_run_overrides(
            run_override_snapshot_controls(message_metadata),
            allow_model_provider=allow_model_provider,
        )
        return

    config = load_codex_config(app_server_url)

    if not config:
        st.warning("Could not read Codex config from App Server.")
        return

    current_model = str(config.get("model") or "")
    current_model_provider = str(config.get("model_provider") or "")
    current_service_tier = config_string(config, "service_tier")

    st.markdown("**Run Overrides**")
    st.caption(
        "Stored only in this Nomad Surface session and sent as overrides on this chat's future turns. Codex config.toml is not modified."
    )

    controls = chat_run_controls(chat)
    selected_model = controls.get("model", "")
    selected_model_provider = controls.get("model_provider", "")
    current_effort = controls.get("reasoning_effort", "")
    provider_options = model_provider_options(
        config, selected_model_provider, current_model_provider
    )

    if allow_model_provider:
        selected_provider_value = st.selectbox(
            "Model provider",
            provider_options,
            index=optional_selectbox_index(provider_options, selected_model_provider),
            key=f"{key_prefix}_model_provider",
            format_func=lambda value: model_provider_label(config, value),
            placeholder=current_model_provider or "Codex default",
            disabled=disabled,
        )
        model_provider = str(selected_provider_value or "")
    else:
        model_provider = selected_model_provider

    active_selected_model = (
        "" if model_provider != selected_model_provider else selected_model
    )
    effective_model_provider = model_provider or current_model_provider
    use_discovered_model_options = effective_model_provider in {"", "openai"}
    models = (
        load_codex_models(app_server_url) if use_discovered_model_options else []
    )
    model_options = [
        codex_model_id(model) for model in models if codex_model_id(model)
    ]
    if current_model and current_model not in model_options:
        model_options.insert(0, current_model)
    provider_models = (
        []
        if use_discovered_model_options
        else load_provider_models(
            effective_model_provider,
            configured_model_provider(config, effective_model_provider),
        )
    )
    provider_model_options = [
        codex_model_id(provider_model)
        for provider_model in provider_models
        if codex_model_id(provider_model)
    ]
    provider_model_value = (
        active_selected_model
        if active_selected_model in provider_model_options
        else (provider_model_options[0] if provider_model_options else "")
    )

    effective_provider = model_provider or current_model_provider or "Codex default"
    if use_discovered_model_options:
        effective_model = (
            active_selected_model if active_selected_model in model_options else ""
        )
        effective_model = effective_model or current_model or "Codex default"
    else:
        effective_model = (
            provider_model_value or active_selected_model or "Model required"
        )
    effective_effort = (
        current_effort
        or config_string(config, "model_reasoning_effort")
        or "Model/Codex default"
    )
    effective_service_tier = (
        controls.get("service_tier", "").strip()
        or current_service_tier
        or "Codex default"
    )
    st.caption(
        "Future turns in this chat: "
        f"Provider {effective_provider} / "
        f"Model {effective_model} / "
        f"Reasoning {effective_effort} / "
        f"Service tier {effective_service_tier}"
    )

    if model_options and use_discovered_model_options:
        displayed_model_options = [""] + model_options
        model = st.selectbox(
            "Model",
            displayed_model_options,
            index=optional_selectbox_index(
                displayed_model_options, active_selected_model
            ),
            key=f"{key_prefix}_model",
            format_func=lambda value: next(
                (
                    codex_model_label(item)
                    for item in models
                    if codex_model_id(item) == value
                ),
                value,
            ),
            placeholder="Use Codex default",
            disabled=disabled,
        )
        model = str(model or "")
    elif provider_model_options:
        displayed_provider_model_options = [""] + provider_model_options
        model = st.selectbox(
            "Model",
            displayed_provider_model_options,
            index=optional_selectbox_index(
                displayed_provider_model_options, active_selected_model
            ),
            key=f"{key_prefix}_provider_model",
            format_func=lambda value: value,
            placeholder="Model name",
            disabled=disabled,
        )
        model = str(model or "")
    else:
        model = st.text_input(
            "Model",
            value=active_selected_model,
            key=f"{key_prefix}_model_text",
            placeholder="Model name",
            disabled=disabled,
        ).strip()

    effort_source_model = model or (
        current_model if use_discovered_model_options else ""
    )
    effort_options = model_effort_options(models, effort_source_model, current_effort)
    reasoning_effort = st.selectbox(
        "Reasoning effort",
        effort_options,
        index=optional_selectbox_index(effort_options, current_effort),
        key=f"{key_prefix}_reasoning_effort",
        format_func=lambda value: value,
        placeholder="Use model/Codex default",
        disabled=disabled,
    )
    reasoning_effort = str(reasoning_effort or "")
    with st.expander("Advanced"):
        service_tier = st.text_input(
            "Service tier",
            value=controls.get("service_tier", ""),
            key=f"{key_prefix}_service_tier",
            placeholder=current_service_tier or "Codex default",
            disabled=disabled,
        ).strip()
        reasoning_summary = st.text_input(
            "Reasoning summary",
            value=controls.get("reasoning_summary", ""),
            key=f"{key_prefix}_reasoning_summary",
            placeholder=config_string(config, "model_reasoning_summary")
            or "Codex default",
            disabled=disabled,
        )
        verbosity = st.text_input(
            "Verbosity",
            value=controls.get("verbosity", ""),
            key=f"{key_prefix}_verbosity",
            placeholder=config_string(config, "model_verbosity") or "Codex default",
            disabled=disabled,
        )
        approval_policy = st.text_input(
            "Approval policy",
            value=controls.get("approval_policy", ""),
            key=f"{key_prefix}_approval_policy",
            placeholder=config_string(config, "approval_policy") or "Codex default",
            disabled=disabled,
        )
        sandbox_policy_json = st.text_area(
            "Sandbox policy JSON",
            value=controls.get("sandbox_policy_json", ""),
            key=f"{key_prefix}_sandbox_policy_json",
            placeholder='{"type":"workspaceWrite","networkAccess":false}',
            height=90,
            disabled=disabled,
        ).strip()
    updates: dict[str, str] = {}
    if model:
        updates["model"] = model
    if reasoning_effort:
        updates["reasoning_effort"] = reasoning_effort
    if allow_model_provider and model_provider:
        updates["model_provider"] = model_provider
    elif selected_model_provider:
        updates["model_provider"] = selected_model_provider
    if service_tier:
        updates["service_tier"] = service_tier
    if approval_policy:
        updates["approval_policy"] = approval_policy
    if reasoning_summary:
        updates["reasoning_summary"] = reasoning_summary
    if verbosity:
        updates["verbosity"] = verbosity
    if sandbox_policy_json:
        if valid_json_object_or_empty(sandbox_policy_json):
            updates["sandbox_policy_json"] = sandbox_policy_json
        else:
            st.error("Sandbox policy JSON must be a JSON object.")
    if valid_json_object_or_empty(sandbox_policy_json):
        save_chat_run_controls(chat, updates)
        if message_metadata is not None:
            message_metadata["controls"] = updates.copy()
    st.caption("Changes apply automatically to this chat's future turns.")


def settings_screen(settings: AppSettings, heading: bool = True) -> None:
    if heading:
        st.subheader("Settings")
    with st.form("server_settings"):
        url = st.text_input("Codex App Server URL", value=settings.app_server_url)
        submitted = st.form_submit_button("Save")
    if submitted:
        settings.app_server_url = url.strip()
        st.session_state.app_server_launch_in_progress = False
        st.session_state.app_server_launch_failure_returncode = None
        persist()
        st.success("Settings saved.")
        saved_client = CodexClient(settings.app_server_url)
        saved_client.status()
        st.rerun()
    process = managed_app_server_process()
    if process is not None:
        st.caption(f"This app started Codex App Server on PID {process.pid}.")
        if st.button("Stop Codex App Server", type="secondary"):
            ok, message = stop_managed_app_server()
            if ok:
                st.success(message)
            else:
                st.error(message)
            st.rerun()


def main_screen() -> None:
    settings = settings_state()
    client = CodexClient(settings.app_server_url)
    status = client.status()

    if not status.ok:
        connection_gate_screen(settings, status)
        return

    server_threads = server_threads_state(client)
    sync_chat_selection_from_url(server_threads)
    project, chat = surface_sidebar(settings, server_threads)
    chat = chat or draft_chat_for_project(project)
    cancel_pending_turn_if_needed(client, chat)
    hydrate_thread_chat(client, chat)
    if chat and chat.id != st.session_state.last_rendered_chat_id:
        st.session_state.last_rendered_chat_id = chat.id

    chat_workspace(client, project, chat)


def main() -> None:
    init_state()
    render_surface_logo()
    if not auth_required():
        st.session_state.authenticated = True
    elif cookie_auth_is_valid():
        st.session_state.authenticated = True
    else:
        st.session_state.authenticated = False
    if not st.session_state.authenticated:
        auth_screen()
        return
    main_screen()


app = App(__file__, middleware=[Middleware(FileContentMiddleware)])


if __name__ == "__main__":
    main()
