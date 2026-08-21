import {
  HistoryManager,
  Store,
  StoreSchema,
  createRecordType,
  type BaseRecord,
  type Editor,
  type RecordId,
} from "tldraw";
import {
  applyCanvasPatch,
  CANVAS_PATCH_MAX_CHANGED_IDS,
  CanvasProtocolError,
  type NormalizedPatchPlan,
  validateCanvasPatch,
} from "./canvas-protocol";
import {
  canBroadcastCanvasSnapshot,
  canCompleteCanvasRequest,
  canProcessCanvasRequest,
} from "./canvas-snapshot-gate";
import {
  CANVAS_READ_MAX_SHAPES,
  CanvasReadError,
  normalizeCanvasReadRequest,
  readCanvasScene,
} from "./canvas-read-protocol";
import { renderCanvasPreview } from "./canvas-preview";

const assert = (condition: unknown, message: string) => {
  if (!condition) throw new Error(message);
};

{
  const scopeFixtures = [
    { type: "all" },
    { type: "viewport" },
    { type: "selection" },
    { type: "bounds", x: 0, y: 0, width: 100, height: 100 },
    { type: "frame", id: "shape:frame" },
    { type: "shape_ids", ids: ["shape:one"] },
  ];
  for (const scope of scopeFixtures) {
    const request = normalizeCanvasReadRequest({ scope });
    assert(
      request.scope.type === scope.type,
      `Scope ${scope.type} was rejected.`,
    );
  }
  let error: unknown;
  try {
    normalizeCanvasReadRequest({ scope: { type: "all", extra: true } });
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasReadError,
    "A read scope with unknown fields passed validation.",
  );
  error = undefined;
  try {
    normalizeCanvasReadRequest({
      scope: { type: "shape_ids", ids: ["shape:one", "shape:one"] },
    });
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasReadError,
    "Duplicate shape IDs passed browser-side validation.",
  );
}

{
  const preview = await renderCanvasPreview(
    {
      getSvgString: async () => {
        throw new Error("forced SVG failure");
      },
    } as unknown as Editor,
    [{ id: "shape:preview" }] as never,
  );
  assert(
    preview.preview_svg === "" &&
      preview.preview_image_url === "" &&
      preview.preview_image_error === "forced SVG failure",
    "A preview export failure escaped its best-effort boundary.",
  );
}

{
  const frame = {
    id: "shape:frame",
    type: "frame",
    parentId: "page:one",
    index: "a1",
    x: 0,
    y: 0,
    rotation: 0,
    props: { w: 400, h: 300, name: "Process" },
    meta: {},
  };
  const child = {
    id: "shape:child",
    type: "geo",
    parentId: frame.id,
    index: "a2",
    x: 20,
    y: 20,
    rotation: 0,
    props: {
      w: 100,
      h: 50,
      richText: {
        type: "doc",
        content: [
          {
            type: "paragraph",
            content: [{ type: "text", text: "Hello world" }],
          },
        ],
      },
    },
    meta: { logicalRef: "child" },
  };
  const unrelated = {
    id: "shape:outside",
    type: "geo",
    parentId: "page:one",
    index: "a3",
    x: 800,
    y: 800,
    rotation: 0,
    props: { w: 100, h: 50 },
    meta: {},
  };
  const binding = {
    id: "binding:external",
    type: "arrow",
    fromId: unrelated.id,
    toId: child.id,
    props: {},
  };
  const shapes = [frame, child, unrelated];
  const editor = {
    getShape: (id: string) => shapes.find((shape) => shape.id === id),
    isShapeInPage: () => true,
    getShapeAndDescendantIds: () => new Set([frame.id, child.id]),
    getCurrentPageShapesSorted: () => shapes,
    getCurrentPageShapeIds: () => new Set(shapes.map((shape) => shape.id)),
    getCurrentPageId: () => "page:one",
    getTextOptions: () => ({}),
    getShapePageBounds: (shape: (typeof shapes)[number]) => ({
      x: shape.x,
      y: shape.y,
      w: shape.props.w,
      h: shape.props.h,
    }),
    getBindingsInvolvingShape: (shape: (typeof shapes)[number]) =>
      shape.id === child.id ? [binding] : [],
  } as unknown as Editor;

  const scoped = readCanvasScene(editor, {
    scope: { type: "frame", id: frame.id },
    include_image: false,
  });
  assert(
    scoped.result.returned_shapes === 2 &&
      !scoped.shapesForExport.some((shape) => shape.id === unrelated.id),
    "A frame read included unrelated canvas content.",
  );
  assert(
    scoped.result.shapes.find((shape) => shape.id === child.id)?.text ===
      "Hello world",
    "A rich-text summary included TipTap structure fields.",
  );
  const scopedBinding = scoped.result.bindings[0];
  assert(
    scopedBinding.from.external_to_scope && !scopedBinding.to.external_to_scope,
    "A scoped binding lost its external endpoint marker.",
  );
}

