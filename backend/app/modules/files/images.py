"""Bounded image decoding and display variants; the original bytes stay untouched."""

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class InvalidImage(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedImage:
    width: int
    height: int
    sha256: str
    reading: bytes
    thumbnail: bytes


def _jpeg(image: Image.Image, edge: int) -> bytes:
    display = image.copy()
    display.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    if display.mode != "RGB":
        background = Image.new("RGB", display.size, "white")
        if display.mode in {"RGBA", "LA"} or "transparency" in display.info:
            display = display.convert("RGBA")
            background.paste(display, mask=display.getchannel("A"))
        else:
            background.paste(display.convert("RGB"))
        display = background
    output = io.BytesIO()
    display.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def process_image(data: bytes, mime: str, max_pixels: int) -> ProcessedImage:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if FORMATS.get(image.format) != mime:
                    raise InvalidImage("image_type_mismatch")
                if image.width * image.height > max_pixels:
                    raise InvalidImage("image_pixels_exceeded")
                if getattr(image, "n_frames", 1) != 1:
                    raise InvalidImage("animated_image_unsupported")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                display = ImageOps.exif_transpose(image)
                return ProcessedImage(
                    width=display.width,
                    height=display.height,
                    sha256=hashlib.sha256(data).hexdigest(),
                    reading=_jpeg(display, 2400),
                    thumbnail=_jpeg(display, 400),
                )
    except InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidImage("image_invalid") from exc
