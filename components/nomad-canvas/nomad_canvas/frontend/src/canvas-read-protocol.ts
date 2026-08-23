import {
  Box,
  Editor,
  TLBinding,
  TLRichText,
  TLShape,
  TLShapeId,
  renderPlaintextFromRichText,
} from "tldraw";
import { semanticReadSummary } from "./canvas-semantic";

export const CANVAS_READ_MAX_REQUESTED_IDS = 100;
export const CANVAS_READ_MAX_SHAPES = 500;
export const CANVAS_READ_MAX_BINDINGS = 1_000;
export const CANVAS_READ_MAX_BOUND_DIMENSION = 1_000_000;
export const CANVAS_READ_MIN_IMAGE_DIMENSION = 64;
export const CANVAS_READ_MAX_IMAGE_DIMENSION = 2048;
const CANVAS_READ_MAX_TEXT = 2_000;
const CANVAS_READ_MAX_FULL_VALUE = 20_000;

export type CanvasReadDetail = "compact" | "standard" | "full";

export type CanvasReadScope =
  | { type: "all" }
  | { type: "viewport" }
  | { type: "selection" }
  | { type: "bounds"; x: number; y: number; width: number; height: number }
  | { type: "frame"; id: string }
  | { type: "shape_ids"; ids: string[]; include_descendants: boolean };

export type CanvasReadRequest = {
  scope: CanvasReadScope;
  detail: CanvasReadDetail;
  include_image: boolean;
  max_image_dimension: number;
};

export class CanvasReadError extends Error {
  constructor(
    public readonly code: "read_validation_failed" | "scope_not_found",
    message: string,
  ) {
    super(message);
  }

  toPayload() {
    return { error: this.code, message: this.message };
  }
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const rejectUnknownKeys = (
  value: Record<string, unknown>,
  allowed: Set<string>,
  path: string,
) => {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length)
    throw new CanvasReadError(
      "read_validation_failed",
      `${path} contains unsupported fields: ${unknown.join(", ")}`,
    );
};

const boundedFiniteNumber = (
  value: unknown,
  path: string,
  positive = false,
) => {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    Math.abs(value) > CANVAS_READ_MAX_BOUND_DIMENSION ||
    (positive && value <= 0)
  ) {
    throw new CanvasReadError(
      "read_validation_failed",
      `${path} is outside the supported page-space range.`,
    );
  }
  return value;
};

