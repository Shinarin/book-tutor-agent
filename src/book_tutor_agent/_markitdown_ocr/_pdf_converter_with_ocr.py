"""
Enhanced PDF Converter with OCR support for embedded images.
Extracts images from PDFs and performs OCR while maintaining document context.
"""

import io
import json
import logging
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

logger = logging.getLogger(__name__)

# Import dependencies
_dependency_exc_info = None
try:
    import pdfminer
    import pdfminer.high_level
    import pdfplumber
    from PIL import Image
except ImportError:
    _dependency_exc_info = sys.exc_info()


def _extract_images_from_page(page: Any) -> list[dict]:
    """
    Extract images from a PDF page by rendering page regions.

    Returns:
        List of dicts with 'stream', 'bbox', 'name', 'y_pos' keys
    """
    images_info = []

    try:
        # Try multiple methods to detect images
        images = []

        # Method 1: Use page.images (standard approach)
        if hasattr(page, "images") and page.images:
            images = page.images

        # Method 2: If no images found, try underlying PDF objects
        if not images and hasattr(page, "objects") and "image" in page.objects:
            images = page.objects.get("image", [])

        # Method 3: Try filtering all objects for image types
        if not images and hasattr(page, "objects"):
            all_objs = page.objects
            for obj_type in all_objs.keys():
                if "image" in obj_type.lower() or "xobject" in obj_type.lower():
                    potential_imgs = all_objs.get(obj_type, [])
                    if potential_imgs:
                        images = potential_imgs
                        break

        for i, img_dict in enumerate(images):
            try:
                # Try to get the actual image stream from the PDF
                img_stream = None
                y_pos = 0

                # Method A: If img_dict has 'stream' key, use it directly
                if "stream" in img_dict and hasattr(img_dict["stream"], "get_data"):
                    try:
                        img_bytes = img_dict["stream"].get_data()

                        # Try to open as PIL Image to validate/decode
                        pil_img = Image.open(io.BytesIO(img_bytes))

                        # Convert to RGB if needed (handle CMYK, etc.)
                        if pil_img.mode not in ("RGB", "L"):
                            pil_img = pil_img.convert("RGB")

                        # Save to stream as PNG
                        img_stream = io.BytesIO()
                        pil_img.save(img_stream, format="PNG")
                        img_stream.seek(0)

                        y_pos = img_dict.get("top", 0)
                    except Exception:
                        pass

                # Method B: Fallback to rendering page region
                if img_stream is None:
                    x0 = img_dict.get("x0", 0)
                    y0 = img_dict.get("top", 0)
                    x1 = img_dict.get("x1", 0)
                    y1 = img_dict.get("bottom", 0)
                    y_pos = y0

                    # Check if dimensions are valid
                    if x1 <= x0 or y1 <= y0:
                        continue

                    # Use pdfplumber's within_bbox to crop, then render
                    # This preserves coordinate system correctly
                    bbox = (x0, y0, x1, y1)
                    cropped_page = page.within_bbox(bbox)

                    # Render at 150 DPI (balance between quality and size)
                    page_img = cropped_page.to_image(resolution=150)

                    # Save to stream
                    img_stream = io.BytesIO()
                    page_img.original.save(img_stream, format="PNG")
                    img_stream.seek(0)

                if img_stream:
                    images_info.append(
                        {
                            "stream": img_stream,
                            "name": f"page_{page.page_number}_img_{i}",
                            "y_pos": y_pos,
                        }
                    )

            except Exception:
                continue

    except Exception:
        pass

    return images_info