{
  const shapes = Array.from(
    { length: CANVAS_READ_MAX_SHAPES + 1 },
    (_, index) => ({
      id: `shape:${index}`,
      type: "geo",
      parentId: "page:one",
      index: String(index).padStart(4, "0"),
      x: index,
      y: index,
      rotation: 0,
      props: { w: 10, h: 10 },
      meta: {},
    }),
  );
  const editor = {
    getCurrentPageShapeIds: () => new Set(shapes.map((shape) => shape.id)),
    getCurrentPageShapesSorted: () => shapes,
    getCurrentPageId: () => "page:one",
    getShapePageBounds: (shape: (typeof shapes)[number]) => ({
      x: shape.x,
      y: shape.y,
      w: 10,
      h: 10,
    }),
    getBindingsInvolvingShape: () => [],
  } as unknown as Editor;
  const result = readCanvasScene(editor, { include_image: false }).result;
  assert(
    result.truncated &&
      result.total_shapes === CANVAS_READ_MAX_SHAPES + 1 &&
      result.returned_shapes === CANVAS_READ_MAX_SHAPES &&
      Boolean(result.suggested_scope),
    "An oversized read did not report structured truncation.",
  );
}

interface HistoryShapeRecord extends BaseRecord<
  "history-shape",
  HistoryShapeRecordId
> {
  type: "geo";
  x: number;
  y: number;
}

type HistoryShapeRecordId = RecordId<HistoryShapeRecord>;

const historySchema = StoreSchema.create<HistoryShapeRecord, null>({
  "history-shape": createRecordType<HistoryShapeRecord>("history-shape", {
    scope: "document",
  }),
});

{
  assert(
    canBroadcastCanvasSnapshot(true, false, 1),
    "A committed snapshot was not eligible for broadcast.",
  );
  assert(
    !canBroadcastCanvasSnapshot(true, true, 1),
    "An uncommitted remote patch could escape through snapshot broadcast.",
  );
  assert(
    !canProcessCanvasRequest(true),
    "A request could read and persist an uncommitted remote patch.",
  );
  assert(
    !canCompleteCanvasRequest(2, 3, false),
    "A read that crossed a commit epoch could return an uncommitted document.",
  );
}

