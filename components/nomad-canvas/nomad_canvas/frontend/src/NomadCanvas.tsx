import { FrontendRendererArgs } from "@streamlit/component-v2-lib";
import {
  Editor,
  TLShapeId,
  TLStoreSnapshot,
  Tldraw,
  createShapeId,
  getSnapshot,
  toRichText,
} from "tldraw";
import "tldraw/tldraw.css";
import { FC, ReactElement, useCallback, useEffect, useRef, useState } from "react";

export type NomadCanvasStateShape = {
  connection_state: string;
  document: TLStoreSnapshot | null;
  preview_svg: string;
  saved_at: string;
  shape_count: number;
};

export type NomadCanvasDataShape = {
  canvasId: string;
  initialDocument: TLStoreSnapshot | null;
  websocketUrl: string;
};

export type NomadCanvasProps = Pick<
  FrontendRendererArgs<NomadCanvasStateShape, NomadCanvasDataShape>,
  "setStateValue"
> &
  NomadCanvasDataShape;

type CanvasRequest = {
  id: string;
  method: "read_scene" | "apply_patch";
  arguments?: Record<string, unknown>;
};

type PatchOperation = Record<string, unknown> & { op?: string };

const bindingProps = (terminal: "start" | "end") => ({
  terminal,
  isExact: false,
  isPrecise: false,
  normalizedAnchor: { x: 0.5, y: 0.5 },
  snap: "none" as const,
});

const compactShape = (shape: Record<string, unknown>) => ({
  id: shape.id,
  type: shape.type,
  x: shape.x,
  y: shape.y,
  rotation: shape.rotation,
  parentId: shape.parentId,
  index: shape.index,
  props: shape.props,
  meta: shape.meta,
});

const safeRef = (value: unknown): string =>
  String(value || "shape")
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .slice(0, 48);

