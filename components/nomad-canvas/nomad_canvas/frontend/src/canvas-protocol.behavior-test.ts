import {
  HistoryManager,
  Store,
  StoreSchema,
  b64Vecs,
  createRecordType,
  createTLStore,
  type BaseRecord,
  type Editor,
  type RecordId,
  type TLRecord,
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
  isRedundantCanvasCheckpoint,
} from "./canvas-snapshot-gate";
import {
  CANVAS_READ_MAX_SHAPES,
  CanvasReadError,
  normalizeCanvasReadRequest,
  readCanvasScene,
} from "./canvas-read-protocol";
import { renderCanvasPreview } from "./canvas-preview";
import {
  lintCanvasPatch,
  mergeNomadMetadata,
  SEMANTIC_ID_PATTERN,
  semanticReadSummary,
} from "./canvas-semantic";

{
  const editor = {
    getCurrentPageShapes: () => [],
    getShape: () => undefined,
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;
  const plan = validateCanvasPatch(editor, {
    command_id: "intuitive-draw",
    base_revision: 0,
    operations: [
      {
        op: "draw",
        ref: "stroke",
        kind: "freehand",
        points: [
          { x: 120, y: 80 },
          { x: 145, y: 92, pressure: 0.7 },
          { x: 180, y: 76 },
        ],
        style: { color: "red", size: "m" },
      },
    ],
  });
  const operation = plan.operations[0];
  assert(
    operation?.op === "create" &&
      operation.shape.type === "draw" &&
      operation.shape.x === 120 &&
      operation.shape.y === 80,
    "The intuitive draw operation was not normalized into a tldraw draw shape.",
  );
  if (operation?.op === "create") {
    const props = operation.shape.props as {
      segments: Array<{ path: string }>;
    };
    const points = b64Vecs.decodePoints(props.segments[0].path);
    assert(
      points[0].x === 0 &&
        points[0].y === 0 &&
        points[1].x === 25 &&
        points[1].y === 12,
      "Absolute Canvas points were not converted to local tldraw coordinates.",
    );
  }
}

{
  const editor = {
    getCurrentPageShapes: () => [],
    getShape: () => undefined,
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;
  const plan = validateCanvasPatch(editor, {
    command_id: "validate-draw-records",
    base_revision: 0,
    operations: [
      {
        op: "draw",
        ref: "freehand",
        kind: "freehand",
        points: [
          { x: 0, y: 0 },
          { x: 20, y: 10 },
        ],
        style: { color: "black", fill: "none", dash: "draw", size: "m" },
      },
      {
        op: "draw",
        ref: "highlight",
        kind: "highlight",
        points: [
          { x: 0, y: 20 },
          { x: 20, y: 30 },
        ],
        style: { color: "yellow", size: "m" },
      },
      {
        op: "draw",
        ref: "line",
        kind: "line",
        points: [
          { x: 0, y: 40 },
          { x: 20, y: 50 },
        ],
        style: { color: "black", dash: "solid", size: "m" },
      },
    ],
  });
  const store = createTLStore();
  store.put(
    plan.operations.map((operation, index) => {
      if (operation.op !== "create")
        throw new Error("Draw did not normalize to create.");
      return {
        id: operation.id,
        typeName: "shape" as const,
        type: operation.shape.type as "draw" | "highlight" | "line",
        x: operation.shape.x as number,
        y: operation.shape.y as number,
        rotation: 0,
        index: `a${index + 1}` as `a${number}`,
        parentId: "page:test" as const,
        isLocked: false,
        opacity: 1,
        props: operation.shape.props,
        meta: {},
      } as unknown as TLRecord;
    }),
  );
}

{
  const editor = {
    getCurrentPageShapes: () => [],
    getShape: () => undefined,
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;
  let error: unknown;
  try {
    validateCanvasPatch(editor, {
      command_id: "reject-raw-draw",
      base_revision: 0,
      operations: [
        {
          op: "create",
          ref: "raw-stroke",
          shape: {
            type: "draw",
            x: 0,
            y: 0,
            props: { segments: [] },
          },
        },
      ],
    });
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasProtocolError &&
      error.operationErrors.some(
        (item) => item.code === "unsupported_shape_type",
      ),
    "Raw tldraw draw props bypassed the intuitive drawing DTO.",
  );
}

{
  let currentToolId = "draw";
  let rollbackCount = 0;
  const editor = {
    getCurrentPageShapes: () => [],
    getCurrentToolId: () => currentToolId,
    setCurrentTool: (toolId: string) => {
      currentToolId = toolId;
    },
    markHistoryStoppingPoint: () => "group-failure",
    run: (apply: () => void) => apply(),
    groupShapes: () => undefined,
    getShape: () => undefined,
    bailToMark: () => {
      rollbackCount += 1;
    },
  } as unknown as Editor;
  const plan = {
    commandId: "group-failure",
    refs: {},
    requestedHeights: {},
    operations: [
      {
        op: "group",
        id: "shape:group",
        ref: "group",
        ids: ["shape:one", "shape:two"],
        metadata: {},
      },
    ],
  } as unknown as NormalizedPatchPlan;

  let error: unknown;
  try {
    applyCanvasPatch(editor, plan);
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasProtocolError &&
      currentToolId === "draw" &&
      rollbackCount === 1,
    "A failed group operation did not restore the previous tool after rollback.",
  );
}

{
  const shapes = [
    { id: "shape:one", type: "geo", meta: {} },
    { id: "shape:two", type: "geo", meta: {} },
    { id: "shape:three", type: "geo", meta: {} },
    { id: "shape:frame", type: "frame", meta: {} },
    { id: "shape:arrow", type: "arrow", meta: {} },
  ];
  const calls: string[] = [];
  const bindings = [
    {
      id: "binding:end",
      type: "arrow",
      fromId: "shape:arrow",
      toId: "shape:three",
      props: { terminal: "end" },
    },
  ];
  const shapeById = new Map(shapes.map((shape) => [shape.id, shape]));
  let currentToolId = "draw";
  const editor = {
    getCurrentPageShapes: () => Array.from(shapeById.values()),
    getShape: (id: string) => shapeById.get(id),
    isShapeOrAncestorLocked: () => false,
    hasAncestor: () => false,
    getSortedChildIdsForParent: () => [],
    markHistoryStoppingPoint: () => "expanded-operations",
    run: (apply: () => void) => apply(),
    getCurrentToolId: () => currentToolId,
    setCurrentTool: (toolId: string) => {
      currentToolId = toolId;
      calls.push(`tool:${toolId}`);
    },
    bailToMark: () => calls.push("rollback"),
    createShape: (shape: { id: string; type: string; meta: object }) => {
      shapeById.set(shape.id, shape);
      calls.push(`create:${shape.type}`);
    },
    groupShapes: (
      _ids: string[],
      options: { groupId: string; select: boolean },
    ) => {
      if (currentToolId !== "select") return;
      shapeById.set(options.groupId, {
        id: options.groupId,
        type: "group",
        meta: {},
      });
      calls.push("group");
    },
    updateShape: (update: { id: string; type: string; meta?: object }) => {
      const existing = shapeById.get(update.id);
      shapeById.set(update.id, {
        ...existing,
        ...update,
        meta: update.meta ?? existing?.meta ?? {},
      });
    },
    bringToFront: () => calls.push("front"),
    rotateShapesBy: () => calls.push("rotate"),
    flipShapes: () => calls.push("flip"),
    alignShapes: () => calls.push("align"),
    distributeShapes: () => calls.push("distribute"),
    stackShapes: () => calls.push("stack"),
    packShapes: () => calls.push("pack"),
    reparentShapes: () => calls.push("reparent"),
    getCurrentPageId: () => "page:one",
    getBindingsFromShape: () => bindings,
    deleteBindings: () => calls.push("disconnect"),
    ungroupShapes: (ids: string[]) => {
      if (currentToolId !== "select") return;
      ids.forEach((id) => shapeById.delete(id));
      calls.push("ungroup");
    },
  } as unknown as Editor;
  const plan = validateCanvasPatch(editor, {
    command_id: "expanded-operations",
    base_revision: 0,
    operations: [
      {
        op: "draw",
        ref: "stroke",
        kind: "highlight",
        points: [
          { x: 0, y: 0 },
          { x: 20, y: 20 },
        ],
      },
      {
        op: "group",
        ref: "cluster",
        targets: [{ id: "shape:one" }, { id: "shape:two" }],
      },
      { op: "reorder", targets: [{ ref: "cluster" }], position: "front" },
      { op: "rotate", targets: [{ ref: "cluster" }], degrees: 45 },
      { op: "flip", targets: [{ ref: "cluster" }], axis: "horizontal" },
      {
        op: "align",
        targets: [{ id: "shape:one" }, { id: "shape:two" }],
        alignment: "left",
      },
      {
        op: "distribute",
        targets: [
          { id: "shape:one" },
          { id: "shape:two" },
          { id: "shape:three" },
        ],
        axis: "horizontal",
      },
      {
        op: "stack",
        targets: [{ id: "shape:one" }, { id: "shape:two" }],
        axis: "vertical",
        gap: 16,
      },
      {
        op: "pack",
        targets: [{ id: "shape:one" }, { id: "shape:two" }],
        gap: 8,
      },
      {
        op: "reparent",
        targets: [{ id: "shape:three" }],
        parent: { id: "shape:frame" },
      },
      {
        op: "disconnect",
        target: { id: "shape:arrow" },
        terminals: ["end"],
      },
      { op: "ungroup", targets: [{ ref: "cluster" }] },
    ],
  });
  const applied = applyCanvasPatch(editor, plan);
  assert(
    [
      "create:highlight",
      "group",
      "front",
      "rotate",
      "flip",
      "align",
      "distribute",
      "stack",
      "pack",
      "reparent",
      "disconnect",
      "ungroup",
      "tool:select",
      "tool:draw",
    ].every((call) => calls.includes(call)) &&
      currentToolId === "draw" &&
      applied.result.refs.cluster &&
      applied.result.refs.stroke,
    "Expanded Canvas operations did not dispatch through public tldraw APIs.",
  );
}

{
  const shapes = [
    { id: "shape:group", type: "group", parentId: "page:one", meta: {} },
    {
      id: "shape:child",
      type: "geo",
      parentId: "shape:group",
      meta: {},
    },
    { id: "shape:other", type: "geo", parentId: "page:one", meta: {} },
  ];
  const shapeById = new Map(shapes.map((shape) => [shape.id, shape]));
  const editor = {
    getCurrentPageShapes: () => shapes,
    getShape: (id: string) => shapeById.get(id),
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;

  let mixedHierarchyError: unknown;
  try {
    validateCanvasPatch(editor, {
      command_id: "reject-mixed-hierarchy",
      base_revision: 0,
      operations: [
        {
          op: "flip",
          targets: [{ id: "shape:group" }, { id: "shape:child" }],
          axis: "horizontal",
        },
      ],
    });
  } catch (caught) {
    mixedHierarchyError = caught;
  }
  assert(
    mixedHierarchyError instanceof CanvasProtocolError &&
      mixedHierarchyError.operationErrors.some(
        (item) =>
          item.path === "targets" && item.message.includes("descendants"),
      ),
    "A layout operation accepted both an ancestor and its descendant.",
  );

  let plannedCycleError: unknown;
  try {
    validateCanvasPatch(editor, {
      command_id: "reject-planned-cycle",
      base_revision: 0,
      operations: [
        {
          op: "group",
          ref: "new-group",
          targets: [{ id: "shape:child" }, { id: "shape:other" }],
        },
        {
          op: "reparent",
          targets: [{ ref: "new-group" }],
          parent: { id: "shape:child" },
        },
      ],
    });
  } catch (caught) {
    plannedCycleError = caught;
  }
  assert(
    plannedCycleError instanceof CanvasProtocolError &&
      plannedCycleError.operationErrors.some(
        (item) => item.path === "parent" && item.code === "invalid_operation",
      ),
    "A same-patch group could be reparented into its planned child.",
  );
}

{
  assert(
    SEMANTIC_ID_PATTERN.test("gate.diagnosis-type") &&
      !SEMANTIC_ID_PATTERN.test("invalid semantic id"),
    "The documented semantic-ID character set was not enforced.",
  );
  const merged = mergeNomadMetadata(
    {
      plugin: { keep: true },
      nomad: { source_refs: [{ document: "old", locator: "section:1" }] },
    },
    "semantic-update",
    { semanticId: "gate.diagnosis-type" },
  );
  assert(
    ((merged as Record<string, unknown>).plugin as { keep?: boolean }).keep ===
      true &&
      Array.isArray((merged.nomad as { source_refs?: unknown }).source_refs) &&
      !("created_by" in merged.nomad),
    "A semantic-only update discarded unrelated metadata or provenance.",
  );
  const created = mergeNomadMetadata(
    {},
    "semantic-create",
    { semanticId: "node.created" },
    "codex",
  );
  assert(
    created.nomad.created_by === "codex",
    "A Codex-created shape lost its creator attribution.",
  );
}

{
  const semanticShape = {
    id: "shape:semantic",
    type: "geo",
    meta: { nomad: { semantic_id: "gate.diagnosis-type" } },
  };
  const editor = {
    getCurrentPageShapes: () => [semanticShape],
    getShape: (id: string) =>
      id === semanticShape.id ? semanticShape : undefined,
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;
  const plan = validateCanvasPatch(editor, {
    command_id: "semantic-target",
    base_revision: 0,
    operations: [
      {
        op: "update",
        target: { semantic_id: "gate.diagnosis-type" },
        text: "Updated",
      },
    ],
  });
  assert(
    plan.operations[0]?.op === "update" &&
      plan.operations[0].id === semanticShape.id,
    "A unique semantic target did not resolve to its tldraw shape ID.",
  );
}

{
  const shapes = ["shape:first", "shape:second"].map((id) => ({
    id,
    type: "geo",
    meta: { nomad: { semantic_id: "duplicate.repairable" } },
  }));
  const editor = {
    getCurrentPageShapes: () => shapes,
    getShape: (id: string) => shapes.find((shape) => shape.id === id),
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;
  const plan = validateCanvasPatch(editor, {
    command_id: "repair-duplicate-semantic",
    base_revision: 0,
    operations: [
      {
        op: "update",
        target: { id: shapes[1].id },
        semantic_id: "duplicate.repaired",
      },
    ],
  });
  assert(
    plan.operations[0]?.op === "update" &&
      plan.operations[0].id === shapes[1].id,
    "An exact-ID patch could not repair duplicate semantic IDs.",
  );
}

{
  const shapes = ["shape:first", "shape:second"].map((id) => ({
    id,
    type: "geo",
    meta: { nomad: { semantic_id: "duplicate.node" } },
  }));
  const editor = {
    getCurrentPageShapes: () => shapes,
    getShape: (id: string) => shapes.find((shape) => shape.id === id),
    isShapeOrAncestorLocked: () => false,
  } as unknown as Editor;
  let error: unknown;
  try {
    validateCanvasPatch(editor, {
      command_id: "duplicate-semantic",
      base_revision: 0,
      operations: [{ op: "move", target: { id: shapes[0].id }, x: 1, y: 1 }],
    });
  } catch (caught) {
    error = caught;
  }
  assert(
    error instanceof CanvasProtocolError &&
      error.operationErrors.some(
        (item) => item.code === "duplicate_semantic_id",
      ),
    "Duplicate semantic IDs were not rejected deterministically.",
  );
}

{
  const shape = {
    id: "shape:provenance",
    type: "geo",
    parentId: "page:one",
    props: { w: 100, h: 50 },
    meta: {
      nomad: {
        source_refs: [{ document: "requirements.md", locator: "section:3" }],
      },
    },
  };
  const editor = {
    getCurrentPageShapes: () => [shape],
    getShape: (id: string) => (id === shape.id ? shape : undefined),
    getShapePageBounds: () => ({ x: 0, y: 0, w: 100, h: 50 }),
    getShapeMaskedPageBounds: () => ({ x: 0, y: 0, w: 100, h: 50 }),
    getBindingsInvolvingShape: () => [],
    getShapeIdsInsideBounds: () => new Set([shape.id]),
  } as unknown as Editor;
  const before = JSON.stringify(shape);
  const warnings = lintCanvasPatch(editor, [shape.id as never]);
  assert(
    warnings.some((item) => item.code === "missing_semantic_id") &&
      JSON.stringify(shape) === before,
    "Semantic lint either missed provenance identity or mutated the document.",
  );
}

{
  const arrow = {
    id: "shape:nomad-arrow",
    type: "arrow",
    parentId: "page:one",
    props: {},
    meta: {
      nomad: {
        schema_version: 1,
        created_by: "codex",
        last_command_id: "create-arrow",
      },
    },
  };
  const editor = {
    getCurrentPageShapes: () => [arrow],
    getShape: (id: string) => (id === arrow.id ? arrow : undefined),
    getShapePageBounds: () => ({ x: 0, y: 0, w: 100, h: 20 }),
    getBindingsInvolvingShape: () => [],
    getBindingsFromShape: () => [],
  } as unknown as Editor;
  const warnings = lintCanvasPatch(editor, [arrow.id as never]);
  assert(
    warnings.some((item) => item.code === "dangling_connector"),
    "A Nomad connector with a missing endpoint was not reported as dangling.",
  );
}

{
  const summary = semanticReadSummary({
    meta: {
      nomad: {
        semantic_id: "invalid semantic id",
        created_by: "x".repeat(65),
        last_command_id: "x".repeat(129),
        source_refs: [
          { document: "requirements.md", locator: "section:1" },
          { document: "x".repeat(501), locator: "section:oversized" },
        ],
      },
    },
  } as never);
  assert(
    !("semantic_id" in summary) &&
      !("created_by" in summary) &&
      !("last_command_id" in summary) &&
      summary.source_refs?.length === 1,
    "A compact semantic read exposed malformed or oversized metadata.",
  );
}

{
  const changed = {
    id: "shape:changed-node",
    type: "geo",
    parentId: "page:one",
    props: {},
    meta: { nomad: { semantic_id: "node.changed" } },
  };
  const neighbor = {
    id: "shape:spatial-neighbor",
    type: "geo",
    parentId: "page:one",
    props: {},
    meta: { nomad: { semantic_id: "node.neighbor" } },
  };
  const unrelated = Array.from({ length: 250 }, (_, index) => ({
    id: `shape:unrelated-${index}`,
    type: "geo",
    parentId: "page:one",
    props: {},
    meta: {},
  }));
  const shapes = [...unrelated, changed, neighbor];
  const editor = {
    getCurrentPageShapes: () => shapes,
    getShape: (id: string) => shapes.find((shape) => shape.id === id),
    getShapePageBounds: (shape: { id: string }) =>
      shape.id === changed.id
        ? { x: 0, y: 0, w: 100, h: 100 }
        : { x: 50, y: 50, w: 100, h: 100 },
    getShapeMaskedPageBounds: (shape: { id: string }) =>
      shape.id === changed.id
        ? { x: 0, y: 0, w: 100, h: 100 }
        : { x: 50, y: 50, w: 100, h: 100 },
    getShapeIdsInsideBounds: () => new Set([changed.id, neighbor.id]),
    getBindingsInvolvingShape: () => [],
  } as unknown as Editor;
  const warnings = lintCanvasPatch(editor, [changed.id as never]);
  assert(
    warnings.some(
      (item) =>
        item.code === "shape_overlap" && item.shape_ids.includes(neighbor.id),
    ),
    "Spatial overlap lint missed a neighbor beyond the first 200 page shapes.",
  );
}

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

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
    meta: {
      nomad: {
        schema_version: 1,
        semantic_id: "node.child",
        source_refs: [
          { document: "requirements.md", locator: "section:child" },
        ],
        created_by: "codex",
        last_command_id: "create-child",
      },
    },
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
  const childResult = scoped.result.shapes.find(
    (shape) => shape.id === child.id,
  );
  assert(
    childResult?.semantic?.semantic_id === "node.child" &&
      JSON.stringify(childResult.semantic.source_refs).includes(
        "section:child",
      ),
    "A scoped read did not round-trip semantic identity and provenance.",
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
  assert(
    isRedundantCanvasCheckpoint(false, "document-a", "document-a"),
    "An unchanged document checkpoint was not suppressed.",
  );
  assert(
    !isRedundantCanvasCheckpoint(true, "document-a", "document-a"),
    "A preview refresh was incorrectly suppressed.",
  );
  assert(
    !isRedundantCanvasCheckpoint(false, "document-a", null),
    "The initial document checkpoint was incorrectly suppressed.",
  );
  assert(
    !isRedundantCanvasCheckpoint(false, "document-b", "document-a"),
    "A changed document checkpoint was incorrectly suppressed.",
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
