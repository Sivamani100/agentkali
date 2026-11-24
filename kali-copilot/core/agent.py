"""
core/agent.py
-------------
Autonomous ReAct (Reason → Act → Observe) agent loop for KaliCopilot.

Supports:
  - Google Gemini (gemini-1.5-flash / gemini-1.5-pro via google-generativeai)
  - OpenAI-compatible APIs (GPT-4o etc.)
  - Local Ollama (llama3, mistral, etc.)

The agent uses structured tool calling to run terminal commands, read the
terminal buffer, and ask the user for confirmation before destructive ops.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Iterator

from core.terminal_bridge import TmuxBridge, CommandResult, is_dangerous

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_STEPS = 10

SYSTEM_PROMPT = """\
You are KaliCopilot v2.0, an elite autonomous AI command-line security copilot embedded inside a live Kali Linux terminal session.
You have direct terminal execution access via tools.

**OPERATIONAL DIRECTIVES:**
1. **BE FAST & ULTRA-CONCISE**: Output zero conversational filler or disclaimers.
2. **EXECUTE IMMEDIATELY**: To fulfill the user's objective, invoke `run_terminal_command` directly.
3. **DIAGNOSE & CHAIN**: Inspect command exit codes and output. If a command fails or reveals partial info, chain follow-up diagnostic or remediation commands autonomously (up to {max_steps} steps).
4. **SAFETY FIRST**: For destructive actions (disk formatting, firewall flush, recursive root deletions), use `ask_user_confirmation`.
5. **FINAL SUMMARY**: Conclude with a clean, 2-4 bullet summary of actions taken, status, and recommended next steps.
""".strip()

# ---------------------------------------------------------------------------
# Tool definitions (for LLM function calling)
# ---------------------------------------------------------------------------
TOOLS_SCHEMA_GEMINI = [
    {
        "name": "run_terminal_command",
        "description": (
            "Execute a shell command in the user's live Kali Linux terminal. "
            "Returns stdout/stderr and exit code. Use for ANY shell operation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The exact shell command to run."},
                "reason":  {"type": "string", "description": "One-sentence reason for running this command."},
            },
            "required": ["command", "reason"],
        },
    },
    {
        "name": "read_terminal_screen",
        "description": "Read the current terminal buffer without running a command. Useful for checking what is currently on screen.",
        "parameters": {
            "type": "object",
            "properties": {
                "lines": {"type": "integer", "description": "Number of recent lines to read (default 50, max 200)."},
            },
            "required": [],
        },
    },
    {
        "name": "ask_user_confirmation",
        "description": "Pause the agent and ask the user to approve or reject a command before running it. Use for destructive or high-impact operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command you want to run."},
                "warning": {"type": "string", "description": "Plain-English explanation of risks."},
            },
            "required": ["command", "warning"],
        },
    },
]

# OpenAI-format tools (superset of Ollama tool format)
TOOLS_SCHEMA_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        }
    }
    for tool in TOOLS_SCHEMA_GEMINI
]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class AgentMessage:
    """A single entry in the conversation / execution log."""
    role: str          # "user" | "assistant" | "tool_result" | "system"
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    command_result: Optional[CommandResult] = None


@dataclass
class AgentConfig:
    provider: str = "gemini"              # "gemini" | "openai" | "ollama"
    model: str    = "gemini-1.5-flash"
    api_key: str  = ""
    ollama_base:  str = "http://localhost:11434"
    openai_base:  str = "https://api.openai.com/v1"
    max_steps:    int = DEFAULT_MAX_STEPS
    auto_mode:    bool = False             # True → run safe commands without asking
    temperature:  float = 0.3


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------
class _GeminiAdapter:
    """Thin wrapper around google-generativeai for function calling."""

    def __init__(self, config: AgentConfig):
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=config.api_key)
        self._model = genai.GenerativeModel(
            model_name=config.model,
            tools=TOOLS_SCHEMA_GEMINI,
            system_instruction=SYSTEM_PROMPT.format(max_steps=config.max_steps),
        )
        self._chat = self._model.start_chat(enable_automatic_function_calling=False)
        self._config = config

    def chat(self, messages: list[AgentMessage], on_token_cb: Optional[Callable[[str], None]] = None) -> dict:
        """Send the conversation and return a normalised response dict."""
        import google.generativeai as genai

        # Build Gemini history from messages (all but last user turn)
        history = []
        for m in messages[:-1]:
            if m.role == "user":
                history.append({"role": "user", "parts": [m.content]})
            elif m.role == "assistant":
                history.append({"role": "model", "parts": [m.content]})
            elif m.role == "tool_result":
                history.append({"role": "user", "parts": [
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=m.tool_name or "tool",
                            response={"result": m.content},
                        )
                    )
                ]})

        self._chat.history = history
        last = messages[-1]
        response = self._chat.send_message(last.content)
        return self._normalise(response)

    def _normalise(self, response) -> dict:
        """Convert Gemini response to a provider-agnostic dict."""
        part = response.candidates[0].content.parts[0]
        if hasattr(part, "function_call") and part.function_call.name:
            fc = part.function_call
            return {
                "type": "tool_call",
                "tool_name": fc.name,
                "tool_args": dict(fc.args),
                "text": "",
            }
        return {"type": "text", "text": part.text, "tool_name": None, "tool_args": None}


class _OpenAIAdapter:
    """Works with any OpenAI-compatible API (OpenAI, Ollama /v1, LM Studio)."""

    def __init__(self, config: AgentConfig):
        try:
            from openai import OpenAI  # type: ignore
            self._client = OpenAI(api_key=config.api_key, base_url=config.openai_base)
        except ImportError:
            raise ImportError("Install 'openai' package: pip install openai")
        self._config = config

    def chat(self, messages: list[AgentMessage], on_token_cb: Optional[Callable[[str], None]] = None) -> dict:
        oai_messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(max_steps=self._config.max_steps)}
        ]
        for m in messages:
            if m.role in ("user", "assistant"):
                oai_messages.append({"role": m.role, "content": m.content})
            elif m.role == "tool_result":
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_name or "call_0",
                    "content": m.content,
                })

        response = self._client.chat.completions.create(
            model=self._config.model,
            messages=oai_messages,
            tools=TOOLS_SCHEMA_OPENAI,
            tool_choice="auto",
            temperature=self._config.temperature,
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            return {
                "type": "tool_call",
                "tool_name": tc.function.name,
                "tool_args": json.loads(tc.function.arguments),
                "text": msg.content or "",
            }
        return {"type": "text", "text": msg.content or "", "tool_name": None, "tool_args": None}


class _OllamaAdapter:
    """
    Ollama adapter using prompt-based ReAct tool calling.
    Works with ANY Ollama model (llama3, qwen2.5, mistral, deepseek, etc.)
    by embedding tool schemas in the system prompt and parsing
    tool calls from the model's output.
    """

    _TOOL_PROMPT = """
