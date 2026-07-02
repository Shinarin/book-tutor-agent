"""Small adapter over the agent SDKs used by the book tools.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import AsyncIterator

from dotenv import load_dotenv

load_dotenv()

AGENT_PROVIDER = os.environ.get("BOOK_TUTOR_AGENT_PROVIDER", "claude").strip().lower()
DEFAULT_CLAUDE_MODEL = os.environ.get("BOOK_TUTOR_CLAUDE_MODEL", "claude-opus-4.7")
DEFAULT_CODEX_MODEL = os.environ.get("BOOK_TUTOR_CODEX_MODEL", "gpt-5.5")
DEFAULT_CODEX_BIN = os.environ.get("BOOK_TUTOR_CODEX_BIN")
DEFAULT_CODEX_SDK_PYTHON = os.environ.get("BOOK_TUTOR_CODEX_SDK_PYTHON")
DEFAULT_CODEX_REASONING_EFFORT = os.environ.get("BOOK_TUTOR_CODEX_REASONING_EFFORT", "xhigh")

CLAUDE_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
CLAUDE_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN")

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")


@dataclass(frozen=True)
class AgentOptions:
    allowed_tools: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    permission_mode: str | None = None
    cwd: str | None = None


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    text: str = ""
    tool_name: str = ""
    tool_input: dict | None = None


def default_model() -> str:
    return DEFAULT_CODEX_MODEL if AGENT_PROVIDER == "codex" else DEFAULT_CLAUDE_MODEL


def _resolve_model(model: str | None) -> str:
    if AGENT_PROVIDER == "codex":
        if not model or model.startswith("claude-") or model == "sonnet":
            return DEFAULT_CODEX_MODEL
        return model
    return model or DEFAULT_CLAUDE_MODEL


@contextmanager
def _pushd(path: str | None):
    if not path:
        yield
        return

    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _tool_summary(name: str, value: object) -> str:
    return f"{name}={str(value)[:50]}"


def format_event(event: AgentEvent, prefix: str = "") -> str | None:
    if event.kind == "text" and event.text:
        return f"{prefix}[LLM] {event.text[:1000]}"
    if event.kind == "tool":
        args = event.tool_input or {}
        rendered = ", ".join(_tool_summary(k, v) for k, v in args.items())
        return f"{prefix}[Tool] {event.tool_name}({rendered})"
    if event.kind == "status" and event.text:
        return f"{prefix}[Codex] {event.text}"
    return None


async def run_agent(prompt: str, options: AgentOptions) -> AsyncIterator[AgentEvent]:
    if AGENT_PROVIDER == "codex":
        async for event in _run_codex(prompt, options):
            yield event
        return

    async for event in _run_claude(prompt, options):
        yield event


async def run_agent_text(prompt: str, options: AgentOptions) -> str:
    result = ""
    async for event in run_agent(prompt, options):
        if event.kind == "result":
            result = event.text
        elif event.kind == "text" and not result:
            result = event.text
    return result


async def _run_claude(prompt: str, options: AgentOptions) -> AsyncIterator[AgentEvent]:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as exc:
        raise RuntimeError(
            "AGENT_PROVIDER=claude requires claude-agent-sdk. Install requirements.txt first."
        ) from exc

    stderr_buffer: deque[str] = deque(maxlen=200)

    def _on_stderr(line: str) -> None:
        stderr_buffer.append(line)
        print(f"[claude-cli stderr] {line}", file=sys.stderr, flush=True)

    agent_env: dict = {
        **os.environ,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "API_TIMEOUT_MS": "600000",
    }
    if CLAUDE_BASE_URL:
        agent_env["ANTHROPIC_BASE_URL"] = CLAUDE_BASE_URL
    if CLAUDE_AUTH_TOKEN:
        agent_env["ANTHROPIC_AUTH_TOKEN"] = CLAUDE_AUTH_TOKEN

    claude_kwargs: dict = {
        "model": _resolve_model(options.model),
        "stderr": _on_stderr,
        "setting_sources": [], # 没有额外的skills占用上下文
        "env": agent_env,
    }
    if options.allowed_tools is not None:
        claude_kwargs["allowed_tools"] = options.allowed_tools
    if options.max_turns is not None:
        claude_kwargs["max_turns"] = options.max_turns
    if options.permission_mode is not None:
        claude_kwargs["permission_mode"] = options.permission_mode
    if options.cwd is not None:
        claude_kwargs["cwd"] = options.cwd
    claude_options = ClaudeAgentOptions(**claude_kwargs)

    try:
        async for message in query(prompt=prompt, options=claude_options):
            if hasattr(message, "content"):
                for block in (message.content if isinstance(message.content, list) else []):
                    if hasattr(block, "text") and block.text:
                        yield AgentEvent("text", text=block.text)
                    elif hasattr(block, "type") and block.type == "tool_use":
                        yield AgentEvent(
                            "tool",
                            tool_name=getattr(block, "name", ""),
                            tool_input=getattr(block, "input", None) or {},
                        )
            if hasattr(message, "result") and message.result:
                yield AgentEvent("result", text=message.result)
    except Exception as exc:
        captured = "\n".join(stderr_buffer).strip()
        if captured:
            raise RuntimeError(
                f"claude-agent-sdk query failed: {exc}\n"
                f"--- captured claude CLI stderr (last {len(stderr_buffer)} lines) ---\n"
                f"{captured}\n"
                f"--- end stderr ---"
            ) from exc
        raise


async def _run_codex(prompt: str, options: AgentOptions) -> AsyncIterator[AgentEvent]:
    _ensure_codex_sdk_src()

    try:
        from codex_app_server import (
            AppServerConfig,
            AskForApproval,
            AsyncCodex,
            SandboxMode,
            TextInput,
        )
        from codex_app_server.generated.v2_all import (
            SandboxPolicy,
            WorkspaceWriteSandboxPolicy,
        )
    except ImportError as exc:
        raise RuntimeError(
            "AGENT_PROVIDER=codex requires the experimental Codex Python SDK "
            "(codex_app_server). See docs/Codex SDK.md."
        ) from exc

    model = _resolve_model(options.model)
    approval_policy = AskForApproval.model_validate("never")
    developer_instructions = _codex_developer_instructions(options)
    thread_config = _codex_thread_config()
    sandbox_policy = SandboxPolicy(
        WorkspaceWriteSandboxPolicy(
            type="workspaceWrite",
            networkAccess=False,
            writableRoots=_codex_writable_roots(options),
        )
    )
    streamed_text: list[str] = []
    pending_delta = ""
    completed_turn_id = ""

    with _pushd(options.cwd):
        config = _codex_app_server_config(AppServerConfig, options)
        async with AsyncCodex(config=config) as codex:
            thread = await codex.thread_start(
                approval_policy=approval_policy,
                config=thread_config,
                developer_instructions=developer_instructions,
                model=model,
                cwd=options.cwd,
                sandbox=SandboxMode.workspace_write,
            )
            turn = await thread.turn(
                TextInput(prompt),
                approval_policy=approval_policy,
                cwd=options.cwd,
                model=model,
                sandbox_policy=sandbox_policy,
            )
            completed_turn_id = getattr(turn, "id", "")

            stream = turn.stream()
            try:
                async for notification in stream:
                    async for event in _codex_notification_events(notification):
                        if event.kind == "text":
                            pending_delta += event.text
                            streamed_text.append(event.text)
                            if "\n" in pending_delta or len(pending_delta) >= 200:
                                yield AgentEvent("text", text=pending_delta.strip())
                                pending_delta = ""
                            continue
                        yield event
            except (asyncio.CancelledError, KeyboardInterrupt):
                await _interrupt_codex_turn(turn)
                raise
            finally:
                await stream.aclose()

            if pending_delta.strip():
                yield AgentEvent("text", text=pending_delta.strip())

            persisted = await thread.read(include_turns=True)
            persisted_turn = _find_turn_by_id(
                getattr(getattr(persisted, "thread", None), "turns", None),
                completed_turn_id,
            )

    final_response = _assistant_text_from_turn(persisted_turn).strip()
    if not final_response:
        final_response = "".join(streamed_text).strip()
    yield AgentEvent("result", text=final_response)


def _codex_app_server_config(app_server_config: type, options: AgentOptions) -> object:
    if DEFAULT_CODEX_BIN is None:
        config = _codex_runtime_config_if_usable()
        if config is not None:
            if options.cwd and hasattr(config, "cwd"):
                config.cwd = options.cwd
            return config

    codex_bin = _resolve_codex_bin()
    if codex_bin is None:
        raise RuntimeError(
            "Unable to locate codex.exe for the Codex Python SDK. Set "
            "BOOK_TUTOR_CODEX_BIN to the full path of a Codex CLI binary."
        )
    return app_server_config(codex_bin=codex_bin, cwd=options.cwd)


def _codex_runtime_config_if_usable() -> object | None:
    runtime_config = _load_codex_runtime_config()
    if runtime_config is None:
        return None

    codex_bin = _installed_codex_runtime_path()
    if codex_bin is not None and not _codex_app_server_looks_usable(codex_bin):
        return None

    return runtime_config()


def _installed_codex_runtime_path() -> str | None:
    try:
        from codex_app_server.client import _installed_codex_path
    except Exception:
        return None

    try:
        return str(_installed_codex_path())
    except Exception:
        return None


def _codex_app_server_looks_usable(codex_bin: str) -> bool:
    try:
        result = subprocess.run(
            [codex_bin, "app-server", "--help"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return False

    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "--listen" in output


def _load_codex_runtime_config():
    bootstrap = _load_codex_bootstrap()
    if bootstrap is None:
        return None
    return getattr(bootstrap, "runtime_config", None)


def _ensure_codex_sdk_src() -> None:
    bootstrap = _load_codex_bootstrap()
    if bootstrap is not None and hasattr(bootstrap, "ensure_local_sdk_src"):
        bootstrap.ensure_local_sdk_src()


def _load_codex_bootstrap():
    if not DEFAULT_CODEX_SDK_PYTHON:
        return None
    sdk_dir = Path(DEFAULT_CODEX_SDK_PYTHON)
    bootstrap_path = sdk_dir / "examples" / "_bootstrap.py"
    if not bootstrap_path.is_file():
        return None

    examples_dir = str(bootstrap_path.parent)
    if examples_dir not in sys.path:
        sys.path.insert(0, examples_dir)

    spec = importlib.util.spec_from_file_location("book_tutor_codex_bootstrap", bootstrap_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _codex_thread_config() -> dict[str, str]:
    return {"model_reasoning_effort": DEFAULT_CODEX_REASONING_EFFORT}


def _codex_writable_roots(options: AgentOptions) -> list[str]:
    roots: list[str] = []
    if options.cwd:
        roots.append(os.path.abspath(options.cwd))
    return roots


async def _interrupt_codex_turn(turn: object) -> None:
    interrupt = getattr(turn, "interrupt", None)
    if interrupt is None:
        return
    try:
        await interrupt()
    except Exception:
        return


def _codex_developer_instructions(options: AgentOptions) -> str:
    notes: list[str] = []

    if options.allowed_tools:
        notes.append(
            "Treat these as the caller's intended high-level operation limits: "
            f"{', '.join(options.allowed_tools)}."
        )

    notes.append(
        "For file changes, prefer Codex file editing facilities. "
        "Avoid shell-based patching for large multi-line Unicode content on Windows."
    )

    # notes.append(
    #     "When reading or writing Markdown/text files on Windows, treat them as UTF-8. "
    #     "If content appears garbled, retry the read with an explicit UTF-8 mode instead "
    #     "of summarizing or preserving mojibake."
    # )

    return "\n".join(notes)

def _resolve_codex_bin() -> str | None:
    if DEFAULT_CODEX_BIN:
        return DEFAULT_CODEX_BIN

    from_path = shutil.which("codex")
    if from_path:
        return from_path

    vscode_extensions = Path.home() / ".vscode" / "extensions"
    candidates = sorted(
        vscode_extensions.glob("openai.chatgpt-*/bin/windows-*/codex.exe"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return str(candidates[0])

    return None


async def _codex_notification_events(notification: object) -> AsyncIterator[AgentEvent]:
    method = getattr(notification, "method", "") or ""
    payload = getattr(notification, "payload", None)

    if method == "turn/started":
        yield AgentEvent("status", text="turn started")
        return

    if method == "turn/completed":
        turn = getattr(payload, "turn", None)
        status = getattr(getattr(turn, "status", None), "value", None) or getattr(turn, "status", "completed")
        yield AgentEvent("status", text=f"turn completed: {status}")
        return

    if method == "item/agentMessage/delta":
        delta = getattr(payload, "delta", "")
        if delta:
            yield AgentEvent("text", text=delta)
        return

    if method in {
        "command/exec/outputDelta",
        "item/commandExecution/outputDelta",
        "item/fileChange/outputDelta",
    }:
        delta = getattr(payload, "delta", "")
        if delta:
            yield AgentEvent("tool", tool_name=method, tool_input={"delta": delta})
        return

    if "command" in method or "fileChange" in method or "Tool" in method or "tool" in method:
        yield AgentEvent("tool", tool_name=method, tool_input=_model_preview(payload))


def _model_preview(value: object) -> dict:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json", exclude_none=True)
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if isinstance(value, dict):
        return value
    return {"value": str(value)}


def _find_turn_by_id(turns: object, turn_id: str) -> object | None:
    for turn in turns or []:
        if getattr(turn, "id", None) == turn_id:
            return turn
    return None


def _assistant_text_from_turn(turn: object | None) -> str:
    if turn is None:
        return ""

    chunks: list[str] = []
    for item in getattr(turn, "items", []) or []:
        raw_item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(raw_item, dict):
            continue

        item_type = raw_item.get("type")
        if item_type == "agentMessage":
            text = raw_item.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
            continue

        if item_type != "message" or raw_item.get("role") != "assistant":
            continue

        for content in raw_item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)

    return "".join(chunks)
