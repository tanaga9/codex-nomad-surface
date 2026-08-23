export type TextOperation = {
  type: "replace" | "insert_before" | "insert_after" | "delete" | "replace_document";
  from_line?: number;
  to_line?: number;
  old_text: string;
  new_text: string;
};

type ResolvedChange = { from: number; to: number; insert: string };

const lineWindow = (
  content: string,
  operation: TextOperation,
): { from: number; to: number } => {
  const lines = content.split("\n");
  if (operation.from_line === undefined) throw new Error("invalid_line_range");
  const fromLine = Number(operation.from_line);
  const toLine = Number(operation.to_line ?? fromLine);
  if (
    !Number.isInteger(fromLine) ||
    !Number.isInteger(toLine) ||
    fromLine < 1 ||
    toLine < fromLine ||
    fromLine > lines.length
  ) {
    throw new Error("invalid_line_range");
  }
  const boundedToLine = Math.min(toLine, lines.length);
  const from = lines.slice(0, fromLine - 1).join("\n").length + (fromLine > 1 ? 1 : 0);
  const to = lines.slice(0, boundedToLine).join("\n").length;
  return { from, to };
};

const resolveOperation = (
  content: string,
  operation: TextOperation,
): ResolvedChange => {
  if (!operation || typeof operation !== "object") {
    throw new Error("invalid_operation");
  }
  if (typeof operation.old_text !== "string" || typeof operation.new_text !== "string") {
    throw new Error("invalid_operation_text");
  }
  if (operation.type === "replace_document") {
    if (operation.old_text !== content) throw new Error("old_text_mismatch");
    return { from: 0, to: content.length, insert: operation.new_text };
  }
  if (!["replace", "insert_before", "insert_after", "delete"].includes(operation.type)) {
    throw new Error("unsupported_operation");
  }

  const window = lineWindow(content, operation);
  const windowText = content.slice(window.from, window.to);
  const matches: number[] = [];
  if (operation.old_text.length === 0) {
    if (windowText.length > 0) throw new Error("old_text_ambiguous");
    matches.push(0);
  } else {
    let index = windowText.indexOf(operation.old_text);
    while (index >= 0) {
      matches.push(index);
      index = windowText.indexOf(operation.old_text, index + 1);
    }
  }
  if (matches.length !== 1) {
    throw new Error(matches.length ? "old_text_ambiguous" : "old_text_mismatch");
  }
  const from = window.from + matches[0];
  const to = from + operation.old_text.length;
  const insert =
    operation.type === "delete"
      ? ""
      : operation.type === "insert_before"
        ? operation.new_text + operation.old_text
        : operation.type === "insert_after"
          ? operation.old_text + operation.new_text
          : operation.new_text;
  if (from === to && insert.length === 0) throw new Error("no_effect_operation");
  return { from, to, insert };
};

export const applyTextOperations = (
  content: string,
  operations: TextOperation[],
): string => {
  if (!Array.isArray(operations) || operations.length === 0) {
    throw new Error("invalid_operations");
  }
  if (
    operations.some((operation) => operation.type === "replace_document") &&
    operations.length !== 1
  ) {
    throw new Error("replace_document_must_be_only_operation");
  }

  const changes = operations.map((operation) => resolveOperation(content, operation));
  changes.sort((left, right) => right.from - left.from || right.to - left.to);
  for (let index = 1; index < changes.length; index += 1) {
    const later = changes[index - 1];
    const earlier = changes[index];
    const sameZeroWidthPoint =
      earlier.from === earlier.to &&
      later.from === later.to &&
      earlier.from === later.from;
    if (earlier.to > later.from || sameZeroWidthPoint) {
      throw new Error("overlapping_operations");
    }
  }

  let result = content;
  for (const change of changes) {
    result = result.slice(0, change.from) + change.insert + result.slice(change.to);
  }
  return result;
};
