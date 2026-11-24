"""
core/terminal_bridge.py
-----------------------
tmux terminal bridge for KaliCopilot.

Responsibilities:
  - Spawn / attach to a dedicated tmux session ("kali_agent_session")
  - Inject commands into the left pane using tmux send-keys
  - Detect command completion via a unique sentinel echo
  - Capture pane output (stdout/stderr) and parse exit codes
  - Tile the host terminal to the left 50% of the screen via wmctrl/xdotool
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SESSION_NAME = "kali_agent_session"
WINDOW_NAME  = "agent"
DONE_MARKER  = "___CMD_DONE_{exit_code}___"
MARKER_RE    = re.compile(r"___CMD_DONE_(\d+)___")
TIMEOUT_DEFAULT = 120          # seconds to wait for a command to finish
POLL_INTERVAL   = 0.10         # Snappy 100ms polling for instant command detection
CAPTURE_LINES   = 200          # lines to capture from pane

# Commands that need an extra warning / confirmation even in auto-mode
DANGEROUS_PATTERNS = [
    re.compile(r"rm\s+-[^-]*r[^-]*f\s+/"),          # rm -rf /
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\(\)\s*\{.*\|.*\&.*\}"),           # fork bomb
    re.compile(r"\b(shutdown|reboot|poweroff)\b"),
    re.compile(r"\biptables\b.*-F"),                  # flush all firewall rules
    re.compile(r"\bufw\b.*--force\s+reset"),
    re.compile(r"\bsystemctl\s+(stop|disable)\s+ssh\b"),
]

PASSWORD_PATTERNS = [
    re.compile(r"\[sudo\]\s+password", re.IGNORECASE),
    re.compile(r"password\s+for\s+\w+:", re.IGNORECASE),
    re.compile(r"enter\s+passphrase", re.IGNORECASE),
    re.compile(r"enter\s+password", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CommandResult:
    command: str
    exit_code: int
    output: str
    timed_out: bool = False
    password_prompt: bool = False
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess, suppressing stderr noise from tmux."""
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=check,
    )


def is_dangerous(command: str) -> bool:
    """Return True if the command matches a dangerous pattern."""
    for pat in DANGEROUS_PATTERNS:
        if pat.search(command):
            return True
    return False


def has_password_prompt(text: str) -> bool:
    """Return True if the pane output contains a password prompt."""
    for pat in PASSWORD_PATTERNS:
        if pat.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# TmuxBridge
