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

const assert = (condition: unknown, message: string) => {
  if (!condition) throw new Error(message);
};

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