const NomadCanvas: FC<NomadCanvasProps> = ({
  canvasId,
  initialDocument,
  websocketUrl,
  setStateValue,
}): ReactElement => {
  const [editor, setEditor] = useState<Editor | null>(null);
  const websocketRef = useRef<WebSocket | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const applyingRemoteRef = useRef(false);

  const publishSnapshot = useCallback(
    async (activeEditor: Editor) => {
      const document = getSnapshot(activeEditor.store).document;
      const shapes = activeEditor.getCurrentPageShapes();
      const exported = shapes.length
        ? await activeEditor.getSvgString(shapes, {
            background: true,
            padding: 32,
          })
        : undefined;
      const previewSvg = exported?.svg || "";
      const savedAt = new Date().toISOString();
      const payload = {
        document,
        preview_svg: previewSvg,
        saved_at: savedAt,
        shape_count: shapes.length,
      };

      setStateValue("document", document);
      setStateValue("preview_svg", previewSvg);
      setStateValue("saved_at", savedAt);
      setStateValue("shape_count", shapes.length);

      if (websocketRef.current?.readyState === WebSocket.OPEN) {
        websocketRef.current.send(
          JSON.stringify({ type: "snapshot", canvas_id: canvasId, ...payload }),
        );
      }
      return payload;
    },
    [canvasId, setStateValue],
  );

  const scheduleSnapshot = useCallback(
    (activeEditor: Editor) => {
      if (applyingRemoteRef.current) return;
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
      }
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null;
        void publishSnapshot(activeEditor);
      }, 700);
    },
    [publishSnapshot],
  );

  const readScene = useCallback((activeEditor: Editor) => {
    const shapes = activeEditor.getCurrentPageShapes();
    const bindingById = new Map<string, unknown>();
    for (const shape of shapes) {
      for (const binding of activeEditor.getBindingsFromShape(shape.id, "arrow")) {
        bindingById.set(binding.id, binding);
      }
    }
    return {
      page_id: activeEditor.getCurrentPageId(),
      shapes: shapes.map((shape) =>
        compactShape(shape as unknown as Record<string, unknown>),
      ),
      bindings: Array.from(bindingById.values()),
    };
  }, []);

  const applyPatch = useCallback(
    async (activeEditor: Editor, args: Record<string, unknown>) => {
      const operations = Array.isArray(args.operations)
        ? (args.operations as PatchOperation[])
        : [];
      if (operations.length > 100) {
        throw new Error("A canvas patch may contain at most 100 operations.");
      }

      const idsByRef = new Map<string, TLShapeId>();
      const changedIds = new Set<TLShapeId>();
      const resolveId = (value: unknown): TLShapeId => {
        const text = String(value || "");
        const mapped = idsByRef.get(text);
        return (mapped || text) as TLShapeId;
      };

      applyingRemoteRef.current = true;
      try {
        activeEditor.run(() => {
          for (const [operationIndex, operation] of operations.entries()) {
            const op = String(operation.op || "");
            if (op === "create") {
              const ref = String(operation.ref || "");
              const shape =
                operation.shape && typeof operation.shape === "object"
                  ? (operation.shape as Record<string, unknown>)
                  : operation;
              const type = String(shape.type || "geo");
              const id = createShapeId(
                `${safeRef(args.command_id)}-${safeRef(ref)}-${operationIndex}`,
              );
              const props = {
                ...((shape.props as Record<string, unknown>) || {}),
              };
              const text = String(shape.text || "");
              if (text) props.richText = toRichText(text);
              if (type === "geo") {
                props.geo = props.geo || "rectangle";
                props.w = Number(props.w || shape.w || 240);
                props.h = Number(props.h || shape.h || 120);
              }
              activeEditor.createShape({
                id,
                type,
                x: Number(shape.x || 0),
                y: Number(shape.y || 0),
                props,
                meta: { source: "codex", logicalRef: ref },
              } as never);
              if (ref) idsByRef.set(ref, id);
              changedIds.add(id);
              continue;
            }

            if (op === "delete") {
              const ids = Array.isArray(operation.ids)
                ? operation.ids.map(resolveId)
                : [resolveId(operation.id)];
              activeEditor.deleteShapes(ids.filter(Boolean));
              ids.forEach((id) => changedIds.add(id));
              continue;
            }

            if (op === "connect") {
              const fromId = resolveId(operation.from);
              const toId = resolveId(operation.to);
              const from = activeEditor.getShape(fromId);
              const to = activeEditor.getShape(toId);
              if (!from || !to) throw new Error("Connector endpoint not found.");
              const fromBounds = activeEditor.getShapePageBounds(fromId);
              const toBounds = activeEditor.getShapePageBounds(toId);
              if (!fromBounds || !toBounds) throw new Error("Connector bounds not found.");
              const arrowId = createShapeId(
                `${safeRef(args.command_id)}-arrow-${changedIds.size}`,
              );
              activeEditor.createShape({
                id: arrowId,
                type: "arrow",
                x: fromBounds.center.x,
                y: fromBounds.center.y,
                props: {
                  start: { x: 0, y: 0 },
                  end: {
                    x: toBounds.center.x - fromBounds.center.x,
                    y: toBounds.center.y - fromBounds.center.y,
                  },
                },
                meta: { source: "codex" },
              } as never);
              activeEditor.createBindings([
                {
                  type: "arrow",
                  fromId: arrowId,
                  toId: fromId,
                  props: bindingProps("start"),
                },
                {
                  type: "arrow",
                  fromId: arrowId,
                  toId,
                  props: bindingProps("end"),
                },
              ] as never);
              changedIds.add(arrowId);
              continue;
            }

            const id = resolveId(operation.id);
            const existing = activeEditor.getShape(id);
            if (!existing) throw new Error(`Shape not found: ${String(operation.id)}`);
            if (op === "move") {
              activeEditor.updateShape({
                id,
                type: existing.type,
                x: Number(operation.x ?? existing.x),
                y: Number(operation.y ?? existing.y),
              } as never);
            } else if (op === "resize") {
              activeEditor.updateShape({
                id,
                type: existing.type,
                props: {
                  w: Number(operation.w || 100),
                  h: Number(operation.h || 100),
                },
              } as never);
            } else if (op === "update") {
              const props = {
                ...((operation.props as Record<string, unknown>) || {}),
              };
              if (operation.text !== undefined) {
                props.richText = toRichText(String(operation.text));
              }
              activeEditor.updateShape({
                id,
                type: existing.type,
                ...(operation.x !== undefined ? { x: Number(operation.x) } : {}),
                ...(operation.y !== undefined ? { y: Number(operation.y) } : {}),
                props,
              } as never);
            } else {
              throw new Error(`Unsupported canvas operation: ${op}`);
            }
            changedIds.add(id);
          }
        });
      } finally {
        applyingRemoteRef.current = false;
      }

      const snapshot = await publishSnapshot(activeEditor);
      return {
        ...snapshot,
        changed_ids: Array.from(changedIds),
        refs: Object.fromEntries(idsByRef),
        scene: readScene(activeEditor),
      };
    },
    [publishSnapshot, readScene],
  );

  useEffect(() => {
    if (!editor) return;
    const unsubscribe = editor.store.listen(
      () => scheduleSnapshot(editor),
      { scope: "document" },
    );
    void publishSnapshot(editor);
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
    const websocket = new WebSocket(resolvedWebsocketUrl);
    websocketRef.current = websocket;
    setStateValue("connection_state", "connecting");

    websocket.onopen = () => {
      setStateValue("connection_state", "connected");
      websocket.send(JSON.stringify({ type: "hello", canvas_id: canvasId }));
    };
    websocket.onclose = () => setStateValue("connection_state", "disconnected");
    websocket.onerror = () => setStateValue("connection_state", "error");
    websocket.onmessage = (event) => {
      void (async () => {
        let message: { type?: string; request?: CanvasRequest };
        try {
          message = JSON.parse(String(event.data));
        } catch {
          return;
        }
        const request = message.request;
        if (message.type !== "request" || !request) return;
        try {
          const payload =
            request.method === "read_scene"
              ? {
                  scene: readScene(editor),
                  ...(await publishSnapshot(editor)),
                }
              : await applyPatch(editor, request.arguments || {});
          websocket.send(
            JSON.stringify({ type: "response", id: request.id, ok: true, payload }),
          );
        } catch (error) {
          websocket.send(
            JSON.stringify({
              type: "response",
              id: request.id,
              ok: false,
              error: error instanceof Error ? error.message : String(error),
            }),
          );
        }
      })();
    };

    return () => {
      websocket.close();
      websocketRef.current = null;
    };
  }, [applyPatch, canvasId, editor, publishSnapshot, readScene, setStateValue, websocketUrl]);

  return (
    <div className="nomad-canvas-root">
      <Tldraw
        snapshot={initialDocument || undefined}
        onMount={setEditor}
      />
    </div>
  );
};

export default NomadCanvas;