# ---------------------------------------------------------------------------
class TmuxBridge:
    """Controls a dedicated tmux session and exposes agent-facing methods."""

    def __init__(self, session: str = SESSION_NAME):
        self.session = session
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def ensure_session(self) -> bool:
        """Create the tmux session if it does not already exist."""
        result = _run(["tmux", "has-session", "-t", self.session])
        if result.returncode == 0:
            logger.info("Existing tmux session '%s' found.", self.session)
            return False  # already existed
        logger.info("Creating new tmux session '%s'.", self.session)
        _run(["tmux", "new-session", "-d", "-s", self.session, "-n", WINDOW_NAME], check=True)
        return True  # newly created

    def session_exists(self) -> bool:
        return _run(["tmux", "has-session", "-t", self.session]).returncode == 0

    # ------------------------------------------------------------------
    # Pane helpers
    # ------------------------------------------------------------------
    def _target(self) -> str:
        return f"{self.session}:{WINDOW_NAME}"

    def capture_pane(self, lines: int = CAPTURE_LINES, ansi: bool = False) -> str:
        """
        Return the last `lines` lines from the tmux pane as a string.
        If ansi=True, tmux captures escape sequences (-e) for native terminal color rendering.
        """
        cmd = ["tmux", "capture-pane", "-p", "-t", self._target(),
               "-S", f"-{lines}", "-E", "-"]
        if ansi:
            cmd.insert(2, "-e")
        result = _run(cmd)
        return result.stdout

    def send_keys(self, keys: str, enter: bool = True) -> None:
        """Send keystrokes to the tmux pane (optionally append Enter)."""
        cmd = ["tmux", "send-keys", "-t", self._target(), keys]
        if enter:
            cmd.append("Enter")
        _run(cmd)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------
    def run_command(
        self,
        command: str,
        timeout: int = TIMEOUT_DEFAULT,
        progress_cb=None,
    ) -> CommandResult:
        """
        Execute *command* in the tmux pane and block until it finishes.

        Uses a unique sentinel marker to detect completion and parse the
        exit code.  Returns a CommandResult with parsed output.

        Args:
            command:     Shell command to execute.
            timeout:     Max seconds to wait (default 120).
            progress_cb: Optional callable(output_so_far: str) for live
                         streaming – called at each poll cycle.
        """
        sentinel = f'; echo "___CMD_DONE_$?___"'
        full_cmd = command.rstrip() + sentinel

        with self._lock:
            start = time.monotonic()
            self.send_keys(full_cmd, enter=True)

            output_lines: list[str] = []
            timed_out = False
            exit_code = -1
            password_detected = False

            while True:
                elapsed = time.monotonic() - start
                if elapsed > timeout:
                    timed_out = True
                    break

                raw = self.capture_pane(CAPTURE_LINES)
                lines = raw.splitlines()

                # Check for password prompt BEFORE looking for done marker
                if has_password_prompt(raw):
                    password_detected = True
                    # Still wait – user will type password in terminal
                    # We do NOT break so we can eventually catch the marker.

                # Walk lines from the bottom to find our sentinel
                marker_found = False
                for i, line in enumerate(reversed(lines)):
                    m = MARKER_RE.search(line)
                    if m:
                        exit_code = int(m.group(1))
                        # Collect lines above the marker line (command output)
                        above_idx = len(lines) - 1 - i
                        output_lines = lines[max(0, above_idx - 300) : above_idx]
                        marker_found = True
                        break

                if marker_found:
                    break

                if progress_cb:
                    progress_cb(raw)

                time.sleep(POLL_INTERVAL)

            duration = time.monotonic() - start

            # Clean the output: strip the echo'd full_cmd line itself
            # and any trailing empty lines
            raw_output = "\n".join(output_lines).strip()

            return CommandResult(
                command=command,
                exit_code=exit_code,
                output=raw_output,
                timed_out=timed_out,
                password_prompt=password_detected,
                duration_s=round(duration, 2),
            )

    def get_cwd(self) -> str:
        """Return the current working directory of the shell in the pane."""
        result = _run([
            "tmux", "display-message", "-t", self._target(),
            "-p", "#{pane_current_path}"
        ])
        return result.stdout.strip()

    # ------------------------------------------------------------------
    # Window tiling
    # ------------------------------------------------------------------
    @staticmethod
    def tile_terminal_left(terminal_pid: Optional[int] = None) -> bool:
        """
        Attempt to tile the most-recently-focused terminal window to the
        left 50% of the screen using wmctrl, then xdotool as fallback.

        Returns True on success, False if neither tool is available.
        """
        try:
            # Get screen dimensions
            result = _run(["xdpyinfo"])
            dims_match = re.search(r"dimensions:\s+(\d+)x(\d+)\s+pixels", result.stdout)
            if not dims_match:
                return False
            screen_w = int(dims_match.group(1))
            screen_h = int(dims_match.group(2))
            half_w = screen_w // 2

            # Try wmctrl first
            wmctrl_check = _run(["which", "wmctrl"])
            if wmctrl_check.returncode == 0:
                # Find window ID of the terminal emulator
                wm_list = _run(["wmctrl", "-l", "-p"])
                terminal_keywords = ["terminal", "qterminal", "konsole",
                                     "xterm", "gnome-terminal", "xfce4-terminal",
                                     "tilix", "alacritty", "kitty"]
                win_id = None
                for line in wm_list.stdout.splitlines():
                    lower = line.lower()
                    if any(kw in lower for kw in terminal_keywords):
                        parts = line.split()
                        if parts:
                            win_id = parts[0]
                            break
                if win_id:
                    # Remove maximized state first
                    _run(["wmctrl", "-i", "-r", win_id, "-b",
                          "remove,maximized_vert,maximized_horz"])
                    time.sleep(0.1)
                    # Move and resize: x=0, y=0, w=half_w, h=screen_h
                    _run(["wmctrl", "-i", "-r", win_id, "-e",
                          f"0,0,0,{half_w},{screen_h}"])
                    return True

            # Fallback: xdotool
            xdotool_check = _run(["which", "xdotool"])
            if xdotool_check.returncode == 0:
                win_id_result = _run(["xdotool", "search", "--onlyvisible",
                                       "--class", "terminal"])
                if not win_id_result.stdout.strip():
                    win_id_result = _run(["xdotool", "getactivewindow"])
                win_id = win_id_result.stdout.strip().splitlines()[0]
                if win_id:
                    _run(["xdotool", "windowsize", win_id, str(half_w), str(screen_h)])
                    _run(["xdotool", "windowmove", win_id, "0", "0"])
                    return True

        except Exception as e:
            logger.warning("Window tiling failed: %s", e)

        return False
