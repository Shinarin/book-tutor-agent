"""Prompts and prompt helpers used by PdfConverterLLMFullPage."""


GLUE_SENTINEL = "<<<PAGE_BREAK_HANDLED>>>"


def context_preamble(context_md: str) -> str:
    return (
        "For context, here is the Markdown produced for the "
        "immediately preceding page(s). Use it to keep "
        "formatting consistent and to continue any paragraph "
        "or table that flows across the page break. Do NOT "
        "repeat this context in your reply.\n\n"
        "<<<PREVIOUS PAGE MARKDOWN>>>\n"
        f"{context_md}\n"
        "<<<END PREVIOUS PAGE MARKDOWN>>>"
    )


PAGE_PROMPT = """\
Transcribe this PDF page into Markdown.

Rules:
1. Reproduce all visible text verbatim, in natural reading order. Do not paraphrase.
2. Render tables as Markdown tables. Render headings with `#`/`##`/etc.
3. Omit page numbers, running headers, and running footers.
4. For every figure, chart, diagram, photo, or illustration on the page,
   emit a placeholder of EXACTLY this form on its own line:

       ![[x0, y0, x1, y1]](images/page_{page_num}_img_{{i}}.png)

   where:
   - `x0, y0, x1, y1` are PIXEL coordinates in the rendered page image,
     origin at the TOP-LEFT, hugging the figure itself (exclude captions,
     figure numbers, and surrounding whitespace).
   - `{{i}}` is a 0-based index for figures on this page (0, 1, 2, ...).
   The page image is {page_w} x {page_h} pixels.

5. Immediately after each image placeholder, on the next lines, add a
   short description block in this exact form:

       *[Image OCR]
       <description of what the figure shows, plus any text/labels/legend/axis values visible inside it>
       [End OCR]*

6. Tables made purely of text should be Markdown tables, NOT image placeholders.
7. Output ONLY the Markdown. No code fences around the whole answer, no
   prose like "Here is the transcription:".
"""


GLUE_PROMPT = """\
You are gluing two adjacent pages of a book back together. The pages were
OCR'd from a PDF by a vision model; the layout (headings, tables, image
placeholders, OCR description blocks, blockquotes) is essentially correct,
but individual words may be misread. The PDF text extraction below is the
ground truth for WORDS only — it has no reliable layout but should be
trusted when an OCR'd word/character looks wrong.

Your job:

1. STITCH the page break. If a sentence or paragraph was cut in half by the
   page boundary, join it into one sentence/paragraph with no extra blank
   line.
2. UN-BISECT paragraphs. If a figure block (`![](images/...)` followed by
   `*[Image OCR] ... [End OCR]*` and any caption lines), a blockquote
   (`> ...`), a bold term-definition pair (`**term**` + italic gloss),
   or a sidebar appears IN THE MIDDLE of a paragraph that was split across
   the page break, move the entire interrupting block to immediately
   BEFORE or AFTER the rejoined paragraph (whichever reads more
   naturally). Keep the interrupting block byte-for-byte identical when
   you move it.
3. FIX OCR typos. Where the markdown text disagrees with the PDF reference
   text on a word, character, number, or name, prefer the PDF. Do NOT
   "improve" punctuation, capitalization, or phrasing that already matches
   the PDF. Do not paraphrase. Do not add or remove sentences.
4. PRESERVE everything else verbatim: all headings (#, ##, ###), all
   `![](images/...)` image links exactly as written, all `*[Image OCR]
   ... [End OCR]*` blocks exactly as written, all blockquote `>` lines,
   all bold/italic markers, all table rows, all citations like
   `(Smith, 2020)`.

OUTPUT FORMAT — read carefully:

Emit the merged content for these two pages as TWO halves separated by a
single line containing exactly:

{sentinel}

The FIRST half must contain everything that originally belonged to the
LEFT page (after any rearrangement from rule 2). The SECOND half must
contain everything that originally belonged to the RIGHT page. If a
paragraph straddled the boundary and you joined it, put the WHOLE joined
paragraph in the FIRST half and start the SECOND half on the next
content. The sentinel line must appear EXACTLY ONCE.

Do NOT emit `## Page N` markers. Do NOT add a preamble like "Here is the
merged text". Do NOT wrap the answer in code fences. Just the markdown,
with the one sentinel line.

==================== LEFT PAGE MARKDOWN ====================
{md_a}
==================== RIGHT PAGE MARKDOWN ====================
{md_b}
==================== LEFT PAGE PDF TEXT (reference) ====================
{pdf_a}
==================== RIGHT PAGE PDF TEXT (reference) ====================
{pdf_b}
"""
