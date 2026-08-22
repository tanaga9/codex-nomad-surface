import { Editor, TLStoreSnapshot, Tldraw, getSnapshot } from "tldraw";
import "tldraw/tldraw.css";
import {
  FC,
  ReactElement,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  applyCanvasPatch,
  CanvasProtocolError,
  validateCanvasPatch,
} from "./canvas-protocol";
import { CanvasReadError, readCanvasScene } from "./canvas-read-protocol";
import { renderCanvasPreview } from "./canvas-preview";
import { lintCanvasPatch } from "./canvas-semantic";
import {
  canBroadcastCanvasSnapshot,
  canCompleteCanvasRequest,
  canProcessCanvasRequest,
} from "./canvas-snapshot-gate";

export type NomadCanvasStateShape = Record<string, never>;

export type NomadCanvasDataShape = {
  canvasId: string;
  initialDocument: TLStoreSnapshot | null;
  websocketUrl: string;
};

export type NomadCanvasProps = NomadCanvasDataShape;

type CanvasRequest = {
  id: string;
  method: "read_scene" | "apply_patch";
  arguments?: Record<string, unknown>;
};

type CanvasResponseAck = {
  ok: boolean;
  error?: string;
};

type PendingCanvasApply = {
  payload: Record<string, unknown>;
  commit: () => void;
  rollback: () => void;
};

type ConnectionState =
  "connecting" | "connected" | "reconnecting" | "disconnected";

const CANVAS_SNAPSHOT_STABILITY_ATTEMPTS = 3;
const CANVAS_DOCUMENT_SAVE_DELAY_MS = 700;
const CANVAS_PREVIEW_SAVE_DELAY_MS = 4_000;

const waitForCanvasGeometry = () =>
  new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      resolve();
    };
    const frame = window.requestAnimationFrame(finish);
    const timeout = window.setTimeout(() => {
      window.cancelAnimationFrame(frame);
      finish();
    }, 100);
  });

const captureDocument = (activeEditor: Editor) =>
  getSnapshot(activeEditor.store).document;

