import { shouldReconnectTextSocket } from "./text-connection-policy";
import { applyTextOperations } from "./text-patch";

const assertEqual = (actual: unknown, expected: unknown, label: string) => {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
};

const assertThrows = (callback: () => void, error: string, label: string) => {
  try {
    callback();
  } catch (caught) {
    assertEqual(caught instanceof Error ? caught.message : String(caught), error, label);
    return;
  }
  throw new Error(`${label}: expected an error`);
};

const base = "alpha\nbeta\ngamma";
assertEqual(
  applyTextOperations(base, [
    {
      type: "insert_after",
      from_line: 1,
      to_line: 1,
      old_text: "alpha",
      new_text: "\ninserted",
    },
    {
      type: "replace",
      from_line: 3,
      to_line: 3,
      old_text: "gamma",
      new_text: "GAMMA",
    },
  ]),
  "alpha\ninserted\nbeta\nGAMMA",
  "operations resolve against the same base document",
);

assertThrows(
  () => applyTextOperations(base, [
    { type: "replace", from_line: 2, to_line: 2, old_text: "beta", new_text: "B" },
    { type: "delete", from_line: 2, to_line: 2, old_text: "beta", new_text: "" },
  ]),
  "overlapping_operations",
  "overlapping operations fail closed",
);

assertThrows(
  () => applyTextOperations(base, [
    { type: "replace_document", old_text: base, new_text: "new" },
    { type: "replace", from_line: 1, to_line: 1, old_text: "alpha", new_text: "A" },
  ]),
  "replace_document_must_be_only_operation",
  "replace_document cannot be combined",
);

assertThrows(
  () => applyTextOperations(base, [
    { type: "replace", from_line: 1, to_line: 1, old_text: "", new_text: "A" },
  ]),
  "old_text_ambiguous",
  "empty old_text is ambiguous in a non-empty line",
);

assertThrows(
  () => applyTextOperations(base, [
    { type: "replace", old_text: "alpha", new_text: "A" },
  ]),
  "invalid_line_range",
  "targeted operations require from_line",
);

assertThrows(
  () => applyTextOperations("aaa", [
    { type: "replace", from_line: 1, to_line: 1, old_text: "aa", new_text: "A" },
  ]),
  "old_text_ambiguous",
  "overlapping old_text matches are ambiguous",
);

assertEqual(
  applyTextOperations("alpha\n\nbeta", [
    { type: "replace", from_line: 2, to_line: 2, old_text: "", new_text: "inserted" },
  ]),
  "alpha\ninserted\nbeta",
  "empty lines provide one zero-width insertion point",
);

assertThrows(
  () => applyTextOperations("", [
    { type: "replace", from_line: 1, to_line: 1, old_text: "", new_text: "A" },
    { type: "insert_before", from_line: 1, to_line: 1, old_text: "", new_text: "B" },
  ]),
  "overlapping_operations",
  "multiple insertions at one zero-width point fail closed",
);

assertEqual(shouldReconnectTextSocket(1006, false), true, "unexpected disconnect reconnects");
assertEqual(shouldReconnectTextSocket(4001, false), false, "replacement does not reconnect");
assertEqual(shouldReconnectTextSocket(1000, true), false, "destroyed component does not reconnect");
