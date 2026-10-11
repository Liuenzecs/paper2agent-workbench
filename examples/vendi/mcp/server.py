"""Launch this package's verified Vendi-Score MCP server over stdio."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent / 'src' / 'Vendi-Score_mcp.py'), run_name='__main__')
