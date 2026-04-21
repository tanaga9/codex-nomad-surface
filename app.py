from __future__ import annotations

import atexit
import hmac
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

from chat_store import ChatMessage, ChatSession
from codex_client import CodexClient, CodexThread, CodexThreadMessages, ConnectionStatus
from settings import (
    AppSettings,
    Project,
    configured_secret,
    load_settings,
    save_settings,
)
from skins import load_skins, skin_by_id


st.set_page_config(
    page_title="Codex Nomad Surface",
    page_icon="▣",
    layout="centered",
    initial_sidebar_state="collapsed",
)


APP_SERVER_STARTUP_CHECK_SECONDS = 1.5
DISCONNECTED_STATUS_POLL_INTERVAL_SECONDS = 2


def init_state() -> None:
    st.session_state.setdefault("authenticated", False)
    st.session_state.setdefault("selected_chat_id", "")
    st.session_state.setdefault("selected_project_key", "")
    st.session_state.setdefault("selected_skin_id", "quick_prompt")
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


def auth_required() -> bool:
    return configured_secret() != ""


def settings_state() -> AppSettings:
    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()
    return st.session_state.settings


def persist() -> None:
    save_settings(st.session_state.settings)


def chats_state() -> list[ChatSession]:
    if "chats" not in st.session_state:
        st.session_state.chats = []
    return st.session_state.chats


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
    paths: set[str] = set()
    names: set[str] = set()
    for thread in server_threads:
        if not thread.cwd or thread.cwd in paths:
            continue
        name = unique_project_name(thread.cwd, names)
        projects.append(Project(name=name, path=thread.cwd))
        paths.add(thread.cwd)
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


def selected_skin_id() -> str:
    return str(st.session_state.get("selected_skin_id") or "quick_prompt")


def set_selected_skin_id(value: str) -> None:
    st.session_state.selected_skin_id = value


def auth_screen() -> None:
    if not auth_required():
        st.session_state.authenticated = True
        st.rerun()

    st.title("Codex Nomad Surface")
    st.caption(
        "Connection details and work content stay hidden until authentication is complete."
    )

    st.info(
        "Passkey / WebAuthn is not wired yet. This version unlocks with a local secret."
    )

    with st.form("auth_form"):
        secret = st.text_input(
            "Authentication",
            type="password",
            placeholder="Enter the local secret",
            key="password_input",
            autocomplete="current-password",
        )
        submitted = st.form_submit_button("Unlock")
    if submitted:
        if hmac.compare_digest(secret, configured_secret()):
            st.session_state.authenticated = True
            st.rerun()
        st.error("The secret does not match.")

    st.info(
        "Set the initial secret with the `NOMAD_AUTH_SECRET` environment variable. If it is unset, the development default is `dev-secret`."
    )


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
            set_selected_project_key(selected_key)
        return next(
            project for project in projects if project_key(project) == selected_key
        )

    st.warning(
        "No projects or threads were returned by Codex App Server. Start a thread for the target project in Codex first."
    )
    return None


def skin_selector(key_prefix: str):
    skins = load_skins()
    if not skins:
        st.error("No Skins found. Add `skins/*.json` files.")
        st.stop()
    selected = skin_by_id(skins, selected_skin_id())
    labels = [f"{skin.name} - {skin.description}" for skin in skins]
    ids = [skin.id for skin in skins]
    index = ids.index(selected.id)
    choice = st.selectbox("Skin", labels, index=index, key=f"{key_prefix}_skin")
    new_skin = skins[labels.index(choice)]
    if new_skin.id != selected_skin_id():
        set_selected_skin_id(new_skin.id)
    return new_skin


def approval_key(approval: dict, fallback: str = "") -> str:
    return str(
        approval.get("id")
        or approval.get("approval_id")
        or approval.get("detail")
        or approval
        or fallback
    )


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


def sync_chat_selection_from_url(server_threads: list[CodexThread]) -> None:
    chat_id = query_chat_id()
    if chat_id == st.session_state.last_query_chat_id:
        return
    st.session_state.last_query_chat_id = chat_id
    if st.session_state.selected_chat_id != chat_id:
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

    labels = [""] + [
        f"{chat.title} · {chat.updated_at or chat.created_at} · {chat.id[:6]}"
        for chat in chats
    ]
    ids = [""] + [chat.id for chat in chats]
    selected_index = ids.index(selected_id)
    choice = st.selectbox(
        "Chat",
        labels,
        index=selected_index,
        key=f"chat_list_{st.session_state.chat_select_version}",
    )
    selected_id = ids[labels.index(choice)]
    if selected_id != st.session_state.selected_chat_id:
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
        ChatMessage(role=message["role"], content=message["content"])
        for message in result.messages
        if message.get("role") in {"user", "assistant"} and message.get("content")
    ]