class PdfConverterWithOCR(DocumentConverter):
    """
    Enhanced PDF Converter with OCR support for embedded images.
    Maintains document structure while extracting text from images inline.
    """

    def __init__(self, ocr_service: Optional[LLMVisionOCRService] = None):
        super().__init__()
        self.ocr_service = ocr_service

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

        # Get OCR service if available (from kwargs or instance)
        ocr_service: LLMVisionOCRService | None = (
            kwargs.get("ocr_service") or self.ocr_service
        )

        save_images_dir: str | None = kwargs.get("save_images_dir")
        md_output_dir: str | None = kwargs.get("md_output_dir")

        # Pages dense with vector primitives (rects/lines/curves) are almost
        # always vector-drawn charts/diagrams whose labels would otherwise be
        # scattered as loose chars in reading order. Render the whole page
        # and let the vision model transcribe it instead.
        vector_obj_threshold: int = int(kwargs.get("vector_obj_threshold", 10))
        force_vision_pages: set[int] = set(kwargs.get("force_vision_pages") or [])

        start_page = kwargs.get("start_page")
        end_page = kwargs.get("end_page")
        start_page = int(start_page) if start_page is not None else None
        end_page = int(end_page) if end_page is not None else None

        # Read PDF into BytesIO
        file_stream.seek(0)
        pdf_bytes = io.BytesIO(file_stream.read())

        markdown_content = []

        try:
            with pdfplumber.open(pdf_bytes) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    if start_page is not None and page_num < start_page:
                        continue
                    if end_page is not None and page_num > end_page:
                        break
                    markdown_content.append(f"\n## Page {page_num}\n")

                    # Detect vector-drawn figures (charts/diagrams). Ask the
                    # LLM where they are, crop them to PNG, then let the
                    # existing bitmap pipeline handle them like any embedded
                    # image. If detection fails entirely, fall back to a
                    # single whole-page transcription for this page.
                    vector_figures: list[dict] = []
                    vector_fallback_md: str | None = None
                    if ocr_service:
                        vec_count = (
                            len(page.rects) + len(page.lines) + len(page.curves)
                        )
                        if (
                            page_num in force_vision_pages
                            or vec_count >= vector_obj_threshold
                        ):
                            logger.warning(
                                "page %d: running _detect_and_crop_vector_figures (vec_count=%d)",
                                page_num,
                                vec_count,
                            )
                            vector_figures, detection_ok = (
                                self._detect_and_crop_vector_figures(
                                    page, ocr_service
                                )
                            )
                            if not detection_ok:
                                vector_fallback_md = self._ocr_single_page(
                                    page, ocr_service, save_images_dir, md_output_dir
                                )

                    if vector_fallback_md is not None:
                        if vector_fallback_md:
                            markdown_content.append(vector_fallback_md)
                        continue

                    # If OCR is enabled, interleave text and images by position
                    if ocr_service:
                        images_on_page = self._extract_page_images(pdf_bytes, page_num)
                        images_on_page.extend(vector_figures)

                        # Chars sitting inside a vector-figure crop are now
                        # represented by the cropped PNG; suppress them from
                        # the text stream so they don't appear twice.
                        exclude_boxes = [
                            f["exclude_bbox"] for f in vector_figures
                            if "exclude_bbox" in f
                        ]

                        def _in_excluded(c: dict) -> bool:
                            cx = (c["x0"] + c["x1"]) / 2
                            cy = (c["top"] + c["bottom"]) / 2
                            for x0, y0, x1, y1 in exclude_boxes:
                                if x0 <= cx <= x1 and y0 <= cy <= y1:
                                    return True
                            return False

                        if images_on_page:
                            # Extract text lines with Y positions
                            chars = page.chars
                            if exclude_boxes:
                                chars = [c for c in chars if not _in_excluded(c)]
                            if chars:
                                # Group chars into lines based on Y position
                                lines_with_y = []
                                current_line = []
                                current_y = None

                                for char in sorted(
                                    chars, key=lambda c: (c["top"], c["x0"])
                                ):
                                    y = char["top"]
                                    if current_y is None:
                                        current_y = y
                                    elif abs(y - current_y) > 2:  # New line threshold
                                        if current_line:
                                            text = "".join(
                                                [c["text"] for c in current_line]
                                            )
                                            lines_with_y.append(
                                                {"y": current_y, "text": text.strip()}
                                            )
                                        current_line = []
                                        current_y = y
                                    current_line.append(char)

                                # Add last line
                                if current_line:
                                    text = "".join([c["text"] for c in current_line])
                                    lines_with_y.append(
                                        {"y": current_y, "text": text.strip()}
                                    )
                            else:
                                # Fallback: use simple text extraction
                                text_content = page.extract_text() or ""
                                lines_with_y = [
                                    {"y": i * 10, "text": line}
                                    for i, line in enumerate(text_content.split("\n"))
                                ]

                            # OCR all images
                            image_data = []
                            for img_info in images_on_page:
                                img_ref: str | None = None
                                if save_images_dir:
                                    img_info["stream"].seek(0)
                                    blob = img_info["stream"].read()
                                    img_info["stream"].seek(0)
                                    rel_path = save_image_blob(
                                        blob,
                                        "image/png",
                                        save_images_dir,
                                        md_output_dir,
                                    )
                                    img_ref = f"![]({rel_path})"

                                ocr_result = ocr_service.extract_text(
                                    img_info["stream"]
                                )
                                if ocr_result.text.strip() or img_ref:
                                    image_data.append(
                                        {
                                            "y_pos": img_info["y_pos"],
                                            "name": img_info["name"],
                                            "ocr_text": ocr_result.text,
                                            "backend": ocr_result.backend_used,
                                            "type": "image",
                                            "img_ref": img_ref,
                                        }
                                    )

                            # Add text items
                            content_items = [
                                {
                                    "y_pos": item["y"],
                                    "text": item["text"],
                                    "type": "text",
                                }
                                for item in lines_with_y
                                if item["text"]
                            ]
                            content_items.extend(image_data)

                            # Sort all items by Y position (top to bottom)
                            content_items.sort(key=lambda x: x["y_pos"])

                            # Build markdown by interleaving text and images
                            for item in content_items:
                                if item["type"] == "text":
                                    markdown_content.append(item["text"])
                                else:  # image
                                    parts = []
                                    if item.get("img_ref"):
                                        parts.append(item["img_ref"])
                                    ocr_text = item.get("ocr_text", "")
                                    if ocr_text.strip():
                                        parts.append(
                                            f"*[Image OCR]\n{ocr_text}\n[End OCR]*"
                                        )
                                    if parts:
                                        markdown_content.append(
                                            "\n\n" + "\n\n".join(parts) + "\n"
                                        )
                        else:
                            # No images detected - just extract regular text
                            text_content = page.extract_text() or ""
                            if text_content.strip():
                                markdown_content.append(text_content.strip())
                    else:
                        # No OCR, just extract text
                        text_content = page.extract_text() or ""
                        if text_content.strip():
                            markdown_content.append(text_content.strip())

                # Build final markdown
                markdown = "\n\n".join(markdown_content).strip()

                # Fallback to pdfminer if empty
                if not markdown:
                    pdf_bytes.seek(0)
                    markdown = pdfminer.high_level.extract_text(pdf_bytes)

        except Exception:
            # Fallback to pdfminer
            try:
                pdf_bytes.seek(0)
                markdown = pdfminer.high_level.extract_text(pdf_bytes)
            except Exception:
                markdown = ""

        # Final fallback: If still empty/whitespace and OCR is available,
        # treat as scanned PDF and OCR full pages
        if ocr_service and (not markdown or not markdown.strip()):
            pdf_bytes.seek(0)
            markdown = self._ocr_full_pages(
                pdf_bytes, ocr_service, start_page=start_page, end_page=end_page
            )

        return DocumentConverterResult(markdown=markdown)

    def _extract_page_images(self, pdf_bytes: io.BytesIO, page_num: int) -> list[dict]:
        """
        Extract images from a PDF page using pdfplumber.

        Args:
            pdf_bytes: PDF file as BytesIO
            page_num: Page number (1-indexed)

        Returns:
            List of image info dicts with 'stream', 'bbox', 'name', 'y_pos'
        """
        images = []

        try:
            pdf_bytes.seek(0)
            with pdfplumber.open(pdf_bytes) as pdf:
                if page_num <= len(pdf.pages):
                    page = pdf.pages[page_num - 1]  # 0-indexed
                    images = _extract_images_from_page(page)
        except Exception:
            pass

        # Sort by vertical position (top to bottom)
        images.sort(key=lambda x: x["y_pos"])

        return images

    def _detect_and_crop_vector_figures(
        self,
        page: Any,
        ocr_service: LLMVisionOCRService,
    ) -> tuple[list[dict], bool]:
        """
        Render the page, ask the LLM where the figures are, crop each one to
        a PNG that mimics the dicts produced by `_extract_page_images` so the
        existing bitmap pipeline can handle them uniformly.

        Returns:
            (figures, detection_ok). `detection_ok` is False when the LLM
            reply could not be parsed or returned no figures — the caller
            should fall back to single-page transcription in that case.
        """
        page_num = page.page_number
        try:
            page_img = page.to_image(resolution=300)
            buf = io.BytesIO()
            page_img.original.save(buf, format="PNG")
            buf.seek(0)
        except Exception as e:
            logger.warning("page %d: render failed for bbox detection: %s", page_num, e)
            return [], False

        prompt = (
            "This is a page from a PDF. Identify every figure, chart, diagram, "
            "or illustration on the page (exclude plain paragraphs of text and "
            "tables made only of text). For each one, return a TIGHT bounding "
            "box that hugs the figure itself. Do NOT include surrounding "
            "whitespace, captions, figure numbers, or body text above/below "
            "the figure. Coordinates are normalized to [0,1] with the origin "
            "at the TOP-LEFT of the image.\n\n"
            "Reply with ONLY a JSON object, no prose, no code fences, in this "
            'exact shape: {"figures": [{"bbox": [x0, y0, x1, y1]}, ...]}.\n'
            "If there are no figures, return {\"figures\": []}."
        )
        try:
            result = ocr_service.extract_text(buf, prompt=prompt)
            raw = (result.text or "").strip()
        except Exception as e:
            logger.warning("page %d: LLM call failed: %s", page_num, e)
            return [], False

        # Strip ``` fences if the model added them despite instructions.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            logger.warning("page %d: no JSON object in LLM reply", page_num)
            return [], False
        try:
            data = json.loads(m.group(0))
            raw_figures = data.get("figures", [])
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning("page %d: JSON parse failed: %s", page_num, e)
            return [], False

        if not raw_figures:
            logger.warning("page %d: LLM reported no figures despite vector density", page_num)
            return [], False

        page_w = float(page.width)
        page_h = float(page.height)
        figures: list[dict] = []
        for i, fig in enumerate(raw_figures):
            bbox = fig.get("bbox") if isinstance(fig, dict) else None
            if not (isinstance(bbox, list) and len(bbox) == 4):
                logger.warning("page %d fig %d: bad bbox shape, skipping", page_num, i)
                continue
            try:
                x0n, y0n, x1n, y1n = [float(v) for v in bbox]
            except (TypeError, ValueError):
                logger.warning("page %d fig %d: non-numeric bbox, skipping", page_num, i)
                continue
            # Sanity-clamp and validate
            x0n, x1n = sorted((max(0.0, min(1.0, x0n)), max(0.0, min(1.0, x1n))))
            y0n, y1n = sorted((max(0.0, min(1.0, y0n)), max(0.0, min(1.0, y1n))))
            area = (x1n - x0n) * (y1n - y0n)
            if area < 0.01 or area > 0.95:
                logger.warning(
                    "page %d fig %d: bbox area %.2f out of [0.01, 0.95], skipping",
                    page_num, i, area,
                )
                continue

            x0 = x0n * page_w
            x1 = x1n * page_w
            y0 = y0n * page_h
            y1 = y1n * page_h

            # LLM bboxes tend to be loose. Shrink to the actual figure
            # contents by collecting vector primitives and chars whose
            # centers fall inside the LLM bbox, then taking their union.
            inner = [
                o for o in (
                    list(page.rects)
                    + list(page.lines)
                    + list(page.curves)
                    + list(page.chars)
                )
                if x0 <= (o["x0"] + o["x1"]) / 2 <= x1
                and y0 <= (o["top"] + o["bottom"]) / 2 <= y1
            ]
            if inner:
                pad = 2.0
                tx0 = max(0.0, min(o["x0"] for o in inner) - pad)
                ty0 = max(0.0, min(o["top"] for o in inner) - pad)
                tx1 = min(page_w, max(o["x1"] for o in inner) + pad)
                ty1 = min(page_h, max(o["bottom"] for o in inner) + pad)
                x0, y0, x1, y1 = tx0, ty0, tx1, ty1

            try:
                cropped = page.within_bbox((x0, y0, x1, y1))
                crop_img = cropped.to_image(resolution=300)
                crop_buf = io.BytesIO()
                crop_img.original.save(crop_buf, format="PNG")
                crop_buf.seek(0)
            except Exception as e:
                logger.warning("page %d fig %d: crop failed: %s", page_num, i, e)
                continue

            figures.append({
                "stream": crop_buf,
                "name": f"page_{page_num}_vec_{i}",
                "y_pos": y0,
                "exclude_bbox": (x0, y0, x1, y1),
            })

        if not figures:
            return [], False
        return figures, True

    def _ocr_single_page(
        self,
        page: Any,
        ocr_service: LLMVisionOCRService,
        save_images_dir: str | None,
        md_output_dir: str | None,
    ) -> str:
        """
        Render one page at 300 DPI and transcribe it via the vision model.
        Used for pages whose layout (vector charts, diagrams) defeats
        coordinate-ordered text extraction.
        """
        try:
            page_img = page.to_image(resolution=300)
            img_stream = io.BytesIO()
            page_img.original.save(img_stream, format="PNG")
            img_stream.seek(0)
        except Exception as e:
            return f"*[Error rendering page {page.page_number}: {e}]*"

        parts: list[str] = []

        prompt = (
            "Transcribe everything visible on this page into Markdown. "
            "Reproduce text verbatim without paraphrasing. "
            "Render charts and diagrams as a brief description followed by "
            "any data they contain as a Markdown table when possible. "
            "Render tabular data as Markdown tables. "
            "Omit page numbers and running headers/footers."
        )
        ocr_result = ocr_service.extract_text(img_stream, prompt=prompt)
        if ocr_result.text.strip():
            parts.append(ocr_result.text.strip())

        return "\n\n".join(parts)

    def _ocr_full_pages(
        self,
        pdf_bytes: io.BytesIO,
        ocr_service: LLMVisionOCRService,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
    ) -> str:
        """
        Fallback for scanned PDFs: Convert entire pages to images and OCR them.
        Used when text extraction returns empty/whitespace results.

        Args:
            pdf_bytes: PDF file as BytesIO
            ocr_service: OCR service to use

        Returns:
            Markdown text extracted from OCR of full pages
        """
        markdown_parts = []

        try:
            pdf_bytes.seek(0)
            with pdfplumber.open(pdf_bytes) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    if start_page is not None and page_num < start_page:
                        continue
                    if end_page is not None and page_num > end_page:
                        break
                    try:
                        markdown_parts.append(f"\n## Page {page_num}\n")

                        # Render page to image
                        page_img = page.to_image(resolution=300)
                        img_stream = io.BytesIO()
                        page_img.original.save(img_stream, format="PNG")
                        img_stream.seek(0)

                        # Run OCR
                        ocr_result = ocr_service.extract_text(img_stream)

                        if ocr_result.text.strip():
                            text = ocr_result.text.strip()
                            markdown_parts.append(f"*[Image OCR]\n{text}\n[End OCR]*")
                        else:
                            markdown_parts.append(
                                "*[No text could be extracted from this page]*"
                            )

                    except Exception as e:
                        markdown_parts.append(
                            f"*[Error processing page {page_num}: {str(e)}]*"
                        )
                        continue

        except Exception:
            # pdfplumber failed (e.g. malformed EOF) — try PyMuPDF for rendering
            markdown_parts = []
            try:
                import fitz  # PyMuPDF

                pdf_bytes.seek(0)
                doc = fitz.open(stream=pdf_bytes.read(), filetype="pdf")
                for page_num in range(1, doc.page_count + 1):
                    try:
                        markdown_parts.append(f"\n## Page {page_num}\n")
                        page = doc[page_num - 1]
                        mat = fitz.Matrix(300 / 72, 300 / 72)  # 300 DPI
                        pix = page.get_pixmap(matrix=mat)
                        img_stream = io.BytesIO(pix.tobytes("png"))
                        img_stream.seek(0)

                        ocr_result = ocr_service.extract_text(img_stream)

                        if ocr_result.text.strip():
                            text = ocr_result.text.strip()
                            markdown_parts.append(f"*[Image OCR]\n{text}\n[End OCR]*")
                        else:
                            markdown_parts.append(
                                "*[No text could be extracted from this page]*"
                            )

                    except Exception as e:
                        markdown_parts.append(
                            f"*[Error processing page {page_num}: {str(e)}]*"
                        )
                        continue
                doc.close()
            except Exception:
                return "*[Error: Could not process scanned PDF]*"

        return "\n\n".join(markdown_parts).strip()
