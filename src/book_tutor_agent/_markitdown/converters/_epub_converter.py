import io
import mimetypes
import os
import posixpath
import uuid
import zipfile
from defusedxml import minidom
from xml.dom.minidom import Document

from typing import BinaryIO, Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from ._html_converter import HtmlConverter
from ._image_sink import save_image_blob
from ._llm_caption import llm_caption
from .._base_converter import DocumentConverterResult
from .._stream_info import StreamInfo

ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/epub",
    "application/epub+zip",
    "application/x-epub+zip",
]

ACCEPTED_FILE_EXTENSIONS = [".epub"]

MIME_TYPE_MAPPING = {
    ".html": "text/html",
    ".xhtml": "application/xhtml+xml",
}


class EpubConverter(HtmlConverter):
    """
    Converts EPUB files to Markdown. Style information (e.g.m headings) and tables are preserved where possible.
    """

    def __init__(self):
        super().__init__()
        self._html_converter = HtmlConverter()

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()

        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True

        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True

        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        save_images_dir: Optional[str] = kwargs.get("save_images_dir")
        md_output_dir: Optional[str] = kwargs.get("md_output_dir")
        llm_client = kwargs.get("llm_client")
        llm_model = kwargs.get("llm_model")
        llm_prompt = kwargs.get("llm_prompt")

        with zipfile.ZipFile(file_stream, "r") as z:
            # Extracts metadata (title, authors, language, publisher, date, description, cover) from an EPUB file."""

            # Locate content.opf
            container_dom = minidom.parse(z.open("META-INF/container.xml"))
            opf_path = container_dom.getElementsByTagName("rootfile")[0].getAttribute(
                "full-path"
            )

            # Parse content.opf
            opf_dom = minidom.parse(z.open(opf_path))
            metadata: Dict[str, Any] = {
                "title": self._get_text_from_node(opf_dom, "dc:title"),
                "authors": self._get_all_texts_from_nodes(opf_dom, "dc:creator"),
                "language": self._get_text_from_node(opf_dom, "dc:language"),
                "publisher": self._get_text_from_node(opf_dom, "dc:publisher"),
                "date": self._get_text_from_node(opf_dom, "dc:date"),
                "description": self._get_text_from_node(opf_dom, "dc:description"),
                "identifier": self._get_text_from_node(opf_dom, "dc:identifier"),
            }

            # Extract manifest items (ID → href mapping) and image media types.
            manifest: Dict[str, str] = {}
            image_media_types: Dict[str, str] = {}
            for item in opf_dom.getElementsByTagName("item"):
                item_id = item.getAttribute("id")
                href = item.getAttribute("href")
                media_type = (item.getAttribute("media-type") or "").lower()
                manifest[item_id] = href
                if media_type.startswith("image/"):
                    image_media_types[href] = media_type

            # Extract spine order (ID refs)
            spine_items = opf_dom.getElementsByTagName("itemref")
            spine_order = [item.getAttribute("idref") for item in spine_items]

            # Convert spine order to actual file paths
            base_path = "/".join(
                opf_path.split("/")[:-1]
            )  # Get base directory of content.opf
            spine = [
                f"{base_path}/{manifest[item_id]}" if base_path else manifest[item_id]
                for item_id in spine_order
                if item_id in manifest
            ]

            # Per-conversion cache: zip-internal absolute path -> (md_src, description).
            # Same image reused across chapters is only saved and captioned once.
            image_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

            # Description text is multi-line markdown that markdownify would
            # collapse or escape if emitted via the HTML tree. We instead embed
            # a unique placeholder per <img> in the HTML and substitute the real
            # markdown block back into the converted output below.
            placeholders: Dict[str, str] = {}

            # Extract and convert the content
            markdown_content: List[str] = []
            for file in spine:
                if file in z.namelist():
                    with z.open(file) as f:
                        raw_bytes = f.read()
                    filename = os.path.basename(file)
                    extension = os.path.splitext(filename)[1].lower()
                    mimetype = MIME_TYPE_MAPPING.get(extension)

                    rewritten_html = self._rewrite_chapter_images(
                        raw_bytes,
                        chapter_path=file,
                        base_path=base_path,
                        zip_file=z,
                        image_media_types=image_media_types,
                        image_cache=image_cache,
                        placeholders=placeholders,
                        save_images_dir=save_images_dir,
                        md_output_dir=md_output_dir,
                        llm_client=llm_client,
                        llm_model=llm_model,
                        llm_prompt=llm_prompt,
                    )

                    converted_content = self._html_converter.convert(
                        io.BytesIO(rewritten_html),
                        StreamInfo(
                            mimetype=mimetype,
                            extension=extension,
                            filename=filename,
                        ),
                        **kwargs,
                    )
                    chapter_md = converted_content.markdown
                    for token, replacement in placeholders.items():
                        if token in chapter_md:
                            chapter_md = chapter_md.replace(token, replacement)
                    markdown_content.append(chapter_md.strip())

            # Format and add the metadata
            metadata_markdown = []
            for key, value in metadata.items():
                if isinstance(value, list):
                    value = ", ".join(value)
                if value:
                    metadata_markdown.append(f"**{key.capitalize()}:** {value}")

            markdown_content.insert(0, "\n".join(metadata_markdown))

            return DocumentConverterResult(
                markdown="\n\n".join(markdown_content), title=metadata["title"]
            )

    def _rewrite_chapter_images(
        self,
        html_bytes: bytes,
        *,
        chapter_path: str,
        base_path: str,
        zip_file: zipfile.ZipFile,
        image_media_types: Dict[str, str],
        image_cache: Dict[str, Tuple[Optional[str], Optional[str]]],
        placeholders: Dict[str, str],
        save_images_dir: Optional[str],
        md_output_dir: Optional[str],
        llm_client: Any,
        llm_model: Any,
        llm_prompt: Any,
    ) -> bytes:
        """Process each <img> in a chapter: optionally save the blob to disk and
        request an LLM description, then rewrite the tag so HtmlConverter emits a
        relative-path image reference followed by a placeholder. The caller
        substitutes the placeholder with the real multi-line description block."""
        soup = BeautifulSoup(html_bytes, "html.parser")
        chapter_dir = posixpath.dirname(chapter_path)
        namelist = set(zip_file.namelist())

        for img in soup.find_all("img"):
            src = img.get("src")
            if not src or src.startswith(("data:", "http://", "https://")):
                continue

            zip_abs = posixpath.normpath(posixpath.join(chapter_dir, src))
            if zip_abs not in namelist:
                continue

            cached = image_cache.get(zip_abs)
            if cached is None:
                md_src, description = self._process_image(
                    zip_file=zip_file,
                    zip_abs=zip_abs,
                    chapter_dir=chapter_dir,
                    base_path=base_path,
                    image_media_types=image_media_types,
                    save_images_dir=save_images_dir,
                    md_output_dir=md_output_dir,
                    llm_client=llm_client,
                    llm_model=llm_model,
                    llm_prompt=llm_prompt,
                )
                image_cache[zip_abs] = (md_src, description)
            else:
                md_src, description = cached

            if md_src is not None:
                img["src"] = md_src

            if description:
                # Token must survive markdownify without being escaped, so use
                # plain alphanumerics only (markdownify escapes _, *, etc).
                token = f"MARKITDOWNEPUBDESC{uuid.uuid4().hex}"
                placeholders[token] = (
                    f"*[Image OCR]\n{description}\n[End OCR]*"
                )
                placeholder_p = soup.new_tag("p")
                placeholder_p.string = token
                img.insert_after(placeholder_p)

        return str(soup).encode("utf-8")

    def _process_image(
        self,
        *,
        zip_file: zipfile.ZipFile,
        zip_abs: str,
        chapter_dir: str,
        base_path: str,
        image_media_types: Dict[str, str],
        save_images_dir: Optional[str],
        md_output_dir: Optional[str],
        llm_client: Any,
        llm_model: Any,
        llm_prompt: Any,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return (md_src, description) for a single image referenced by zip_abs.

        md_src is None when no save dir is configured (caller leaves src unchanged).
        description is None when no llm client is configured or the call failed —
        on failure an inline error placeholder is returned instead.
        """
        try:
            with zip_file.open(zip_abs) as img_f:
                blob = img_f.read()
        except Exception:
            return None, None

        # Resolve content type: manifest entries are keyed by href relative to the
        # opf base path, so strip the base_path prefix when looking up. Sniff the
        # actual bytes with PIL as the authoritative source — EPUBs in the wild
        # misdeclare media-type (e.g. "image/jpeg" for PNG bytes), which makes
        # downstream LLM APIs reject the request.
        manifest_key = zip_abs
        if base_path and zip_abs.startswith(base_path + "/"):
            manifest_key = zip_abs[len(base_path) + 1 :]
        content_type = image_media_types.get(manifest_key)
        if not content_type:
            guessed, _ = mimetypes.guess_type(zip_abs)
            content_type = guessed or "application/octet-stream"

        try:
            from PIL import Image

            with Image.open(io.BytesIO(blob)) as probe:
                fmt = (probe.format or "").lower()
            if fmt:
                sniffed = "image/" + ("jpeg" if fmt == "jpg" else fmt)
                if sniffed != content_type:
                    content_type = sniffed
        except Exception:
            pass

        md_src: Optional[str] = None
        if save_images_dir:
            md_src = save_image_blob(
                blob,
                content_type,
                save_images_dir,
                md_output_dir,
            )

        description: Optional[str] = None
        if llm_client is not None and llm_model is not None:
            ext = mimetypes.guess_extension(content_type) or os.path.splitext(zip_abs)[1]
            stream_info = StreamInfo(
                mimetype=content_type,
                extension=ext,
                filename=posixpath.basename(zip_abs),
            )
            try:
                description = llm_caption(
                    io.BytesIO(blob),
                    stream_info,
                    client=llm_client,
                    model=llm_model,
                    prompt=llm_prompt,
                )
            except Exception as e:
                description = f"*[Image Description failed: {e}]*"

        return md_src, description

    def _get_text_from_node(self, dom: Document, tag_name: str) -> str | None:
        """Convenience function to extract a single occurrence of a tag (e.g., title)."""
        texts = self._get_all_texts_from_nodes(dom, tag_name)
        if len(texts) > 0:
            return texts[0]
        else:
            return None

    def _get_all_texts_from_nodes(self, dom: Document, tag_name: str) -> List[str]:
        """Helper function to extract all occurrences of a tag (e.g., multiple authors)."""
        texts: List[str] = []
        for node in dom.getElementsByTagName(tag_name):
            if node.firstChild and hasattr(node.firstChild, "nodeValue"):
                texts.append(node.firstChild.nodeValue.strip())
        return texts
