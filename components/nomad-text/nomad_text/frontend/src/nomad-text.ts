import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { syntaxHighlighting, defaultHighlightStyle } from "@codemirror/language";
import { searchKeymap } from "@codemirror/search";
import { Compartment, EditorSelection, EditorState, Extension, Transaction } from "@codemirror/state";
import {
  EditorView,
  highlightActiveLine,
  keymap,
  lineNumbers,
  placeholder,
} from "@codemirror/view";

import { markdownAssistance } from "./markdown-assistance";
import { shouldReconnectTextSocket } from "./text-connection-policy";
import { applyTextOperations, TextOperation } from "./text-patch";

export type TextFormat = "plain" | "markdown";
export type MarkdownPresentation = "raw" | "assisted";
export type EditorKind = "text" | "document";

export interface NomadTextData {
  textId: string;
  initialText: string;
  format: TextFormat;
  editorKind: EditorKind;
  presentation: MarkdownPresentation;
  revision: number;
  websocketUrl: string;
  placeholder?: string;
}

export type NomadTextState = Record<string, never>;

type RequestMessage = {
  type: "request";
  request: { id: string; method: string; arguments: Record<string, unknown> };
};

const language = new Compartment();
const assistance = new Compartment();
const editability = new Compartment();

const languageExtensions = (format: TextFormat): Extension[] =>
  format === "markdown"
    ? [markdown(), syntaxHighlighting(defaultHighlightStyle)]
    : [lineNumbers()];

const assistanceExtensions = (
  format: TextFormat,
  presentation: MarkdownPresentation,
): Extension[] => (format === "markdown" && presentation === "assisted" ? [markdownAssistance] : []);

export class NomadText {
  private readonly root: HTMLElement;
  private readonly view: EditorView;
  private socket: WebSocket | null = null;
  private data: NomadTextData;
  private revision: number;
  private saveTimer: number | null = null;
  private reconnectTimer: number | null = null;
  private pendingCheckpoint = false;
  private checkpointInFlight = false;
  private applyingRemote = false;
  private destroyed = false;
  private synced = false;
  private status: HTMLElement;
  private presentationButton: HTMLButtonElement;
  private conflictActions: HTMLElement;
  private conflict: { revision: number; content: string } | null = null;
  private preparedPatches = new Map<
    string,
    { baseRevision: number; before: string; after: string }
  >();
  private snapshotRequests = new Map<
    string,
    { content: string; purpose: "export" | "sync" }
  >();

