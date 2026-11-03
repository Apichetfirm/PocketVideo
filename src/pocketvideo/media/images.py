"""Real offline image decoding/encoding for the shared media queue.

Uses Pillow and the binary-wheel libheif codecs from pillow-heif for HEIC/HEIF.
Rejects unsupported animation and prevents silent alpha loss / EXIF rotations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pocketvideo.core.models import EncodingJobSpec, ImagePreset, MediaMetadata

SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".bmp", ".tif", ".tiff"
}
IMAGE_OUTPUT_FORMATS = ("jpeg", "png", "webp", "heic", "avif")

_HEIF_READY = False


def initialize_image_plugins(*, require_heif: bool = False) -> bool:
    """Register available plugins, but never block ordinary JPEG/PNG/WebP images."""
    global _HEIF_READY
    if _HEIF_READY:
        return True
    try:
        import pillow_heif
        pillow_heif.register_heif_opener(thumbnails=False)
        # Some pillow-heif versions omit an AVIF plugin; Pillow itself may
        # provide AVIF. Never make successful HEIF registration depend on it.
        register_avif = getattr(pillow_heif, "register_avif_opener", None)
        if callable(register_avif):
            register_avif(thumbnails=False)
        _HEIF_READY = True
        return True
    except (ImportError, OSError, RuntimeError) as exc:
        if require_heif:
            raise RuntimeError(
                "HEIC/HEIF support is unavailable; check bundled pillow-heif Windows codecs."
            ) from exc
        return False


def _load_pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow image backend is unavailable.") from exc
    return Image, ImageOps


def inspect_image(path: Path) -> MediaMetadata:
    Image, _ = _load_pillow()
    initialize_image_plugins(
        require_heif=Path(path).suffix.lower() in (".heic", ".heif")
    )
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Image is missing: {path}")
    with Image.open(path) as im:
        im.load()
        if getattr(im, "n_frames", 1) > 1:
            raise ValueError("Animated/multi-frame images are not supported; no frames were discarded.")
        w, h = im.size
        fmt = str(im.format or path.suffix.lstrip(".")).lower()
    return MediaMetadata(
        file_path=path,
        container_format=fmt,
        duration_seconds=0.0,
        file_size_bytes=path.stat().st_size,
        overall_bit_rate=0,
        media_kind="image",
        image_width=w,
        image_height=h,
    )


def image_quality_value(preset: ImagePreset, custom: int = 82) -> int:
    if preset == ImagePreset.BEST_QUALITY:
        return 95
    if preset == ImagePreset.SMALLEST:
        return 65
    if preset == ImagePreset.FASTEST:
        return 80
    if preset == ImagePreset.CUSTOM:
        return max(1, min(100, int(custom)))
    return 83


def encode_image(
    spec: EncodingJobSpec,
    staged_output: Path,
) -> tuple[int, int, list[str]]:
    """Encode to the same disk as the output; caller verifies and safely promotes."""
    Image, ImageOps = _load_pillow()
    initialize_image_plugins(
        require_heif=spec.target_container.lower() in ("heic",)
        or spec.input_path.suffix.lower() in (".heic", ".heif")
    )
    src = spec.input_path
    dst_fmt = spec.target_container.lower()
    if dst_fmt not in IMAGE_OUTPUT_FORMATS:
        raise ValueError(f"Unsupported image output format: {dst_fmt}")

    fmt = {
        "jpeg": "JPEG", "png": "PNG", "webp": "WEBP",
        "heic": "HEIF", "avif": "AVIF"
    }[dst_fmt]
    # Pillow loads many native encoders lazily. Enumerating Image.SAVE without
    # Image.init() wrongly reports WEBP unavailable even on a standard wheel.
    Image.init()
    if fmt not in Image.SAVE:
        raise RuntimeError(f"{fmt} image encoder is not available in this offline build.")

    quality = image_quality_value(spec.image_preset, spec.image_quality)
    warnings: list[str] = []
    staged_output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as opened:
        opened.load()
        if getattr(opened, "n_frames", 1) != 1:
            raise ValueError("Animated/multi-frame images require an explicit multi-frame export mode.")
        img = ImageOps.exif_transpose(opened)
        width, height = img.size
        if width <= 0 or height <= 0:
            raise ValueError("Image dimensions are invalid.")
        opts: dict[str, Any] = {}
        if spec.advanced.preserve_metadata:
            exif = img.getexif()
            if exif:
                opts["exif"] = exif.tobytes()
            if img.info.get("icc_profile"):
                opts["icc_profile"] = img.info["icc_profile"]
            if img.info.get("xmp"):
                opts["xmp"] = img.info["xmp"]

        if fmt == "JPEG":
            if img.mode in ("RGBA", "LA", "P") and ("A" in img.getbands() or "transparency" in img.info):
                raise ValueError(
                    "JPEG cannot preserve transparency. Select PNG, WebP, HEIC or AVIF instead."
                )
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            opts.update(quality=quality, optimize=(spec.image_preset != ImagePreset.FASTEST))
        elif fmt == "PNG":
            opts.update(optimize=(spec.image_preset != ImagePreset.FASTEST),
                        compress_level=1 if spec.image_preset == ImagePreset.FASTEST else 9)
            warnings.append("PNG is lossless; image-quality presets change compression speed/size only.")
        elif fmt == "WEBP":
            opts.update(quality=quality, method=1 if spec.image_preset == ImagePreset.FASTEST else 6)
        else:  # HEIC / AVIF via pillow-heif/libheif
            opts["quality"] = quality

        img.save(staged_output, format=fmt, **opts)

    if not staged_output.is_file() or staged_output.stat().st_size < 16:
        raise OSError("Image output is missing, possibly due to USB/HDD disconnect.")
    with Image.open(staged_output) as check:
        check.load()
        if check.size != (width, height):
            raise ValueError("Image output verification failed: resolution changed unexpectedly.")
        if getattr(check, "n_frames", 1) != 1:
            raise ValueError("Image output verification failed: unexpected extra frames.")
    return width, height, warnings


def thumbnail_png(source: Path, destination: Path, size: tuple[int, int] = (176, 100)) -> Path:
    Image, ImageOps = _load_pillow()
    initialize_image_plugins(
        require_heif=source.suffix.lower() in (".heic", ".heif")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as im:
        im.load()
        img = ImageOps.exif_transpose(im)
        img.thumbnail(size)
        img.convert("RGBA" if "A" in img.getbands() else "RGB").save(destination, "PNG")
    return destination
