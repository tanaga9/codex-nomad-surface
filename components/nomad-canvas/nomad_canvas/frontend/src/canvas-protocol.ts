import {
  Editor,
  TLAssetId,
  TLShapeId,
  b64Vecs,
  createShapeId,
  getIndices,
  toRichText,
} from "tldraw";
import {
  allDocumentShapes,
  buildSemanticIndex,
  CANVAS_MAX_SOURCE_REFS,
  type CanvasSourceRef,
  isValidSourceRef,
  mergeNomadMetadata,
  SEMANTIC_ID_PATTERN,
} from "./canvas-semantic";

export type CanvasOperationError = {
  operation_index: number;
  path: string;
  code:
    | "invalid_operation"
    | "unsupported_shape_type"
    | "unsupported_property"
    | "invalid_enum_value"
    | "invalid_number"
    | "duplicate_ref"
    | "duplicate_semantic_id"
    | "target_not_found"
    | "target_locked"
    | "target_type_mismatch"
    | "connector_endpoint_not_found";
  message: string;
  suggestion?: string;
};

export class CanvasProtocolError extends Error {
  constructor(
    public readonly errorCode: "patch_validation_failed" | "patch_apply_failed",
    public readonly operationErrors: CanvasOperationError[],
  ) {
    super(
      errorCode === "patch_validation_failed"
        ? "Canvas patch validation failed."
        : "Canvas patch application failed and was rolled back.",
    );
  }

  toPayload() {
    return { error: this.errorCode, operation_errors: this.operationErrors };
  }
}

type JsonObject = Record<string, unknown>;
type ShapeType =
  "geo" | "text" | "note" | "frame" | "image" | "draw" | "highlight" | "line";
type CreateShapeType = "geo" | "text" | "note" | "frame" | "image";
type Target = { id: string } | { semantic_id: string } | { ref: string };
type MetadataPatch = {
  semanticId?: string | null;
  sourceRefs?: CanvasSourceRef[];
};
type PlannedShape = {
  id: TLShapeId;
  type: ShapeType | "arrow" | "group";
  availableAt: number;
};
type NormalizedImageAsset = {
  id: TLAssetId;
  src: string;
  name: string;
  width: number;
  height: number;
  byteSize: number;
  originalByteSize: number;
  quality: number;
  contentHash: string;
};
type NormalizedOperation =
  | {
      op: "create";
      id: TLShapeId;
      ref: string;
      shape: JsonObject;
      metadata: MetadataPatch;
      asset?: NormalizedImageAsset;
      resize?: { width: number; height: number };
    }
  | {
      op: "update";
      id: TLShapeId;
      type: string;
      update: JsonObject;
      metadata: MetadataPatch;
    }
  | { op: "move"; id: TLShapeId; type: string; x: number; y: number }
  | { op: "resize"; id: TLShapeId; type: string; width: number; height: number }
  | { op: "delete"; ids: TLShapeId[] }
  | {
      op: "connect";
      id: TLShapeId;
      ref: string;
      fromId: TLShapeId;
      toId: TLShapeId;
      props: JsonObject;
      metadata: MetadataPatch;
    }
  | { op: "disconnect"; id: TLShapeId; terminals: ("start" | "end")[] }
  | {
      op: "group";
      id: TLShapeId;
      ref: string;
      ids: TLShapeId[];
      metadata: MetadataPatch;
    }
  | { op: "ungroup"; ids: TLShapeId[]; childIds: TLShapeId[] }
  | {
      op: "reparent";
      ids: TLShapeId[];
      parentId: TLShapeId | null;
    }
  | {
      op: "reorder";
      ids: TLShapeId[];
      position: "back" | "backward" | "forward" | "front";
      considerAllShapes: boolean;
    }
  | { op: "rotate"; ids: TLShapeId[]; radians: number }
  | { op: "flip"; ids: TLShapeId[]; axis: "horizontal" | "vertical" }
  | {
      op: "align";
      ids: TLShapeId[];
      alignment:
        | "bottom"
        | "center-horizontal"
        | "center-vertical"
        | "left"
        | "right"
        | "top";
    }
  | {
      op: "distribute";
      ids: TLShapeId[];
      axis: "horizontal" | "vertical";
    }
  | {
      op: "stack";
      ids: TLShapeId[];
      axis: "horizontal" | "vertical";
      gap?: number;
    }
  | { op: "pack"; ids: TLShapeId[]; gap?: number };

export type NormalizedPatchPlan = Readonly<{
  commandId: string;
  operations: readonly NormalizedOperation[];
  refs: Readonly<Record<string, TLShapeId>>;
  requestedHeights: Readonly<Record<string, number>>;
}>;

export const CANVAS_PATCH_MAX_CHANGED_IDS = 500;

const SHAPE_TYPES = new Set<ShapeType>([
  "geo",
  "text",
  "note",
  "frame",
  "image",
  "draw",
  "highlight",
  "line",
]);
const CREATE_SHAPE_TYPES = new Set<CreateShapeType>([
  "geo",
  "text",
  "note",
  "frame",
  "image",
]);
const STYLE_KEYS: Record<ShapeType | "arrow", Set<string>> = {
  geo: new Set([
    "geo",
    "color",
    "fill",
    "dash",
    "size",
    "font",
    "align",
    "vertical_align",
  ]),
  text: new Set(["color", "size", "font", "align"]),
  note: new Set(["color", "size", "font", "align", "vertical_align"]),
  frame: new Set(["color"]),
  image: new Set(),
  draw: new Set(["color", "fill", "dash", "size"]),
  highlight: new Set(["color", "size"]),
  line: new Set(["color", "dash", "size"]),
  arrow: new Set(["color", "size", "dash", "arrowhead_start", "arrowhead_end"]),
};
const STYLE_VALUES: Record<string, Set<string>> = {
  color: new Set([
    "black",
    "grey",
    "light-violet",
    "violet",
    "blue",
    "light-blue",
    "yellow",
    "orange",
    "green",
    "light-green",
    "light-red",
    "red",
    "white",
  ]),
  size: new Set(["s", "m", "l", "xl"]),
  font: new Set(["draw", "sans", "serif", "mono"]),
  geo: new Set([
    "cloud",
    "rectangle",
    "ellipse",
    "triangle",
    "diamond",
    "pentagon",
    "hexagon",
    "octagon",
    "star",
    "rhombus",
    "rhombus-2",
    "oval",
    "trapezoid",
    "arrow-right",
    "arrow-left",
    "arrow-up",
    "arrow-down",
    "x-box",
    "check-box",
    "heart",
  ]),
  fill: new Set(["none", "semi", "solid", "pattern", "fill", "lined-fill"]),
  dash: new Set(["draw", "solid", "dashed", "dotted", "none"]),
  align: new Set(["start", "middle", "end"]),
  vertical_align: new Set(["start", "middle", "end"]),
  arrowhead_start: new Set([
    "none",
    "arrow",
    "triangle",
    "square",
    "dot",
    "diamond",
    "pipe",
    "inverted",
    "bar",
  ]),
  arrowhead_end: new Set([
    "none",
    "arrow",
    "triangle",
    "square",
    "dot",
    "diamond",
    "pipe",
    "inverted",
    "bar",
  ]),
};

const isObject = (value: unknown): value is JsonObject =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const safeRef = (value: string): string => {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const prefix = value.replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 40) || "shape";
  return `${prefix}-${(hash >>> 0).toString(36)}`;
};

const finiteNumber = (
  value: unknown,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
  positive = false,
): number | undefined => {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    Math.abs(value) > 1_000_000 ||
    (positive && value <= 0)
  ) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_number",
      message: `${path} must be a finite ${positive ? "positive " : ""}number within Canvas limits.`,
    });
    return undefined;
  }
  return value;
};

const boundedPositiveNumber = (
  value: unknown,
  maximum: number,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
): number | undefined => {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value <= 0 ||
    value > maximum
  ) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_number",
      message: `${path} must be a positive number within Canvas limits.`,
    });
    return undefined;
  }
  return value;
};

