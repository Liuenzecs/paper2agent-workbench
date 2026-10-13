"""Input preservation, genuine upstream calls, and bounded adapter acceptance."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from code_contracts import ContractError, adapt_inputs, check_contract, serialize_result, validate_value, validate_rules
from code_workbench import WorkbenchError, build_mcp, package_mcp, scan_repository, test_mcp as invoke


NUM = {'type': 'number'}
VEC = {'type': 'array', 'items': NUM, 'minItems': 1}


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / '源代码 with spaces'
    repo.mkdir()
    (repo / 'LICENSE').write_text('MIT License\n')
    (repo / 'compute.py').write_text(
        'import numpy as np\nfrom pathlib import Path\n'
        'def array_summary(values):\n'
        '    if type(values) is not np.ndarray: raise TypeError("expected source ndarray")\n'
        '    return {"total": values.sum(), "count": values.size, "dtype": str(values.dtype)}\n'
        'async def increment(x: int):\n    return x + 1\n'
        'def missing(x: float):\n    return {"cells": np.array([[np.nan, x], [np.nan, np.nan]])}\n'
        'def infinite(x: float):\n    return float("inf")\n'
        'class Scaler:\n'
        '    def __init__(self, factor):\n        self.factor = factor\n'
        '    async def apply(self, values):\n        return values * self.factor\n'
        'def file_copy(input_file, output_file):\n'
        '    output_file.write_text(input_file.read_text() + " source")\n'
        '    return output_file\n'
        'def frame_total(frame):\n'
        '    return {"total": frame["value"].sum(), "frame": frame}\n', encoding='utf-8')
    return repo


def contract(repo, symbol, params, **kwargs):
    return {'schema_version': 1, 'tools': {'compute:' + symbol: {
        'reviewed': True, 'source_sha256': hashlib.sha256((repo / 'compute.py').read_bytes()).hexdigest(),
        'parameters': params, **kwargs}}}


def ndarray(schema=VEC, shape=None):
    return {'schema': schema, 'adapter': {'kind': 'ndarray', 'dtype': 'float64', 'shape': [None] if shape is None else shape}}


def test_contract_requires_review_sha_exact_parameters_and_declares_schema(source, tmp_path):
    scanned = {c['id']: c for c in scan_repository(source)['candidates']}
    c = scanned['compute:array_summary']
    assert c['review_required'] and c['contract_template']['reviewed'] is False
    assert not c['runnable']
    with pytest.raises(WorkbenchError):
        build_mcp(source, ['compute:array_summary'], 'test', tmp_path / 'out')
    config = contract(source, 'array_summary', {'values': ndarray()})
    for change in ({'reviewed': False}, {'source_sha256': '0' * 64}, {'parameters': {}}):
        bad = json.loads(json.dumps(config))
        bad['tools']['compute:array_summary'].update(change)
        with pytest.raises(WorkbenchError):
            build_mcp(source, ['compute:array_summary'], 'test', tmp_path / 'out', bad)
    assert not (tmp_path / 'out').exists()


def test_ndarray_real_stdio_changed_inputs_bool_finite_rank_and_ragged_errors(source, tmp_path):
    config = contract(source, 'array_summary', {'values': ndarray()})
    built = build_mcp(source, ['compute:array_summary'], 'arrays', tmp_path / 'out', config)
    for values, expected in [([1.5, -2, 4], 3.5), ([10, 20], 30)]:
        result = invoke(built['project_path'], sys.executable, 'compute__array_summary', {'values': values})
        assert result['decision'] == 'completed', result.get('error')
        assert result['result']['data']['native_result'] == {'total': expected, 'count': len(values), 'dtype': 'float64'}
    for values in ([True, 1], [], [[1, 2], [3]], [[1, 2]]):
        result = invoke(built['project_path'], sys.executable, 'compute__array_summary', {'values': values})
        assert result['decision'] == 'execution_error'
    with pytest.raises(ContractError):
        validate_value([float('nan')], VEC)
    script = Path(built['project_path']) / 'schema_probe.py'
    script.write_text('import asyncio,json\nfrom fastmcp import Client\nfrom pathlib import Path\nasync def main():\n async with Client(Path(__file__).with_name("server.py")) as c:\n  t=await c.list_tools()\n  print(json.dumps(t[0].inputSchema))\nasyncio.run(main())\n')
    response = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert response.returncode == 0, response.stderr
    schema = json.loads(response.stdout)
    assert schema['properties']['values']['items']['type'] == 'number'
    assert schema['properties']['values']['minItems'] == 1
    assert schema['required'] == ['values']


def test_async_typed_function_and_explicit_instance_lifecycle(source, tmp_path):
    built = build_mcp(source, ['compute:increment'], 'async', tmp_path / 'out')
    result = invoke(built['project_path'], sys.executable, 'compute__increment', {'x': 8})
    assert result['decision'] == 'completed', result.get('error')
    assert result['result']['data']['native_result'] == 9
    bad = invoke(built['project_path'], sys.executable, 'compute__increment', {'x': True})
    assert bad['decision'] == 'execution_error'
    config = contract(source, 'Scaler.apply', {'values': ndarray()}, constructor={'parameters': {'factor': {'schema': NUM}}})
    built = build_mcp(source, ['compute:Scaler.apply'], 'methods', tmp_path / 'out', config)
    for factor, values, expected in [(3, [1, 2], [3, 6]), (-.5, [4, 8], [-2, -4])]:
        result = invoke(built['project_path'], sys.executable, 'compute__Scaler__apply', {'values': values, 'constructor': {'factor': factor}})
        assert result['decision'] == 'completed', result.get('error')
        assert result['result']['data']['native_result'] == expected
    result = invoke(built['project_path'], sys.executable, 'compute__Scaler__apply', {'values': [1]})
    assert result['decision'] == 'execution_error'


def test_missingness_explicit_policy_with_path_mask_and_unexpected_nan_rejected(source, tmp_path):
    built = build_mcp(source, ['compute:missing'], 'reject', tmp_path / 'out')
    assert invoke(built['project_path'], sys.executable, 'compute__missing', {'x': 2})['decision'] == 'execution_error'
    assert not (Path(built['project_path']) / 'outputs').exists()
    config = contract(source, 'missing', {'x': {'schema': NUM}}, result={'nonfinite': 'null_with_mask', 'nan_regions': [{'field': 'cells', 'region': 'lower_triangle'}]})
    built = build_mcp(source, ['compute:missing'], 'masked', tmp_path / 'out', config)
    result = invoke(built['project_path'], sys.executable, 'compute__missing', {'x': 7})
    assert result['decision'] == 'completed', result.get('error')
    data = result['result']['data']
    assert data['native_result'] == {'cells': [[None, 7], [None, None]]}
    assert data['nonfinite_mask'] == [{'path': '/cells/0/0', 'kind': 'nan'}, {'path': '/cells/1/0', 'kind': 'nan'}, {'path': '/cells/1/1', 'kind': 'nan'}]
    assert any(Path(x['path']).name == 'serialization-audit.json' for x in data['artifacts'])
    with pytest.raises(ContractError):
        serialize_result({'cells': [[0, float('nan')], [0, 0]]}, config['tools']['compute:missing']['result'])
    with pytest.raises(ContractError):
        serialize_result(float('inf'), {'nonfinite': 'null_with_mask'})


def test_managed_file_paths_real_calls_and_parent_traversal_rejection(source, tmp_path):
    config = contract(source, 'file_copy', {
        'input_file': {'schema': {'type': 'string'}, 'adapter': {'kind': 'path', 'mode': 'input'}},
        'output_file': {'schema': {'type': 'string'}, 'adapter': {'kind': 'path', 'mode': 'output'}}})
    built = build_mcp(source, ['compute:file_copy'], 'files', tmp_path / 'out', config)
    project = Path(built['project_path'])
    inputs = project / 'workspace' / 'inputs'
    inputs.mkdir(parents=True)
    (inputs / 'first.txt').write_text('first')
    (inputs / 'second.txt').write_text('second')
    outputs = []
    for name in ['first', 'second']:
        result = invoke(project, sys.executable, 'compute__file_copy', {'input_file': name + '.txt', 'output_file': 'result.txt'})
        assert result['decision'] == 'completed', result.get('error')
        path = Path(result['result']['data']['native_result'])
        assert path.read_text() == name + ' source'
        outputs.append(path)
    assert outputs[0] != outputs[1]
    for bad in ['../first.txt', 'C:/private.txt', str(inputs / 'first.txt'), 'missing.txt']:
        result = invoke(project, sys.executable, 'compute__file_copy', {'input_file': bad, 'output_file': 'result.txt'})
        assert result['decision'] == 'execution_error'
    result = invoke(project, sys.executable, 'compute__file_copy', {'input_file': 'first.txt', 'output_file': 'native-result.json'})
    assert result['decision'] == 'execution_error'
    packaged = package_mcp(project)
    assert not any('workspace/' in x for x in packaged['files'])


def test_dataframe_split_contract_preserves_columns_and_native_result(source, tmp_path):
    columns = {'label': {'type': 'string'}, 'value': NUM}
    schema = {'type': 'object', 'properties': {
        'columns': {'type': 'array', 'items': {'type': 'string'}, 'const': ['label', 'value']},
        'data': {'type': 'array', 'items': {'type': 'array', 'prefixItems': list(columns.values()), 'items': False, 'minItems': 2, 'maxItems': 2}}},
        'required': ['columns', 'data'], 'additionalProperties': False}
    config = contract(source, 'frame_total', {'frame': {'schema': schema, 'adapter': {'kind': 'dataframe', 'columns': columns}}})
    built = build_mcp(source, ['compute:frame_total'], 'frames', tmp_path / 'out', config)
    result = invoke(built['project_path'], sys.executable, 'compute__frame_total', {'frame': {'columns': ['label', 'value'], 'data': [['a', 2], ['b', 5]]}})
    assert result['decision'] == 'completed', result.get('error')
    assert result['result']['data']['native_result'] == {'total': 7, 'frame': {'columns': ['label', 'value'], 'index': [0, 1], 'data': [['a', 2], ['b', 5]]}}
    bad = invoke(built['project_path'], sys.executable, 'compute__frame_total', {'frame': {'columns': ['label', 'value'], 'data': [[2, 'a']]}})
    assert bad['decision'] == 'execution_error'


def test_rules_are_bounded_data_not_executable_and_adapters_cannot_drift_schema():
    spec = {'reviewed': True, 'source_sha256': 'a' * 64, 'parameters': {'x': ndarray()}, 'rules': [{'op': 'exec', 'code': 'raise RuntimeError()'}]}
    with pytest.raises(ContractError):
        check_contract(spec)
    spec['rules'] = []
    spec['parameters']['x']['adapter']['shape'] = [3]
    with pytest.raises(ContractError):
        check_contract(spec)
    with pytest.raises(ContractError):
        adapt_inputs({'x': [1], 'extra': 1}, {'x': ndarray()}, Path('.'), Path('.'))


def test_source_moment_precondition_rejects_overflow_and_constant_roundoff():
    rules = [{'op': 'finite_moments', 'field': 'Y', 'require_positive_std': True}]
    validate_rules({'Y': [1, 2, 4, 8]}, rules)
    for values in [[.4] * 640, [1e308, -1e308] * 320, [True, 2], [float('nan'), 1]]:
        with pytest.raises(ContractError):
            validate_rules({'Y': values}, rules)


def test_public_call_script_and_legacy_package_are_both_supported(source, tmp_path):
    built = build_mcp(source, ['compute:increment'], 'call entry', tmp_path / 'out')
    project = Path(built['project_path'])
    packaged = package_mcp(project)
    assert 'call_tool.py' in packaged['files']
    assert 'example_call.py' not in packaged['files']
    (project / 'call_tool.py').rename(project / 'example_call.py')
    manifest_path = project / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    del manifest['invocation_script']
    del manifest['invocation_script_sha256']
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    packaged = package_mcp(project)
    assert 'example_call.py' in packaged['files']


def test_lax_parent_mount_cannot_coerce_reviewed_bool_or_number(source, tmp_path):
    config = contract(source, 'array_summary', {'values': ndarray()})
    built = build_mcp(source, ['compute:array_summary'], 'reviewed', tmp_path / 'out', config)
    project = Path(built['project_path'])
    driver = project / 'lax_mount_probe.py'
    driver.write_text('import asyncio,json,runpy\nfrom fastmcp import Client,FastMCP\nfrom fastmcp.exceptions import ToolError\n'
                      'async def main():\n m=FastMCP("lax",strict_input_validation=False)\n s=runpy.run_path("server.py")["mcp"]\n m.mount(s)\n async with Client(m) as c:\n'
                      '  try:\n   await c.call_tool("compute__array_summary", {"values":[True,1]})\n  except ToolError:\n   print("REJECTED")\n   return\n  raise RuntimeError("bool was coerced before scientific call")\nasyncio.run(main())\n')
    result = subprocess.run([sys.executable, str(driver)], cwd=project, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'REJECTED' in result.stdout
