import {
  AssetRecordType,
  Editor,
  getAssetInfo,
  type JsonObject,
  type TLAsset,
  type TLAssetStore,
} from "tldraw";

export const CANVAS_IMAGE_SOURCE_MAX_BYTES = 10 * 1024 * 1024;
export const CANVAS_IMAGE_MAX_DIMENSION = 2_048;
export const CANVAS_IMAGE_MAX_FILES_AT_ONCE = 2;
export const CANVAS_IMAGE_MIME_TYPES = [
  "image/jpeg",
  "image/png",
  "image/webp",
] as const;

type CanvasAssetUploadResult = {
  src: string;
  name: string;
  mime_type: "image/webp";
  content_hash: string;
  byte_size: number;
  original_byte_size: number;
  width: number;
  height: number;
  quality: number;
};

const isObject = (value: unknown): value is JsonObject =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const uploadResultFromMeta = (
  src: string,
  meta: JsonObject | undefined,
): CanvasAssetUploadResult => {
  const nomad = isObject(meta?.nomad) ? meta.nomad : undefined;
  const result = {
    src,
    name: nomad?.name,
    mime_type: nomad?.mime_type,
    content_hash: nomad?.content_hash,
    byte_size: nomad?.byte_size,
    original_byte_size: nomad?.original_byte_size,
    width: nomad?.width,
    height: nomad?.height,
    quality: nomad?.quality,
  };
  const contentHash =
    typeof result.content_hash === "string" &&
    /^sha256:[0-9a-f]{64}$/.test(result.content_hash)
      ? result.content_hash.slice("sha256:".length)
      : "";
  if (
    typeof result.src !== "string" ||
    !/^\/api\/canvas\/canvas-[0-9a-f]{24}\/assets\/[0-9a-f]{64}\.webp$/.test(
      result.src,
    ) ||
    !contentHash ||
    !result.src.endsWith(`/${contentHash}.webp`) ||
    typeof result.name !== "string" ||
    !result.name.toLowerCase().endsWith(".webp") ||
    result.mime_type !== "image/webp" ||
    typeof result.byte_size !== "number" ||
    result.byte_size <= 0 ||
    result.byte_size > 768 * 1024 ||
    typeof result.original_byte_size !== "number" ||
    result.original_byte_size <= 0 ||
    result.original_byte_size > CANVAS_IMAGE_SOURCE_MAX_BYTES ||
    typeof result.width !== "number" ||
    result.width <= 0 ||
    result.width > CANVAS_IMAGE_MAX_DIMENSION ||
    typeof result.height !== "number" ||
    result.height <= 0 ||
    result.height > CANVAS_IMAGE_MAX_DIMENSION ||
    typeof result.quality !== "number" ||
    result.quality <= 0 ||
    result.quality > 100
  ) {
    throw new Error("The optimized image metadata is invalid.");
  }
  return result as CanvasAssetUploadResult;
};

export const normalizeUploadedCanvasImageAsset = (
  asset: TLAsset,
  result: CanvasAssetUploadResult,
): TLAsset => {
  if (asset.type !== "image") {
    throw new Error("Canvas accepts image assets only.");
  }
  const { pixelRatio: _originalPixelRatio, ...originalProps } = asset.props;
  return AssetRecordType.create({
    ...asset,
    props: {
      ...originalProps,
      src: result.src,
      name: result.name,
      w: result.width,
      h: result.height,
      fileSize: result.byte_size,
      mimeType: result.mime_type,
      isAnimated: false,
    },
    meta: {
      ...asset.meta,
      nomad: {
        content_hash: result.content_hash,
        byte_size: result.byte_size,
        original_byte_size: result.original_byte_size,
        width: result.width,
        height: result.height,
        quality: result.quality,
        optimized: true,
      },
    },
  });
};

export const registerCanvasImageAssetHandler = (editor: Editor): void => {
  editor.registerExternalAssetHandler("file", async ({ file, assetId }) => {
    const asset = await getAssetInfo(editor, file, assetId);
    if (!asset || asset.type !== "image") {
      throw new Error("Canvas accepts static image assets only.");
    }
    const uploaded = await editor.uploadAsset(asset, file);
    return normalizeUploadedCanvasImageAsset(
      asset,
      uploadResultFromMeta(uploaded.src, uploaded.meta),
    );
  });
};

const responseMessage = async (response: Response): Promise<string> => {
  try {
    const payload = (await response.json()) as { message?: unknown };
    if (typeof payload.message === "string" && payload.message) {
      return payload.message;
    }
  } catch {
    // The status text below remains a useful bounded fallback.
  }
  return response.statusText || "The image could not be added.";
};

export const createCanvasAssetStore = (
  canvasId: string,
  onError: (message: string) => void,
): TLAssetStore => ({
  async upload(_asset, file, abortSignal) {
    if (file.size > CANVAS_IMAGE_SOURCE_MAX_BYTES) {
      const message = "The original image exceeds the 10 MiB Canvas limit.";
      onError(message);
      throw new Error(message);
    }
    try {
      const response = await fetch(`/api/canvas/${canvasId}/assets`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": file.type,
          "X-Nomad-Filename": encodeURIComponent(file.name),
        },
        body: file,
        signal: abortSignal,
      });
      if (!response.ok) throw new Error(await responseMessage(response));
      const result = (await response.json()) as CanvasAssetUploadResult;
      onError("");
      return {
        src: result.src,
        meta: {
          nomad: {
            name: result.name,
            mime_type: result.mime_type,
            content_hash: result.content_hash,
            byte_size: result.byte_size,
            original_byte_size: result.original_byte_size,
            width: result.width,
            height: result.height,
            quality: result.quality,
          },
        },
      };
    } catch (error) {
      if (abortSignal?.aborted) throw error;
      const message =
        error instanceof Error
          ? error.message
          : "The image could not be added.";
      onError(message);
      throw error;
    }
  },
  resolve(asset) {
    return "src" in asset.props && typeof asset.props.src === "string"
      ? asset.props.src
      : null;
  },
});
