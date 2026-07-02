"""把 book_audiobook.py 产出的听感稿转成 .mp3 语音。

Usage:
    python book_tts.py <book_path> [--audiobook-dir path] [--output-dir path]
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv

load_dotenv()

DEFAULT_VOICE = "zh-CN-Xiaochen:DragonHDLatestNeural"
DEFAULT_STYLE = "reflective" # reflective, calm 和 serious 感觉差不多，都试了一下听起来区别不大
DEFAULT_TEMPERATURE = 0.2
MAX_CHUNK_CHARS = 500
MAX_RETRIES = 6
RETRY_BACKOFF_SECONDS = [5, 15, 45, 90, 180, 500]


def detect_summary_dir(book_path: str) -> str | None:
    summary_dir = os.path.join(os.path.dirname(os.path.abspath(book_path)), "summaries")
    if os.path.exists(os.path.join(summary_dir, "progress.json")):
        return summary_dir
    return None


def load_audiobook_files(
    summary_dir: str, audiobook_dir: str, seqs: list[int] | None = None
) -> list[str]:
    """按 progress.json 顺序在 audiobook_dir 下找听感稿。"""
    with open(os.path.join(summary_dir, "progress.json"), encoding="utf-8") as f:
        progress = json.load(f)

    files: list[str] = []
    for seq_str in sorted(progress.keys(), key=int):
        seq = int(seq_str)
        if seqs and seq not in seqs:
            continue
        name = progress[seq_str]["file_name"]
        audio_path = os.path.join(audiobook_dir, name)
        if not os.path.isfile(audio_path):
            print(f"Warning: 听感稿不存在，跳过: {audio_path}", file=sys.stderr)
            continue
        files.append(audio_path)
    return files


def build_speech_config(voice: str) -> speechsdk.SpeechConfig:
    endpoint_url = os.environ.get("AZURE_SPEECH_ENDPOINT")
    speech_key = os.environ.get("AZURE_SPEECH_KEY")
    if not endpoint_url or not speech_key:
        print(
            "Error: 缺少 AZURE_SPEECH_ENDPOINT 或 AZURE_SPEECH_KEY，请在 .env 中设置",
            file=sys.stderr,
        )
        sys.exit(1)

    parsed = urlparse(endpoint_url)
    base_endpoint = f"{parsed.scheme}://{parsed.netloc}"

    config = speechsdk.SpeechConfig(subscription=speech_key, endpoint=base_endpoint)
    config.speech_synthesis_voice_name = voice
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz160KBitRateMonoMp3
    )
    return config


_SSML_TAG_RE = re.compile(
    r"</?(?:say-as|sub|break)\b[^<>]*/?>",
    re.IGNORECASE,
)


def _ssml_tags_balanced(text: str) -> bool:
    """检查 chunk 内 SSML 标签是否成对 (open/close 匹配，自闭合 OK)。"""
    stack: list[str] = []
    for m in _SSML_TAG_RE.finditer(text):
        tag = m.group(0)
        if tag.endswith("/>"):
            continue
        name_match = re.match(r"</?([a-zA-Z\-]+)", tag)
        if not name_match:
            continue
        name = name_match.group(1).lower()
        if tag.startswith("</"):
            if not stack or stack[-1] != name:
                return False
            stack.pop()
        else:
            stack.append(name)
    return not stack


def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """先按空行切段落，再把超长段落按句号/问号/感叹号/分号拆，最后按 max_chars 贪心合并。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        sentences = re.split(r"(?<=[。！？；!?;])", para)
        buf = ""
        for s in sentences:
            if not s:
                continue
            if len(s) > max_chars:
                if buf:
                    pieces.append(buf)
                    buf = ""
                for i in range(0, len(s), max_chars):
                    pieces.append(s[i : i + max_chars])
                continue
            if len(buf) + len(s) > max_chars:
                pieces.append(buf)
                buf = s
            else:
                buf += s
        if buf:
            pieces.append(buf)

    chunks: list[str] = []
    buf = ""
    for p in pieces:
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= max_chars:
            buf += "\n\n" + p
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)

    merged: list[str] = []
    i = 0
    while i < len(chunks):
        cur = chunks[i]
        while not _ssml_tags_balanced(cur):
            if i + 1 >= len(chunks):
                raise ValueError(
                    f"SSML tags are not balanced in the last chunk (len={len(cur)}). "
                    f"This likely means an SSML tag was split across chunk boundaries "
                    f"or the source contains a malformed tag. First 200 chars: {cur[:200]!r}"
                )
            i += 1
            cur = cur + "\n\n" + chunks[i]
        merged.append(cur)
        i += 1
    return merged


