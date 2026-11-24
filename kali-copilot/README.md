# KaliCopilot 🤖⚡

**An autonomous AI agent embedded inside your live Kali Linux terminal — with a Chrome-style slide-out drawer UI.**

---

## What It Does

KaliCopilot floats a small **"✨ Ask AI"** pill button in the bottom-right corner of your screen. Clicking it slides open a dark-mode half-screen drawer docked to the right side of your display, while your Kali terminal is auto-tiled to the left half.

The agent is **not a passive chatbot** — it autonomously executes shell commands in your live `tmux` terminal session, reads the output, chains follow-up commands, and reports results as interactive **Command Cards** in the UI.

```
┌────────────────────┬────────────────────┐
│                    │  🤖 KaliCopilot    │
│   Kali Terminal    │  ─────────────── │
│   (tmux pane)      │  User: scan ports  │
│                    │  Agent: Running... │
│   $ nmap -sS...    │  ┌─────────────┐  │
│   Starting Nmap... │  │$ nmap -sS   │  │
│                    │  │✅ 0  2.3s   │  │
│                    │  └─────────────┘  │
│                    │  [Input box]       │
└────────────────────┴────────────────────┘
                              [✨ Ask AI]
```

---

## Project Structure

```
kali-copilot/
├── install.sh                   # Automated setup script
├── main.py                      # Entry point
├── kali-copilot.sh              # Launcher (created by install.sh)
├── core/
│   ├── __init__.py
│   ├── terminal_bridge.py       # tmux controller + window tiling
│   └── agent.py                 # ReAct agent loop (Gemini/OpenAI/Ollama)
├── ui/
│   ├── __init__.py
│   ├── floating_button.py       # Floating pill button
│   └── drawer.py                # Half-screen dark side panel
└── plugins_example/
    └── example_plugin.py        # Sample plugin (whois, nmap)
```

---

## Installation

### 1. Run the automated installer

```bash
cd ~/kali-copilot
chmod +x install.sh
./install.sh
```

This installs: `tmux`, `wmctrl`, `xdotool`, `python3-pyqt6`, Python venv, and all Python dependencies.

### 2. Set your API key

**Option A — Environment variable (recommended):**

```bash
# For Google Gemini (default, free tier available)
export GEMINI_API_KEY="your_gemini_api_key"

# For OpenAI
export OPENAI_API_KEY="your_openai_api_key"
```

Add to `~/.bashrc` or `~/.zshrc` to persist across sessions.

**Option B — Config file:**

Edit `~/.config/kali-copilot/config.json`:

```json
{
  "provider":    "gemini",
  "model":       "gemini-1.5-flash",
  "api_key":     "your_api_key_here",
  "openai_base": "https://api.openai.com/v1",
  "ollama_base": "http://localhost:11434",
  "max_steps":   10,
  "auto_mode":   false,
  "temperature": 0.3
}
```

### 3. Using Ollama (100% local, no API key needed)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull llama3     # general purpose
ollama pull mistral    # fast
ollama pull codellama  # code-focused

# Start Ollama service
ollama serve
```

Then in KaliCopilot's model selector dropdown, choose **"Ollama (llama3)"** (or whichever you pulled).

---

## Launching KaliCopilot

```bash
cd ~/kali-copilot

# Option 1: launcher script
./kali-copilot.sh

# Option 2: direct
python main.py

