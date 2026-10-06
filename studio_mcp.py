"""stdio launcher for Fatima Image Studio's MCP server, for agents that start local commands.

  command: python   args: ["<this folder>\\studio_mcp.py"]

Fatima Image Studio itself must be running; this forwards to it using the port and API key in data/config.json.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio import config  # noqa: E402
from studio.mcp_server import build  # noqa: E402

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)  # stderr only; stdout carries the MCP messages
    cfg = config.load()
    server = build(f"http://{cfg['host']}:{cfg['port']}", cfg["api_key"])
    server.settings.log_level = "WARNING"
    server.run("stdio")
