import { Editor, TLShape, TLShapeId } from "tldraw";

export const SEMANTIC_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
export const CANVAS_MAX_SOURCE_REFS = 20;
export const CANVAS_MAX_LINT_WARNINGS = 100;
const CANVAS_MAX_LINT_SHAPES = 200;

export type CanvasSourceRef = {
  document: string;
  locator: string;
  label?: string;
  content_hash?: string;
};

export type NomadMetadata = {
  schema_version: 1;
  semantic_id?: string;
  source_refs?: CanvasSourceRef[];
  created_by?: string;
  last_command_id: string;
};

export type CanvasLintWarning = {
  code:
    | "duplicate_semantic_id"
    | "missing_semantic_id"
    | "invalid_source_ref"
    | "text_overflow"
    | "unexpected_grow_y"
    | "shape_overlap"
    | "outside_frame"
    | "unbound_semantic_arrow"
    | "dangling_connector";
  severity: "error" | "warning";
  shape_ids: string[];
  semantic_ids: string[];
  message: string;
  details: Record<string, unknown>;
  suggested_action: string;
};

const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

export const readNomadMetadata = (shape: Pick<TLShape, "meta">) => {
  const meta = isObject(shape.meta) ? shape.meta : {};
  return isObject(meta.nomad) ? meta.nomad : {};
};

export const semanticIdOf = (shape: Pick<TLShape, "meta">) => {
  const value = readNomadMetadata(shape).semantic_id;
  return typeof value === "string" ? value : undefined;
};

export const sourceRefsOf = (shape: Pick<TLShape, "meta">) => {
  const value = readNomadMetadata(shape).source_refs;
  return Array.isArray(value) ? value : undefined;
};

export const mergeNomadMetadata = (
  existing: unknown,
  commandId: string,
  patch: { semanticId?: string | null; sourceRefs?: CanvasSourceRef[] },
  createdBy?: string,
) => {
  const meta = isObject(existing) ? existing : {};
  const priorNomad = isObject(meta.nomad) ? meta.nomad : {};
  const nomad: Record<string, unknown> = {
    ...priorNomad,
    schema_version: 1,
    last_command_id: commandId,
  };
  if (
    typeof priorNomad.created_by !== "string" &&
    typeof createdBy === "string"
  )
    nomad.created_by = createdBy;
  if (patch.semanticId === null) delete nomad.semantic_id;
  else if (patch.semanticId !== undefined) nomad.semantic_id = patch.semanticId;
  if (patch.sourceRefs !== undefined) nomad.source_refs = patch.sourceRefs;
  return { ...meta, nomad };
};

export const allDocumentShapes = (editor: Editor): TLShape[] => {
  if (
    typeof editor.getPages === "function" &&
    typeof editor.getSortedChildIdsForParent === "function" &&
    typeof editor.getShapeAndDescendantIds === "function"
  ) {
    const ids = new Set<TLShapeId>();
    for (const page of editor.getPages()) {
      const topLevel = editor.getSortedChildIdsForParent(page.id);
      for (const id of editor.getShapeAndDescendantIds(topLevel)) ids.add(id);
    }
    return Array.from(ids, (id) => editor.getShape(id)).filter(
      (shape): shape is TLShape => Boolean(shape),
    );
  }
  return typeof editor.getCurrentPageShapes === "function"
    ? editor.getCurrentPageShapes()
    : [];
};

export const buildSemanticIndex = (editor: Editor) => {
  const index = new Map<string, TLShape[]>();
  for (const shape of allDocumentShapes(editor)) {
    const semanticId = semanticIdOf(shape);
    if (!semanticId) continue;
    const matches = index.get(semanticId) ?? [];
    matches.push(shape);
    index.set(semanticId, matches);
  }
  return index;
};

export const isValidSourceRef = (value: unknown): value is CanvasSourceRef => {
  if (!isObject(value)) return false;
  if (
    Object.keys(value).some(
      (key) => !["document", "locator", "label", "content_hash"].includes(key),
    )
  )
    return false;
  if (
    typeof value.document !== "string" ||
    value.document.length < 1 ||
    value.document.length > 500 ||
    typeof value.locator !== "string" ||
    value.locator.length < 1 ||
    value.locator.length > 500 ||
    (value.label !== undefined &&
      (typeof value.label !== "string" || value.label.length > 500)) ||
    (value.content_hash !== undefined &&
      (typeof value.content_hash !== "string" ||
        value.content_hash.length > 128))
  )
    return false;
  return (
    value.content_hash === undefined ||
    /^sha256:[0-9a-f]{64}$/.test(value.content_hash)
  );
};