{
  const editor = {
    getShape: (id: string) => ({ id, type: "geo", isLocked: false }),
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;

  let error: unknown;
  try {
    validateCanvasPatch(editor, {
      command_id: "empty-update",
      base_revision: 0,
      operations: [{ op: "update", target: { id: "shape:unchanged" } }],
    });
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasProtocolError &&
      error.operationErrors.some(
        (item) =>
          item.path === "operation" && item.code === "invalid_operation",
      ),
    "An empty update passed preflight and could be receipted as changed.",
  );
}

{
  const editor = {
    getShape: (id: string) => ({ id, type: "geo", isLocked: true }),
    isShapeOrAncestorLocked: () => true,
  } as unknown as Editor;

  let error: unknown;
  try {
    validateCanvasPatch(editor, {
      command_id: "locked-target",
      base_revision: 0,
      operations: [
        {
          op: "move",
          target: { id: "shape:locked" },
          x: 10,
          y: 20,
        },
      ],
    });
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasProtocolError &&
      error.operationErrors.some((item) => item.code === "target_locked"),
    "A locked mutation target passed preflight as a silent no-op.",
  );
}

{
  const targets = Array.from(
    { length: CANVAS_PATCH_MAX_CHANGED_IDS + 1 },
    (_, index) => ({ id: `shape:limit-${index}` }),
  );
  const editor = {
    getShape: (id: string) => ({ id, type: "geo" }),
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;
  const operations = Array.from(
    { length: Math.ceil(targets.length / 100) },
    (_, index) => ({
      op: "delete",
      targets: targets.slice(index * 100, (index + 1) * 100),
    }),
  );

  let error: unknown;
  try {
    validateCanvasPatch(editor, {
      command_id: "changed-id-limit",
      base_revision: 0,
      operations,
    });
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasProtocolError &&
      error.operationErrors.some(
        (item) =>
          item.path === "operations" &&
          item.message.includes(String(CANVAS_PATCH_MAX_CHANGED_IDS)),
      ),
    "A patch exceeding the receipt changed-ID limit passed preflight.",
  );
}

{
  const firstId = historySchema.types["history-shape"].createId("first");
  const secondId = historySchema.types["history-shape"].createId("second");
  const store = new Store({ schema: historySchema, props: null });
  store.put([
    historySchema.types["history-shape"].create({
      id: firstId,
      type: "geo",
      x: 0,
      y: 0,
    }),
    historySchema.types["history-shape"].create({
      id: secondId,
      type: "geo",
      x: 5,
      y: 5,
    }),
  ]);
  const history = new HistoryManager<HistoryShapeRecord>({ store });
  const markHistory = history as HistoryManager<HistoryShapeRecord> & {
    _mark(id: string): void;
  };
  let writes = 0;
  let failSecondWrite = true;
  const editor = {
    markHistoryStoppingPoint: () => {
      const mark = `protocol-mark-${writes}`;
      markHistory._mark(mark);
      return mark;
    },
    run: (apply: () => void) => history.batch(apply),
    updateShape: (change: {
      id: HistoryShapeRecordId;
      x: number;
      y: number;
    }) => {
      writes += 1;
      if (failSecondWrite && writes === 2) {
        throw new Error("forced second-operation failure");
      }
      store.update(change.id, (record) => ({
        ...record,
        x: change.x,
        y: change.y,
      }));
    },
    bailToMark: (mark: string) => history.bailToMark(mark),
  } as unknown as Editor;
  const updatePlan = {
    commandId: "rollback-test",
    refs: {},
    operations: [
      { op: "move", id: firstId, type: "geo", x: 10, y: 20 },
      { op: "move", id: secondId, type: "geo", x: 30, y: 40 },
    ],
  } as unknown as NormalizedPatchPlan;

  let error: unknown;
  try {
    applyCanvasPatch(editor, updatePlan);
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasProtocolError,
    "Apply failure was not structured.",
  );
  assert(
    store.get(firstId)?.x === 0 && store.get(firstId)?.y === 0,
    "The real tldraw store retained a partial first update.",
  );
  assert(
    store.get(secondId)?.x === 5 && store.get(secondId)?.y === 5,
    "The real tldraw store changed the failing second update.",
  );

  writes = 0;
  failSecondWrite = false;
  const applied = applyCanvasPatch(editor, updatePlan);
  assert(
    store.get(firstId)?.x === 10 && store.get(secondId)?.x === 30,
    "The real tldraw store did not commit the successful batch.",
  );
  assert(
    history.getNumUndos() > 0,
    "The successful patch was not recorded in tldraw history.",
  );
  applied.rollback();
  assert(
    store.get(firstId)?.x === 0 && store.get(secondId)?.x === 5,
    "The real tldraw history did not roll back to the commit barrier.",
  );
  history.dispose();
}

{
  let runCount = 0;
  let markCount = 0;
  let rollbackCount = 0;
  const resizeScales: Array<{ x: number; y: number }> = [];
  const resizePlan = {
    commandId: "resize-test",
    refs: {},
    operations: [
      {
        op: "resize",
        id: "shape:rotated",
        type: "geo",
        width: 200,
        height: 100,
      },
    ],
  } as unknown as NormalizedPatchPlan;
  const editor = {
    markHistoryStoppingPoint: () => {
      markCount += 1;
      return "success-mark";
    },
    run: (apply: () => void) => {
      runCount += 1;
      apply();
    },
    getShapeGeometry: () => ({ bounds: { w: 100, h: 50 } }),
    resizeShape: (_id: string, scale: { x: number; y: number }) => {
      resizeScales.push(scale);
    },
    bailToMark: () => {
      rollbackCount += 1;
    },
  } as unknown as Editor;

  const applied = applyCanvasPatch(editor, resizePlan);
  assert(markCount === 1, "A successful batch created multiple history marks.");
  assert(runCount === 1, "A successful batch used multiple Editor.run calls.");
  assert(
    resizeScales.length === 1 &&
      resizeScales[0].x === 2 &&
      resizeScales[0].y === 2,
    "Resize did not use shape-space geometry.",
  );
  applied.rollback();
  assert(
    rollbackCount === 1,
    "The commit barrier could not roll back its mark.",
  );
}