def _escape_ssml(text: str) -> str:
    """Escape XML special chars in body text, but preserve allowed SSML tags verbatim."""
    parts: list[str] = []
    last = 0
    for m in _SSML_TAG_RE.finditer(text):
        parts.append(_escape_xml(text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(_escape_xml(text[last:]))
    return "".join(parts)


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_ssml(text: str, voice: str, style: str | None, temperature: float | None) -> str:
    params = []
    if temperature is not None:
        params.append(f"temperature={temperature}")
    voice_attrs = f'name="{voice}"'
    if params:
        voice_attrs += f' parameters="{";".join(params)}"'

    body = _escape_ssml(text)
    if style:
        body = f"[{style}] {body}"

    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">'
        f"<voice {voice_attrs}>{body}</voice></speak>"
    )


def synthesize_chunk(
    text: str, output_path: str, speech_config: speechsdk.SpeechConfig,
    voice: str, style: str | None, temperature: float | None,
) -> tuple[bool, str]:
    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_config
    )
    ssml = build_ssml(text, voice, style, temperature)
    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return True, ""
    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        msg = f"{details.reason}"
        if details.reason == speechsdk.CancellationReason.Error:
            msg += f" | {details.error_details}"
        return False, msg
    return False, f"unexpected reason: {result.reason}"


def synthesize_file(
    text_path: str, output_path: str, speech_config: speechsdk.SpeechConfig,
    voice: str, style: str | None, temperature: float | None,
    idx: int, total: int,
) -> bool:
    print(f"\n[{idx}/{total}] 开始: {os.path.basename(text_path)}")
    print(f"  输入: {text_path}")
    print(f"  输出: {output_path}")

    with open(text_path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        print(f"  跳过: 空文件", file=sys.stderr)
        return False

    chunks = split_text(text)
    n = len(chunks)
    print(f"  切分: {len(text)} 字符 -> {n} 段 (上限 {MAX_CHUNK_CHARS}/段)")

    stem = os.path.splitext(output_path)[0]
    part_paths: list[str] = []
    start_time = time.time()

    for i, chunk in enumerate(chunks, 1):
        part_path = output_path if n == 1 else f"{stem}.part{i:03d}.mp3"
        part_paths.append(part_path)

        if os.path.exists(part_path) and os.path.getsize(part_path) > 0:
            print(f"  [{i}/{n}] 段已存在，跳过 | {os.path.getsize(part_path)/1024:.0f} KB")
            continue

        last_err = ""
        for attempt in range(1, MAX_RETRIES + 1):
            ok, err = synthesize_chunk(chunk, part_path, speech_config, voice, style, temperature)
            if ok:
                print(f"  [{i}/{n}] 段完成 | {len(chunk)} 字符 | {os.path.getsize(part_path)/1024:.0f} KB"
                      + (f" | 重试 {attempt - 1} 次" if attempt > 1 else ""))
                break
            last_err = err
            if os.path.exists(part_path):
                os.remove(part_path)
            if attempt < MAX_RETRIES:
                sleep_s = RETRY_BACKOFF_SECONDS[attempt - 1]
                print(f"  [{i}/{n}] 段合成失败 (第 {attempt}/{MAX_RETRIES} 次): {err}，{sleep_s}s 后重试",
                      file=sys.stderr)
                time.sleep(sleep_s)
        else:
            print(f"  [{i}/{n}] 段合成失败，已重试 {MAX_RETRIES} 次，放弃本章: {last_err}", file=sys.stderr)
            print(f"  已生成的 part 文件保留在: {os.path.dirname(output_path)}，下次运行会续传",
                  file=sys.stderr)
            return False

    if n > 1:
        with open(output_path, "wb") as out:
            for p in part_paths:
                with open(p, "rb") as src:
                    out.write(src.read())
        for p in part_paths:
            os.remove(p)

    elapsed = time.time() - start_time
    size_kb = os.path.getsize(output_path) / 1024
    print(f"[{idx}/{total}] 完成: {os.path.basename(text_path)} | 耗时: {elapsed:.1f}s | {size_kb:.0f} KB")
    return True


def tts_book(
    book_path: str,
    audiobook_dir: str,
    output_dir: str,
    summary_dir: str | None,
    voice: str,
    style: str | None,
    temperature: float | None,
    seqs: list[int] | None = None,
) -> None:
    book_path = os.path.abspath(book_path)
    audiobook_dir = os.path.abspath(audiobook_dir)
    output_dir = os.path.abspath(output_dir)

    summary_dir = summary_dir or detect_summary_dir(book_path)
    if not summary_dir:
        print("Error: 无法自动检测总结目录 (期望 <book_dir>/summaries/progress.json)，请用 --summary-dir 指定", file=sys.stderr)
        sys.exit(1)
    summary_dir = os.path.abspath(summary_dir)
    if not os.path.isfile(os.path.join(summary_dir, "progress.json")):
        print(f"Error: progress.json 不存在: {summary_dir}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(audiobook_dir):
        print(f"Error: 听感稿目录不存在: {audiobook_dir}", file=sys.stderr)
        sys.exit(1)

    audiobook_files = load_audiobook_files(summary_dir, audiobook_dir, seqs)
    if not audiobook_files:
        hint = f" (筛选 seqs={seqs})" if seqs else ""
        print(f"Error: progress.json 中没有可用的听感稿{hint} (检查 {audiobook_dir})", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    speech_config = build_speech_config(voice)

    total = len(audiobook_files)
    print(f"原书: {book_path}")
    print(f"听感稿目录: {audiobook_dir}")
    print(f"progress.json: {os.path.join(summary_dir, 'progress.json')}")
    print(f"音频输出目录: {output_dir}")
    print(f"音色: {voice}")
    print(f"style: {style or '(无)'} | temperature: {temperature}")
    print(f"找到 {total} 份听感稿")

    book_start = time.time()
    succeeded = 0
    skipped = 0
    for idx, text_path in enumerate(audiobook_files, 1):
        stem = os.path.splitext(os.path.basename(text_path))[0]
        output_path = os.path.join(output_dir, f"{stem}.mp3")
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"\n[{idx}/{total}] 跳过 (已存在): {os.path.basename(text_path)} | {os.path.getsize(output_path)/1024:.0f} KB")
            succeeded += 1
            skipped += 1
            continue
        if synthesize_file(text_path, output_path, speech_config, voice, style, temperature, idx, total):
            succeeded += 1

    total_elapsed = time.time() - book_start
    print(f"\n{'='*60}")
    print(f"全部完成! 成功 {succeeded}/{total} 份 (跳过 {skipped} 份已存在) | 总耗时: {total_elapsed:.1f}s | 输出: {output_dir}")
    print(f"{'='*60}")


def _default_audiobook_dir(book_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(book_path)), "audiobook")


def _default_output_dir(book_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(book_path)), "audio")