const NomadCanvas: FC<NomadCanvasProps> = ({
  canvasId,
  initialDocument,
  websocketUrl,
}): ReactElement => {
  const [editor, setEditor] = useState<Editor | null>(null);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("connecting");
  const initialDocumentRef = useRef(initialDocument);
  const websocketRef = useRef<WebSocket | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const previewTimerRef = useRef<number | null>(null);
  const applyingRemoteRef = useRef(false);
  const commitEpochRef = useRef(0);
  const publishQueueRef = useRef<Promise<void>>(Promise.resolve());
  const documentVersionRef = useRef(0);

  const persistSnapshot = useCallback(
    (activeEditor: Editor, broadcast = true, includePreview = true) => {
      const publish = async () => {
        for (
          let attempt = 0;
          attempt < CANVAS_SNAPSHOT_STABILITY_ATTEMPTS;
          attempt += 1
        ) {
          const documentVersion = documentVersionRef.current;
          const document = captureDocument(activeEditor);
          const documentFingerprint = JSON.stringify(document);
          const shapes = activeEditor.getCurrentPageShapes();
          const preview = includePreview
            ? await renderCanvasPreview(activeEditor, shapes)
            : {};
          const currentDocument = captureDocument(activeEditor);
          if (
            documentVersion !== documentVersionRef.current ||
            documentFingerprint !== JSON.stringify(currentDocument)
          ) {
            continue;
          }

          const savedAt = new Date().toISOString();
          const payload = {
            document,
            saved_at: savedAt,
            shape_count: shapes.length,
            ...preview,
          };

          const activeWebsocket = websocketRef.current;
          if (
            activeWebsocket &&
            canBroadcastCanvasSnapshot(
              broadcast,
              applyingRemoteRef.current,
              activeWebsocket.readyState,
            )
          ) {
            activeWebsocket.send(
              JSON.stringify({
                type: "snapshot",
                canvas_id: canvasId,
                ...payload,
              }),
            );
          }
          return payload;
        }
        throw new Error("Canvas changed while preparing its visual snapshot.");
      };

      const queued = publishQueueRef.current.then(publish, publish);
      publishQueueRef.current = queued.then(
        () => undefined,
        () => undefined,
      );
      return queued;
    },
    [canvasId],
  );

  const buildReadPayload = useCallback(
    async (activeEditor: Editor, args: Record<string, unknown>) => {
      for (
        let attempt = 0;
        attempt < CANVAS_SNAPSHOT_STABILITY_ATTEMPTS;
        attempt += 1
      ) {
        const documentVersion = documentVersionRef.current;
        const scoped = readCanvasScene(activeEditor, args);
        const preview = scoped.request.include_image
          ? await renderCanvasPreview(
              activeEditor,
              scoped.shapesForExport,
              scoped.request.max_image_dimension,
            )
          : {
              preview_image_url: "",
              preview_image_width: 0,
              preview_image_height: 0,
              preview_image_error: undefined,
            };
        if (documentVersion !== documentVersionRef.current) continue;
        return {
          ...scoped.result,
          image: {
            included: Boolean(preview.preview_image_url),
            width: preview.preview_image_width,
            height: preview.preview_image_height,
            scope: scoped.request.scope,
            ...(preview.preview_image_error
              ? { error: preview.preview_image_error }
              : {}),
          },
          preview_image_url: preview.preview_image_url,
        };
      }
      throw new Error("Canvas changed while preparing the scoped scene.");
    },
    [],
  );

  const scheduleSnapshot = useCallback(
    (activeEditor: Editor) => {
      if (applyingRemoteRef.current) return;
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
      }
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null;
        void persistSnapshot(activeEditor, true, false).catch(() => undefined);
      }, CANVAS_DOCUMENT_SAVE_DELAY_MS);
      if (previewTimerRef.current !== null) {
        window.clearTimeout(previewTimerRef.current);
      }
      previewTimerRef.current = window.setTimeout(() => {
        previewTimerRef.current = null;
        void persistSnapshot(activeEditor).catch(() => undefined);
      }, CANVAS_PREVIEW_SAVE_DELAY_MS);
    },
    [persistSnapshot],
  );

  const applyPatch = useCallback(
    async (
      activeEditor: Editor,
      args: Record<string, unknown>,
    ): Promise<PendingCanvasApply> => {
      const plan = validateCanvasPatch(activeEditor, args);
      commitEpochRef.current += 1;
      applyingRemoteRef.current = true;
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
      if (previewTimerRef.current !== null) {
        window.clearTimeout(previewTimerRef.current);
        previewTimerRef.current = null;
      }
      const wasReadonly = activeEditor.getIsReadonly();
      let rollback: (() => void) | undefined;
      const finish = () => {
        activeEditor.updateInstanceState({ isReadonly: wasReadonly });
        applyingRemoteRef.current = false;
      };
      try {
        const applied = applyCanvasPatch(activeEditor, plan);
        rollback = applied.rollback;
        activeEditor.updateInstanceState({ isReadonly: true });
        await activeEditor.fonts.loadRequiredFontsForCurrentPage(20);
        await waitForCanvasGeometry();
        const warnings = lintCanvasPatch(
          activeEditor,
          applied.result.changed_ids,
          plan.requestedHeights,
        );
        const snapshot = await persistSnapshot(activeEditor, false);
        let settled = false;
        return {
          payload: {
            ...snapshot,
            ...applied.result,
            warnings,
            semantic_success: !warnings.some(
              (warning) => warning.severity === "error",
            ),
          },
          commit: () => {
            if (settled) return;
            settled = true;
            finish();
          },
          rollback: () => {
            if (settled) return;
            settled = true;
            try {
              applied.rollback();
            } finally {
              finish();
            }
          },
        };
      } catch (error) {
        try {
          rollback?.();
        } finally {
          finish();
        }
        throw error;
      }
    },
    [persistSnapshot],
  );

  useEffect(() => {
    if (!editor) return;
    const unsubscribe = editor.store.listen(
      () => {
        documentVersionRef.current += 1;
        scheduleSnapshot(editor);
      },
      { scope: "document" },
    );
    void persistSnapshot(editor, true, false).catch(() => undefined);
    return () => {
      unsubscribe();
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
      if (previewTimerRef.current !== null) {
        window.clearTimeout(previewTimerRef.current);
        previewTimerRef.current = null;
      }
    };
  }, [editor, persistSnapshot, scheduleSnapshot]);

  useEffect(() => {
    if (!editor || !websocketUrl) return;
    const resolvedWebsocketUrl = websocketUrl.startsWith("/")
      ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${websocketUrl}`
      : websocketUrl;
    let disposed = false;
    let retryTimer: number | null = null;
    let websocket: WebSocket | null = null;
    const responseAcks = new Map<
      string,
      {
        arguments: Record<string, unknown>;
        resolve: (ack: CanvasResponseAck) => void;
      }
    >();
    const failPendingAcks = (error: string) => {
      for (const pending of responseAcks.values()) {
        pending.resolve({ ok: false, error });
      }
      responseAcks.clear();
    };
    const requestPendingCommitStatuses = (activeWebsocket: WebSocket) => {
      if (activeWebsocket.readyState !== WebSocket.OPEN) return;
      for (const [requestId, pending] of responseAcks) {
        activeWebsocket.send(
          JSON.stringify({
            type: "command_status",
            id: requestId,
            arguments: pending.arguments,
          }),
        );
      }
    };
    const waitForResponseAck = (
      requestId: string,
      args: Record<string, unknown>,
    ) =>
      new Promise<CanvasResponseAck>((resolve) => {
        responseAcks.set(requestId, {
          arguments: args,
          resolve: (ack) => {
            responseAcks.delete(requestId);
            resolve(ack);
          },
        });
      });
    const statusTimer = window.setInterval(() => {
      if (websocket) requestPendingCommitStatuses(websocket);
    }, 5_000);
    const sendCommitPending = (
      activeWebsocket: WebSocket,
      requestId: string,
    ) => {
      if (activeWebsocket.readyState !== WebSocket.OPEN) return;
      activeWebsocket.send(
        JSON.stringify({
          type: "response",
          id: requestId,
          ok: false,
          error: "canvas_commit_pending",
          payload: {
            error: "canvas_commit_pending",
            retryable: true,
            message:
              "The previous Canvas command is still awaiting commit acknowledgement.",
          },
        }),
      );
    };

    const connect = () => {
      if (disposed) return;
      setConnectionState(websocket ? "reconnecting" : "connecting");
      const nextWebsocket = new WebSocket(resolvedWebsocketUrl);
      websocket = nextWebsocket;
      websocketRef.current = nextWebsocket;

      nextWebsocket.onopen = () => {
        if (disposed) return;
        setConnectionState("connected");
        nextWebsocket.send(
          JSON.stringify({ type: "hello", canvas_id: canvasId }),
        );
        requestPendingCommitStatuses(nextWebsocket);
        if (responseAcks.size === 0) {
          void persistSnapshot(editor).catch(() => undefined);
        }
      };
      nextWebsocket.onclose = (event) => {
        if (disposed) return;
        if (websocketRef.current === nextWebsocket) {
          websocketRef.current = null;
        }
        if ([4001, 4401, 4404].includes(event.code)) {
          failPendingAcks("canvas_commit_status_unavailable");
          setConnectionState("disconnected");
          return;
        }
        setConnectionState("reconnecting");
        retryTimer = window.setTimeout(connect, 1500);
      };
      nextWebsocket.onerror = () => nextWebsocket.close();
      nextWebsocket.onmessage = (event) => {
        void (async () => {
          let message: {
            type?: string;
            request?: CanvasRequest;
            id?: string;
            ok?: boolean;
            error?: string;
          };
          try {
            message = JSON.parse(String(event.data));
          } catch {
            return;
          }
          if (message.type === "response_ack" && message.id) {
            responseAcks.get(message.id)?.resolve({
              ok: Boolean(message.ok),
              error: message.error,
            });
            return;
          }
          const request = message.request;
          if (message.type !== "request" || !request) return;
          if (!canProcessCanvasRequest(applyingRemoteRef.current)) {
            sendCommitPending(nextWebsocket, request.id);
            return;
          }
          if (request.method === "apply_patch") {
            let pendingApply: PendingCanvasApply;
            try {
              pendingApply = await applyPatch(editor, request.arguments || {});
            } catch (error) {
              if (nextWebsocket.readyState === WebSocket.OPEN) {
                const payload =
                  error instanceof CanvasProtocolError
                    ? error.toPayload()
                    : undefined;
                nextWebsocket.send(
                  JSON.stringify({
                    type: "response",
                    id: request.id,
                    ok: false,
                    error:
                      error instanceof Error ? error.message : String(error),
                    ...(payload ? { payload } : {}),
                  }),
                );
              }
              return;
            }

            const ack = waitForResponseAck(request.id, request.arguments || {});
            nextWebsocket.send(
              JSON.stringify({
                type: "response",
                id: request.id,
                ok: true,
                payload: pendingApply.payload,
              }),
            );
            const commitResult = await ack;
            if (commitResult.ok) pendingApply.commit();
            else pendingApply.rollback();
            return;
          }
          try {
            const startedAtCommitEpoch = commitEpochRef.current;
            const payload = await buildReadPayload(
              editor,
              request.arguments || {},
            );
            if (
              !canCompleteCanvasRequest(
                startedAtCommitEpoch,
                commitEpochRef.current,
                applyingRemoteRef.current,
              )
            ) {
              sendCommitPending(nextWebsocket, request.id);
              return;
            }
            nextWebsocket.send(
              JSON.stringify({
                type: "response",
                id: request.id,
                ok: true,
                payload,
              }),
            );
          } catch (error) {
            if (nextWebsocket.readyState === WebSocket.OPEN) {
              const payload =
                error instanceof CanvasProtocolError ||
                error instanceof CanvasReadError
                  ? error.toPayload()
                  : undefined;
              nextWebsocket.send(
                JSON.stringify({
                  type: "response",
                  id: request.id,
                  ok: false,
                  error: error instanceof Error ? error.message : String(error),
                  ...(payload ? { payload } : {}),
                }),
              );
            }
          }
        })();
      };
    };

    connect();

    return () => {
      disposed = true;
      failPendingAcks("canvas_component_disposed_before_commit");
      window.clearInterval(statusTimer);
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      websocket?.close();
      websocketRef.current = null;
    };
  }, [
    applyPatch,
    buildReadPayload,
    canvasId,
    editor,
    persistSnapshot,
    websocketUrl,
  ]);

  return (
    <div className="nomad-canvas-root">
      <div
        className="nomad-canvas-connection"
        data-state={connectionState}
        role="status"
      >
        {connectionState === "connected"
          ? "Connected"
          : connectionState === "connecting"
            ? "Connecting…"
            : connectionState === "reconnecting"
              ? "Reconnecting…"
              : "Disconnected"}
      </div>
      <Tldraw
        snapshot={initialDocumentRef.current || undefined}
        onMount={setEditor}
      />
    </div>
  );
};

export default NomadCanvas;