export const normalizeCanvasReadRequest = (
  raw: Record<string, unknown>,
): CanvasReadRequest => {
  rejectUnknownKeys(
    raw,
    new Set(["scope", "detail", "include_image", "max_image_dimension"]),
    "request",
  );
  const detail = raw.detail ?? "compact";
  if (detail !== "compact" && detail !== "standard" && detail !== "full")
    throw new CanvasReadError(
      "read_validation_failed",
      "detail must be compact, standard, or full.",
    );
  const includeImage = raw.include_image ?? true;
  if (typeof includeImage !== "boolean")
    throw new CanvasReadError(
      "read_validation_failed",
      "include_image must be a boolean.",
    );
  const maxImageDimension = raw.max_image_dimension ?? 1536;
  if (
    !Number.isInteger(maxImageDimension) ||
    (maxImageDimension as number) < CANVAS_READ_MIN_IMAGE_DIMENSION ||
    (maxImageDimension as number) > CANVAS_READ_MAX_IMAGE_DIMENSION
  )
    throw new CanvasReadError(
      "read_validation_failed",
      `max_image_dimension must be ${CANVAS_READ_MIN_IMAGE_DIMENSION}–${CANVAS_READ_MAX_IMAGE_DIMENSION}.`,
    );

  const scopeValue = raw.scope ?? { type: "all" };
  if (!isObject(scopeValue) || typeof scopeValue.type !== "string")
    throw new CanvasReadError(
      "read_validation_failed",
      "scope must be a scoped-read object.",
    );
  let scope: CanvasReadScope;
  if (
    scopeValue.type === "all" ||
    scopeValue.type === "viewport" ||
    scopeValue.type === "selection"
  ) {
    rejectUnknownKeys(scopeValue, new Set(["type"]), "scope");
    scope = { type: scopeValue.type };
  } else if (scopeValue.type === "bounds") {
    rejectUnknownKeys(
      scopeValue,
      new Set(["type", "x", "y", "width", "height"]),
      "scope",
    );
    scope = {
      type: "bounds",
      x: boundedFiniteNumber(scopeValue.x, "scope.x"),
      y: boundedFiniteNumber(scopeValue.y, "scope.y"),
      width: boundedFiniteNumber(scopeValue.width, "scope.width", true),
      height: boundedFiniteNumber(scopeValue.height, "scope.height", true),
    };
  } else if (scopeValue.type === "frame") {
    rejectUnknownKeys(scopeValue, new Set(["type", "id"]), "scope");
    if (
      typeof scopeValue.id !== "string" ||
      !scopeValue.id.length ||
      scopeValue.id.length > 256
    )
      throw new CanvasReadError(
        "read_validation_failed",
        "frame scope requires an ID of 1–256 characters.",
      );
    scope = { type: "frame", id: scopeValue.id };
  } else if (scopeValue.type === "shape_ids") {
    rejectUnknownKeys(
      scopeValue,
      new Set(["type", "ids", "include_descendants"]),
      "scope",
    );
    if (
      !Array.isArray(scopeValue.ids) ||
      !scopeValue.ids.length ||
      scopeValue.ids.length > CANVAS_READ_MAX_REQUESTED_IDS ||
      new Set(scopeValue.ids).size !== scopeValue.ids.length ||
      scopeValue.ids.some(
        (id) => typeof id !== "string" || !id.length || id.length > 256,
      )
    )
      throw new CanvasReadError(
        "read_validation_failed",
        `shape_ids requires 1–${CANVAS_READ_MAX_REQUESTED_IDS} valid IDs.`,
      );
    if (
      scopeValue.include_descendants !== undefined &&
      typeof scopeValue.include_descendants !== "boolean"
    )
      throw new CanvasReadError(
        "read_validation_failed",
        "include_descendants must be a boolean.",
      );
    scope = {
      type: "shape_ids",
      ids: scopeValue.ids as string[],
      include_descendants: scopeValue.include_descendants !== false,
    };
  } else {
    throw new CanvasReadError(
      "read_validation_failed",
      `Unsupported scope type: ${scopeValue.type}`,
    );
  }

  return {
    scope,
    detail,
    include_image: includeImage,
    max_image_dimension: maxImageDimension as number,
  };
};

const descendants = (editor: Editor, ids: TLShapeId[]) =>
  editor.getShapeAndDescendantIds(ids);

const resolveScopeIds = (editor: Editor, scope: CanvasReadScope) => {
  if (scope.type === "all") return editor.getCurrentPageShapeIds();
  if (scope.type === "viewport")
    return editor.getShapeIdsInsideBounds(editor.getViewportPageBounds());
  if (scope.type === "bounds")
    return editor.getShapeIdsInsideBounds(
      new Box(scope.x, scope.y, scope.width, scope.height),
    );
  if (scope.type === "selection")
    return descendants(editor, editor.getSelectedShapeIds());
  if (scope.type === "frame") {
    const frame = editor.getShape(scope.id as TLShapeId);
    if (!frame || frame.type !== "frame" || !editor.isShapeInPage(frame))
      throw new CanvasReadError(
        "scope_not_found",
        `Canvas frame was not found on the current page: ${scope.id}`,
      );
    return descendants(editor, [frame.id]);
  }
  const found = scope.ids
    .map((id) => editor.getShape(id as TLShapeId))
    .filter((shape): shape is TLShape =>
      Boolean(shape && editor.isShapeInPage(shape)),
    );
  if (found.length !== scope.ids.length)
    throw new CanvasReadError(
      "scope_not_found",
      "One or more requested Canvas shapes were not found on the current page.",
    );
  const ids = found.map((shape) => shape.id);
  return scope.include_descendants ? descendants(editor, ids) : new Set(ids);
};

const truncateText = (value: string, maximum = CANVAS_READ_MAX_TEXT) =>
  value.length <= maximum ? value : `${value.slice(0, maximum)}…`;

const extractText = (editor: Editor, value: unknown): string => {
  if (typeof value === "string") return truncateText(value.trim());
  if (!isObject(value) || value.type !== "doc") return "";
  return truncateText(
    renderPlaintextFromRichText(editor, value as TLRichText).trim(),
  );
};

const boundedJsonValue = (value: unknown) => {
  try {
    const serialized = JSON.stringify(value);
    return serialized.length <= CANVAS_READ_MAX_FULL_VALUE
      ? value
      : {
          truncated: true,
          preview: serialized.slice(0, CANVAS_READ_MAX_FULL_VALUE),
        };
  } catch {
    return { truncated: true };
  }
};

