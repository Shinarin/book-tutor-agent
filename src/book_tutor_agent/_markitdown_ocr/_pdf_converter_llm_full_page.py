"""
PDF Converter that transcribes each page as a whole-page image via a vision
LLM. The LLM emits Markdown with image placeholders carrying pixel bounding
boxes; this module crops those regions out of the rendered page PNG and
rewrites the placeholders to point at the saved crops.

Useful for PDFs whose layout (multi-column, vector charts, mixed figures)
defeats coordinate-ordered text extraction.
"""

import base64
import io
import logging
import os
import re
import sys
from typing import Any, BinaryIO, Optional

from book_tutor_agent._markitdown import DocumentConverter, DocumentConverterResult, StreamInfo
from book_tutor_agent._markitdown._exceptions import (
    MissingDependencyException,
    MISSING_DEPENDENCY_MESSAGE,
)
from book_tutor_agent._markitdown.converters._image_sink import save_image_blob
from ._ocr_service import LLMVisionOCRService
from ._pdf_llm_prompts import (
    GLUE_PROMPT,
    GLUE_SENTINEL,
    PAGE_PROMPT,
    context_preamble,
)

logger = logging.getLogger(__name__)

_dependency_exc_info = None
try:
    import pdfplumber
    from PIL import Image
except ImportError:
    _dependency_exc_info = sys.exc_info()

try:
    import fitz  # PyMuPDF, optional — only used for embedded-image extraction
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False


# Matches ![[x0, y0, x1, y1]](anything)
_BBOX_PLACEHOLDER_RE = re.compile(
    r"!\[\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\]\]\(([^)]*)\)"
)


_ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4.7")
_ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_API_KEY")


