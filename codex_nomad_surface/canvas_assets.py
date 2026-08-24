from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from codex_nomad_surface.canvas_store import save_canvas_asset


CANVAS_IMAGE_SOURCE_MAX_BYTES = 10 * 1024 * 1024
CANVAS_IMAGE_MAX_PIXELS = 20_000_000
CANVAS_IMAGE_MAX_DIMENSION = 2_048
CANVAS_IMAGE_MIN_DIMENSION = 1_280
CANVAS_IMAGE_OPTIMIZED_MAX_BYTES = 768 * 1024
CANVAS_IMAGE_WEBP_QUALITIES = (80, 72, 64, 56)
CANVAS_IMAGE_SUPPORTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class CanvasImageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NormalizedCanvasImage:
    content: bytes
    width: int
    height: int
    original_byte_size: int
    original_width: int
    original_height: int
    quality: int


def _encode_webp(image: Image.Image, quality: int) -> bytes:
    output = io.BytesIO()
    try:
        image.save(
            output,
            format="WEBP",
            quality=quality,
            method=6,
            exact=True,
        )
    except (KeyError, OSError) as exc:
        raise CanvasImageError(
            "image_encoding_failed", "The image could not be encoded as WebP."
        ) from exc
    return output.getvalue()


def normalize_canvas_image(content: bytes) -> NormalizedCanvasImage:
    if not content:
        raise CanvasImageError("image_empty", "The image file is empty.")
    if len(content) > CANVAS_IMAGE_SOURCE_MAX_BYTES:
        raise CanvasImageError(
            "image_source_too_large",
            "The original image exceeds the 10 MiB Canvas limit.",
        )

    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in CANVAS_IMAGE_SUPPORTED_FORMATS:
                raise CanvasImageError(
                    "image_type_unsupported",
                    "Canvas accepts static JPEG, PNG, and WebP images only.",
                )
            original_width, original_height = source.size
            if (
                original_width <= 0
                or original_height <= 0
                or original_width * original_height > CANVAS_IMAGE_MAX_PIXELS
            ):
                raise CanvasImageError(
                    "image_dimensions_too_large",
                    "The image exceeds the 20 megapixel Canvas limit.",
                )
            if bool(getattr(source, "is_animated", False)) or int(
                getattr(source, "n_frames", 1)
            ) > 1:
                raise CanvasImageError(
                    "image_animation_unsupported",
                    "Animated images are not supported on Canvas.",
                )

            source.load()
            transposed = ImageOps.exif_transpose(source)
            has_alpha = transposed.mode in {"RGBA", "LA"} or (
                transposed.mode == "P" and "transparency" in transposed.info
            )
            image = transposed.convert("RGBA" if has_alpha else "RGB")
    except CanvasImageError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise CanvasImageError(
            "image_decode_failed", "The image is invalid or could not be decoded."
        ) from exc

    image.thumbnail(
        (CANVAS_IMAGE_MAX_DIMENSION, CANVAS_IMAGE_MAX_DIMENSION),
        Image.Resampling.LANCZOS,
    )
    while True:
        for quality in CANVAS_IMAGE_WEBP_QUALITIES:
            optimized = _encode_webp(image, quality)
            if len(optimized) <= CANVAS_IMAGE_OPTIMIZED_MAX_BYTES:
                return NormalizedCanvasImage(
                    content=optimized,
                    width=image.width,
                    height=image.height,
                    original_byte_size=len(content),
                    original_width=original_width,
                    original_height=original_height,
                    quality=quality,
                )

        longest_edge = max(image.size)
        if longest_edge <= CANVAS_IMAGE_MIN_DIMENSION:
            break
        next_edge = max(CANVAS_IMAGE_MIN_DIMENSION, int(longest_edge * 0.85))
        scale = next_edge / longest_edge
        image = image.resize(
            (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )

    raise CanvasImageError(
        "image_optimization_failed",
        "The image could not be reduced below the 768 KiB Canvas limit without excessive quality loss.",
    )


def normalize_and_save_canvas_image(
    canvas_id: str, content: bytes, filename: str = "image"
) -> dict[str, object]:
    normalized = normalize_canvas_image(content)
    digest = hashlib.sha256(normalized.content).hexdigest()
    stored = save_canvas_asset(canvas_id, normalized.content, digest, "webp")
    safe_name = Path(filename).name[:240] or "image"
    output_name = f"{Path(safe_name).stem or 'image'}.webp"
    return {
        **stored,
        "src": f"/api/canvas/{canvas_id}/assets/{digest}.webp",
        "name": output_name,
        "mime_type": "image/webp",
        "width": normalized.width,
        "height": normalized.height,
        "byte_size": len(normalized.content),
        "original_byte_size": normalized.original_byte_size,
        "original_width": normalized.original_width,
        "original_height": normalized.original_height,
        "quality": normalized.quality,
    }
