import {
  Editor,
  parseTldrawJsonFile,
  serializeTldrawJson,
  type TLStore,
  type TLSchema,
  type TldrawFile,
} from "tldraw";

export const OBSIDIAN_TLDRAW_PLUGIN_VERSION = "1.31.0";
export const OBSIDIAN_TLDRAW_START_MARKER =
  "!!!_START_OF_TLDRAW_DATA__DO_NOT_CHANGE_THIS_PHRASE_!!!";
export const OBSIDIAN_TLDRAW_END_MARKER =
  "!!!_END_OF_TLDRAW_DATA__DO_NOT_CHANGE_THIS_PHRASE_!!!";

export type ObsidianTldrawMetadata = {
  uuid: string;
  "plugin-version": string;
  "tldraw-version": string;
};

export type ObsidianTldrawDocument = {
  meta: ObsidianTldrawMetadata;
  raw: TldrawFile;
};

export class ObsidianTldrawFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ObsidianTldrawFormatError";
  }
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const requireString = (value: unknown, path: string): string => {
  if (typeof value !== "string" || !value.trim()) {
    throw new ObsidianTldrawFormatError(`${path} must be a non-empty string.`);
  }
  return value;
};

export const formatObsidianTldrawMarkdown = (
  raw: TldrawFile,
  metadata: ObsidianTldrawMetadata,
): string => {
  const payload: ObsidianTldrawDocument = {
    meta: metadata,
    raw,
  };

  return [
    "---",
    "tldraw-file: true",
    "tags:",
    "  - tldraw",
    "---",
    "",
    "",
    `\`\`\`json ${OBSIDIAN_TLDRAW_START_MARKER}`,
    JSON.stringify(payload, null, "\t"),
    OBSIDIAN_TLDRAW_END_MARKER,
    "```",
    "",
  ].join("\n");
};

export const parseObsidianTldrawMarkdown = (
  markdown: string,
): ObsidianTldrawDocument => {
  const startMarkerIndex = markdown.indexOf(OBSIDIAN_TLDRAW_START_MARKER);
  if (startMarkerIndex < 0) {
    throw new ObsidianTldrawFormatError(
      "The Obsidian tldraw start marker was not found.",
    );
  }

  const jsonStartIndex = markdown.indexOf("\n", startMarkerIndex);
  const endMarkerIndex = markdown.indexOf(
    OBSIDIAN_TLDRAW_END_MARKER,
    jsonStartIndex + 1,
  );
  if (jsonStartIndex < 0 || endMarkerIndex < 0) {
    throw new ObsidianTldrawFormatError(
      "The Obsidian tldraw data block is incomplete.",
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(markdown.slice(jsonStartIndex + 1, endMarkerIndex));
  } catch (error) {
    throw new ObsidianTldrawFormatError(
      `The Obsidian tldraw JSON is invalid: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  if (!isObject(parsed) || !isObject(parsed.meta) || !isObject(parsed.raw)) {
    throw new ObsidianTldrawFormatError(
      "The Obsidian tldraw document must contain meta and raw objects.",
    );
  }

  const metadata: ObsidianTldrawMetadata = {
    uuid: requireString(parsed.meta.uuid, "meta.uuid"),
    "plugin-version": requireString(
      parsed.meta["plugin-version"],
      "meta.plugin-version",
    ),
    "tldraw-version": requireString(
      parsed.meta["tldraw-version"],
      "meta.tldraw-version",
    ),
  };
  if (
    typeof parsed.raw.tldrawFileFormatVersion !== "number" ||
    !isObject(parsed.raw.schema) ||
    !Array.isArray(parsed.raw.records)
  ) {
    throw new ObsidianTldrawFormatError(
      "The raw object is not a recognizable tldraw file.",
    );
  }

  return {
    meta: metadata,
    raw: parsed.raw as unknown as TldrawFile,
  };
};

export const parseObsidianTldrawStore = (
  markdown: string,
  schema: TLSchema,
): TLStore => {
  const document = parseObsidianTldrawMarkdown(markdown);
  const result = parseTldrawJsonFile({
    json: JSON.stringify(document.raw),
    schema,
  });
  if (!result.ok) {
    throw new ObsidianTldrawFormatError(
      `The embedded tldraw file could not be loaded: ${result.error.type}.`,
    );
  }
  return result.value;
};

export const serializeObsidianTldrawMarkdown = async (
  editor: Editor,
  uuid: string,
): Promise<string> => {
  const raw = JSON.parse(await serializeTldrawJson(editor)) as TldrawFile;
  return formatObsidianTldrawMarkdown(raw, {
    uuid,
    "plugin-version": OBSIDIAN_TLDRAW_PLUGIN_VERSION,
    "tldraw-version": __TLDRAW_VERSION__,
  });
};

export const downloadObsidianTldrawMarkdown = async (
  editor: Editor,
  uuid: string,
  filename: string,
): Promise<void> => {
  const markdown = await serializeObsidianTldrawMarkdown(editor, uuid);
  const url = URL.createObjectURL(
    new Blob([markdown], { type: "text/markdown;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
};