export const semanticReadSummary = (shape: Pick<TLShape, "meta">) => {
  const nomad = readNomadMetadata(shape);
  const sourceRefs = Array.isArray(nomad.source_refs)
    ? nomad.source_refs
        .slice(0, CANVAS_MAX_SOURCE_REFS)
        .filter(isValidSourceRef)
    : undefined;
  return {
    ...(typeof nomad.semantic_id === "string" &&
    SEMANTIC_ID_PATTERN.test(nomad.semantic_id)
      ? { semantic_id: nomad.semantic_id }
      : {}),
    ...(sourceRefs ? { source_refs: sourceRefs } : {}),
    ...(typeof nomad.created_by === "string" && nomad.created_by.length <= 64
      ? { created_by: nomad.created_by }
      : {}),
    ...(typeof nomad.last_command_id === "string" &&
    nomad.last_command_id.length <= 128
      ? { last_command_id: nomad.last_command_id }
      : {}),
  };
};

const intersectsMaterially = (
  first: { x: number; y: number; w: number; h: number },
  second: { x: number; y: number; w: number; h: number },
) => {
  const width =
    Math.min(first.x + first.w, second.x + second.w) -
    Math.max(first.x, second.x);
  const height =
    Math.min(first.y + first.h, second.y + second.h) -
    Math.max(first.y, second.y);
  return (
    width > 8 &&
    height > 8 &&
    width * height > 0.1 * Math.min(first.w * first.h, second.w * second.h)
  );
};

const warning = (
  shapeIds: string[],
  semanticIds: string[],
  fields: Omit<CanvasLintWarning, "shape_ids" | "semantic_ids">,
): CanvasLintWarning => ({
  ...fields,
  shape_ids: shapeIds,
  semantic_ids: semanticIds,
});

