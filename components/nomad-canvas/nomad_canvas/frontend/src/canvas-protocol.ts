import { Editor, TLShapeId, createShapeId, toRichText } from "tldraw";

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
type ShapeType = "geo" | "text" | "note" | "frame";
type Target = { id: string } | { ref: string };
type PlannedShape = {
  id: TLShapeId;
  type: ShapeType | "arrow";
  availableAt: number;
};
type NormalizedOperation =
  | {
      op: "create";
      id: TLShapeId;
      ref: string;
      shape: JsonObject;
      resize?: { width: number; height: number };
    }
  | { op: "update"; id: TLShapeId; type: string; update: JsonObject }
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
    };

export type NormalizedPatchPlan = Readonly<{
  commandId: string;
  operations: readonly NormalizedOperation[];
  refs: Readonly<Record<string, TLShapeId>>;
}>;

export const CANVAS_PATCH_MAX_CHANGED_IDS = 500;

const SHAPE_TYPES = new Set<ShapeType>(["geo", "text", "note", "frame"]);
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
      message: `${path} must contain exactly one id or ref.`,
    });
    return undefined;
  }
  rejectUnknownKeys(
    value,
    new Set(["id", "ref"]),
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
  if ((id ? 1 : 0) + (ref ? 1 : 0) !== 1) {
    errors.push({
      operation_index: operationIndex,
      path,
      code: "invalid_operation",
      message: `${path} must contain exactly one non-empty id or ref.`,
    });
    return undefined;
  }
  return id ? { id } : { ref: ref! };
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
  rawOperations.forEach((raw, index) => {
    if (!isObject(raw)) return;
    const op = raw.op;
    if (op !== "create" && op !== "connect") return;
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
        : isObject(raw.shape)
          ? raw.shape.type
          : undefined;
    const id = createShapeId(`${safeRef(commandId)}-${safeRef(ref)}-${index}`);
    plannedByRef.set(ref, {
      id,
      type: SHAPE_TYPES.has(type as ShapeType) ? (type as ShapeType) : "arrow",
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
  const unavailableIds = new Set<TLShapeId>();

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

  const normalized: NormalizedOperation[] = [];
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
      if (!SHAPE_TYPES.has(type as ShapeType)) {
        errors.push({
          operation_index: index,
          path: "shape.type",
          code: "unsupported_shape_type",
          message: `Unsupported shape type: ${String(type)}`,
        });
        return;
      }
      const shapeType = type as ShapeType;
      const shapeKeys: Record<ShapeType, Set<string>> = {
        geo: new Set(["type", "x", "y", "width", "height", "text", "style"]),
        text: new Set(["type", "x", "y", "width", "text", "style"]),
        note: new Set(["type", "x", "y", "width", "height", "text", "style"]),
        frame: new Set(["type", "x", "y", "width", "height", "name", "style"]),
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
      const shapeText =
        shape.text === undefined
          ? undefined
          : boundedString(shape.text, index, "shape.text", errors, 20_000);
      const shapeName =
        shape.name === undefined
          ? undefined
          : boundedString(shape.name, index, "shape.name", errors, 500);
      if (shapeType === "text" && shape.text === undefined) {
        errors.push({
          operation_index: index,
          path: "shape.text",
          code: "invalid_operation",
          message: "Text shapes require text.",
        });
      }
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
      } else {
        props.w = width ?? 320;
        props.h = height ?? 180;
        props.name = shapeName ?? "";
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
          meta: { source: "codex", logicalRef: ref },
        },
        ...(shapeType === "note" && width !== undefined && height !== undefined
          ? { resize: { width, height } }
          : {}),
      });
      return;
    }

    if (op === "delete") {
      rejectUnknownKeys(raw, new Set(["op", "targets"]), index, "", errors);
      const targets = Array.isArray(raw.targets) ? raw.targets : [];
      const ids = targets
        .map(
          (item, targetIndex) =>
            resolveTarget(item, index, `targets.${targetIndex}`)?.id,
        )
        .filter((id): id is TLShapeId => Boolean(id));
      if (!targets.length)
        errors.push({
          operation_index: index,
          path: "targets",
          code: "invalid_operation",
          message: "delete requires at least one target.",
        });
      if (targets.length > 100)
        errors.push({
          operation_index: index,
          path: "targets",
          code: "invalid_operation",
          message: "delete accepts at most 100 targets.",
        });
      normalized.push({ op: "delete", ids });
      ids.forEach((id) => unavailableIds.add(id));
      return;
    }

    if (op === "connect") {
      rejectUnknownKeys(
        raw,
        new Set(["op", "ref", "from", "to", "text", "style"]),
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
      if (!hasDirectUpdate && !hasStyleUpdate) {
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
    if (raw.x !== undefined) update.x = finiteNumber(raw.x, index, "x", errors);
    if (raw.y !== undefined) update.y = finiteNumber(raw.y, index, "y", errors);
    const props = normalizeStyle(raw.style, shapeType, index, "style", errors);
    if (raw.text !== undefined) {
      if (shapeType === "frame")
        errors.push({
          operation_index: index,
          path: "text",
          code: "target_type_mismatch",
          message: "Frame shapes use name instead of text.",
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
      if (shapeType === "note")
        errors.push({
          operation_index: index,
          path: "width",
          code: "target_type_mismatch",
          message: "Use resize for note dimensions.",
        });
      else props.w = finiteNumber(raw.width, index, "width", errors, true);
    }
    if (raw.height !== undefined) {
      const height = finiteNumber(raw.height, index, "height", errors, true);
      if (shapeType === "note")
        errors.push({
          operation_index: index,
          path: "height",
          code: "target_type_mismatch",
          message: "Use resize for note dimensions.",
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
    normalized.push({ op: "update", id: target.id, type: target.type, update });
  });

  const changedIds = new Set<TLShapeId>();
  for (const operation of normalized) {
    if (operation.op === "delete") {
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
  const mark = editor.markHistoryStoppingPoint(
    `canvas:${safeRef(plan.commandId)}`,
  );
  try {
    editor.run(() => {
      for (const operation of plan.operations) {
        if (operation.op === "create") {
          editor.createShape({ id: operation.id, ...operation.shape } as never);
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
            meta: { source: "codex", logicalRef: operation.ref },
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
        } else {
          editor.updateShape({
            id: operation.id,
            type: operation.type,
            ...operation.update,
          } as never);
          changedIds.add(operation.id);
        }
      }
    });
  } catch (error) {
    let rollbackMessage = "";
    try {
      editor.bailToMark(mark);
    } catch (rollbackError) {
      rollbackMessage = ` Rollback also failed: ${rollbackError instanceof Error ? rollbackError.message : String(rollbackError)}`;
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
      warnings: [] as string[],
    },
    rollback: () => editor.bailToMark(mark),
  };
};
