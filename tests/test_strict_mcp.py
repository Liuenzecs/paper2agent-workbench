"""Transport regression: rejected caller types must never run the source function."""
import asyncio
import json
from pathlib import Path

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from code_workbench import build_mcp


def test_strict_stdio_types_defaults_and_no_success_artifacts(tmp_path, monkeypatch):
    # Explicit constructor policy must win even when the parent's setting is false.
    monkeypatch.setenv('FASTMCP_STRICT_INPUT_VALIDATION', 'false')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'LICENSE').write_text('MIT synthetic fixture')
    (source / 'ops.py').write_text(
        'def inspect(x: list[float | None], period: int = 12, alpha: float = .05) -> dict:\n'
        '    return {"x": x, "period": period, "alpha": alpha}\n')
    built = build_mcp(source, ['ops:inspect'], 'strict-contract', tmp_path / 'projects')
    project = Path(built['project_path'])

    async def verify():
        async with Client(project / 'server.py') as client:
            result = await client.call_tool('ops__inspect', {'x': [1, None, 3.5]})
            assert result.data['native_result'] == {'x': [1, None, 3.5], 'period': 12, 'alpha': .05}
            before = set((project / 'outputs').rglob('*.json'))
            for invalid in [{'x': [True, 2]}, {'x': ['1', 2]}, {'x': [[1], 2]},
                            {'x': [1, 2], 'period': True}, {'x': [1, 2], 'period': '12'},
                            {'x': [1, 2], 'period': 1.5}, {'x': [1, 2], 'alpha': True},
                            {'x': [1, 2], 'alpha': '.05'}]:
                with pytest.raises(ToolError):
                    await client.call_tool('ops__inspect', invalid)
            assert set((project / 'outputs').rglob('*.json')) == before
    asyncio.run(verify())
    assert json.loads((project / 'manifest.json').read_text(encoding='utf-8'))['input_validation'] == 'strict_json_types'