def update_thread_history_state(thread_id: str, result: CodexThreadMessages) -> None:
    st.session_state.thread_history_cursors[thread_id] = result.before_offset
    st.session_state.thread_history_has_older[thread_id] = result.has_older


def hydrate_thread_chat(client: CodexClient, chat: ChatSession | None) -> None:
    if not chat or not chat.thread_id or chat.messages:
        return
    if chat.thread_id in st.session_state.loaded_thread_ids:
        return

    result = client.read_thread_messages(chat.thread_id, limit=20)
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
        before_offset = st.session_state.thread_history_cursors.get(chat.thread_id)
        result = client.read_thread_messages(
            chat.thread_id, limit=40, before_offset=before_offset
        )
        update_thread_history_state(chat.thread_id, result)
        messages = thread_messages_from_result(result)
        if messages:
            chat.messages = messages + chat.messages
            chat.touch()
        st.session_state.chat_history_autoscroll = False
        st.rerun()


def render_chat(chat: ChatSession | None, skip_latest_user: bool = False) -> None:
    if not chat:
        return
    if not chat.messages:
        if chat.thread_id:
            st.caption(
                "An App Server thread is selected. Previous messages have not been loaded yet. The next submission will continue this thread."
            )
            return
        st.caption("No messages yet. Use Input Assist or type a request for Codex.")
        return

    messages = (
        chat.messages[:-1]
        if skip_latest_user and chat.messages and chat.messages[-1].role == "user"
        else chat.messages
    )
    for message in messages:
        with st.chat_message(message.role):
            st.markdown(message.content)