  constructor(root: HTMLElement, data: NomadTextData) {
    this.root = root;
    this.data = data;
    this.revision = data.revision;
    root.replaceChildren();

    const toolbar = document.createElement("div");
    toolbar.className = "nomad-text-toolbar";
    const label = document.createElement("span");
    label.className = "nomad-text-format";
    label.textContent = data.editorKind === "document" ? "Document" : "Text";
    this.presentationButton = document.createElement("button");
    this.presentationButton.type = "button";
    this.presentationButton.className = "nomad-text-presentation";
    this.presentationButton.onclick = () => this.togglePresentation();
    this.status = document.createElement("span");
    this.status.className = "nomad-text-status";
    this.status.textContent = "Connecting…";
    this.conflictActions = document.createElement("span");
    this.conflictActions.className = "nomad-text-conflict-actions";
    this.conflictActions.hidden = true;
    const keepMine = document.createElement("button");
    keepMine.type = "button";
    keepMine.textContent = "Keep mine";
    keepMine.onclick = () => this.keepLocalConflict();
    const useSaved = document.createElement("button");
    useSaved.type = "button";
    useSaved.textContent = "Use saved";
    useSaved.onclick = () => this.useSavedConflict();
    this.conflictActions.append(keepMine, useSaved);
    toolbar.append(label, this.presentationButton, this.conflictActions, this.status);

    const editor = document.createElement("div");
    editor.className = "nomad-text-editor";
    root.append(toolbar, editor);

    this.view = new EditorView({
      parent: editor,
      state: EditorState.create({
        doc: data.initialText,
        extensions: [
          EditorView.lineWrapping,
          history(),
          highlightActiveLine(),
          keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
          placeholder(data.placeholder || "Start writing…"),
          language.of(languageExtensions(data.format)),
          assistance.of(assistanceExtensions(data.format, data.presentation)),
          editability.of(EditorView.editable.of(true)),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && !this.applyingRemote) this.scheduleCheckpoint();
          }),
          EditorView.theme({
            "&": { height: "100%" },
            ".cm-scroller": { overflow: "auto" },
            ".cm-content": { minHeight: "100%", padding: "2.25rem 2rem 6rem" },
            ".cm-line": { padding: "0 0.15rem" },
            "&.cm-focused": { outline: "none" },
          }),
        ],
      }),
    });
    this.refreshPresentationButton();
    this.connect();
  }

  update(data: NomadTextData) {
    const formatChanged = data.format !== this.data.format;
    const presentationChanged = data.presentation !== this.data.presentation;
    const editorKindChanged = data.editorKind !== this.data.editorKind;
    this.data = data;
    if (formatChanged || presentationChanged) {
      this.view.dispatch({
        effects: [
          language.reconfigure(languageExtensions(data.format)),
          assistance.reconfigure(assistanceExtensions(data.format, data.presentation)),
        ],
      });
      this.refreshPresentationButton();
    }
    if (editorKindChanged) {
      const label = this.root.querySelector<HTMLElement>(".nomad-text-format");
      if (label) {
        label.textContent = data.editorKind === "document" ? "Document" : "Text";
      }
    }
  }

  destroy() {
    if (this.destroyed) return;
    if (this.saveTimer !== null) window.clearTimeout(this.saveTimer);
    this.saveTimer = null;
    // WebSocket.close() queues its closing handshake after already-sent data,
    // so make one final best-effort checkpoint before tearing down the editor.
    this.checkpoint();
    this.destroyed = true;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    if (this.socket) {
      this.socket.onclose = null;
      this.socket.close();
      this.socket = null;
    }
    this.view.destroy();
  }

  private connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = new URL(this.data.websocketUrl, window.location.href);
    url.protocol = protocol;
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket || this.destroyed) return;
      this.status.textContent = "Syncing…";
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket || this.destroyed) return;
      this.handleMessage(JSON.parse(String(event.data)));
    };
    socket.onclose = (event) => {
      if (this.socket !== socket || this.destroyed) return;
      this.socket = null;
      this.synced = false;
      if (this.checkpointInFlight) this.pendingCheckpoint = true;
      this.checkpointInFlight = false;
      if (!shouldReconnectTextSocket(event.code, this.destroyed)) {
        this.setEditable(false);
        this.status.textContent = "Opened elsewhere";
        return;
      }
      this.status.textContent = "Reconnecting…";
      this.reconnectTimer = window.setTimeout(() => this.connect(), 1200);
    };
    socket.onerror = () => {
      if (this.socket !== socket || this.destroyed) return;
      this.status.textContent = "Offline";
    };
  }

  private scheduleCheckpoint() {
    this.pendingCheckpoint = true;
    this.status.textContent = "Saving…";
    if (this.saveTimer !== null) window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => this.checkpoint(), 450);
  }

  private checkpoint() {
    this.saveTimer = null;
    if (
      !this.pendingCheckpoint ||
      this.checkpointInFlight ||
      this.conflict ||
      this.preparedPatches.size > 0 ||
      this.snapshotRequests.size > 0 ||
      !this.synced ||
      this.socket?.readyState !== WebSocket.OPEN
    ) return;
    this.pendingCheckpoint = false;
    this.checkpointInFlight = true;
    this.socket.send(JSON.stringify({
      type: "checkpoint",
      base_revision: this.revision,
      content: this.view.state.doc.toString(),
    }));
  }

  private handleMessage(message: Record<string, unknown>) {
    if (
      message.type === "sync" &&
      typeof message.revision === "number" &&
      typeof message.content === "string"
    ) {
      this.handleSync(message.revision, message.content);
      return;
    }
    if (message.type === "checkpoint_ack") {
      this.checkpointInFlight = false;
      if (message.ok && typeof message.revision === "number") {
        this.revision = message.revision;
        this.status.textContent = "Saved";
        if (this.pendingCheckpoint) this.checkpoint();
      } else {
        if (
          message.error === "revision_conflict" &&
          typeof message.revision === "number" &&
          typeof message.content === "string"
        ) {
          this.enterConflict(message.revision, message.content);
        } else {
          this.pendingCheckpoint = true;
          this.status.textContent = "Save failed";
        }
      }
      return;
    }
    if (
      message.type === "commit" &&
      typeof message.id === "string" &&
      typeof message.revision === "number" &&
      typeof message.content === "string"
    ) {
      this.commitPreparedPatch(message.id, message.revision, message.content);
      return;
    }
    if (
      message.type === "snapshot_commit" &&
      typeof message.id === "string" &&
      typeof message.revision === "number" &&
      typeof message.content === "string"
    ) {
      this.commitSnapshot(message.id, message.revision, message.content);
      return;
    }
    if (message.type === "reject" && typeof message.id === "string") {
      this.rejectPreparedPatch(
        message.id,
        String(message.error || "apply_failed"),
        typeof message.revision === "number" ? message.revision : undefined,
        typeof message.content === "string" ? message.content : undefined,
      );
      return;
    }
    if (message.type === "request") this.handleRequest(message as unknown as RequestMessage);
  }

  private handleRequest(message: RequestMessage) {
    const { id, method, arguments: args } = message.request;
    try {
      const payload =
        method === "read"
          ? this.read(args)
          : method === "export_snapshot"
            ? this.prepareSnapshot(id, "export")
            : method === "sync_snapshot"
              ? this.prepareSnapshot(id, "sync")
            : method === "apply_patch"
              ? this.preparePatch(id, args)
              : (() => { throw new Error("unsupported_method"); })();
      this.respond(id, true, payload);
    } catch (error) {
      this.respond(id, false, undefined, error instanceof Error ? error.message : String(error));
    }
  }

  private read(_args: Record<string, unknown>): Record<string, unknown> {
    if (this.conflict) throw new Error("save_conflict");
    const document = this.view.state.doc;
    const range = this.view.state.selection.main;
    const point = (position: number) => {
      const line = document.lineAt(position);
      return {
        line: line.number,
        column: Array.from(document.sliceString(line.from, position)).length + 1,
      };
    };
    return {
      revision: this.revision,
      content: document.toString(),
      selection: {
        selection_kind: range.empty ? "caret" : "range",
        column_unit: "unicode_code_point",
        anchor: point(range.anchor),
        head: point(range.head),
        from_line: document.lineAt(range.from).number,
        to_line: document.lineAt(range.to).number,
        text: document.sliceString(range.from, range.to),
      },
    };
  }

  private prepareSnapshot(
    id: string,
    purpose: "export" | "sync",
  ): Record<string, unknown> {
    if (this.conflict) throw new Error("save_conflict");
    if (this.preparedPatches.size > 0) throw new Error("apply_in_progress");
    if (this.snapshotRequests.size > 0) throw new Error("snapshot_in_progress");
    const content = this.view.state.doc.toString();
    this.snapshotRequests.set(id, { content, purpose });
    this.setEditable(false);
    this.status.textContent = purpose === "sync" ? "Syncing…" : "Exporting…";
    return {
      text: content,
      revision: this.revision,
    };
  }

  private preparePatch(id: string, args: Record<string, unknown>): Record<string, unknown> {
    if (this.conflict) throw new Error("save_conflict");
    if (this.preparedPatches.size > 0) throw new Error("apply_in_progress");
    if (this.snapshotRequests.size > 0) throw new Error("snapshot_in_progress");
    const baseRevision = Number(args.base_revision);
    if (baseRevision !== this.revision) throw new Error("revision_conflict");
    const operations = args.operations;
    if (!Array.isArray(operations) || operations.length === 0) throw new Error("invalid_operations");
    const before = this.view.state.doc.toString();
    const after = applyTextOperations(before, operations as TextOperation[]);
    this.preparedPatches.set(id, { baseRevision, before, after });
    this.setEditable(false);
    this.status.textContent = "Applying AI…";
    return { content: after, base_revision: baseRevision, changed_operations: operations.length };
  }

  private handleSync(revision: number, content: string) {
    this.synced = true;
    if (this.preparedPatches.size > 0) {
      this.preparedPatches.clear();
    }
    if (this.snapshotRequests.size > 0) {
      this.snapshotRequests.clear();
    }
    this.refreshOperationEditability();
    const local = this.view.state.doc.toString();
    if (
      (this.conflict || this.pendingCheckpoint || this.checkpointInFlight) &&
      local !== content
    ) {
      this.checkpointInFlight = false;
      this.enterConflict(revision, content);
      return;
    }
    this.applyCanonical(content, false);
    this.conflict = null;
    this.conflictActions.hidden = true;
    this.revision = revision;
    this.checkpointInFlight = false;
    this.pendingCheckpoint = false;
    this.status.textContent = "Saved";
  }

  private commitSnapshot(id: string, revision: number, content: string) {
    const snapshot = this.snapshotRequests.get(id);
    this.snapshotRequests.delete(id);
    this.refreshOperationEditability();
    if (
      snapshot === undefined ||
      snapshot.content !== content ||
      snapshot.content !== this.view.state.doc.toString()
    ) {
      this.enterConflict(revision, content);
      return;
    }
    this.revision = revision;
    this.pendingCheckpoint = false;
    this.checkpointInFlight = false;
    this.status.textContent = "Saved";
  }

  private commitPreparedPatch(id: string, revision: number, content: string) {
    const prepared = this.preparedPatches.get(id);
    this.preparedPatches.delete(id);
    this.refreshOperationEditability();
    if (
      !prepared ||
      prepared.after !== content ||
      prepared.baseRevision !== this.revision ||
      prepared.before !== this.view.state.doc.toString()
    ) {
      this.enterConflict(revision, content);
      return;
    }
    this.applyCanonical(content, true);
    this.revision = revision;
    this.pendingCheckpoint = false;
    this.checkpointInFlight = false;
    this.status.textContent = "Saved";
  }

  private rejectPreparedPatch(
    id: string,
    error: string,
    revision?: number,
    content?: string,
  ) {
    const wasPatch = this.preparedPatches.delete(id);
    const snapshot = this.snapshotRequests.get(id);
    const wasSnapshot = this.snapshotRequests.delete(id);
    this.refreshOperationEditability();
    if (typeof revision === "number" && typeof content === "string") {
      this.enterConflict(revision, content);
      return;
    }
    this.status.textContent =
      error === "revision_conflict"
        ? "Save conflict"
        : wasSnapshot
          ? snapshot?.purpose === "sync" ? "Sync failed" : "Export failed"
          : wasPatch
            ? "AI change failed"
            : "Request failed";
  }

  private applyCanonical(content: string, addToHistory: boolean) {
    if (this.view.state.doc.toString() === content) return;
    const selection = EditorSelection.cursor(
      Math.min(content.length, this.view.state.selection.main.head),
    );
    this.applyingRemote = true;
    try {
      this.view.dispatch({
        changes: { from: 0, to: this.view.state.doc.length, insert: content },
        selection,
        annotations: Transaction.addToHistory.of(addToHistory),
      });
    } finally {
      this.applyingRemote = false;
    }
  }

  private setEditable(value: boolean) {
    this.view.dispatch({ effects: editability.reconfigure(EditorView.editable.of(value)) });
  }

  private refreshOperationEditability() {
    this.setEditable(
      this.preparedPatches.size === 0 && this.snapshotRequests.size === 0,
    );
  }

  private enterConflict(revision: number, content: string) {
    this.conflict = { revision, content };
    this.pendingCheckpoint = false;
    this.checkpointInFlight = false;
    this.conflictActions.hidden = false;
    this.status.textContent = "Save conflict";
  }

  private keepLocalConflict() {
    if (!this.conflict) return;
    this.revision = this.conflict.revision;
    this.conflict = null;
    this.conflictActions.hidden = true;
    this.pendingCheckpoint = true;
    this.status.textContent = "Saving…";
    this.checkpoint();
  }

  private useSavedConflict() {
    if (!this.conflict) return;
    const conflict = this.conflict;
    this.conflict = null;
    this.conflictActions.hidden = true;
    this.applyCanonical(conflict.content, true);
    this.revision = conflict.revision;
    this.pendingCheckpoint = false;
    this.checkpointInFlight = false;
    this.status.textContent = "Saved";
  }

  private respond(id: string, ok: boolean, payload?: Record<string, unknown>, error = "") {
    this.socket?.send(JSON.stringify({ type: "response", id, ok, error, payload }));
  }

  private togglePresentation() {
    if (this.data.format !== "markdown") return;
    this.data.presentation = this.data.presentation === "assisted" ? "raw" : "assisted";
    this.view.dispatch({ effects: assistance.reconfigure(assistanceExtensions(this.data.format, this.data.presentation)) });
    this.refreshPresentationButton();
    this.socket?.send(JSON.stringify({ type: "presentation", presentation: this.data.presentation }));
  }

  private refreshPresentationButton() {
    const markdown = this.data.format === "markdown";
    this.presentationButton.hidden = !markdown;
    this.presentationButton.textContent = this.data.presentation === "assisted" ? "Assisted" : "Raw";
  }
}
