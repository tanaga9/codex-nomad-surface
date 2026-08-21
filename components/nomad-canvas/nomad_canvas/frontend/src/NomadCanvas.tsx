import {
  Editor,
  TLStoreSnapshot,
  Tldraw,
  getSvgAsImage,
  getSnapshot,
} from "tldraw";
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

const CANVAS_VISION_MAX_DIMENSION = 1536;
const CANVAS_VISION_MAX_BYTES = 8 * 1024 * 1024;
const CANVAS_VISION_LOSSY_QUALITY = 0.9;
const CANVAS_VISION_EXPORT_ATTEMPTS = 3;
const CANVAS_SNAPSHOT_STABILITY_ATTEMPTS = 3;
const CANVAS_EXPORT_PADDING = 32;

const blobToDataUrl = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () =>
      reject(reader.error || new Error("Image export failed."));
    reader.readAsDataURL(blob);
  });

type SceneSvgExport = {
  svg: string;
  width: number;
  height: number;
};

const renderSceneImageBlob = async (
  exported: SceneSvgExport,
  pixelRatio: number,
) => {
  const formats = [
    {
      type: "webp",
      mimeType: "image/webp",
      quality: CANVAS_VISION_LOSSY_QUALITY,
    },
    {
      type: "jpeg",
      mimeType: "image/jpeg",
      quality: CANVAS_VISION_LOSSY_QUALITY,
    },
    { type: "png", mimeType: "image/png", quality: undefined },
  ] as const;

  for (const format of formats) {
    try {
      const image = await getSvgAsImage(exported.svg, {
        type: format.type,
        width: exported.width,
        height: exported.height,
        ...(format.quality === undefined ? {} : { quality: format.quality }),
        pixelRatio,
      });
      if (image?.type === format.mimeType) return image;
    } catch {
      // Retry the same export using the next preferred image format.
    }
  }

  throw new Error("Could not construct canvas image as WebP, JPEG, or PNG.");
};

const renderSceneImage = async (exported: SceneSvgExport | undefined) => {
  if (!exported) {
    return {
      preview_image_url: "",
      preview_image_width: 0,
      preview_image_height: 0,
    };
  }

  let pixelRatio = Math.min(
    1,
    CANVAS_VISION_MAX_DIMENSION / Math.max(exported.width, exported.height),
  );
  for (let attempt = 0; attempt < CANVAS_VISION_EXPORT_ATTEMPTS; attempt += 1) {
    const image = await renderSceneImageBlob(exported, pixelRatio);
    if (image.size <= CANVAS_VISION_MAX_BYTES) {
      return {
        preview_image_url: await blobToDataUrl(image),
        preview_image_width: Math.round(exported.width * pixelRatio),
        preview_image_height: Math.round(exported.height * pixelRatio),
      };
    }
    const sizeRatio = Math.sqrt(CANVAS_VISION_MAX_BYTES / image.size) * 0.95;
    pixelRatio *= Math.min(0.9, sizeRatio);
  }
  throw new Error("Canvas image remains too large after downscaling.");
};

const renderSceneImageSafely = async (exported: SceneSvgExport | undefined) => {
  try {
    return await renderSceneImage(exported);
  } catch (error) {
    return {
      preview_image_url: "",
      preview_image_width: 0,
      preview_image_height: 0,
      preview_image_error:
        error instanceof Error ? error.message : "Canvas image export failed.",
    };
  }
};

const compactShape = (
  shape: Record<string, unknown>,
  pageBounds: { x: number; y: number; w: number; h: number } | undefined,
) => ({
  id: shape.id,
  type: shape.type,
  x: shape.x,
  y: shape.y,
  rotation: shape.rotation,
  parentId: shape.parentId,
  index: shape.index,
  props: shape.props,
  meta: shape.meta,
  ...(pageBounds
    ? {
        page_bounds: {
          x: pageBounds.x,
          y: pageBounds.y,
          w: pageBounds.w,
          h: pageBounds.h,
          center: {
            x: pageBounds.x + pageBounds.w / 2,
            y: pageBounds.y + pageBounds.h / 2,
          },
        },
      }
    : {}),
});

const readScene = (
  activeEditor: Editor,
  shapes = activeEditor.getCurrentPageShapes(),
) => {
  const bindingById = new Map<string, unknown>();
  for (const shape of shapes) {
    for (const binding of activeEditor.getBindingsFromShape(
      shape.id,
      "arrow",
    )) {
      bindingById.set(binding.id, binding);
    }
  }
  return {
    page_id: activeEditor.getCurrentPageId(),
    shapes: shapes.map((shape) => {
      const pageBounds = activeEditor.getShapePageBounds(shape.id);
      return compactShape(
        shape as unknown as Record<string, unknown>,
        pageBounds
          ? {
              x: pageBounds.x,
              y: pageBounds.y,
              w: pageBounds.w,
              h: pageBounds.h,
            }
          : undefined,
      );
    }),
    bindings: Array.from(bindingById.values()),
  };
};

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
  const applyingRemoteRef = useRef(false);
  const commitEpochRef = useRef(0);
  const publishQueueRef = useRef<Promise<void>>(Promise.resolve());
  const documentVersionRef = useRef(0);

  const publishSnapshot = useCallback(
    (activeEditor: Editor, broadcast = true) => {
      const publish = async () => {
        for (
          let attempt = 0;
          attempt < CANVAS_SNAPSHOT_STABILITY_ATTEMPTS;
          attempt += 1
        ) {
          const documentVersion = documentVersionRef.current;
          const document = getSnapshot(activeEditor.store).document;
          const documentFingerprint = JSON.stringify(document);
          const shapes = activeEditor.getCurrentPageShapes();
          const scene = readScene(activeEditor, shapes);
          const exported = shapes.length
            ? await activeEditor.getSvgString(shapes, {
                background: true,
                padding: CANVAS_EXPORT_PADDING,
              })
            : undefined;
          const sceneImage = await renderSceneImageSafely(exported);
          const currentDocument = getSnapshot(activeEditor.store).document;
          if (
            documentVersion !== documentVersionRef.current ||
            documentFingerprint !== JSON.stringify(currentDocument)
          ) {
            continue;
          }

          const previewSvg = exported?.svg || "";
          const savedAt = new Date().toISOString();
          const payload = {
            document,
            preview_svg: previewSvg,
            saved_at: savedAt,
            shape_count: shapes.length,
            ...sceneImage,
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
          return { ...payload, scene };
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

  const scheduleSnapshot = useCallback(
    (activeEditor: Editor) => {
      if (applyingRemoteRef.current) return;
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
      }
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null;
        void publishSnapshot(activeEditor).catch(() => undefined);
      }, 700);
    },
    [publishSnapshot],
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
        const snapshot = await publishSnapshot(activeEditor, false);
        let settled = false;
        return {
          payload: { ...snapshot, ...applied.result },
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
    [publishSnapshot],
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
    void publishSnapshot(editor).catch(() => undefined);
    return () => {
      unsubscribe();
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
    };
  }, [editor, publishSnapshot, scheduleSnapshot]);

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
          void publishSnapshot(editor).catch(() => undefined);
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
            const payload = await publishSnapshot(editor);
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
                error instanceof CanvasProtocolError
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
  }, [applyPatch, canvasId, editor, publishSnapshot, websocketUrl]);

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