# Option 3: with Gemini key inline
GEMINI_API_KEY="your_key" python main.py
```

A small **"✨ Ask AI"** button will appear in the bottom-right corner of your screen. Click it to open the drawer.

---

## Using the Agent

### Basic Usage

1. Click **"✨ Ask AI"** to open the drawer.
2. Type any question or task in the input box and press **Enter**.
3. Watch the agent think, run commands in your terminal, and report back.

### Examples

| You type | Agent does |
|----------|-----------|
| `scan localhost for open ports` | Runs `nmap -sV localhost`, shows results |
| `what processes are using port 8080?` | `ss -tlnp \| grep 8080` → explains output |
| `check disk usage and find large files` | `df -h` then `du -sh /* 2>/dev/null` |
| `show my network interfaces and IPs` | `ip addr show` with explanation |
| `what failed SSH logins are in the logs?` | `journalctl -u ssh --since today \| grep Failed` |

### Quick-Action Chips

Click the chip buttons above the input for one-click common tasks:
- **Explain last error** — reads your terminal and explains the last error
- **System status** — CPU, memory, disk, uptime
- **List open ports** — `ss -tlnp`
- **Show running processes** — `ps aux --sort=-%cpu`
- **Network interfaces** — `ip addr`
- **Disk usage** — `df -h`

### Autonomous Mode Toggle

The **"Auto-run"** checkbox in the header controls command approval:

| Mode | Behaviour |
|------|-----------|
| ☐ Off (default) | Agent asks your approval before **every** command |
| ☑ On | Safe commands run automatically; dangerous commands still require approval |

> **Always-confirmed regardless of mode:** `rm -rf /`, `mkfs`, `dd if=`, fork bombs, `shutdown`, firewall resets.

### Stop Agent

Click the red **🛑 Stop Agent** button at any time to halt the agent loop after the current step completes.

---

## Model Selector

Use the dropdown in the header to switch models without restarting:

| Option | Provider | Notes |
|--------|----------|-------|
| Gemini 1.5 Flash | Google | Fast, cheap, good for most tasks |
| Gemini 1.5 Pro | Google | Smarter, higher quota cost |
| Gemini 2.0 Flash | Google | Latest, multimodal |
| GPT-4o | OpenAI | Best reasoning |
| GPT-4o mini | OpenAI | Fast + cheap |
| Ollama (llama3) | Local | Fully offline |
| Ollama (mistral) | Local | Fast local option |
| Ollama (codellama) | Local | Best for shell/code tasks |

---

## Writing Plugin Tools

Place Python scripts in `~/.config/kali-copilot/plugins/`.  
Any function decorated with `@register_tool` becomes an AI-callable tool.

```python
# ~/.config/kali-copilot/plugins/my_tools.py

@register_tool
def check_service(service_name: str) -> str:
    """
    Check if a systemd service is running.
    
    Args:
        service_name: Name of the service (e.g., 'ssh', 'apache2')
    
    Returns:
        Service status output.
    """
    import subprocess
    result = subprocess.run(
        ["systemctl", "status", service_name],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout or result.stderr
```

The docstring is used as the tool description for the LLM. The agent will automatically discover and use this tool.

See `plugins_example/example_plugin.py` for more examples (`whois_lookup`, `nmap_quick_scan`).

---

## tmux Session

KaliCopilot creates a dedicated tmux session called `kali_agent_session`.

```bash
# View the session manually
tmux attach -t kali_agent_session

# Kill the session
tmux kill-session -t kali_agent_session
```

Commands injected by the agent run in the `agent` window of this session and are **visible in your terminal** in real time.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `No display detected` | Ensure `$DISPLAY` is set: `echo $DISPLAY` → should show `:0` |
| `tmux: command not found` | `sudo apt install tmux` |
| Drawer doesn't tile terminal | Install `wmctrl`: `sudo apt install wmctrl` |
| PyQt6 import error | Re-run `install.sh` or `pip install PyQt6` in the venv |
| Gemini quota exceeded | Switch to `gemini-1.5-flash` or use Ollama |
| Agent hangs on sudo command | Type your password in the left terminal pane when prompted |
| Wayland: window not visible | Try `export QT_QPA_PLATFORM=xcb` before launching |

---

## Security Notes

- **API keys** are read from environment variables or `~/.config/kali-copilot/config.json` (permissions: `600`). Never commit them.
- The agent operates with **your user's privileges** — it can only do what you can do.
- Destructive commands require a **click confirmation** in the UI, even in Auto-run mode.
- The `max_steps` limit (default 10) prevents runaway agent loops.
- Review every Command Card before clicking Re-run on unfamiliar commands.

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.11+ |
| PyQt6 | 6.4+ |
| tmux | 3.0+ |
| Kali Linux | Rolling (2023+) |
| google-generativeai | 0.7+ (Gemini) |
| openai | 1.0+ (OpenAI/Ollama) |

---

*Built for Kali Linux power users who want the speed of a terminal with the intelligence of an AI copilot.*