const boundedString = (
  value: unknown,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
  maxLength: number,
): string | undefined => {
  if (typeof value !== "string" || value.length > maxLength) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must be a string of at most ${maxLength} characters.`,
    });
    return undefined;
  }
  return value;
};

const normalizeSemanticId = (
  value: unknown,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
  allowNull = false,
): string | null | undefined => {
  if (allowNull && value === null) return null;
  if (typeof value !== "string" || !SEMANTIC_ID_PATTERN.test(value)) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must be 1–128 characters using letters, digits, dot, underscore, colon, or hyphen.`,
    });
    return undefined;
  }
  return value;
};

const normalizeSourceRefs = (
  value: unknown,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
): CanvasSourceRef[] | undefined => {
  if (
    !Array.isArray(value) ||
    value.length > CANVAS_MAX_SOURCE_REFS ||
    value.some((item) => !isValidSourceRef(item))
  ) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must contain at most ${CANVAS_MAX_SOURCE_REFS} bounded source locators with optional sha256 hashes.`,
    });
    return undefined;
  }
  return value as CanvasSourceRef[];
};

const normalizeMetadata = (
  value: JsonObject,
  operationIndex: number,
  pathPrefix: string,
  errors: CanvasOperationError[],
  allowClearSemanticId = false,
): MetadataPatch => ({
  ...(value.semantic_id !== undefined
    ? {
        semanticId: normalizeSemanticId(
          value.semantic_id,
          operationIndex,
          `${pathPrefix}semantic_id`,
          errors,
          allowClearSemanticId,
        ),
      }
    : {}),
  ...(value.source_refs !== undefined
    ? {
        sourceRefs: normalizeSourceRefs(
          value.source_refs,
          operationIndex,
          `${pathPrefix}source_refs`,
          errors,
        ),
      }
    : {}),
});

const rejectUnknownKeys = (
  value: JsonObject,
  allowed: Set<string>,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
) => {
  for (const key of Object.keys(value)) {
    if (allowed.has(key)) continue;
    const propertyPath = path ? `${path}.${key}` : key;
    errors.push({
      operation_index: operationIndex,
      path: propertyPath,
      code: "unsupported_property",
      message: `${propertyPath} is not supported.`,
      ...(propertyPath === "props.text" || propertyPath.endsWith(".props.text")
        ? { suggestion: "Use shape.text. It is converted to tldraw richText." }
        : {}),
    });
  }
};

const normalizeStyle = (
  value: unknown,
  shapeType: ShapeType | "arrow",
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
) => {
  if (value === undefined) return {};
  if (!isObject(value)) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must be an object.`,
    });
    return {};
  }
  rejectUnknownKeys(value, STYLE_KEYS[shapeType], operationIndex, path, errors);
  const props: JsonObject = {};
  for (const [key, item] of Object.entries(value)) {
    if (!STYLE_KEYS[shapeType].has(key)) continue;
    if (typeof item !== "string" || !STYLE_VALUES[key]?.has(item)) {
      errors.push({
        operation_index: operationIndex,
        path: `${path}.${key}`,
        code: "invalid_enum_value",
        message: `${String(item)} is not a supported value for ${key}.`,
      });
      continue;
    }
    const propName =
      key === "vertical_align"
        ? "verticalAlign"
        : key === "align" && shapeType === "text"
          ? "textAlign"
          : key === "arrowhead_start"
            ? "arrowheadStart"
            : key === "arrowhead_end"
              ? "arrowheadEnd"
              : key;
    props[propName] = item;
  }
  return props;
};

type CanvasPoint = { x: number; y: number; z: number };