def inject_chat_input_ime_guard() -> None:
    # Streamlit's built-in chat input can still misfire on some IME confirm
    # paths, so we add a narrow client-side guard for chat-input Enter events.
    st.html(
        """
        <div id="chat-input-ime-guard" style="display:none"></div>
        <script>
        (() => {
          if (window.__codexNomadChatInputImeGuardInstalled) {
            return;
          }
          window.__codexNomadChatInputImeGuardInstalled = true;

          const ENTER_KEYS = new Set(["Enter", "NumpadEnter"]);
          const RECENT_COMPOSITION_WINDOW_MS = 200;

          const isChatInputTarget = (target) =>
            target instanceof HTMLElement &&
            target.dataset?.testid === "stChatInputTextArea";

          document.addEventListener(
            "compositionstart",
            (event) => {
              if (!isChatInputTarget(event.target)) {
                return;
              }
              event.target.dataset.codexImeComposing = "true";
              event.target.dataset.codexImeLastCompositionAt = String(Date.now());
            },
            true,
          );

          document.addEventListener(
            "compositionend",
            (event) => {
              if (!isChatInputTarget(event.target)) {
                return;
              }
              event.target.dataset.codexImeComposing = "false";
              event.target.dataset.codexImeLastCompositionAt = String(Date.now());
            },
            true,
          );

          document.addEventListener(
            "keydown",
            (event) => {
              if (!isChatInputTarget(event.target)) {
                return;
              }
              if (!ENTER_KEYS.has(event.key) && event.keyCode !== 13 && event.keyCode !== 229) {
                return;
              }
              if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) {
                return;
              }

              const lastCompositionAt = Number(
                event.target.dataset.codexImeLastCompositionAt || "0",
              );
              const recentlyComposed = Date.now() - lastCompositionAt < RECENT_COMPOSITION_WINDOW_MS;
              const composing =
                event.isComposing ||
                event.keyCode === 229 ||
                event.target.dataset.codexImeComposing === "true" ||
                recentlyComposed;

              if (!composing) {
                return;
              }

              event.preventDefault();
              event.stopPropagation();
              event.stopImmediatePropagation();
            },
            true,
          );
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def render_add_starter_button(starter: str, disabled: bool) -> None:
    starter = starter.strip()
    disabled_attr = "disabled" if disabled else ""
    starter_json = json.dumps(starter)
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
        <script>
        (() => {{
          const button = document.currentScript.previousElementSibling;
          if (!(button instanceof HTMLButtonElement)) {{
            return;
          }}

          const starter = {starter_json};
          const textareaSelector = '[data-testid="stChatInputTextArea"]';
          const referenceButtonSelectors = [
            '[data-testid="stBaseButton-secondary"]:not([data-codex-add-starter="true"])',
            '.stButton > button:not([data-codex-add-starter="true"])',
            'button[kind="secondary"]:not([data-codex-add-starter="true"])',
          ];
          const themeSyncObservers = [];

          const syncButtonTheme = () => {{
            const referenceButton = referenceButtonSelectors
              .map((selector) => document.querySelector(selector))
              .find((candidate) => candidate instanceof HTMLButtonElement);

            if (!(referenceButton instanceof HTMLButtonElement)) {{
              return;
            }}

            const computed = window.getComputedStyle(referenceButton);
            const copiedProperties = [
              "background",
              "backgroundColor",
              "border",
              "borderColor",
              "borderRadius",
              "boxShadow",
              "color",
              "fontFamily",
              "fontSize",
              "fontWeight",
              "lineHeight",
              "minHeight",
              "padding",
              "transition",
            ];

            copiedProperties.forEach((property) => {{
              button.style[property] = computed[property];
            }});
            button.style.width = "100%";
            button.style.display = "inline-flex";
            button.style.alignItems = "center";
            button.style.justifyContent = "center";
            button.style.boxSizing = "border-box";

            if (button.disabled) {{
              button.style.cursor = "not-allowed";
              button.style.opacity = "0.55";
            }} else {{
              button.style.cursor = computed.cursor || "pointer";
              button.style.opacity = "1";
            }}
          }};

          const observeThemeChanges = () => {{
            const observerTargets = [
              document.documentElement,
              document.body,
              ...referenceButtonSelectors
                .map((selector) => document.querySelector(selector))
                .filter((candidate) => candidate instanceof HTMLElement),
            ];

            observerTargets.forEach((target) => {{
              if (!(target instanceof HTMLElement)) {{
                return;
              }}
              const observer = new MutationObserver(() => {{
                window.requestAnimationFrame(syncButtonTheme);
              }});
              observer.observe(target, {{
                attributes: true,
                attributeFilter: ["class", "style", "data-theme"],
              }});
              themeSyncObservers.push(observer);
            }});
          }};

          const appendStarter = () => {{
            const textarea = document.querySelector(textareaSelector);
            if (!(textarea instanceof HTMLTextAreaElement) || !starter) {{
              return;
            }}

            const current = textarea.value ?? "";
            let separator = "";
            if (current) {{
              separator = current.endsWith("\\n\\n")
                ? ""
                : current.endsWith("\\n")
                  ? "\\n"
                  : "\\n\\n";
            }}
            const next = `${{current}}${{separator}}${{starter}}`;

            const valueSetter = Object.getOwnPropertyDescriptor(
              HTMLTextAreaElement.prototype,
              "value",
            )?.set;
            valueSetter?.call(textarea, next);
            textarea.dispatchEvent(new Event("input", {{ bubbles: true }}));
            textarea.focus();
            textarea.setSelectionRange(next.length, next.length);
          }};

          syncButtonTheme();
          observeThemeChanges();
          button.onclick = (event) => {{
            if (button.disabled) {{
              return;
            }}
            event.preventDefault();
            appendStarter();
          }};
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def chat_history_panel(
    client: CodexClient, project: Project | None, chat: ChatSession | None
) -> None:
    load_older_history(client, chat)
    autoscroll = bool(st.session_state.chat_history_autoscroll)
    with st.container(
        height="stretch", autoscroll=autoscroll, key="chat-history-panel"
    ):
        render_chat(chat, skip_latest_user=bool(st.session_state.get("pending_turn")))
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

            def update_stream(output: str) -> None:
                pending["output"] = output
                output_placeholder.markdown(output.strip() or " ")

            with st.spinner("Sending to Codex...", show_time=True):
                result = client.start_chat_turn(
                    project.path,
                    pending["text"],
                    pending["skin_id"],
                    pending["fields"],
                    chat.thread_id,
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

    def update_stream(output: str) -> None:
        pending["output"] = output
        output_placeholder.markdown(output.strip() or " ")

    existing_output = str(pending.get("output") or "").strip()
    if existing_output:
        output_placeholder.markdown(existing_output)

    approval = pending["approval"]
    key = approval_key(approval)
    in_progress = st.session_state.approval_action_in_progress == key
    title = (
        approval.get("title")
        or approval.get("command")
        or "Operation requires approval"
    )
    st.markdown(f"**{title}**")
    st.code(
        str(approval.get("detail") or approval.get("body") or approval), language="text"
    )
    if st.button("Approve", key=f"inline-approve-{key}", disabled=in_progress):
        st.session_state.approval_action_in_progress = key
        with st.spinner("Approving and continuing...", show_time=True):
            result = client.respond_chat_turn(
                pending["runtime"], approval, "approve", output_callback=update_stream
            )
        handle_turn_result(chat, pending, result)
    if st.button("Reject", key=f"inline-reject-{key}", disabled=in_progress):
        st.session_state.approval_action_in_progress = key
        with st.spinner("Rejecting and continuing...", show_time=True):
            result = client.respond_chat_turn(
                pending["runtime"], approval, "reject", output_callback=update_stream
            )
        handle_turn_result(chat, pending, result)


def handle_turn_result(chat: ChatSession, pending: dict, result: dict) -> None:
    if result.get("thread_id"):
        chat.thread_id = result["thread_id"]

    if result.get("status") == "approval" and result.get("approval"):
        pending["runtime"] = result["runtime"]
        pending["approval"] = result["approval"]
        pending["output"] = str(result.get("output") or "").strip()
        st.session_state.approval_action_in_progress = ""
        st.session_state.chat_history_autoscroll = True
        st.rerun()

    response_text = str(result.get("output") or "").strip() or "The response was empty."
    chat.add_message("assistant", response_text)
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


def queue_user_turn(
    chat: ChatSession, user_text: str, skin_id: str, field_values: dict[str, str]
) -> None:
    pending = st.session_state.get("pending_turn")
    if pending and pending.get("runtime"):
        return
    chat.add_message("user", user_text)
    st.session_state.pending_turn = {
        "chat_id": chat.id,
        "text": user_text,
        "skin_id": skin_id,
        "fields": field_values,
    }
    st.session_state.chat_history_autoscroll = True
    st.rerun()


def input_assist_panel():
    skins = load_skins()
    if not skins:
        st.error("No Skins found. Add `skins/*.json` files.")
        st.stop()

    field_values: dict[str, str] = {}
    skin = skin_by_id(skins, selected_skin_id())
    with st.expander("Input Assist", expanded=False):
        skin = skin_selector("chat")
        st.caption(skin.description)
        if skin.quick_prompts:
            quick = st.selectbox(
                "Starter prompts", [""] + skin.quick_prompts, key="chat_quick_prompt"
            )
        else:
            quick = ""
        if skin.fields:
            for field in skin.fields:
                field_values[field["id"]] = st.text_input(
                    field["label"],
                    placeholder=field.get("placeholder", ""),
                    key=f'chat_field_{field["id"]}',
                )
        if skin.quick_prompts:
            can_add_quick = bool(quick and not st.session_state.get("pending_turn"))
            render_add_starter_button(quick, disabled=not can_add_quick)
    return skin, field_values


def chat_composer(
    project: Project | None,
    chat: ChatSession | None,
    skin,
    field_values: dict[str, str],
) -> None:
    prompt_disabled = not project or bool(st.session_state.get("pending_turn"))
    prompt = st.chat_input(
        skin.placeholder, key="chat_prompt_input", disabled=prompt_disabled
    )
    if prompt and project:
        user_text = prompt.strip()
        if user_text:
            queue_user_turn(
                chat or create_chat(project), user_text, skin.id, field_values
            )

    if not project:
        st.caption("Select a project and enter a message before sending.")


def chat_workspace(
    client: CodexClient,
    project: Project | None,
    chat: ChatSession | None,
    skin,
    field_values: dict[str, str],
) -> None:
    # Keep the native st.chat_input UI, but install the IME-specific Enter guard
    # before rendering the composer.
    inject_chat_input_ime_guard()
    chat_history_panel(client, project, chat)
    chat_composer(project, chat, skin, field_values)


def surface_sidebar(
    settings: AppSettings, server_threads: list[CodexThread]
) -> tuple[Project | None, ChatSession | None, object, dict[str, str]]:
    with st.sidebar:
        st.markdown("**Codex Nomad Surface**")
        if st.button("Settings", key="open_settings_dialog"):
            settings_dialog(settings)
        project = project_selector(server_threads, "sidebar")
        chat = select_chat(project, server_threads)
        skin, field_values = input_assist_panel()
    return project, chat, skin, field_values


@st.dialog("Settings", width="large")
def settings_dialog(settings: AppSettings) -> None:
    settings_screen(settings, heading=False)


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
    project, chat, skin, field_values = surface_sidebar(settings, server_threads)
    cancel_pending_turn_if_needed(client, chat)
    hydrate_thread_chat(client, chat)
    if chat and chat.id != st.session_state.last_rendered_chat_id:
        st.session_state.last_rendered_chat_id = chat.id

    chat_workspace(client, project, chat, skin, field_values)


def main() -> None:
    init_state()
    if not auth_required():
        st.session_state.authenticated = True
    if not st.session_state.authenticated:
        auth_screen()
        return
    main_screen()


if __name__ == "__main__":
    main()