You have access to terminal tools. To execute a command, output ONLY a JSON block like this:

```json
{"tool": "run_terminal_command", "args": {"command": "ip -c a", "reason": "inspect network interfaces"}}
```

Available tools:
1. run_terminal_command(command: str, reason: str) — Execute a shell command in the terminal. Returns stdout/stderr and exit code.
2. read_terminal_screen(lines: int) — Read current terminal buffer without running a command.
3. ask_user_confirmation(command: str, warning: str) — Ask user to approve a dangerous command before running it.

When your goal is complete or you want to give a final answer, respond in plain text (no JSON block).
Be direct, fast, and concise.
""".strip()

    def __init__(self, config: AgentConfig):
        import requests  # lightweight HTTP, always available
        self._base = config.ollama_base.rstrip("/")
        self._model = config.model
        self._config = config
        self._requests = requests

    def chat(self, messages: list["AgentMessage"], on_token_cb: Optional[Callable[[str], None]] = None) -> dict:
        import json, re

        system = SYSTEM_PROMPT.format(max_steps=self._config.max_steps) + "\n\n" + self._TOOL_PROMPT

        oai_messages = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                oai_messages.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                oai_messages.append({"role": "assistant", "content": m.content})
            elif m.role == "tool_result":
                oai_messages.append({
                    "role": "user",
                    "content": f"[Tool result for {m.tool_name}]:\n{m.content}"
                })

        payload = {
            "model": self._model,
            "messages": oai_messages,
            "stream": True,
            "options": {
                "temperature": 0.1,      # Low temperature for precise command generation
                "num_predict": 512,      # Cap tokens for ultra-fast generation
                "top_p": 0.9,
            },
        }

        try:
            resp = self._requests.post(
                f"{self._base}/api/chat",
                json=payload,
                stream=True,
                timeout=120,
            )
            resp.raise_for_status()
            collected = []
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        collected.append(content)
                        if on_token_cb:
                            on_token_cb(content)
                except Exception:
                    pass
            text = "".join(collected).strip()
        except Exception as e:
            raise RuntimeError(f"Ollama request failed: {e}")

        # Parse tool call from model output
        call = None
        start_idx = 0

        # 1. Look for ```json ... ``` block
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            try:
                call = json.loads(json_match.group(1))
                start_idx = json_match.start()
            except Exception:
                pass

        # 2. Look between outermost curly braces if "tool" or "run_terminal_command" or "command" is present
        if not call and any(k in text for k in ("tool", "run_terminal_command", "command", "args")):
            fb = text.find("{")
            lb = text.rfind("}")
            if fb != -1 and lb > fb:
                try:
                    call = json.loads(text[fb:lb+1])
                    start_idx = fb
                except Exception:
                    pass

        if call and isinstance(call, dict):
            tool_name = call.get("tool") or call.get("name") or call.get("action", "")
            tool_args = call.get("args") or call.get("arguments") or call.get("action_input", {})
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except Exception:
                    tool_args = {"command": tool_args, "reason": "Execute shell command"}

            if tool_name in ("run_terminal_command", "read_terminal_screen", "ask_user_confirmation"):
                visible = text[:start_idx].strip()
                return {
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "text": visible,
                }

        # 3. Fallback: If smaller model outputs ```bash ... ``` or `Command: <cmd>`
        cmd_match = re.search(r"```(?:bash|sh)\s*\n([^\n`]+)\n```", text)
        if cmd_match:
            cmd = cmd_match.group(1).strip()
            # If it's a single line command, turn it into a tool call
            if cmd and "\n" not in cmd and not cmd.startswith("#"):
                visible = text[:cmd_match.start()].strip()
                return {
                    "type": "tool_call",
                    "tool_name": "run_terminal_command",
                    "tool_args": {"command": cmd, "reason": "Extracted command from recommendation"},
                    "text": visible,
                }

        return {"type": "text", "text": text, "tool_name": None, "tool_args": None}


# ---------------------------------------------------------------------------
# Plugin loader
# ---------------------------------------------------------------------------
def _load_plugins(plugin_dir: str) -> dict[str, Callable]:
    """Dynamically load @register_tool decorated functions from plugin_dir."""
    import importlib.util
    import sys

    registry: dict[str, Callable] = {}
    if not os.path.isdir(plugin_dir):
        return registry

    for fname in os.listdir(plugin_dir):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(plugin_dir, fname)
        spec = importlib.util.spec_from_file_location(fname[:-3], path)
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        # Provide the register_tool decorator to plugin modules
        module.register_tool = lambda fn: registry.update({fn.__name__: fn}) or fn  # type: ignore
        try:
            spec.loader.exec_module(module)  # type: ignore
        except Exception as e:
            logger.warning("Failed to load plugin %s: %s", fname, e)

    return registry


# ---------------------------------------------------------------------------
# Main Agent class
# ---------------------------------------------------------------------------
class KaliAgent:
    """
    Autonomous ReAct agent.  Run via `run_async(goal, callbacks)`.

    Callbacks:
        on_message(msg: AgentMessage)       → new chat entry to display
        on_tool_start(name, args)           → tool is about to execute
        on_tool_result(result: CommandResult) → command finished
        on_confirm_request(command, warning, approve_cb, reject_cb)
        on_step(step, max_steps)            → progress indicator
        on_done()                           → loop finished
        on_error(err: str)                  → fatal error
    """

    def __init__(
        self,
        config: AgentConfig,
        bridge: TmuxBridge,
        plugin_dir: str = os.path.expanduser("~/.config/kali-copilot/plugins"),
    ):
        self.config = config
        self.bridge = bridge
        self.plugins = _load_plugins(plugin_dir)
        self._stopped = threading.Event()
        self._adapter = self._build_adapter(config)
        self._pending_confirm: Optional[threading.Event] = None
        self._confirm_approved: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def stop(self):
        """Signal the agent loop to stop after the current step."""
        self._stopped.set()
        logger.info("Agent stop requested.")

    def approve_confirm(self):
        """Called from UI when user clicks [Approve & Run]."""
        self._confirm_approved = True
        if self._pending_confirm:
            self._pending_confirm.set()

    def reject_confirm(self):
        """Called from UI when user clicks [Reject]."""
        self._confirm_approved = False
        if self._pending_confirm:
            self._pending_confirm.set()

    def run_async(
        self,
        goal: str,
        history: list[AgentMessage],
        callbacks: dict[str, Callable],
    ) -> threading.Thread:
        """
        Start the agent loop in a background thread and return it.
        The caller is responsible for joining or tracking the thread.
        """
        self._stopped.clear()
        t = threading.Thread(
            target=self._loop,
            args=(goal, list(history), callbacks),
            daemon=True,
        )
        t.start()
        return t

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_adapter(self, config: AgentConfig):
        if config.provider == "gemini":
            return _GeminiAdapter(config)
        elif config.provider == "ollama":
            return _OllamaAdapter(config)
        else:
            return _OpenAIAdapter(config)

    def _context_prefix(self) -> str:
        """Build a concise live terminal context snippet for the LLM."""
        try:
            cwd = self.bridge.get_cwd()
            recent = self.bridge.capture_pane(15)
            # Filter empty lines and limit to 400 chars for maximum CPU performance
            clean_lines = [l for l in recent.splitlines() if l.strip()][-10:]
            snippet = "\n".join(clean_lines)[-400:]
            if snippet.strip():
                return f"[Directory: {cwd}]\n[Recent terminal activity]:\n{snippet}\n\n"
            return f"[Directory: {cwd}]\n\n"
        except Exception:
            return ""

    def _loop(
        self,
        goal: str,
        history: list[AgentMessage],
        cb: dict[str, Callable],
    ):
        """Main agent ReAct loop (runs in background thread)."""
        on_msg     = cb.get("on_message",        lambda m: None)
        on_ts      = cb.get("on_tool_start",     lambda n, a: None)
        on_tr      = cb.get("on_tool_result",    lambda r: None)
        on_confirm = cb.get("on_confirm_request", lambda c, w, ap, rj: None)
        on_step    = cb.get("on_step",           lambda s, m: None)
        on_done    = cb.get("on_done",           lambda: None)
        on_error   = cb.get("on_error",          lambda e: None)
        on_token   = cb.get("on_token",          lambda t: None)

        # Inject terminal context into the goal
        ctx = self._context_prefix()
        augmented_goal = ctx + goal
        messages = list(history) + [AgentMessage(role="user", content=augmented_goal)]

        for step in range(1, self.config.max_steps + 1):
            if self._stopped.is_set():
                on_msg(AgentMessage(role="assistant", content="🛑 Agent stopped by user."))
                break

            on_step(step, self.config.max_steps)

            try:
                response = self._adapter.chat(messages, on_token_cb=on_token)
            except Exception as e:
                on_error(f"LLM error on step {step}: {e}")
                break

            if response["type"] == "text":
                # LLM gave a final text answer — we're done
                final = AgentMessage(role="assistant", content=response["text"])
                on_msg(final)
                messages.append(final)
                break

            # ---- Tool call ------------------------------------------------
            tool_name = response["tool_name"]
            tool_args = response["tool_args"] or {}

            # Optional explanation text before the tool call
            if response.get("text"):
                think_msg = AgentMessage(role="assistant", content=response["text"])
                on_msg(think_msg)
                messages.append(think_msg)

            on_ts(tool_name, tool_args)

            tool_result_content = ""

            if tool_name == "run_terminal_command":
                cmd = tool_args.get("command", "")
                reason = tool_args.get("reason", "")
                tool_result_content = self._handle_run_command(
                    cmd, reason, on_msg, on_tr, on_confirm, messages
                )

            elif tool_name == "read_terminal_screen":
                lines = min(int(tool_args.get("lines", 50)), 200)
                screen = self.bridge.capture_pane(lines)
                tool_result_content = f"Terminal screen ({lines} lines):\n```\n{screen}\n```"
                screen_msg = AgentMessage(
                    role="tool_result",
                    content=tool_result_content,
                    tool_name=tool_name,
                )
                on_msg(screen_msg)
                messages.append(screen_msg)

            elif tool_name == "ask_user_confirmation":
                cmd = tool_args.get("command", "")
                warning = tool_args.get("warning", "")
                approved = self._wait_for_confirmation(cmd, warning, on_confirm)
                if approved:
                    tool_result_content = self._handle_run_command(
                        cmd, "User approved execution.", on_msg, on_tr, on_confirm, messages
                    )
                else:
                    tool_result_content = "User rejected the command. Do not retry it."
                    reject_msg = AgentMessage(
                        role="tool_result",
                        content=tool_result_content,
                        tool_name=tool_name,
                    )
                    on_msg(reject_msg)
                    messages.append(reject_msg)

            elif tool_name in self.plugins:
                # Dynamic plugin tool
                try:
                    plugin_result = self.plugins[tool_name](**tool_args)
                    tool_result_content = str(plugin_result)
                except Exception as e:
                    tool_result_content = f"Plugin error: {e}"
                plugin_msg = AgentMessage(
                    role="tool_result",
                    content=tool_result_content,
                    tool_name=tool_name,
                )
                on_msg(plugin_msg)
                messages.append(plugin_msg)

            else:
                tool_result_content = f"Unknown tool: {tool_name}"
                unknown_msg = AgentMessage(
                    role="tool_result",
                    content=tool_result_content,
                    tool_name=tool_name,
                )
                on_msg(unknown_msg)
                messages.append(unknown_msg)

        else:
            # Exceeded max_steps
            limit_msg = AgentMessage(
                role="assistant",
                content=(
                    f"⚠️ Reached maximum step limit ({self.config.max_steps}). "
                    "The task may not be fully complete. Please review the terminal output "
                    "and continue manually or start a new query."
                ),
            )
            on_msg(limit_msg)

        on_done()

    def _handle_run_command(
        self,
        cmd: str,
        reason: str,
        on_msg,
        on_tr,
        on_confirm,
        messages: list[AgentMessage],
    ) -> str:
        """Execute a command (with safety checks) and return result text."""
        # Safety gate
        if is_dangerous(cmd):
            approved = self._wait_for_confirmation(
                cmd,
                "⚠️ This command is potentially destructive or irreversible.",
                on_confirm,
            )
            if not approved:
                result_text = "User rejected dangerous command. Skipping."
                messages.append(AgentMessage(
                    role="tool_result",
                    content=result_text,
                    tool_name="run_terminal_command",
                ))
                return result_text

        # In manual mode (not auto), ask confirmation for every command
        if not self.config.auto_mode:
            approved = self._wait_for_confirmation(
                cmd,
                f"Agent wants to run this command:\n`{cmd}`\n\nReason: {reason}",
                on_confirm,
            )
            if not approved:
                result_text = "User rejected the command."
                messages.append(AgentMessage(
                    role="tool_result",
                    content=result_text,
                    tool_name="run_terminal_command",
                ))
                return result_text

        result = self.bridge.run_command(cmd)
        on_tr(result)

        status = "✅ Success" if result.exit_code == 0 else f"❌ Exit code {result.exit_code}"
        if result.timed_out:
            status += " (⏱️ Timed out)"
        if result.password_prompt:
            status += "\n\n⚠️ **Password prompt detected** — please type your password in the left terminal pane."

        result_text = (
            f"{status}\n"
            f"**Command:** `{result.command}`\n"
            f"**Output:**\n```\n{result.output[-3000:]}\n```"
        )

        result_msg = AgentMessage(
            role="tool_result",
            content=result_text,
            tool_name="run_terminal_command",
            tool_args={"command": cmd, "reason": reason},
            command_result=result,
        )
        on_msg(result_msg)
        messages.append(result_msg)
        return result_text

    def _wait_for_confirmation(
        self,
        command: str,
        warning: str,
        on_confirm,
    ) -> bool:
        """Block the agent thread until the user approves or rejects."""
        event = threading.Event()
        self._pending_confirm = event
        self._confirm_approved = False

        def approve():
            self._confirm_approved = True
            event.set()

        def reject():
            self._confirm_approved = False
            event.set()

        on_confirm(command, warning, approve, reject)
        event.wait()  # blocks agent thread only
        self._pending_confirm = None
        return self._confirm_approved
