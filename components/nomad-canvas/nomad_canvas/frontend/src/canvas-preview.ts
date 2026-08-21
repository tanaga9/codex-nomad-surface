import { Editor, TLShape, getSvgAsImage } from "tldraw";

const CANVAS_VISION_MAX_DIMENSION = 1536;
const CANVAS_VISION_MAX_BYTES = 8 * 1024 * 1024;
const CANVAS_VISION_LOSSY_QUALITY = 0.9;
const CANVAS_VISION_EXPORT_ATTEMPTS = 3;
const CANVAS_EXPORT_PADDING = 32;

const blobToDataUrl = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () =>
      reject(reader.error || new Error("Image export failed."));
    reader.readAsDataURL(blob);
  });

type SceneSvgExport = {
  svg: string;
  width: number;
  height: number;
};

const renderSceneImageBlob = async (
  exported: SceneSvgExport,
  pixelRatio: number,
) => {
  const formats = [
    {
      type: "webp",
      mimeType: "image/webp",
      quality: CANVAS_VISION_LOSSY_QUALITY,
    },
    {
      type: "jpeg",
      mimeType: "image/jpeg",
      quality: CANVAS_VISION_LOSSY_QUALITY,
    },
    { type: "png", mimeType: "image/png", quality: undefined },
  ] as const;

  for (const format of formats) {
    try {
      const image = await getSvgAsImage(exported.svg, {
        type: format.type,
        width: exported.width,
        height: exported.height,
        ...(format.quality === undefined ? {} : { quality: format.quality }),
        pixelRatio,
      });
      if (image?.type === format.mimeType) return image;
    } catch {
      // Retry the same export using the next preferred image format.
    }
  }

  throw new Error("Could not construct canvas image as WebP, JPEG, or PNG.");
};

const renderSceneImage = async (
  exported: SceneSvgExport | undefined,
  maxDimension: number,
) => {
  if (!exported) {
    return {
      preview_image_url: "",
      preview_image_width: 0,
      preview_image_height: 0,
    };
  }

  let pixelRatio = Math.min(
    1,
    maxDimension / Math.max(exported.width, exported.height),
  );
  for (let attempt = 0; attempt < CANVAS_VISION_EXPORT_ATTEMPTS; attempt += 1) {
    const image = await renderSceneImageBlob(exported, pixelRatio);
    if (image.size <= CANVAS_VISION_MAX_BYTES) {
      return {
        preview_image_url: await blobToDataUrl(image),
        preview_image_width: Math.round(exported.width * pixelRatio),
        preview_image_height: Math.round(exported.height * pixelRatio),
      };
    }
    const sizeRatio = Math.sqrt(CANVAS_VISION_MAX_BYTES / image.size) * 0.95;
    pixelRatio *= Math.min(0.9, sizeRatio);
  }
  throw new Error("Canvas image remains too large after downscaling.");
};

const renderSceneImageSafely = async (
  exported: SceneSvgExport | undefined,
  maxDimension: number,
) => {
  try {
    return await renderSceneImage(exported, maxDimension);
  } catch (error) {
    return {
      preview_image_url: "",
      preview_image_width: 0,
      preview_image_height: 0,
      preview_image_error:
        error instanceof Error ? error.message : "Canvas image export failed.",
    };
  }
};

export const renderCanvasPreview = async (
  editor: Editor,
  shapes: TLShape[] = editor.getCurrentPageShapes(),
  maxImageDimension = CANVAS_VISION_MAX_DIMENSION,
) => {
  let exported: SceneSvgExport | undefined;
  try {
    exported = shapes.length
      ? await editor.getSvgString(shapes, {
          background: true,
          padding: CANVAS_EXPORT_PADDING,
        })
      : undefined;
  } catch (error) {
    return {
      preview_svg: "",
      preview_image_url: "",
      preview_image_width: 0,
      preview_image_height: 0,
      preview_image_error:
        error instanceof Error ? error.message : "Canvas SVG export failed.",
    };
  }
  return {
    preview_svg: exported?.svg || "",
    ...(await renderSceneImageSafely(exported, maxImageDimension)),
  };
};
