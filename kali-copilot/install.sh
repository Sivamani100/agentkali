#!/usr/bin/env bash
# =============================================================================
# install.sh — KaliCopilot automated setup for Kali Linux
# =============================================================================
# Usage:
#   chmod +x install.sh && ./install.sh
#
# What it does:
#   1. Installs system packages (tmux, wmctrl, xdotool, python3-pyqt6, pip, xdpyinfo)
#   2. Creates a Python virtual environment in ~/kali-copilot/.venv
#   3. Installs Python dependencies into the venv
#   4. Creates a launcher script at ~/kali-copilot/kali-copilot.sh
#   5. Optionally creates a ~/.config/kali-copilot/config.json if not present
# =============================================================================
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info()    { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*" >&2; }
heading() { echo -e "\n${BOLD}$*${RESET}"; }

# ---------------------------------------------------------------------------
heading "=== KaliCopilot Installer ==="
echo "Project directory: $SCRIPT_DIR"

# ---------------------------------------------------------------------------
heading "1. System packages"
PKGS=(
    tmux
    wmctrl
    xdotool
    x11-utils        # provides xdpyinfo
    python3-pip
    python3-venv
    python3-pyqt6
    python3-dev
    libxcb-cursor0   # required by some PyQt6 builds
    libxcb-icccm4
    libxcb-image0
    libxcb-keysyms1
    libxcb-randr0
    libxcb-render-util0
    libxcb-xinerama0
    libxcb-xkb1
    libxkbcommon-x11-0
    qt6-wayland      # Wayland fallback
)

if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends "${PKGS[@]}" 2>&1 | grep -E "(Installed|already installed|error)" || true
    info "System packages installed."
else
    warn "apt-get not found — skipping system package installation. Install manually: ${PKGS[*]}"
fi

# ---------------------------------------------------------------------------
heading "2. Python virtual environment"
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" --system-site-packages
    info "Virtual environment created at $VENV_DIR"
else
    info "Virtual environment already exists at $VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# Upgrade pip silently
"$PIP" install --upgrade pip --quiet

# ---------------------------------------------------------------------------
heading "3. Python dependencies"
# Core dependencies
"$PIP" install --quiet \
    google-generativeai \
    openai \
    requests \
    markdown \
    pygments

info "Python dependencies installed."

# ---------------------------------------------------------------------------
heading "4. Package init files"
# Ensure packages are importable
touch "$SCRIPT_DIR/core/__init__.py"
touch "$SCRIPT_DIR/ui/__init__.py"

# ---------------------------------------------------------------------------
heading "5. Launcher script"
LAUNCHER="$SCRIPT_DIR/kali-copilot.sh"
cat > "$LAUNCHER" <<'LAUNCHER_EOF'
#!/usr/bin/env bash
# KaliCopilot launcher
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "ERROR: Virtual environment not found. Run ./install.sh first."
    exit 1
fi

# Export any API keys from shell environment
# (Alternatively set them in ~/.config/kali-copilot/config.json)
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# Launch — set QT_QPA_PLATFORM if no display detected
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "No display detected. Set DISPLAY or WAYLAND_DISPLAY."
    exit 1
fi

# Wayland: use xcb platform for better compatibility
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

exec "$PYTHON" "$SCRIPT_DIR/main.py" "$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
info "Launcher created: $LAUNCHER"

# ---------------------------------------------------------------------------
heading "6. Default config"
CFG_DIR="$HOME/.config/kali-copilot"
CFG_FILE="$CFG_DIR/config.json"
mkdir -p "$CFG_DIR/plugins"

if [ ! -f "$CFG_FILE" ]; then
    cat > "$CFG_FILE" <<'CFG_EOF'
{
  "provider":    "gemini",
  "model":       "gemini-1.5-flash",
  "api_key":     "",
  "openai_base": "https://api.openai.com/v1",
  "ollama_base": "http://localhost:11434",
  "max_steps":   10,
  "auto_mode":   false,
  "temperature": 0.3
}
CFG_EOF
    info "Default config written to $CFG_FILE"
else
    info "Config already exists at $CFG_FILE — skipping."
fi

# ---------------------------------------------------------------------------
heading "=== Installation Complete! ==="
echo ""
echo "  To set your API key (choose one):"
echo ""
echo "  # Gemini:"
echo "  export GEMINI_API_KEY='your_api_key_here'"
echo ""
echo "  # OpenAI:"
echo "  export OPENAI_API_KEY='your_api_key_here'"
echo ""
echo "  # Or local Ollama (no key needed):"
echo "  ollama pull llama3   # first time"
echo ""
echo "  Then launch KaliCopilot:"
echo ""
echo "  ${BOLD}cd $SCRIPT_DIR && ./kali-copilot.sh${RESET}"
echo ""
echo "  Or directly:"
echo "  ${BOLD}python $SCRIPT_DIR/main.py${RESET}"
echo ""