class PdfConverterLLMFullPage(DocumentConverter):
    """
    Whole-page LLM transcription PDF converter.

    Each page is rendered to a PNG and handed to a vision LLM, which returns
    Markdown with bbox-annotated image placeholders. This converter then
    crops those bboxes out of the page PNG, saves them as standalone images,
    and rewrites the placeholders.

    Unlike PdfConverterWithOCR, this converter requires an OCR service and
    does not attempt structural text extraction first. It is intended for
    complex layouts where layout-aware extraction is unreliable.
    """

    def __init__(self, ocr_service: Optional[LLMVisionOCRService] = None):
        super().__init__()
        self.ocr_service = ocr_service
        self._fitz_doc: Any = None  # per-convert PyMuPDF handle for raw image extract

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()
        if extension == ".pdf":
            return True
        if mimetype.startswith("application/pdf") or mimetype.startswith(
            "application/x-pdf"
        ):
            return True
        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        if _dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(
                    converter=type(self).__name__,
                    extension=".pdf",
                    feature="pdf",
                )
            ) from _dependency_exc_info[1].with_traceback(
                _dependency_exc_info[2]
            )  # type: ignore[union-attr]

        ocr_service: LLMVisionOCRService | None = (
            kwargs.get("ocr_service") or self.ocr_service
        )
        if ocr_service is None or ocr_service.client is None:
            raise ValueError(
                "PdfConverterLLMFullPage requires a configured LLMVisionOCRService"
            )

        save_images_dir: str | None = kwargs.get("save_images_dir")
        md_output_dir: str | None = kwargs.get("md_output_dir")
        output_md_path: str | None = kwargs.get("output_md_path")
        context_pages: int = int(kwargs.get("context_pages", 1))
        render_dpi: int = int(kwargs.get("render_dpi", 200))
        crop_dpi: int = int(kwargs.get("crop_dpi", 400))
        flush_every: int = int(kwargs.get("flush_every", 10))

        start_page = kwargs.get("start_page")
        end_page = kwargs.get("end_page")
        start_page = int(start_page) if start_page is not None else None
        end_page = int(end_page) if end_page is not None else None

        file_stream.seek(0)
        pdf_bytes = io.BytesIO(file_stream.read())

        if _HAS_FITZ:
            try:
                self._fitz_doc = fitz.open(stream=pdf_bytes.getvalue(), filetype="pdf")
            except Exception as e:
                logger.warning("PyMuPDF open failed, raw image extract disabled: %s", e)
                self._fitz_doc = None
        else:
            self._fitz_doc = None

        settled: list[str] = []
        pending: str | None = None
        prev_page_num: int | None = None
        pdf_text_cache: dict[int, str] = {}
        processed_count = 0

        with pdfplumber.open(pdf_bytes) as pdf:
            total_pages = len(pdf.pages)
            pages_to_do = total_pages
            if start_page is not None:
                pages_to_do -= start_page - 1
            if end_page is not None:
                pages_to_do = min(pages_to_do, end_page - (start_page or 1) + 1)

            for page_num, page in enumerate(pdf.pages, 1):
                if start_page is not None and page_num < start_page:
                    continue
                if end_page is not None and page_num > end_page:
                    break

                logger.warning("processing page %d/%d", page_num, total_pages)

                page_md: str
                try:
                    page_png, page_w, page_h = self._render_page(page, render_dpi)
                    page_pt_w = float(page.width)
                    page_pt_h = float(page.height)
                except Exception as e:
                    logger.warning("page %d: render failed: %s", page_num, e)
                    page_md = f"*[Error rendering page {page_num}: {e}]*"
                else:
                    context_md = self._build_context(
                        settled, pending, context_pages
                    )
                    try:
                        raw_md = self._transcribe_page(
                            ocr_service,
                            page_png,
                            page_num,
                            page_w,
                            page_h,
                            context_md,
                        )
                    except Exception as e:
                        logger.warning("page %d: LLM call failed: %s", page_num, e)
                        page_md = f"*[Error transcribing page {page_num}: {e}]*"
                    else:
                        if not raw_md.strip():
                            page_md = f"*[Empty transcription for page {page_num}]*"
                        else:
                            page_md = self._process_placeholders(
                                raw_md,
                                page,
                                page_png,
                                page_w,
                                page_h,
                                page_pt_w,
                                page_pt_h,
                                page_num,
                                crop_dpi,
                                save_images_dir,
                                md_output_dir,
                            )

                if pending is None:
                    pending = page_md
                else:
                    pdf_a = self._pdf_text_for(pdf, prev_page_num, pdf_text_cache)
                    pdf_b = self._pdf_text_for(pdf, page_num, pdf_text_cache)
                    try:
                        left, right = self._glue_boundary(
                            ocr_service, pending, page_md,
                            pdf_a, pdf_b, prev_page_num, page_num,
                        )
                    except Exception as e:
                        logger.warning(
                            "boundary %d/%d: glue failed (%s); keeping pages unmerged",
                            prev_page_num, page_num, e,
                        )
                        left, right = pending, page_md
                    settled.append(left)
                    pending = right

                prev_page_num = page_num
                processed_count += 1
                if processed_count % flush_every == 0:
                    logger.info(
                        "completed %d/%d pages", processed_count, pages_to_do
                    )
                self._maybe_flush(
                    settled, pending, output_md_path,
                    processed_count, flush_every,
                )

        if pending is not None:
            settled.append(pending)
        markdown = "\n\n".join(s for s in settled if s).strip()
        if output_md_path:
            self._write_output(output_md_path, markdown)

        if self._fitz_doc is not None:
            try:
                self._fitz_doc.close()
            except Exception:
                pass
            self._fitz_doc = None

        return DocumentConverterResult(markdown=markdown)

    @staticmethod
    def _write_output(path: str, markdown: str) -> None:
        import os
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(markdown)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _maybe_flush(
        self,
        settled: list[str],
        pending: str | None,
        output_md_path: str | None,
        processed_count: int,
        flush_every: int,
    ) -> None:
        if not output_md_path or processed_count % flush_every != 0:
            return
        parts = list(settled)
        if pending is not None:
            parts.append(pending)
        body = "\n\n".join(s for s in parts if s).strip()
        try:
            self._write_output(output_md_path, body)
        except Exception as e:
            logger.warning("incremental flush to %s failed: %s", output_md_path, e)

    def _render_page(
        self, page: Any, dpi: int
    ) -> tuple[Image.Image, int, int]:
        page_img = page.to_image(resolution=dpi)
        pil = page_img.original
        if pil.mode not in ("RGB", "RGBA", "L"):
            pil = pil.convert("RGB")
        return pil, pil.width, pil.height

    def _render_bbox_hires(
        self,
        page: Any,
        page_pt_w: float,
        page_pt_h: float,
        page_w: int,
        page_h: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        dpi: int,
    ) -> Image.Image | None:
        # Map pixel bbox (top-left origin) back to PDF point coords.
        sx = page_pt_w / float(page_w)
        sy = page_pt_h / float(page_h)
        pt_x0 = max(0.0, x0 * sx)
        pt_x1 = min(page_pt_w, x1 * sx)
        pt_y0 = max(0.0, y0 * sy)
        pt_y1 = min(page_pt_h, y1 * sy)
        if pt_x1 <= pt_x0 or pt_y1 <= pt_y0:
            return None
        try:
            sub = page.crop((pt_x0, pt_y0, pt_x1, pt_y1))
            img = sub.to_image(resolution=dpi).original
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")
            return img
        except Exception as e:
            logger.warning("hires bbox render failed: %s", e)
            return None

    # Formats we'll pass through as raw bytes. Others (jbig2, jpx, jb2, ...)
    # fall through to the render path so the output is a portable PNG.
    _RAW_PASSTHROUGH_EXT = {
        "jpeg": "image/jpeg",
        "jpg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }

    def _extract_embedded_image(
        self,
        page_num: int,
        page_pt_w: float,
        page_pt_h: float,
        page_w: int,
        page_h: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        min_img_coverage: float = 0.7,
        min_bbox_coverage: float = 0.5,
    ) -> tuple[bytes, str] | None:
        """
        If the LLM-supplied pixel bbox tightly matches a single raster image
        embedded in the PDF page, return its (original bytes, mime) so we can
        write them through without a decode/re-encode round-trip. Returns None
        when no embedded raster dominates the bbox or its format is not one
        Markdown viewers handle directly (JBIG2, JPEG2000, ...).
        """
        if self._fitz_doc is None:
            return None
        try:
            fpage = self._fitz_doc[page_num - 1]
        except Exception:
            return None

        sx = page_pt_w / float(page_w)
        sy = page_pt_h / float(page_h)
        bbox_pt = fitz.Rect(
            max(0.0, x0 * sx),
            max(0.0, y0 * sy),
            min(page_pt_w, x1 * sx),
            min(page_pt_h, y1 * sy),
        )
        if bbox_pt.is_empty or bbox_pt.width <= 1 or bbox_pt.height <= 1:
            return None
        bbox_area = bbox_pt.get_area()

        best: tuple[float, int] | None = None  # (overlap_ratio, xref)
        try:
            imgs = fpage.get_images(full=True)
        except Exception:
            return None
        for entry in imgs:
            xref = entry[0]
            try:
                rects = fpage.get_image_rects(xref)
            except Exception:
                continue
            for r in rects:
                inter = r & bbox_pt
                if inter.is_empty:
                    continue
                inter_area = inter.get_area()
                # Image must be (almost) fully inside the bbox AND occupy a
                # meaningful fraction of it. Pure cov_img would also match a
                # bbox that frames many images — the cov_bbox floor guards
                # against that.
                cov_bbox = inter_area / bbox_area if bbox_area else 0.0
                r_area = r.get_area()
                cov_img = inter_area / r_area if r_area else 0.0
                if cov_img >= min_img_coverage and cov_bbox >= min_bbox_coverage:
                    if best is None or cov_img > best[0]:
                        best = (cov_img, xref)
        if best is None:
            return None

        xref = best[1]
        try:
            info = self._fitz_doc.extract_image(xref)
        except Exception as e:
            logger.warning("extract_image(%d) failed: %s", xref, e)
            return None

        blob = info.get("image")
        ext = (info.get("ext") or "").lower()
        if not blob:
            return None
        mime = self._RAW_PASSTHROUGH_EXT.get(ext)
        if mime is None:
            return None

        # Cheap validation: make sure PIL can parse the header & dims. We
        # don't decode pixels — just confirm it's a real image stream and
        # not something pathologically tiny.
        try:
            with Image.open(io.BytesIO(blob)) as probe:
                if probe.width < 2 or probe.height < 2:
                    return None
                probe_mode = probe.mode
        except Exception as e:
            logger.warning("PIL probe of embedded xref %d failed: %s", xref, e)
            return None

        # CMYK JPEGs from PDFs need re-encoding to RGB so browsers/Markdown
        # viewers can display them. The PDF `/Decode` array (which may invert
        # samples per Adobe convention) is NOT preserved by extract_image,
        # so we can't reliably know whether to invert from the blob alone.
        # Fall back to the page-rendering path, which honors /Decode.
        if ext in ("jpeg", "jpg") and probe_mode == "CMYK":
            return None

        return blob, mime

    def _build_context(
        self,
        settled: list[str],
        pending: str | None,
        context_pages: int,
    ) -> str:
        if context_pages <= 0:
            return ""
        parts = list(settled)
        if pending is not None:
            parts.append(pending)
        if not parts:
            return ""
        tail = parts[-context_pages:]
        return "\n\n".join(t for t in tail if t)

    def _pdf_text_for(
        self,
        pdf: Any,
        page_num: int | None,
        cache: dict[int, str],
    ) -> str:
        if page_num is None:
            return ""
        if page_num in cache:
            return cache[page_num]
        try:
            txt = pdf.pages[page_num - 1].extract_text() or ""
        except Exception as e:
            logger.warning("page %d: pdfplumber extract failed: %s", page_num, e)
            txt = ""
        cache[page_num] = txt
        return txt

    def _glue_boundary(
        self,
        ocr_service: LLMVisionOCRService,
        md_a: str,
        md_b: str,
        pdf_a: str,
        pdf_b: str,
        n_a: int | None,
        n_b: int,
    ) -> tuple[str, str]:
        prompt = GLUE_PROMPT.format(
            sentinel=GLUE_SENTINEL,
            md_a=md_a,
            md_b=md_b,
            pdf_a=(pdf_a or "").strip() or "(empty)",
            pdf_b=(pdf_b or "").strip() or "(empty)",
        )
        response = ocr_service.client.chat.completions.create(
            model=ocr_service.model,
            messages=[{"role": "user", "content": prompt}],
        )
        merged = (response.choices[0].message.content or "").strip()
        parts = merged.split(GLUE_SENTINEL)
        if len(parts) == 2:
            return parts[0].strip("\n"), parts[1].strip("\n")
        if len(parts) > 2:
            logger.warning(
                "boundary %s/%d: sentinel appeared %d times; joining tail halves",
                n_a, n_b, len(parts),
            )
            return parts[0].strip("\n"), GLUE_SENTINEL.join(parts[1:]).strip("\n")
        logger.warning(
            "boundary %s/%d: sentinel MISSING; keeping pages unmerged",
            n_a, n_b,
        )
        return md_a, md_b

    def _transcribe_page(
        self,
        ocr_service: LLMVisionOCRService,
        page_png: Image.Image,
        page_num: int,
        page_w: int,
        page_h: int,
        context_md: str,
    ) -> str:
        buf = io.BytesIO()
        page_png.save(buf, format="PNG")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("utf-8")
        data_uri = f"data:image/png;base64,{b64}"

        instruction = PAGE_PROMPT.format(
            page_num=page_num, page_w=page_w, page_h=page_h
        )

        user_content: list[dict] = []
        if context_md:
            user_content.append(
                {"type": "text", "text": context_preamble(context_md)}
            )
        user_content.append({"type": "text", "text": instruction})
        user_content.append({"type": "image_url", "image_url": {"url": data_uri}})

        try:
            response = ocr_service.client.chat.completions.create(
                model=ocr_service.model,
                messages=[{"role": "user", "content": user_content}],
            )
            text = response.choices[0].message.content or ""
            return text.strip()
        except Exception as e:
            logger.warning(
                "page %d: primary LLM call failed (%s); trying Claude fallback",
                page_num, e,
            )
            return self._transcribe_page_anthropic(
                b64, instruction, context_md, page_num
            )

    def _transcribe_page_anthropic(
        self,
        b64_png: str,
        instruction: str,
        context_md: str,
        page_num: int,
    ) -> str:
        from anthropic import Anthropic

        client = Anthropic(
            base_url=_ANTHROPIC_BASE_URL,
            api_key=_ANTHROPIC_AUTH_TOKEN,
        )
        content: list[dict] = []
        if context_md:
            content.append({"type": "text", "text": context_preamble(context_md)})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64_png,
            },
        })
        content.append({"type": "text", "text": instruction})

        resp = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        )
        logger.warning("page %d: Claude fallback succeeded (%d chars)", page_num, len(text))
        return text.strip()

    @staticmethod
    def _encode_crop(crop: Image.Image) -> tuple[bytes, str]:
        # Photos compress vastly better as JPEG; line art / charts must stay
        # lossless. Heuristic: opaque + large enough → JPEG, else PNG.
        has_alpha = crop.mode in ("RGBA", "LA") or (
            crop.mode == "P" and "transparency" in crop.info
        )
        pixels = crop.width * crop.height
        use_jpeg = not has_alpha and pixels >= 200_000
        buf = io.BytesIO()
        if use_jpeg:
            img = crop if crop.mode in ("RGB", "L") else crop.convert("RGB")
            img.save(buf, format="JPEG", quality=88, optimize=True)
            return buf.getvalue(), "image/jpeg"
        crop.save(buf, format="PNG")
        return buf.getvalue(), "image/png"

    def _process_placeholders(
        self,
        raw_md: str,
        page: Any,
        page_png: Image.Image,
        page_w: int,
        page_h: int,
        page_pt_w: float,
        page_pt_h: float,
        page_num: int,
        crop_dpi: int,
        save_images_dir: str | None,
        md_output_dir: str | None,
    ) -> str:
        # If no sink configured, strip the bbox coords so the markdown still
        # reads sanely, but we can't save crops.
        figure_idx = 0

        def replace(m: re.Match) -> str:
            nonlocal figure_idx
            try:
                x0 = float(m.group(1))
                y0 = float(m.group(2))
                x1 = float(m.group(3))
                y1 = float(m.group(4))
            except ValueError:
                return ""

            # Clamp + validate
            x0 = max(0.0, min(float(page_w), x0))
            x1 = max(0.0, min(float(page_w), x1))
            y0 = max(0.0, min(float(page_h), y0))
            y1 = max(0.0, min(float(page_h), y1))
            if x1 <= x0 + 1 or y1 <= y0 + 1:
                logger.warning(
                    "page %d fig %d: degenerate bbox (%s,%s,%s,%s), dropping",
                    page_num, figure_idx, x0, y0, x1, y1,
                )
                figure_idx += 1
                return ""

            raw = self._extract_embedded_image(
                page_num, page_pt_w, page_pt_h,
                page_w, page_h, x0, y0, x1, y1,
            )
            crop = None
            if raw is None:
                crop = self._render_bbox_hires(
                    page, page_pt_w, page_pt_h,
                    page_w, page_h, x0, y0, x1, y1, crop_dpi,
                )
            if raw is None and crop is None:
                try:
                    crop = page_png.crop((int(x0), int(y0), int(x1), int(y1)))
                except Exception as e:
                    logger.warning(
                        "page %d fig %d: crop failed: %s", page_num, figure_idx, e
                    )
                    figure_idx += 1
                    return ""

            if save_images_dir:
                if raw is not None:
                    blob, mime = raw
                else:
                    blob, mime = self._encode_crop(crop)
                try:
                    rel = save_image_blob(
                        blob, mime, save_images_dir, md_output_dir
                    )
                except Exception as e:
                    logger.warning(
                        "page %d fig %d: save failed: %s",
                        page_num, figure_idx, e,
                    )
                    figure_idx += 1
                    return ""
                figure_idx += 1
                return f"![]({rel})"
            else:
                # No save dir: keep a bare placeholder so structure survives.
                figure_idx += 1
                return "![](image)"

        return _BBOX_PLACEHOLDER_RE.sub(replace, raw_md)