def main() -> None:
    parser = argparse.ArgumentParser(description="把听感稿目录里的所有 .md 转成 Azure TTS .mp3")
    parser.add_argument("book_path", help="原书 Markdown 文件路径 (用来定位听感稿和输出目录)")
    parser.add_argument("--summary-dir", default=None,
                        help="总结目录路径 (默认: <book_dir>/summaries/)")
    parser.add_argument("--audiobook-dir", default=None,
                        help="听感稿目录 (默认: <book_dir>/audiobook/)")
    parser.add_argument("--output-dir", default=None,
                        help="音频输出目录 (默认: <book_dir>/audio/)")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help=f"Azure TTS voice name (默认: {DEFAULT_VOICE})")
    parser.add_argument("--style", default=DEFAULT_STYLE,
                        help=f"HD voice style tag，写成 [tag] 前缀；传空字符串关闭 (默认: {DEFAULT_STYLE})")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE,
                        help=f"voice 的 temperature，越低越稳 (默认: {DEFAULT_TEMPERATURE})")
    parser.add_argument("--seqs", type=str, default=None,
                        help="只处理指定 seq 的听感稿，逗号分隔 (e.g. 1 或 1,3,5)，方便先测一章")
    args = parser.parse_args()

    if not args.book_path.endswith(".md"):
        print("Error: 只支持 .md 文件", file=sys.stderr)
        sys.exit(1)

    book_path = os.path.abspath(args.book_path)
    if not os.path.isfile(book_path):
        print(f"Error: 原书文件不存在: {book_path}", file=sys.stderr)
        sys.exit(1)

    audiobook_dir = os.path.abspath(args.audiobook_dir or _default_audiobook_dir(book_path))
    output_dir = os.path.abspath(args.output_dir or _default_output_dir(book_path))
    if output_dir == audiobook_dir:
        print("Error: 输出目录不能与听感稿目录相同", file=sys.stderr)
        sys.exit(1)

    seqs = [int(c) for c in args.seqs.split(",")] if args.seqs else None
    style = args.style or None

    try:
        tts_book(book_path, audiobook_dir, output_dir, args.summary_dir,
                 args.voice, style, args.temperature, seqs)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        os._exit(130)


if __name__ == "__main__":
    main()