const standardProps = (shape: TLShape) => {
  const props = shape.props as Record<string, unknown>;
  const allowed = new Set([
    "w",
    "h",
    "geo",
    "color",
    "fill",
    "dash",
    "size",
    "font",
    "align",
    "verticalAlign",
    "autoSize",
    "start",
    "end",
    "arrowheadStart",
    "arrowheadEnd",
    "name",
  ]);
  return Object.fromEntries(
    Object.entries(props).filter(([key]) => allowed.has(key)),
  );
};

const shapeResult = (
  editor: Editor,
  shape: TLShape,
  detail: CanvasReadDetail,
) => {
  const bounds = editor.getShapePageBounds(shape);
  const props = shape.props as Record<string, unknown>;
  const text = extractText(
    editor,
    props.richText ?? props.text ?? props.name ?? "",
  );
  const semantic = semanticReadSummary(shape);
  const compact = {
    id: shape.id,
    type: shape.type,
    parent_id: shape.parentId,
    index: shape.index,
    ...(bounds
      ? {
          page_bounds: {
            x: bounds.x,
            y: bounds.y,
            width: bounds.w,
            height: bounds.h,
          },
        }
      : {}),
    ...(text ? { text } : {}),
    ...(Object.keys(semantic).length ? { semantic } : {}),
  };
  if (detail === "compact") return compact;
  const standard = {
    ...compact,
    x: shape.x,
    y: shape.y,
    rotation: shape.rotation,
    props: standardProps(shape),
  };
  if (detail === "standard") return standard;
  return {
    ...standard,
    props: boundedJsonValue(shape.props),
    meta: boundedJsonValue(shape.meta),
  };
};

const bindingResult = (
  binding: TLBinding,
  includedIds: Set<TLShapeId>,
  detail: CanvasReadDetail,
) => {
  const result = {
    id: binding.id,
    type: binding.type,
    from: {
      id: binding.fromId,
      external_to_scope: !includedIds.has(binding.fromId),
    },
    to: {
      id: binding.toId,
      external_to_scope: !includedIds.has(binding.toId),
    },
  };
  if (detail === "compact") return result;
  const props = binding.props as unknown as Record<string, unknown>;
  if (detail === "standard") {
    const allowed = new Set([
      "terminal",
      "normalizedAnchor",
      "isPrecise",
      "isExact",
      "snap",
    ]);
    return {
      ...result,
      props: Object.fromEntries(
        Object.entries(props).filter(([key]) => allowed.has(key)),
      ),
    };
  }
  return { ...result, props: boundedJsonValue(props) };
};

export const readCanvasScene = (
  editor: Editor,
  rawRequest: Record<string, unknown>,
) => {
  const request = normalizeCanvasReadRequest(rawRequest);
  const resolvedIds = resolveScopeIds(editor, request.scope);
  const ordered = editor
    .getCurrentPageShapesSorted()
    .filter((shape) => resolvedIds.has(shape.id));
  const totalShapes = ordered.length;
  const returned = ordered.slice(0, CANVAS_READ_MAX_SHAPES);
  const includedIds = new Set(returned.map((shape) => shape.id));
  const bindings = new Map<string, TLBinding>();
  for (const shape of returned) {
    for (const binding of editor.getBindingsInvolvingShape(shape))
      bindings.set(binding.id, binding);
  }
  const allBindings = Array.from(bindings.values());
  const returnedBindings = allBindings.slice(0, CANVAS_READ_MAX_BINDINGS);
  const truncated =
    totalShapes > returned.length ||
    allBindings.length > returnedBindings.length;
  return {
    request,
    shapesForExport: returned,
    result: {
      page_id: editor.getCurrentPageId(),
      scope: request.scope,
      detail: request.detail,
      truncated,
      total_shapes: totalShapes,
      returned_shapes: returned.length,
      total_bindings: allBindings.length,
      returned_bindings: returnedBindings.length,
      ...(truncated
        ? { suggested_scope: "Use frame, viewport, bounds, or shape_ids." }
        : {}),
      shapes: returned.map((shape) =>
        shapeResult(editor, shape, request.detail),
      ),
      bindings: returnedBindings.map((binding) =>
        bindingResult(binding, includedIds, request.detail),
      ),
    },
  };
};