export const lintCanvasPatch = (
  editor: Editor,
  changedIds: readonly TLShapeId[],
  requestedHeights: Readonly<Record<string, number>> = {},
) => {
  const warnings: CanvasLintWarning[] = [];
  const push = (item: CanvasLintWarning) => {
    if (warnings.length < CANVAS_MAX_LINT_WARNINGS) warnings.push(item);
  };
  const semanticIndex = buildSemanticIndex(editor);
  for (const [semanticId, shapes] of semanticIndex) {
    if (shapes.length < 2) continue;
    push(
      warning(
        shapes.map((shape) => shape.id),
        [semanticId],
        {
          code: "duplicate_semantic_id",
          severity: "error",
          message: `Semantic ID is duplicated: ${semanticId}`,
          details: { count: shapes.length },
          suggested_action:
            "Assign a unique semantic_id to each domain object.",
        },
      ),
    );
  }

  const changed = changedIds
    .map((id) => editor.getShape(id))
    .filter((shape): shape is TLShape => Boolean(shape));
  const affected = new Map<TLShapeId, TLShape>();
  for (const shape of changed) {
    affected.set(shape.id, shape);
    if (typeof editor.getBindingsInvolvingShape === "function") {
      for (const binding of editor.getBindingsInvolvingShape(shape)) {
        const from = editor.getShape(binding.fromId);
        const to = editor.getShape(binding.toId);
        if (from) affected.set(from.id, from);
        if (to) affected.set(to.id, to);
      }
    }
  }
  const scoped = Array.from(affected.values()).slice(0, CANVAS_MAX_LINT_SHAPES);

  for (const shape of scoped) {
    const semanticId = semanticIdOf(shape);
    const sourceRefs = sourceRefsOf(shape);
    const shapeSemanticIds = semanticId ? [semanticId] : [];
    if (sourceRefs?.length && !semanticId)
      push(
        warning([shape.id], [], {
          code: "missing_semantic_id",
          severity: "warning",
          message: "A shape with provenance has no semantic ID.",
          details: {},
          suggested_action: "Assign a document-unique semantic_id.",
        }),
      );
    if (
      sourceRefs !== undefined &&
      (sourceRefs.length > CANVAS_MAX_SOURCE_REFS ||
        sourceRefs.some((item) => !isValidSourceRef(item)))
    )
      push(
        warning([shape.id], shapeSemanticIds, {
          code: "invalid_source_ref",
          severity: "warning",
          message: "Shape provenance is malformed.",
          details: {},
          suggested_action:
            "Replace source_refs with bounded document locators and hashes.",
        }),
      );

    const bounds = editor.getShapePageBounds(shape);
    const requestedHeight = requestedHeights[shape.id];
    if (bounds && requestedHeight && bounds.h > requestedHeight + 2) {
      const details = {
        requested_height: requestedHeight,
        actual_height: bounds.h,
        overflow: bounds.h - requestedHeight,
      };
      push(
        warning([shape.id], shapeSemanticIds, {
          code: "unexpected_grow_y",
          severity: "warning",
          message: "Actual shape height exceeds the requested height.",
          details,
          suggested_action: "Increase height or shorten the label.",
        }),
      );
      const props = shape.props as Record<string, unknown>;
      if (props.richText)
        push(
          warning([shape.id], shapeSemanticIds, {
            code: "text_overflow",
            severity: "warning",
            message: "Text exceeds the requested content height.",
            details,
            suggested_action: "Increase height or shorten the label.",
          }),
        );
    }

    if (shape.type === "arrow") {
      const bindings = editor.getBindingsFromShape(shape, "arrow");
      const terminals = new Set(
        bindings
          .filter((binding) => Boolean(editor.getShape(binding.toId)))
          .map((binding) => (binding.props as { terminal?: string }).terminal),
      );
      const hasMissingTarget = bindings.some(
        (binding) => !editor.getShape(binding.toId),
      );
      const hasMissingTerminal =
        !terminals.has("start") || !terminals.has("end");
      const nomad = readNomadMetadata(shape);
      if (
        hasMissingTarget ||
        (!semanticId && nomad.schema_version === 1 && hasMissingTerminal)
      )
        push(
          warning([shape.id], shapeSemanticIds, {
            code: "dangling_connector",
            severity: "error",
            message: "A connector endpoint no longer resolves.",
            details: {},
            suggested_action: "Reconnect or delete the connector.",
          }),
        );
      if (semanticId && hasMissingTerminal)
        push(
          warning([shape.id], shapeSemanticIds, {
            code: "unbound_semantic_arrow",
            severity: "error",
            message: "A semantic connector lacks start or end bindings.",
            details: { bound_terminals: Array.from(terminals) },
            suggested_action:
              "Bind both connector endpoints to semantic shapes.",
          }),
        );
    }

    if (bounds && shape.parentId.startsWith("shape:")) {
      const parent = editor.getShape(shape.parentId as TLShapeId);
      const parentBounds =
        parent?.type === "frame"
          ? editor.getShapePageBounds(parent)
          : undefined;
      if (
        parentBounds &&
        (bounds.x < parentBounds.x - 2 ||
          bounds.y < parentBounds.y - 2 ||
          bounds.x + bounds.w > parentBounds.x + parentBounds.w + 2 ||
          bounds.y + bounds.h > parentBounds.y + parentBounds.h + 2)
      )
        push(
          warning([shape.id, parent!.id], shapeSemanticIds, {
            code: "outside_frame",
            severity: "warning",
            message: "A frame child is materially outside its frame.",
            details: {},
            suggested_action: "Move or resize the child within the frame.",
          }),
        );
    }

    if (
      !bounds ||
      shape.type === "arrow" ||
      shape.type === "frame" ||
      (!semanticId && !sourceRefs?.length)
    )
      continue;
    const maskedBounds = editor.getShapeMaskedPageBounds(shape) ?? bounds;
    const neighborIds = Array.from(
      editor.getShapeIdsInsideBounds(maskedBounds),
    ).slice(0, CANVAS_MAX_LINT_SHAPES);
    for (const otherId of neighborIds) {
      const other = editor.getShape(otherId);
      if (!other) continue;
      if (
        other.id === shape.id ||
        other.type === "arrow" ||
        other.type === "frame" ||
        shape.parentId !== other.parentId
      )
        continue;
      const otherSemanticId = semanticIdOf(other);
      if (!otherSemanticId && !sourceRefsOf(other)?.length) continue;
      const otherBounds =
        editor.getShapeMaskedPageBounds(other) ??
        editor.getShapePageBounds(other);
      if (!otherBounds || !intersectsMaterially(maskedBounds, otherBounds))
        continue;
      if (shape.id > other.id && affected.has(other.id)) continue;
      push(
        warning(
          [shape.id, other.id],
          [semanticId, otherSemanticId].filter((item): item is string =>
            Boolean(item),
          ),
          {
            code: "shape_overlap",
            severity: "warning",
            message: "Diagram nodes overlap beyond the lint tolerance.",
            details: {},
            suggested_action: "Move or resize one of the overlapping nodes.",
          },
        ),
      );
    }
  }
  return warnings;
};
