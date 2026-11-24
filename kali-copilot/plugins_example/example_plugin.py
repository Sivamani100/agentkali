"""
plugins_example/example_plugin.py
-----------------------------------
Example KaliCopilot plugin demonstrating the @register_tool decorator pattern.

Place plugin scripts in ~/.config/kali-copilot/plugins/
They are auto-loaded at agent start.  Any function decorated with
@register_tool becomes a tool the agent can call.

This example adds two tools:
  - `whois_lookup(domain)`: Runs whois on a domain and returns structured data.
  - `nmap_quick_scan(target)`: Runs a quick nmap ping sweep.
"""

# 'register_tool' is injected by the plugin loader — no import needed.
# The type annotation is here only for IDE support.
from typing import Callable
try:
    register_tool  # type: ignore  # noqa: F821
except NameError:
    # Fallback when running standalone (not via loader)
    def register_tool(fn: Callable) -> Callable:  # type: ignore
        return fn


import subprocess


@register_tool
def whois_lookup(domain: str) -> str:
    """
    Perform a WHOIS lookup for the given domain name.

    Args:
        domain: Domain name or IP address (e.g., example.com)

    Returns:
        WHOIS output as a string.
    """
    try:
        result = subprocess.run(
            ["whois", domain],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout or result.stderr
        # Truncate to avoid overwhelming the LLM context
        return output[:3000] if output else "No WHOIS data returned."
    except FileNotFoundError:
        return "whois is not installed. Run: sudo apt install whois"
    except subprocess.TimeoutExpired:
        return f"WHOIS lookup for {domain} timed out."
    except Exception as e:
        return f"Error during WHOIS lookup: {e}"


@register_tool
def nmap_quick_scan(target: str) -> str:
    """
    Run a quick nmap ping sweep (host discovery only, no port scan).
    Safe for local network reconnaissance.

    Args:
        target: IP address, CIDR range, or hostname (e.g., 192.168.1.0/24)

    Returns:
        nmap output as a string.
    """
    try:
        result = subprocess.run(
            ["nmap", "-sn", "-T4", target],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout or result.stderr
        return output[:4000] if output else "No nmap output."
    except FileNotFoundError:
        return "nmap is not installed. Run: sudo apt install nmap"
    except subprocess.TimeoutExpired:
        return f"nmap scan of {target} timed out after 60 seconds."
    except Exception as e:
        return f"Error during nmap scan: {e}"