const normalizePoints = (
  value: unknown,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
  maximum: number,
): CanvasPoint[] | undefined => {
  if (!Array.isArray(value) || value.length < 2 || value.length > maximum) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must contain 2–${maximum} points.`,
    });
    return undefined;
  }
  const points: CanvasPoint[] = [];
  value.forEach((rawPoint, pointIndex) => {
    if (!isObject(rawPoint)) {
      errors.push({
        operation_index: operationIndex,
        path: `${path}.${pointIndex}`,
        code: "invalid_operation",
        message: "Each point must be an object containing x and y.",
      });
      return;
    }
    rejectUnknownKeys(
      rawPoint,
      new Set(["x", "y", "pressure"]),
      operationIndex,
      `${path}.${pointIndex}`,
      errors,
    );
    const x = finiteNumber(
      rawPoint.x,
      operationIndex,
      `${path}.${pointIndex}.x`,
      errors,
    );
    const y = finiteNumber(
      rawPoint.y,
      operationIndex,
      `${path}.${pointIndex}.y`,
      errors,
    );
    const pressure = rawPoint.pressure ?? 0.5;
    if (
      typeof pressure !== "number" ||
      !Number.isFinite(pressure) ||
      pressure < 0 ||
      pressure > 1
    ) {
      errors.push({
        operation_index: operationIndex,
        path: `${path}.${pointIndex}.pressure`,
        code: "invalid_number",
        message: "Point pressure must be between 0 and 1.",
      });
      return;
    }
    if (x !== undefined && y !== undefined) points.push({ x, y, z: pressure });
  });
  return points.length === value.length ? points : undefined;
};

const parseTarget = (
  value: unknown,
  operationIndex: number,
  path: string,
  errors: CanvasOperationError[],
): Target | undefined => {
  if (!isObject(value)) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must contain exactly one id, semantic_id, or ref.`,
    });
    return undefined;
  }
  rejectUnknownKeys(
    value,
    new Set(["id", "semantic_id", "ref"]),
    operationIndex,
    path,
    errors,
  );
  const id =
    typeof value.id === "string" && value.id && value.id.length <= 256
      ? value.id
      : undefined;
  const ref =
    typeof value.ref === "string" && value.ref && value.ref.length <= 128
      ? value.ref
      : undefined;
  const semanticId =
    typeof value.semantic_id === "string" &&
    SEMANTIC_ID_PATTERN.test(value.semantic_id)
      ? value.semantic_id
      : undefined;
  if ((id ? 1 : 0) + (semanticId ? 1 : 0) + (ref ? 1 : 0) !== 1) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must contain exactly one valid id, semantic_id, or ref.`,
    });
    return undefined;
  }
  return id ? { id } : semanticId ? { semantic_id: semanticId } : { ref: ref! };
};

export const validateCanvasPatch = (
  editor: Editor,
  args: Record<string, unknown>,
): NormalizedPatchPlan => {
  const errors: CanvasOperationError[] = [];
  rejectUnknownKeys(
    args,
    new Set(["command_id", "base_revision", "operations"]),
    -1,
    "",
    errors,
  );
  const commandId = typeof args.command_id === "string" ? args.command_id : "";
  const rawOperations = Array.isArray(args.operations) ? args.operations : [];
  if (
    !commandId ||
    commandId.length > 128 ||
    !Number.isInteger(args.base_revision) ||
    (args.base_revision as number) < 0 ||
    rawOperations.length < 1 ||
    rawOperations.length > 100
  ) {
    errors.push({
      operation_index: -1,
      path: "request",
      code: "invalid_operation",
      message: "command_id and 1–100 operations are required.",
    });
  }

  const plannedByRef = new Map<string, PlannedShape>();
  const plannedParentById = new Map<TLShapeId, TLShapeId | null>();
  const plannedChildrenByParent = new Map<TLShapeId, Set<TLShapeId>>();
  const semanticIndex = buildSemanticIndex(editor);
  const semanticOwners = new Map<string, Set<TLShapeId>>();
  const semanticByShapeId = new Map<TLShapeId, string>();
  for (const [semanticId, shapes] of semanticIndex) {
    semanticOwners.set(semanticId, new Set(shapes.map((shape) => shape.id)));
    for (const shape of shapes) semanticByShapeId.set(shape.id, semanticId);
  }
  rawOperations.forEach((raw, index) => {
    if (!isObject(raw)) return;
    const op = raw.op;
    if (op !== "create" && op !== "draw" && op !== "connect" && op !== "group")
      return;
    const ref = typeof raw.ref === "string" ? raw.ref : "";
    if (!ref) return;
    if (plannedByRef.has(ref)) {
      errors.push({
        operation_index: index,
        path: "ref",
        code: "duplicate_ref",
        message: `Patch ref is duplicated: ${ref}`,
      });
      return;
    }
    const type =
      op === "connect"
        ? "arrow"
        : op === "group"
          ? "group"
          : op === "draw"
            ? raw.kind === "freehand"
              ? "draw"
              : raw.kind === "highlight"
                ? "highlight"
                : "line"
            : isObject(raw.shape)
              ? raw.shape.type
              : undefined;
    const id = createShapeId(`${safeRef(commandId)}-${safeRef(ref)}-${index}`);
    plannedByRef.set(ref, {
      id,
      type:
        type === "group"
          ? "group"
          : SHAPE_TYPES.has(type as ShapeType)
            ? (type as ShapeType)
            : "arrow",
      availableAt: index,
    });
    if (editor.getShape(id)) {
      errors.push({
        operation_index: index,
        path: "ref",
        code: "duplicate_ref",
        message: `The deterministic shape ID for ref ${ref} already exists.`,
      });
    }
  });
  for (const shape of allDocumentShapes(editor)) {
    const parentId = editor.getShape(shape.parentId as TLShapeId)
      ? (shape.parentId as TLShapeId)
      : null;
    plannedParentById.set(shape.id, parentId);
    if (parentId) {
      const children = plannedChildrenByParent.get(parentId) ?? new Set();
      children.add(shape.id);
      plannedChildrenByParent.set(parentId, children);
    }
  }
  for (const planned of plannedByRef.values()) {
    if (!plannedParentById.has(planned.id))
      plannedParentById.set(planned.id, null);
  }
  const unavailableIds = new Set<TLShapeId>();

  const setPlannedParent = (id: TLShapeId, parentId: TLShapeId | null) => {
    const previousParentId = plannedParentById.get(id) ?? null;
    if (previousParentId) {
      const previousChildren = plannedChildrenByParent.get(previousParentId);
      previousChildren?.delete(id);
      if (!previousChildren?.size)
        plannedChildrenByParent.delete(previousParentId);
    }
    plannedParentById.set(id, parentId);
    if (parentId) {
      const children = plannedChildrenByParent.get(parentId) ?? new Set();
      children.add(id);
      plannedChildrenByParent.set(parentId, children);
    }
  };

  const removePlannedShape = (id: TLShapeId) => {
    const parentId = plannedParentById.get(id) ?? null;
    if (parentId) {
      const siblings = plannedChildrenByParent.get(parentId);
      siblings?.delete(id);
      if (!siblings?.size) plannedChildrenByParent.delete(parentId);
    }
    plannedParentById.delete(id);
    plannedChildrenByParent.delete(id);
  };

  const hasPlannedAncestor = (shapeId: TLShapeId, ancestorId: TLShapeId) => {
    const visited = new Set<TLShapeId>();
    let parentId = plannedParentById.get(shapeId) ?? null;
    while (parentId) {
      if (parentId === ancestorId) return true;
      if (visited.has(parentId)) return false;
      visited.add(parentId);
      parentId = plannedParentById.get(parentId) ?? null;
    }
    return false;
  };

  const rejectMixedHierarchyTargets = (
    ids: TLShapeId[],
    index: number,
    path: string,
  ) => {
    for (let left = 0; left < ids.length; left += 1) {
      for (let right = left + 1; right < ids.length; right += 1) {
        if (
          hasPlannedAncestor(ids[left], ids[right]) ||
          hasPlannedAncestor(ids[right], ids[left])
        ) {
          errors.push({
            operation_index: index,
            path,
            code: "invalid_operation",
            message:
              "Targets must not contain both a shape and one of its descendants.",
          });
          return;
        }
      }
    }
  };

  const plannedChildrenOf = (parentId: TLShapeId) =>
    Array.from(plannedChildrenByParent.get(parentId) ?? []);

  const nearestCommonPlannedAncestor = (ids: TLShapeId[]) => {
    if (!ids.length) return null;
    const chains = ids.map((id) => {
      const chain: TLShapeId[] = [];
      const visited = new Set<TLShapeId>();
      let parentId = plannedParentById.get(id) ?? null;
      while (parentId && !visited.has(parentId)) {
        chain.push(parentId);
        visited.add(parentId);
        parentId = plannedParentById.get(parentId) ?? null;
      }
      return chain;
    });
    return (
      chains[0].find((id) =>
        chains.slice(1).every((chain) => chain.includes(id)),
      ) ?? null
    );
  };

  const claimSemanticId = (id: TLShapeId, metadata: MetadataPatch) => {
    if (metadata.semanticId === undefined) return;
    const currentSemanticId = semanticByShapeId.get(id);
    if (currentSemanticId) {
      const currentOwners = semanticOwners.get(currentSemanticId);
      currentOwners?.delete(id);
      if (!currentOwners?.size) semanticOwners.delete(currentSemanticId);
      semanticByShapeId.delete(id);
    }
    if (metadata.semanticId === null) {
      return;
    }
    const owners = semanticOwners.get(metadata.semanticId) ?? new Set();
    owners.add(id);
    semanticOwners.set(metadata.semanticId, owners);
    semanticByShapeId.set(id, metadata.semanticId);
  };

  const resolveTarget = (
    targetValue: unknown,
    index: number,
    path: string,
    endpoint = false,
  ): { id: TLShapeId; type: string } | undefined => {
    const target = parseTarget(targetValue, index, path, errors);
    if (!target) return undefined;
    if ("ref" in target) {
      const planned = plannedByRef.get(target.ref);
      if (!planned || planned.availableAt >= index) {
        errors.push({
          operation_index: index,
          path,
          code: endpoint ? "connector_endpoint_not_found" : "target_not_found",
          message: `Earlier patch ref was not found: ${target.ref}`,
        });
        return undefined;
      }
      if (unavailableIds.has(planned.id)) {
        errors.push({
          operation_index: index,
          path,
          code: endpoint ? "connector_endpoint_not_found" : "target_not_found",
          message: `Patch ref was already deleted: ${target.ref}`,
        });
        return undefined;
      }
      return { id: planned.id, type: planned.type };
    }
    if ("semantic_id" in target) {
      const matches = semanticIndex.get(target.semantic_id) ?? [];
      if (matches.length > 1) {
        errors.push({
          operation_index: index,
          path,
          code: "duplicate_semantic_id",
          message: `Semantic target is ambiguous because ${target.semantic_id} is duplicated.`,
        });
        return undefined;
      }
      const shape = matches[0];
      if (!shape || unavailableIds.has(shape.id)) {
        errors.push({
          operation_index: index,
          path,
          code: endpoint ? "connector_endpoint_not_found" : "target_not_found",
          message: `Canvas semantic target was not found: ${target.semantic_id}`,
        });
        return undefined;
      }
      if (!endpoint && editor.isShapeOrAncestorLocked(shape)) {
        errors.push({
          operation_index: index,
          path,
          code: "target_locked",
          message: `Canvas semantic target is locked: ${target.semantic_id}`,
        });
        return undefined;
      }
      return { id: shape.id, type: shape.type };
    }
    const shape = editor.getShape(target.id as TLShapeId);
    if (!shape) {
      errors.push({
        operation_index: index,
        path,
        code: endpoint ? "connector_endpoint_not_found" : "target_not_found",
        message: `Canvas shape was not found: ${target.id}`,
      });
      return undefined;
    }
    if (unavailableIds.has(shape.id)) {
      errors.push({
        operation_index: index,
        path,
        code: endpoint ? "connector_endpoint_not_found" : "target_not_found",
        message: `Canvas shape was already deleted by this patch: ${target.id}`,
      });
      return undefined;
    }
    if (!endpoint && editor.isShapeOrAncestorLocked(shape)) {
      errors.push({
        operation_index: index,
        path,
        code: "target_locked",
        message: `Canvas shape is locked and cannot be changed: ${target.id}`,
      });
      return undefined;
    }
    return { id: shape.id, type: shape.type };
  };

  const resolveTargets = (
    value: unknown,
    index: number,
    path: string,
    minimum = 1,
  ) => {
    if (!Array.isArray(value) || value.length < minimum || value.length > 100) {
      errors.push({
        operation_index: index,
        path,
        code: "invalid_operation",
        message: `${path} must contain ${minimum}–100 targets.`,
      });
      return [] as { id: TLShapeId; type: string }[];
    }
    const resolved = value
      .map((item, targetIndex) =>
        resolveTarget(item, index, `${path}.${targetIndex}`),
      )
      .filter((target): target is { id: TLShapeId; type: string } =>
        Boolean(target),
      );
    if (new Set(resolved.map((target) => target.id)).size !== resolved.length) {
      errors.push({
        operation_index: index,
        path,
        code: "invalid_operation",
        message: `${path} must not contain duplicate targets.`,
      });
    }
    return resolved;
  };

  const normalized: NormalizedOperation[] = [];
  const requestedHeights: Record<string, number> = {};
  rawOperations.forEach((raw, index) => {
    if (!isObject(raw) || typeof raw.op !== "string") {
      errors.push({
        operation_index: index,
        path: "op",
        code: "invalid_operation",
        message: "Each operation must be an object with an op.",
      });
      return;
    }
    const op = raw.op;
    if (op === "draw") {
      rejectUnknownKeys(
        raw,
        new Set([
          "op",
          "ref",
          "kind",
          "points",
          "closed",
          "spline",
          "style",
          "semantic_id",
          "source_refs",
        ]),
        index,
        "",
        errors,
      );
      const ref = typeof raw.ref === "string" ? raw.ref : "";
      if (!ref || ref.length > 128)
        errors.push({
          operation_index: index,
          path: "ref",
          code: "invalid_operation",
          message: "draw requires a non-empty ref.",
        });
      const shapeType =
        raw.kind === "freehand"
          ? "draw"
          : raw.kind === "highlight"
            ? "highlight"
            : raw.kind === "line"
              ? "line"
              : undefined;
      if (!shapeType) {
        errors.push({
          operation_index: index,
          path: "kind",
          code: "invalid_enum_value",
          message: "draw kind must be freehand, highlight, or line.",
        });
        return;
      }
      const points = normalizePoints(
        raw.points,
        index,
        "points",
        errors,
        shapeType === "line" ? 100 : 600,
      );
      const planned = plannedByRef.get(ref);
      if (!points || !planned) return;
      if (
        raw.closed !== undefined &&
        (shapeType !== "draw" || typeof raw.closed !== "boolean")
      )
        errors.push({
          operation_index: index,
          path: "closed",
          code: "invalid_operation",
          message:
            "closed is supported only as a boolean for freehand drawing.",
        });
      if (
        raw.spline !== undefined &&
        (shapeType !== "line" ||
          (raw.spline !== "line" && raw.spline !== "cubic"))
      )
        errors.push({
          operation_index: index,
          path: "spline",
          code: "invalid_enum_value",
          message:
            "spline is supported only for line drawing as line or cubic.",
        });
      const props = normalizeStyle(
        raw.style,
        shapeType,
        index,
        "style",
        errors,
      );
      const origin = points[0];
      const localPoints = points.map((point) => ({
        x: point.x - origin.x,
        y: point.y - origin.y,
        z: point.z,
      }));
      if (shapeType === "draw" || shapeType === "highlight") {
        props.segments = [
          { type: "free", path: b64Vecs.encodePoints(localPoints) },
        ];
        props.isComplete = true;
        props.isPen = points.some((point) => point.z !== 0.5);
        props.scale = 1;
        props.scaleX = 1;
        props.scaleY = 1;
        if (shapeType === "draw") props.isClosed = raw.closed === true;
      } else {
        const indices = getIndices(localPoints.length);
        props.points = Object.fromEntries(
          localPoints.map((point, pointIndex) => [
            `p${pointIndex + 1}`,
            {
              id: `p${pointIndex + 1}`,
              index: indices[pointIndex],
              x: point.x,
              y: point.y,
            },
          ]),
        );
        props.spline = raw.spline === "cubic" ? "cubic" : "line";
        props.scale = 1;
      }
      const metadata = normalizeMetadata(raw, index, "", errors);
      claimSemanticId(planned.id, metadata);
      normalized.push({
        op: "create",
        id: planned.id,
        ref,
        shape: { type: shapeType, x: origin.x, y: origin.y, props },
        metadata,
      });
      return;
    }
    if (op === "create") {
      rejectUnknownKeys(
        raw,
        new Set(["op", "ref", "shape"]),
        index,
        "",
        errors,
      );
      if (typeof raw.ref !== "string" || !raw.ref || raw.ref.length > 128) {
        errors.push({
          operation_index: index,
          path: "ref",
          code: "invalid_operation",
          message: "create requires a non-empty ref.",
        });
      }
      if (!isObject(raw.shape)) {
        errors.push({
          operation_index: index,
          path: "shape",
          code: "invalid_operation",
          message: "shape must be an object.",
        });
        return;
      }
      const shape = raw.shape;
      if ("props" in shape) {
        const props = isObject(shape.props) ? shape.props : {};
        rejectUnknownKeys(props, new Set(), index, "shape.props", errors);
      }
      const type = shape.type;
      if (!CREATE_SHAPE_TYPES.has(type as CreateShapeType)) {
        errors.push({
          operation_index: index,
          path: "shape.type",
          code: "unsupported_shape_type",
          message: `Unsupported shape type: ${String(type)}`,
        });
        return;
      }
      const shapeType = type as CreateShapeType;
      const shapeKeys: Record<CreateShapeType, Set<string>> = {
        geo: new Set([
          "type",
          "x",
          "y",
          "width",
          "height",
          "text",
          "style",
          "semantic_id",
          "source_refs",
        ]),
        text: new Set([
          "type",
          "x",
          "y",
          "width",
          "text",
          "style",
          "semantic_id",
          "source_refs",
        ]),
        note: new Set([
          "type",
          "x",
          "y",
          "width",
          "height",
          "text",
          "style",
          "semantic_id",
          "source_refs",
        ]),
        frame: new Set([
          "type",
          "x",
          "y",
          "width",
          "height",
          "name",
          "style",
          "semantic_id",
          "source_refs",
        ]),
        image: new Set([
          "type",
          "x",
          "y",
          "width",
          "height",
          "alt_text",
          "semantic_id",
          "source_refs",
          "asset",
        ]),
      };
      rejectUnknownKeys(shape, shapeKeys[shapeType], index, "shape", errors);
      const x = finiteNumber(shape.x, index, "shape.x", errors);
      const y = finiteNumber(shape.y, index, "shape.y", errors);
      const width =
        shape.width === undefined
          ? undefined
          : finiteNumber(shape.width, index, "shape.width", errors, true);
      const height =
        shape.height === undefined
          ? undefined
          : finiteNumber(shape.height, index, "shape.height", errors, true);
      const ref = typeof raw.ref === "string" ? raw.ref : "";
      const planned = plannedByRef.get(ref);
      if (x === undefined || y === undefined || !planned) return;
      if (
        shapeType === "note" &&
        (width === undefined || height === undefined)
      ) {
        errors.push({
          operation_index: index,
          path: "shape",
          code: "invalid_operation",
          message: "Note shapes require width and height.",
        });
        return;
      }
      const props: JsonObject = normalizeStyle(
        shape.style,
        shapeType,
        index,
        "shape.style",
        errors,
      );
      const metadata = normalizeMetadata(shape, index, "shape.", errors);
      claimSemanticId(planned.id, metadata);
      const shapeText =
        shape.text === undefined
          ? undefined
          : boundedString(shape.text, index, "shape.text", errors, 20_000);
      const shapeName =
        shape.name === undefined
          ? undefined
          : boundedString(shape.name, index, "shape.name", errors, 500);
      const altText =
        shape.alt_text === undefined
          ? ""
          : boundedString(
              shape.alt_text,
              index,
              "shape.alt_text",
              errors,
              1_000,
            );
      if (shapeType === "text" && shape.text === undefined) {
        errors.push({
          operation_index: index,
          path: "shape.text",
          code: "invalid_operation",
          message: "Text shapes require text.",
        });
      }
      let imageAsset: NormalizedImageAsset | undefined;
      if (shapeType === "geo") {
        props.geo = props.geo || "rectangle";
        props.w = width ?? 240;
        props.h = height ?? 120;
        if (shapeText !== undefined) props.richText = toRichText(shapeText);
      } else if (shapeType === "text") {
        props.richText = toRichText(shapeText ?? "");
        if (width !== undefined) {
          props.w = width;
          props.autoSize = false;
        }
      } else if (shapeType === "note") {
        props.richText = toRichText(shapeText ?? "");
      } else if (shapeType === "frame") {
        props.w = width ?? 320;
        props.h = height ?? 180;
        props.name = shapeName ?? "";
      } else {
        const asset = shape.asset;
        if (!isObject(asset)) {
          errors.push({
            operation_index: index,
            path: "shape.asset",
            code: "invalid_operation",
            message: "The optimized image asset is missing.",
          });
          return;
        }
        rejectUnknownKeys(
          asset,
          new Set([
            "src",
            "name",
            "mime_type",
            "width",
            "height",
            "byte_size",
            "original_byte_size",
            "quality",
            "content_hash",
          ]),
          index,
          "shape.asset",
          errors,
        );
        const assetWidth = finiteNumber(
          asset.width,
          index,
          "shape.asset.width",
          errors,
          true,
        );
        const assetHeight = finiteNumber(
          asset.height,
          index,
          "shape.asset.height",
          errors,
          true,
        );
        const assetByteSize = finiteNumber(
          asset.byte_size,
          index,
          "shape.asset.byte_size",
          errors,
          true,
        );
        const originalByteSize = boundedPositiveNumber(
          asset.original_byte_size,
          10 * 1024 * 1024,
          index,
          "shape.asset.original_byte_size",
          errors,
        );
        const quality = boundedPositiveNumber(
          asset.quality,
          100,
          index,
          "shape.asset.quality",
          errors,
        );
        const src = typeof asset.src === "string" ? asset.src : "";
        const name = typeof asset.name === "string" ? asset.name : "";
        const contentHash =
          typeof asset.content_hash === "string" ? asset.content_hash : "";
        if (
          asset.mime_type !== "image/webp" ||
          !/^\/api\/canvas\/canvas-[0-9a-f]{24}\/assets\/[0-9a-f]{64}\.webp$/.test(
            src,
          ) ||
          !/^sha256:[0-9a-f]{64}$/.test(contentHash) ||
          !name ||
          assetWidth === undefined ||
          assetHeight === undefined ||
          assetByteSize === undefined ||
          originalByteSize === undefined ||
          quality === undefined ||
          assetByteSize > 768 * 1024
        ) {
          errors.push({
            operation_index: index,
            path: "shape.asset",
            code: "invalid_operation",
            message: "The optimized image asset is invalid.",
          });
          return;
        }
        const hash = contentHash.slice("sha256:".length);
        imageAsset = {
          id: `asset:${hash}` as TLAssetId,
          src,
          name,
          width: assetWidth,
          height: assetHeight,
          byteSize: assetByteSize,
          originalByteSize,
          quality,
          contentHash,
        };
        const naturalScale = Math.min(
          1,
          640 / Math.max(assetWidth, assetHeight),
        );
        const naturalWidth = assetWidth * naturalScale;
        const naturalHeight = assetHeight * naturalScale;
        if (width !== undefined && height !== undefined) {
          props.w = width;
          props.h = height;
        } else if (width !== undefined) {
          props.w = width;
          props.h = width * (assetHeight / assetWidth);
        } else if (height !== undefined) {
          props.h = height;
          props.w = height * (assetWidth / assetHeight);
        } else {
          props.w = naturalWidth;
          props.h = naturalHeight;
        }
        props.assetId = imageAsset.id;
        props.url = "";
        props.crop = null;
        props.flipX = false;
        props.flipY = false;
        props.playing = true;
        props.altText = altText ?? "";
      }
      normalized.push({
        op: "create",
        id: planned.id,
        ref,
        shape: {
          type: shapeType,
          x,
          y,
          props,
        },
        metadata,
        ...(imageAsset ? { asset: imageAsset } : {}),
        ...(shapeType === "note" && width !== undefined && height !== undefined
          ? { resize: { width, height } }
          : {}),
      });
      if (height !== undefined) requestedHeights[planned.id] = height;
      return;
    }

    if (op === "delete") {
      rejectUnknownKeys(raw, new Set(["op", "targets"]), index, "", errors);
      const targetIds = resolveTargets(raw.targets, index, "targets").map(
        (target) => target.id,
      );
      const expandedIds = new Set<TLShapeId>();
      const pendingIds = [...targetIds];
      while (pendingIds.length) {
        const id = pendingIds.pop()!;
        if (expandedIds.has(id)) continue;
        expandedIds.add(id);
        pendingIds.push(...plannedChildrenOf(id));
      }
      const ids = Array.from(expandedIds);
      normalized.push({ op: "delete", ids });
      ids.forEach((id) => {
        unavailableIds.add(id);
        removePlannedShape(id);
        const semanticId = semanticByShapeId.get(id);
        const owners = semanticId ? semanticOwners.get(semanticId) : undefined;
        owners?.delete(id);
        if (semanticId && !owners?.size) semanticOwners.delete(semanticId);
        semanticByShapeId.delete(id);
      });
      return;
    }

    if (op === "group") {
      rejectUnknownKeys(
        raw,
        new Set(["op", "ref", "targets", "semantic_id", "source_refs"]),
        index,
        "",
        errors,
      );
      const ref = typeof raw.ref === "string" ? raw.ref : "";
      if (!ref || ref.length > 128)
        errors.push({
          operation_index: index,
          path: "ref",
          code: "invalid_operation",
          message: "group requires a non-empty ref.",
        });
      const planned = plannedByRef.get(ref);
      const ids = resolveTargets(raw.targets, index, "targets", 2).map(
        (target) => target.id,
      );
      if (!planned || planned.type !== "group") return;
      rejectMixedHierarchyTargets(ids, index, "targets");
      const metadata = normalizeMetadata(raw, index, "", errors);
      claimSemanticId(planned.id, metadata);
      setPlannedParent(planned.id, nearestCommonPlannedAncestor(ids));
      ids.forEach((id) => setPlannedParent(id, planned.id));
      normalized.push({
        op: "group",
        id: planned.id,
        ref,
        ids,
        metadata,
      });
      return;
    }

    if (op === "ungroup") {
      rejectUnknownKeys(raw, new Set(["op", "targets"]), index, "", errors);
      const targets = resolveTargets(raw.targets, index, "targets");
      const childIds: TLShapeId[] = [];
      for (const target of targets) {
        if (target.type !== "group") {
          errors.push({
            operation_index: index,
            path: "targets",
            code: "target_type_mismatch",
            message: "ungroup targets must be group shapes.",
          });
          continue;
        }
        const plannedChildren = plannedChildrenOf(target.id);
        childIds.push(...plannedChildren);
        const groupParentId = plannedParentById.get(target.id) ?? null;
        plannedChildren.forEach((id) => setPlannedParent(id, groupParentId));
        removePlannedShape(target.id);
        unavailableIds.add(target.id);
        const semanticId = semanticByShapeId.get(target.id);
        semanticOwners.get(semanticId ?? "")?.delete(target.id);
        semanticByShapeId.delete(target.id);
      }
      normalized.push({
        op: "ungroup",
        ids: targets.map((target) => target.id),
        childIds,
      });
      return;
    }

    if (op === "disconnect") {
      rejectUnknownKeys(
        raw,
        new Set(["op", "target", "terminals"]),
        index,
        "",
        errors,
      );
      const target = resolveTarget(raw.target, index, "target");
      if (!target) return;
      if (target.type !== "arrow") {
        errors.push({
          operation_index: index,
          path: "target",
          code: "target_type_mismatch",
          message: "disconnect target must be an arrow.",
        });
        return;
      }
      const terminals =
        raw.terminals === undefined
          ? ["start", "end"]
          : Array.isArray(raw.terminals) &&
              raw.terminals.length > 0 &&
              raw.terminals.length <= 2 &&
              raw.terminals.every(
                (terminal) => terminal === "start" || terminal === "end",
              )
            ? Array.from(new Set(raw.terminals))
            : undefined;
      if (!terminals) {
        errors.push({
          operation_index: index,
          path: "terminals",
          code: "invalid_enum_value",
          message: "terminals must contain start, end, or both.",
        });
        return;
      }
      normalized.push({
        op: "disconnect",
        id: target.id,
        terminals: terminals as ("start" | "end")[],
      });
      return;
    }

    if (op === "reparent") {
      rejectUnknownKeys(
        raw,
        new Set(["op", "targets", "parent"]),
        index,
        "",
        errors,
      );
      const targets = resolveTargets(raw.targets, index, "targets");
      const parent =
        raw.parent === null ? null : resolveTarget(raw.parent, index, "parent");
      if (raw.parent !== null && !parent) return;
      if (parent && parent.type !== "frame" && parent.type !== "group") {
        errors.push({
          operation_index: index,
          path: "parent",
          code: "target_type_mismatch",
          message:
            "reparent parent must be a frame, group, or null for the page.",
        });
      }
      if (
        parent &&
        targets.some(
          (target) =>
            target.id === parent.id || hasPlannedAncestor(parent.id, target.id),
        )
      )
        errors.push({
          operation_index: index,
          path: "parent",
          code: "invalid_operation",
          message:
            "A shape cannot be reparented into itself or its descendant.",
        });
      normalized.push({
        op: "reparent",
        ids: targets.map((target) => target.id),
        parentId: parent?.id ?? null,
      });
      targets.forEach((target) =>
        setPlannedParent(target.id, parent?.id ?? null),
      );
      return;
    }

    if (
      op === "reorder" ||
      op === "rotate" ||
      op === "flip" ||
      op === "align" ||
      op === "distribute" ||
      op === "stack" ||
      op === "pack"
    ) {
      const minimum =
        op === "distribute"
          ? 3
          : op === "align" || op === "stack" || op === "pack"
            ? 2
            : 1;
      const targets = resolveTargets(raw.targets, index, "targets", minimum);
      const ids = targets.map((target) => target.id);
      if (op !== "reorder") rejectMixedHierarchyTargets(ids, index, "targets");
      if (op === "reorder") {
        rejectUnknownKeys(
          raw,
          new Set(["op", "targets", "position", "consider_all_shapes"]),
          index,
          "",
          errors,
        );
        if (
          !["back", "backward", "forward", "front"].includes(
            String(raw.position),
          )
        )
          errors.push({
            operation_index: index,
            path: "position",
            code: "invalid_enum_value",
            message: "position must be back, backward, forward, or front.",
          });
        if (
          raw.consider_all_shapes !== undefined &&
          typeof raw.consider_all_shapes !== "boolean"
        )
          errors.push({
            operation_index: index,
            path: "consider_all_shapes",
            code: "invalid_operation",
            message: "consider_all_shapes must be a boolean.",
          });
        normalized.push({
          op,
          ids,
          position: raw.position as "back" | "backward" | "forward" | "front",
          considerAllShapes: raw.consider_all_shapes === true,
        });
      } else if (op === "rotate") {
        rejectUnknownKeys(
          raw,
          new Set(["op", "targets", "degrees"]),
          index,
          "",
          errors,
        );
        const degrees = finiteNumber(raw.degrees, index, "degrees", errors);
        if (degrees !== undefined)
          normalized.push({ op, ids, radians: (degrees * Math.PI) / 180 });
      } else if (op === "flip") {
        rejectUnknownKeys(
          raw,
          new Set(["op", "targets", "axis"]),
          index,
          "",
          errors,
        );
        if (raw.axis !== "horizontal" && raw.axis !== "vertical")
          errors.push({
            operation_index: index,
            path: "axis",
            code: "invalid_enum_value",
            message: "axis must be horizontal or vertical.",
          });
        else normalized.push({ op, ids, axis: raw.axis });
      } else if (op === "align") {
        rejectUnknownKeys(
          raw,
          new Set(["op", "targets", "alignment"]),
          index,
          "",
          errors,
        );
        const alignments = [
          "bottom",
          "center-horizontal",
          "center-vertical",
          "left",
          "right",
          "top",
        ] as const;
        if (!alignments.includes(raw.alignment as (typeof alignments)[number]))
          errors.push({
            operation_index: index,
            path: "alignment",
            code: "invalid_enum_value",
            message: "alignment is not supported.",
          });
        else
          normalized.push({
            op,
            ids,
            alignment: raw.alignment as (typeof alignments)[number],
          });
      } else if (op === "distribute") {
        rejectUnknownKeys(
          raw,
          new Set(["op", "targets", "axis"]),
          index,
          "",
          errors,
        );
        if (raw.axis !== "horizontal" && raw.axis !== "vertical")
          errors.push({
            operation_index: index,
            path: "axis",
            code: "invalid_enum_value",
            message: "axis must be horizontal or vertical.",
          });
        else normalized.push({ op, ids, axis: raw.axis });
      } else {
        rejectUnknownKeys(
          raw,
          op === "stack"
            ? new Set(["op", "targets", "axis", "gap"])
            : new Set(["op", "targets", "gap"]),
          index,
          "",
          errors,
        );
        const gap =
          raw.gap === undefined
            ? undefined
            : finiteNumber(raw.gap, index, "gap", errors);
        if (op === "stack") {
          if (raw.axis !== "horizontal" && raw.axis !== "vertical")
            errors.push({
              operation_index: index,
              path: "axis",
              code: "invalid_enum_value",
              message: "axis must be horizontal or vertical.",
            });
          else if (raw.gap === undefined || gap !== undefined)
            normalized.push({
              op,
              ids,
              axis: raw.axis,
              ...(gap !== undefined ? { gap } : {}),
            });
        } else if (raw.gap === undefined || gap !== undefined) {
          normalized.push({ op, ids, ...(gap !== undefined ? { gap } : {}) });
        }
      }
      return;
    }

    if (op === "connect") {
      rejectUnknownKeys(
        raw,
        new Set([
          "op",
          "ref",
          "from",
          "to",
          "text",
          "style",
          "semantic_id",
          "source_refs",
        ]),
        index,
        "",
        errors,
      );
      if (typeof raw.ref !== "string" || !raw.ref || raw.ref.length > 128) {
        errors.push({
          operation_index: index,
          path: "ref",
          code: "invalid_operation",
          message: "connect requires a non-empty ref.",
        });
      }
      const from = resolveTarget(raw.from, index, "from", true);
      const to = resolveTarget(raw.to, index, "to", true);
      const ref = typeof raw.ref === "string" ? raw.ref : "";
      const planned = plannedByRef.get(ref);
      if (!from || !to || !planned) return;
      const props = normalizeStyle(raw.style, "arrow", index, "style", errors);
      const metadata = normalizeMetadata(raw, index, "", errors);
      claimSemanticId(planned.id, metadata);
      if (raw.text !== undefined) {
        const text = boundedString(raw.text, index, "text", errors, 20_000);
        if (text !== undefined) props.richText = toRichText(text);
      }
      normalized.push({
        op: "connect",
        id: planned.id,
        ref,
        fromId: from.id,
        toId: to.id,
        props,
        metadata,
      });
      return;
    }

    if (op !== "update" && op !== "move" && op !== "resize") {
      errors.push({
        operation_index: index,
        path: "op",
        code: "invalid_operation",
        message: `Unsupported canvas operation: ${op}`,
      });
      return;
    }
    const allowed =
      op === "update"
        ? new Set([
            "op",
            "target",
            "x",
            "y",
            "width",
            "height",
            "text",
            "name",
            "style",
            "semantic_id",
            "source_refs",
          ])
        : op === "move"
          ? new Set(["op", "target", "x", "y"])
          : new Set(["op", "target", "width", "height"]);
    rejectUnknownKeys(raw, allowed, index, "", errors);
    if (op === "update") {
      const hasDirectUpdate = [
        "x",
        "y",
        "width",
        "height",
        "text",
        "name",
      ].some((key) => raw[key] !== undefined);
      const hasStyleUpdate =
        isObject(raw.style) && Object.keys(raw.style).length > 0;
      const hasMetadataUpdate =
        raw.semantic_id !== undefined || raw.source_refs !== undefined;
      if (!hasDirectUpdate && !hasStyleUpdate && !hasMetadataUpdate) {
        errors.push({
          operation_index: index,
          path: "operation",
          code: "invalid_operation",
          message: "update requires at least one field to change.",
        });
      }
    }
    const target = resolveTarget(raw.target, index, "target");
    if (!target) return;
    if (op === "move") {
      const x = finiteNumber(raw.x, index, "x", errors);
      const y = finiteNumber(raw.y, index, "y", errors);
      if (x !== undefined && y !== undefined)
        normalized.push({ op, id: target.id, type: target.type, x, y });
      return;
    }
    if (op === "resize") {
      const width = finiteNumber(raw.width, index, "width", errors, true);
      const height = finiteNumber(raw.height, index, "height", errors, true);
      if (width !== undefined && height !== undefined)
        normalized.push({
          op,
          id: target.id,
          type: target.type,
          width,
          height,
        });
      if (height !== undefined) requestedHeights[target.id] = height;
      return;
    }
    if (!SHAPE_TYPES.has(target.type as ShapeType)) {
      errors.push({
        operation_index: index,
        path: "target",
        code: "target_type_mismatch",
        message: `Shape type ${target.type} cannot be updated by this protocol.`,
      });
      return;
    }
    const shapeType = target.type as ShapeType;
    const update: JsonObject = {};
    const metadata = normalizeMetadata(raw, index, "", errors, true);
    claimSemanticId(target.id, metadata);
    if (raw.x !== undefined) update.x = finiteNumber(raw.x, index, "x", errors);
    if (raw.y !== undefined) update.y = finiteNumber(raw.y, index, "y", errors);
    const props = normalizeStyle(raw.style, shapeType, index, "style", errors);
    if (raw.text !== undefined) {
      if (!new Set<ShapeType>(["geo", "text", "note"]).has(shapeType))
        errors.push({
          operation_index: index,
          path: "text",
          code: "target_type_mismatch",
          message: `${shapeType} shapes do not support text updates.`,
        });
      else {
        const text = boundedString(raw.text, index, "text", errors, 20_000);
        if (text !== undefined) props.richText = toRichText(text);
      }
    }
    if (raw.name !== undefined) {
      if (shapeType !== "frame")
        errors.push({
          operation_index: index,
          path: "name",
          code: "target_type_mismatch",
          message: "Only frame shapes support name.",
        });
      else {
        const name = boundedString(raw.name, index, "name", errors, 500);
        if (name !== undefined) props.name = name;
      }
    }
    if (raw.width !== undefined) {
      if (
        shapeType === "note" ||
        shapeType === "draw" ||
        shapeType === "highlight" ||
        shapeType === "line"
      )
        errors.push({
          operation_index: index,
          path: "width",
          code: "target_type_mismatch",
          message: `Use resize for ${shapeType} dimensions.`,
        });
      else props.w = finiteNumber(raw.width, index, "width", errors, true);
    }
    if (raw.height !== undefined) {
      const height = finiteNumber(raw.height, index, "height", errors, true);
      if (
        shapeType === "note" ||
        shapeType === "draw" ||
        shapeType === "highlight" ||
        shapeType === "line"
      )
        errors.push({
          operation_index: index,
          path: "height",
          code: "target_type_mismatch",
          message: `Use resize for ${shapeType} dimensions.`,
        });
      else if (shapeType === "text")
        errors.push({
          operation_index: index,
          path: "height",
          code: "target_type_mismatch",
          message:
            "Text height is content-driven; use resize to scale the shape.",
        });
      else props.h = height;
    }
    update.props = props;
    normalized.push({
      op: "update",
      id: target.id,
      type: target.type,
      update,
      metadata,
    });
    if (typeof raw.height === "number")
      requestedHeights[target.id] = raw.height;
  });

  for (const [semanticId, owners] of semanticOwners) {
    if (owners.size < 2) continue;
    errors.push({
      operation_index: -1,
      path: "document.meta.nomad.semantic_id",
      code: "duplicate_semantic_id",
      message: `Canvas document would contain duplicate semantic ID ${semanticId}: ${Array.from(owners).join(", ")}`,
      suggestion:
        "Use exact tldraw IDs to delete or assign a unique semantic_id to each duplicate.",
    });
  }

  const changedIds = new Set<TLShapeId>();
  for (const operation of normalized) {
    if (
      operation.op === "delete" ||
      operation.op === "ungroup" ||
      operation.op === "reparent" ||
      operation.op === "reorder" ||
      operation.op === "rotate" ||
      operation.op === "flip" ||
      operation.op === "align" ||
      operation.op === "distribute" ||
      operation.op === "stack" ||
      operation.op === "pack"
    ) {
      operation.ids.forEach((id) => changedIds.add(id));
      if (operation.op === "ungroup")
        operation.childIds.forEach((id) => changedIds.add(id));
    } else if (operation.op === "group") {
      changedIds.add(operation.id);
      operation.ids.forEach((id) => changedIds.add(id));
    } else {
      changedIds.add(operation.id);
    }
  }
  if (changedIds.size > CANVAS_PATCH_MAX_CHANGED_IDS) {
    errors.push({
      operation_index: -1,
      path: "operations",
      code: "invalid_operation",
      message: `A patch may change at most ${CANVAS_PATCH_MAX_CHANGED_IDS} unique shapes.`,
    });
  }

  if (errors.length)
    throw new CanvasProtocolError("patch_validation_failed", errors);
  return Object.freeze({
    commandId,
    operations: Object.freeze(normalized),
    refs: Object.freeze(
      Object.fromEntries(
        Array.from(plannedByRef, ([ref, shape]) => [ref, shape.id]),
      ),
    ),
    requestedHeights: Object.freeze(requestedHeights),
  });
};

const bindingProps = (terminal: "start" | "end") => ({
  terminal,
  isExact: false,
  isPrecise: false,
  normalizedAnchor: { x: 0.5, y: 0.5 },
  snap: "none" as const,
});

export const applyCanvasPatch = (editor: Editor, plan: NormalizedPatchPlan) => {
  const changedIds = new Set<TLShapeId>();
  const beforeShapes = new Map(
    allDocumentShapes(editor).map((shape) => [shape.id, shape]),
  );
  const requiresSelectTool = plan.operations.some(
    (operation) => operation.op === "group" || operation.op === "ungroup",
  );
  const previousToolId = requiresSelectTool
    ? editor.getCurrentToolId()
    : undefined;
  let switchedTool = false;
  const restoreTool = () => {
    if (!switchedTool || !previousToolId) return;
    editor.setCurrentTool(previousToolId);
    switchedTool = false;
  };
  const mark = editor.markHistoryStoppingPoint(
    `canvas:${safeRef(plan.commandId)}`,
  );
  try {
    if (previousToolId && previousToolId !== "select") {
      editor.setCurrentTool("select");
      switchedTool = true;
    }
    editor.run(() => {
      for (const operation of plan.operations) {
        if (operation.op === "create") {
          if (operation.asset && !editor.getAsset(operation.asset.id)) {
            editor.createAssets([
              {
                id: operation.asset.id,
                typeName: "asset",
                type: "image",
                props: {
                  w: operation.asset.width,
                  h: operation.asset.height,
                  name: operation.asset.name,
                  isAnimated: false,
                  mimeType: "image/webp",
                  src: operation.asset.src,
                  fileSize: operation.asset.byteSize,
                },
                meta: {
                  nomad: {
                    content_hash: operation.asset.contentHash,
                    original_byte_size: operation.asset.originalByteSize,
                    quality: operation.asset.quality,
                    optimized: true,
                  },
                },
              },
            ] as never);
          }
          editor.createShape({
            id: operation.id,
            ...operation.shape,
            meta: mergeNomadMetadata(
              {},
              plan.commandId,
              operation.metadata,
              "codex",
            ),
          } as never);
          if (operation.resize) {
            const bounds = editor.getShapeGeometry(operation.id).bounds;
            if (bounds.w <= 0 || bounds.h <= 0)
              throw new Error("Created shape bounds are not resizable.");
            editor.resizeShape(operation.id, {
              x: operation.resize.width / bounds.w,
              y: operation.resize.height / bounds.h,
            });
          }
          changedIds.add(operation.id);
        } else if (operation.op === "delete") {
          editor.deleteShapes(operation.ids);
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "connect") {
          const fromBounds = editor.getShapePageBounds(operation.fromId);
          const toBounds = editor.getShapePageBounds(operation.toId);
          if (!fromBounds || !toBounds)
            throw new Error("Connector bounds not found.");
          editor.createShape({
            id: operation.id,
            type: "arrow",
            x: fromBounds.center.x,
            y: fromBounds.center.y,
            props: {
              start: { x: 0, y: 0 },
              end: {
                x: toBounds.center.x - fromBounds.center.x,
                y: toBounds.center.y - fromBounds.center.y,
              },
              ...operation.props,
            },
            meta: mergeNomadMetadata(
              {},
              plan.commandId,
              operation.metadata,
              "codex",
            ),
          } as never);
          editor.createBindings([
            {
              type: "arrow",
              fromId: operation.id,
              toId: operation.fromId,
              props: bindingProps("start"),
            },
            {
              type: "arrow",
              fromId: operation.id,
              toId: operation.toId,
              props: bindingProps("end"),
            },
          ] as never);
          changedIds.add(operation.id);
        } else if (operation.op === "move") {
          editor.updateShape({
            id: operation.id,
            type: operation.type,
            x: operation.x,
            y: operation.y,
          } as never);
          changedIds.add(operation.id);
        } else if (operation.op === "resize") {
          const bounds = editor.getShapeGeometry(operation.id).bounds;
          if (bounds.w <= 0 || bounds.h <= 0)
            throw new Error("Shape bounds are not resizable.");
          editor.resizeShape(operation.id, {
            x: operation.width / bounds.w,
            y: operation.height / bounds.h,
          });
          changedIds.add(operation.id);
        } else if (operation.op === "disconnect") {
          const shape = editor.getShape(operation.id);
          if (!shape)
            throw new Error("Disconnect target disappeared during apply.");
          const bindings = editor
            .getBindingsFromShape(shape, "arrow")
            .filter((binding) =>
              operation.terminals.includes(
                (binding.props as { terminal?: "start" | "end" }).terminal ??
                  "start",
              ),
            );
          editor.deleteBindings(bindings);
          changedIds.add(operation.id);
        } else if (operation.op === "group") {
          editor.groupShapes(operation.ids, {
            groupId: operation.id,
            select: false,
          });
          const group = editor.getShape(operation.id);
          if (!group) throw new Error("Group was not created.");
          editor.updateShape({
            id: group.id,
            type: group.type,
            meta: mergeNomadMetadata(
              group.meta,
              plan.commandId,
              operation.metadata,
              "codex",
            ),
          } as never);
          changedIds.add(operation.id);
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "ungroup") {
          editor.ungroupShapes(operation.ids, { select: false });
          if (operation.ids.some((id) => editor.getShape(id)))
            throw new Error("One or more groups were not ungrouped.");
          operation.ids.forEach((id) => changedIds.add(id));
          operation.childIds.forEach((id) => changedIds.add(id));
        } else if (operation.op === "reparent") {
          editor.reparentShapes(
            operation.ids,
            operation.parentId ?? editor.getCurrentPageId(),
          );
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "reorder") {
          if (operation.position === "back") editor.sendToBack(operation.ids);
          else if (operation.position === "front")
            editor.bringToFront(operation.ids);
          else if (operation.position === "backward")
            editor.sendBackward(operation.ids, {
              considerAllShapes: operation.considerAllShapes,
            });
          else
            editor.bringForward(operation.ids, {
              considerAllShapes: operation.considerAllShapes,
            });
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "rotate") {
          editor.rotateShapesBy(operation.ids, operation.radians);
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "flip") {
          editor.flipShapes(operation.ids, operation.axis);
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "align") {
          editor.alignShapes(operation.ids, operation.alignment);
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "distribute") {
          editor.distributeShapes(operation.ids, operation.axis);
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "stack") {
          editor.stackShapes(operation.ids, operation.axis, operation.gap);
          operation.ids.forEach((id) => changedIds.add(id));
        } else if (operation.op === "pack") {
          editor.packShapes(operation.ids, operation.gap);
          operation.ids.forEach((id) => changedIds.add(id));
        } else {
          const existing = editor.getShape(operation.id);
          if (!existing)
            throw new Error("Update target disappeared during apply.");
          editor.updateShape({
            id: operation.id,
            type: operation.type,
            ...operation.update,
            meta: mergeNomadMetadata(
              existing.meta,
              plan.commandId,
              operation.metadata,
            ),
          } as never);
          changedIds.add(operation.id);
        }
      }
    });
    const afterShapes = new Map(
      allDocumentShapes(editor).map((shape) => [shape.id, shape]),
    );
    for (const id of new Set([...beforeShapes.keys(), ...afterShapes.keys()])) {
      if (beforeShapes.get(id) !== afterShapes.get(id)) changedIds.add(id);
    }
    if (changedIds.size > CANVAS_PATCH_MAX_CHANGED_IDS)
      throw new Error(
        `Patch changed more than ${CANVAS_PATCH_MAX_CHANGED_IDS} shapes after tldraw layout effects.`,
      );
    restoreTool();
  } catch (error) {
    let rollbackMessage = "";
    try {
      editor.bailToMark(mark);
    } catch (rollbackError) {
      rollbackMessage = ` Rollback also failed: ${rollbackError instanceof Error ? rollbackError.message : String(rollbackError)}`;
    }
    try {
      restoreTool();
    } catch (restoreError) {
      rollbackMessage += ` Tool restoration also failed: ${restoreError instanceof Error ? restoreError.message : String(restoreError)}`;
    }
    throw new CanvasProtocolError("patch_apply_failed", [
      {
        operation_index: -1,
        path: "operations",
        code: "invalid_operation",
        message: `${error instanceof Error ? error.message : String(error)}${rollbackMessage}`,
      },
    ]);
  }
  return {
    result: {
      changed_ids: Array.from(changedIds),
      refs: plan.refs,
      warnings: [],
      optimized_images: plan.operations.flatMap((operation) =>
        operation.op === "create" && operation.asset
          ? [
              {
                ref: operation.ref,
                name: operation.asset.name,
                width: operation.asset.width,
                height: operation.asset.height,
                byte_size: operation.asset.byteSize,
                original_byte_size: operation.asset.originalByteSize,
                quality: operation.asset.quality,
              },
            ]
          : [],
      ),
    },
    rollback: () => editor.bailToMark(mark),
  };
};
