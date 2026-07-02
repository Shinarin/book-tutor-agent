import base64
import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Optional, Tuple


_MIME_EXT_OVERRIDES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "image/svg+xml": ".svg",
}


def _ext_for_mime(content_type: Optional[str]) -> str:
    if not content_type:
        return ".bin"
    ct = content_type.split(";")[0].strip().lower()
    if ct in _MIME_EXT_OVERRIDES:
        return _MIME_EXT_OVERRIDES[ct]
    guessed = mimetypes.guess_extension(ct)
    return guessed or ".bin"


def save_image_blob(
    blob: bytes,
    content_type: Optional[str],
    save_dir: str,
    md_output_dir: Optional[str] = None,
) -> str:
    """Write image bytes to <save_dir>/<sha1[:12]><ext>, idempotently.

    Returns a path suitable for use inside a Markdown image reference: relative
    to md_output_dir when provided, otherwise an absolute POSIX path.
    """
    digest = hashlib.sha1(blob).hexdigest()[:12]
    ext = _ext_for_mime(content_type)
    filename = f"{digest}{ext}"

    save_dir_abs = os.path.abspath(save_dir)
    os.makedirs(save_dir_abs, exist_ok=True)
    file_path = os.path.join(save_dir_abs, filename)

    if not os.path.exists(file_path):
        with open(file_path, "wb") as f:
            f.write(blob)

    if md_output_dir:
        try:
            rel = os.path.relpath(file_path, os.path.abspath(md_output_dir))
            return Path(rel).as_posix()
        except ValueError:
            # Different drive on Windows — fall back to absolute path.
            pass

    return Path(file_path).as_posix()


def decode_data_uri(src: str) -> Optional[Tuple[bytes, Optional[str]]]:
    """Decode a base64 ``data:`` URI to (blob, content_type). Returns None if
    the URI isn't a base64 data URI we can decode."""
    if not src.startswith("data:"):
        return None
    try:
        header, payload = src[5:].split(",", 1)
    except ValueError:
        return None
    parts = header.split(";")
    content_type = parts[0] or None
    if "base64" not in parts[1:]:
        return None
    try:
        return base64.b64decode(payload), content_type
    except Exception:
        return None
