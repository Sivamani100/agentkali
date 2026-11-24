#!/usr/bin/env bash
# =============================================================================
# run_tui.sh — KaliCopilot v2.0 Terminal Launcher
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv" --system-site-packages
    "$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip textual requests openai --quiet
fi

# Ensure tmux session is ready
if ! tmux has-session -t kali_agent_session 2>/dev/null; then
    tmux new-session -d -s kali_agent_session -n agent
fi

# Launch TUI inside the terminal window
exec "$PYTHON" "$SCRIPT_DIR/main_tui.py" "$@"
