"""Stdio server mounting the two source-backed, CPU-only Vendi diversity tools."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.vendi_metrics import vendi_metrics_mcp
from fastmcp import FastMCP

mcp = FastMCP(name='Vendi-Score')
mcp.mount(vendi_metrics_mcp)

if __name__ == '__main__':
    mcp.run(transport='stdio', show_banner=False)
