"""Fresh verification: native research oracle, real stdio, and strict I/O."""
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
np = pytest.importorskip('numpy')
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.exceptions import ToolError

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from code_workbench import build_mcp, scan_repository, package_mcp, WorkbenchError
from code_contracts import ContractError, adapt_inputs, serialize_result, validate_value

pytestmark = pytest.mark.skipif(not os.environ.get('P2A_INDEPENDENT_RUN'), reason='Independent integration acceptance requires an explicit output directory and pinned scientific checkout.')
RUN = Path(os.environ.get('P2A_INDEPENDENT_RUN', str(APP / '.independent-checks-disabled')))
if os.environ.get('P2A_INDEPENDENT_RUN'):
    RUN.mkdir(parents=True, exist_ok=True)
NUMBER = {'type': 'number'}
VECTOR = {'type': 'array', 'items': NUMBER, 'minItems': 1}

def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def calls(project, cases, label):
    """Keep every successful and failed stdio response, plus genuine inventory."""
    async def execute():
        path = RUN / label
        path.mkdir()
        output = []
        transport = PythonStdioTransport(str(Path(project) / 'server.py'), python_cmd=sys.executable, cwd=str(project))
        async with Client(transport) as client:
            inventory = await client.list_tools()
            save(path / 'inventory.json', [tool.model_dump(mode='json') for tool in inventory])
            for i, (tool, arguments, error) in enumerate(cases):
                original = copy.deepcopy(arguments)
                record = {'tool': tool, 'arguments': arguments, 'expected_error': error}
                try:
                    response = await client.call_tool(tool, arguments)
                    record.update(outcome='success', response=response.data)
                except ToolError as exc:
                    record.update(outcome='mcp_tool_error', error=str(exc))
                finally:
                    save(path / f'call-{i:03}.json', record)
                assert arguments == original, 'Client inputs changed'
                assert (record['outcome'] == 'mcp_tool_error') == error, record
                output.append(record)
        return output
    return asyncio.run(execute())

@pytest.fixture(scope='module')
def plumbing():
    repo = RUN / 'binding source 空格'
    repo.mkdir()
    (repo / 'LICENSE').write_text('MIT License\n', encoding='utf-8')
    source = repo / 'fresh_native.py'
    source.write_text('from pathlib import Path\nimport numpy as np\n'
        'def typed(flag: bool, count: int, values: list[float]):\n'
        '    return {"flag": flag, "count": count, "values": values}\n'
        'def matrix(a):\n    return {"value": a.T, "dtype": str(a.dtype)}\n'
        'def frame(df):\n    return df.iloc[::-1]\n'
        'async def awaited(value):\n    return {"value": value, "awaited": True}\n'
        'class Multiplier:\n'
        '    def __init__(self, factor):\n        self.factor = factor\n'
        '    async def apply(self, value):\n        return value * self.factor\n'
        'def files(src, dst):\n    dst.write_bytes(src.read_bytes())\n    return dst\n'
        'def missing(value):\n    return {"matrix": np.array([[np.nan, value], [np.nan, np.nan]])}\n'
        'def invalid(value: float):\n    return float("inf")\n'
        'def reserved(dst):\n    dst.write_text("upstream-original")\n    return "written"\n', encoding='utf-8')
    columns = {'identifier': {'type': 'string'}, 'value': NUMBER}
    frame_schema = {'type': 'object', 'properties': {
        'columns': {'type': 'array', 'items': {'type': 'string'}, 'const': list(columns)},
        'data': {'type': 'array', 'items': {'type': 'array', 'prefixItems': list(columns.values()), 'items': False, 'minItems': 2, 'maxItems': 2}}},
        'required': ['columns', 'data'], 'additionalProperties': False}
    specs = {
        'matrix': {'parameters': {'a': {'schema': {'type': 'array', 'items': {'type': 'array', 'items': NUMBER, 'minItems': 2, 'maxItems': 2}, 'minItems': 1}, 'adapter': {'kind': 'ndarray', 'dtype': 'float64', 'shape': [None, 2]}}}},
        'frame': {'parameters': {'df': {'schema': frame_schema, 'adapter': {'kind': 'dataframe', 'columns': columns}}}},
        'awaited': {'parameters': {'value': {'schema': NUMBER}}},
        'Multiplier.apply': {'parameters': {'value': {'schema': NUMBER}}, 'constructor': {'parameters': {'factor': {'schema': NUMBER}}}},
        'files': {'parameters': {key: {'schema': {'type': 'string'}, 'adapter': {'kind': 'path', 'mode': mode}} for key, mode in [('src', 'input'), ('dst', 'output')]}},
        'missing': {'parameters': {'value': {'schema': NUMBER}}, 'result': {'nonfinite': 'null_with_mask', 'nan_regions': [{'field': 'matrix', 'region': 'lower_triangle'}]}},
        'reserved': {'parameters': {'dst': {'schema': {'type': 'string'}, 'adapter': {'kind': 'path', 'mode': 'output'}}}}
    }
    config = {'schema_version': 1, 'tools': {'fresh_native:' + key: {'reviewed': True, 'source_sha256': sha(source), **value} for key, value in specs.items()}}
    names = ['fresh_native:typed', 'fresh_native:invalid'] + list(config['tools'])
    build = build_mcp(repo, names, 'Independent interface verification', RUN / 'generated', config)
    project = Path(build['project_path'])
    spec = importlib.util.spec_from_file_location('independent_oracle_native', source)
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    save(RUN / 'plumbing-build.json', build)
    return repo, project, upstream, config

def test_independent_static_and_schema(plumbing):
    repo, project, _, config = plumbing
    marker = RUN / 'must-not-be-created'
    harmful = repo / 'static_only.py'
    harmful.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\ndef operation(x: int):\n return x\n')
    assert any(x['id'] == 'static_only:operation' for x in scan_repository(repo)['candidates'])
    assert not marker.exists()
    records = calls(project, [('fresh_native__typed', {'flag': True, 'count': 3, 'values': [1.25, -3]}, False)], 'schema')
    inventory = json.loads((RUN / 'schema/inventory.json').read_text(encoding='utf-8'))
    lookup = {x['name']: x.get('inputSchema', x.get('input_schema')) for x in inventory}
    assert set(lookup) == {'fresh_native__' + x.replace('.', '__') for x in ['typed', 'invalid', 'matrix', 'frame', 'awaited', 'Multiplier.apply', 'files', 'missing', 'reserved']}
    assert lookup['fresh_native__matrix']['properties']['a']['items']['maxItems'] == 2
    assert lookup['fresh_native__matrix']['properties']['a']['description']
    assert lookup['fresh_native__Multiplier__apply']['properties']['constructor']['description']
    assert set(lookup['fresh_native__Multiplier__apply']['required']) == {'value', 'constructor'}
    assert records[0]['response']['native_result']['count'] == 3
    import ast
    generated = ast.parse((project / 'server.py').read_text(encoding='utf-8'))
    public = [node for node in generated.body if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith('fresh_native__')]
    assert all(len(ast.get_docstring(node).splitlines()) == 2 for node in public)
    original = copy.deepcopy(config)
    bad = copy.deepcopy(config)
    bad['tools']['fresh_native:matrix']['source_sha256'] = '0' * 64
    with pytest.raises(WorkbenchError):
        build_mcp(repo, ['fresh_native:matrix'], 'Bad binding', RUN / 'rejected', {'schema_version': 1, 'tools': {'fresh_native:matrix': bad['tools']['fresh_native:matrix']}})
    assert original == config

def test_independent_numeric_strictness_and_array(plumbing):
    _, project, upstream, _ = plumbing
    changed = [[[2.25, -7], [1, 0]], [[11, 13], [17, 19], [-23, -29]]]
    records = calls(project,
        [('fresh_native__matrix', {'a': x}, False) for x in changed] +
        [('fresh_native__matrix', {'a': x}, True) for x in [[[True, 1]], [[1, 2, 3]], [[1]], [1, 2]]] +
        [('fresh_native__typed', {'flag': x, 'count': 2, 'values': [1]}, True) for x in [1, 'true']] +
        [('fresh_native__typed', {'flag': True, 'count': x, 'values': [1]}, True) for x in [True, 2.0, '2']] +
        [('fresh_native__typed', {'flag': True, 'count': 2, 'values': [True]}, True)], 'strict-numeric')
    for case, record in zip(changed, records):
        native = upstream.matrix(np.asarray(case, dtype='float64'))
        np.testing.assert_array_equal(np.asarray(record['response']['native_result']['value']), native['value'])
        assert record['response']['native_result']['dtype'] == 'float64'
    for value in [float('nan'), float('inf'), -float('inf')]:
        with pytest.raises(ContractError):
            validate_value([value], VECTOR)

def test_independent_dataframe_and_async_methods(plumbing):
    _, project, upstream, _ = plumbing
    data = {'columns': ['identifier', 'value'], 'data': [['z', 7.5], ['a', -2], ['z', 11]]}
    records = calls(project, [
        ('fresh_native__frame', {'df': data}, False),
        ('fresh_native__frame', {'df': {'columns': ['value', 'identifier'], 'data': [[7, 'z']]}}, True),
        ('fresh_native__frame', {'df': {'columns': ['identifier', 'value'], 'data': [['a', True]]}}, True),
        ('fresh_native__awaited', {'value': -9.5}, False),
        ('fresh_native__Multiplier__apply', {'value': 3.75, 'constructor': {'factor': -4}}, False),
        ('fresh_native__Multiplier__apply', {'value': -2, 'constructor': {'factor': 6}}, False),
        ('fresh_native__Multiplier__apply', {'value': 3}, True),
        ('fresh_native__Multiplier__apply', {'value': 3, 'constructor': {'factor': True}}, True)], 'frame-async')
    import pandas as pd
    native = upstream.frame(pd.DataFrame(data['data'], columns=data['columns']))
    assert records[0]['response']['native_result'] == {'columns': native.columns.tolist(), 'index': native.index.tolist(), 'data': native.to_numpy().tolist()}
    assert records[3]['response']['native_result'] == asyncio.run(upstream.awaited(-9.5))
    assert records[4]['response']['native_result'] == asyncio.run(upstream.Multiplier(-4).apply(3.75))
    assert records[5]['response']['native_result'] == asyncio.run(upstream.Multiplier(6).apply(-2))

def test_independent_paths_and_original_preservation(plumbing):
    _, project, _, _ = plumbing
    inputs = project / 'workspace/inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    payload = b'\x00native\xffresearch\n'
    (inputs / 'original.bin').write_bytes(payload)
    cases = [('fresh_native__files', {'src': 'original.bin', 'dst': 'nested/native.bin'}, False)] * 2
    cases += [('fresh_native__files', {'src': x, 'dst': 'result.bin'}, True) for x in ['../original.bin', str(inputs / 'original.bin'), 'C:original.bin', 'missing.bin']]
    cases += [('fresh_native__files', {'src': 'original.bin', 'dst': x}, True) for x in ['../escape.bin', 'native-result.json', 'serialization-audit.json']]
    records = calls(project, cases, 'paths')
    paths = [Path(record['response']['native_result']) for record in records[:2]]
    assert paths[0] != paths[1]
    assert all(path.read_bytes() == payload for path in paths)
    assert (inputs / 'original.bin').read_bytes() == payload
    # Windows filename aliases must cause a failed envelope, preserving the original.
    records = calls(project, [('fresh_native__reserved', {'dst': 'NATIVE-RESULT.JSON'}, True)], 'reserved-alias')
    originals = list((project / 'workspace/outputs').glob('*/NATIVE-RESULT.JSON'))
    assert any(path.read_text() == 'upstream-original' for path in originals)
    outside = RUN / 'outside-original.bin'
    outside.write_bytes(b'outside')
    link = inputs / 'escape-link.bin'
    try:
        link.symlink_to(outside)
    except OSError as exc:
        save(RUN / 'symlink-platform-limitation.json', {'exception_type': type(exc).__name__, 'scope': 'OS symlink creation unavailable; traversal and absolute paths tested'})
    else:
        calls(project, [('fresh_native__files', {'src': 'escape-link.bin', 'dst': 'copied.bin'}, True)], 'symlink')
        assert outside.read_bytes() == b'outside'

def test_independent_nonfinite_mask(plumbing):
    _, project, upstream, _ = plumbing
    records = calls(project, [('fresh_native__missing', {'value': -6.25}, False), ('fresh_native__invalid', {'value': 3}, True)], 'missingness')
    response = records[0]['response']
    native = upstream.missing(-6.25)['matrix']
    restored = np.asarray([[np.nan if x is None else x for x in row] for row in response['native_result']['matrix']])
    np.testing.assert_array_equal(restored, native)
    expected_mask = [{'path': f'/matrix/{i}/{j}', 'kind': 'nan'} for i, j in np.argwhere(np.isnan(native))]
    assert response['nonfinite_mask'] == expected_mask
    with pytest.raises(ContractError):
        serialize_result({'matrix': [[1, float('nan')], [2, 3]]}, {'nonfinite': 'null_with_mask', 'nan_regions': [{'field': 'matrix', 'region': 'lower_triangle'}]})

def test_independent_tamper_evidence(plumbing):
    _, project, _, _ = plumbing
    for target in [project / 'code_contracts.py', project / 'vendor/repo/fresh_native.py']:
        original = target.read_bytes()
        target.write_bytes(original + b'\n# independent integrity test\n')
        try:
            calls(project, [('fresh_native__typed', {'flag': True, 'count': 1, 'values': []}, True)], 'tamper-' + target.parent.name)
            with pytest.raises(WorkbenchError):
                package_mcp(project)
        finally:
            target.write_bytes(original)
        assert sha(target) == hashlib.sha256(original).hexdigest()

@pytest.fixture(scope='module')
def salib():
    if not os.environ.get('P2A_SALIB_REPOSITORY') or not os.environ.get('P2A_SALIB_CONTRACTS'):
        pytest.skip('Scientific integration requires P2A_SALIB_REPOSITORY and P2A_SALIB_CONTRACTS')
    repo = Path(os.environ['P2A_SALIB_REPOSITORY']).resolve(strict=True)
    config_path = Path(os.environ['P2A_SALIB_CONTRACTS']).resolve(strict=True)
    config = json.loads(config_path.read_text(encoding='utf-8'))
    build = build_mcp(repo, list(config['tools']), 'Independent SALib transport', RUN / 'salib-generated', config)
    project = Path(build['project_path'])
    save(RUN / 'salib-build.json', build)
    sys.path.insert(0, str(repo / 'src'))
    import SALib.sample.sobol as sampler
    import SALib.analyze.sobol as analyzer
    assert Path(sampler.__file__).resolve() == (repo / 'src/SALib/sample/sobol.py').resolve()
    assert Path(analyzer.__file__).resolve() == (repo / 'src/SALib/analyze/sobol.py').resolve()
    return repo, project, sampler, analyzer

def test_independent_salib_complete_arrays_and_changed_parameters(salib):
    _, project, sampler, analyzer = salib
    from SALib.test_functions import Ishigami
    observed = []
    for index, (n, second, seed, bounds, a, b, keep, confidence) in enumerate([
        (128, True, 307, [[-np.pi, np.pi]] * 3, 7, .1, True, .9),
        (256, False, 701, [[-2, 1], [-1.5, 2.5], [-3, 2]], 4.5, .04, False, .975),
        (127, False, 509, [[-np.pi, np.pi]] * 3, 2, .2, True, .8)]):
        problem = {'num_vars': 3, 'names': ['x1', 'x2', 'x3'], 'bounds': bounds}
        original = copy.deepcopy(problem)
        x = sampler.sample(copy.deepcopy(problem), n, calc_second_order=second, scramble=True, skip_values=0, seed=seed)
        y = Ishigami.evaluate(x, A=a, B=b)
        native = analyzer.analyze(copy.deepcopy(problem), y.copy(), calc_second_order=second, num_resamples=37, conf_level=confidence, print_to_console=False, parallel=False, n_processors=None, keep_resamples=keep, seed=seed + 1)
        np.savez(RUN / f'native-oracle-{index}.npz', X=x, Y=y, **native)
        sample_args = {'problem': problem, 'N': n, 'calc_second_order': second, 'scramble': True, 'skip_values': 0, 'seed': seed}
        analyze_args = {'problem': problem, 'Y': y.tolist(), 'calc_second_order': second, 'num_resamples': 37, 'conf_level': confidence, 'print_to_console': False, 'parallel': False, 'n_processors': None, 'keep_resamples': keep, 'seed': seed + 1}
        records = calls(project, [('salib_sample_sobol', sample_args, False), ('salib_analyze_sobol', analyze_args, False)], f'salib-scientific-{index}')
        np.testing.assert_array_equal(np.asarray(records[0]['response']['native_result']), x)
        response = records[1]['response']
        assert set(response['native_result']) == set(native)
        masks = []
        values = {}
        for key, array in native.items():
            converted = np.asarray(response['native_result'][key], dtype=float)
            np.testing.assert_allclose(converted, array, rtol=0, atol=0, equal_nan=True)
            values[key] = converted
            masks.extend({'path': '/' + key + '/' + '/'.join(map(str, cell)), 'kind': 'nan'} for cell in np.argwhere(np.isnan(array)))
        assert response['nonfinite_mask'] == masks
        np.savez(RUN / f'transport-observed-{index}.npz', X=np.asarray(records[0]['response']['native_result']), **values)
        assert problem == original
        observed.append({'index': index, 'N': n, 'second_order': second, 'sample_seed': seed, 'analysis_seed': seed + 1, 'conf_level': confidence, 'A': a, 'B': b, 'keep_resamples': keep, 'oracle_origin': {'sample': str(sampler.__file__), 'analyze': str(analyzer.__file__)}, 'comparison': 'every array and missingness entry exact'})
    save(RUN / 'scientific-comparisons.json', observed)

def test_independent_salib_error_admissibility(salib):
    _, project, _, _ = salib
    problem = {'num_vars': 3, 'names': ['a', 'b', 'c'], 'bounds': [[-1, 1]] * 3}
    sample = {'problem': problem, 'N': 16, 'calc_second_order': False, 'scramble': True, 'skip_values': 0, 'seed': 0}
    analyze = {'problem': problem, 'Y': list(range(80)), 'calc_second_order': False, 'num_resamples': 23, 'conf_level': .95, 'print_to_console': False, 'parallel': False, 'n_processors': None, 'keep_resamples': False, 'seed': 1}
    cases = [('salib_sample_sobol', {**sample, 'N': True}, True), ('salib_sample_sobol', {**sample, 'N': 16.0}, True), ('salib_sample_sobol', {**sample, 'calc_second_order': 0}, True)]
    for update in [{'Y': [.4] * 80}, {'Y': [1e308, -1e308] * 40}, {'Y': [True] * 80}, {'Y': list(range(81))}, {'num_resamples': 1}, {'conf_level': True}, {'parallel': True}]:
        cases.append(('salib_analyze_sobol', {**analyze, **update}, True))
    for update in [{'num_vars': 2}, {'names': ['a', 'a', 'c']}, {'bounds': [[1, -1]] * 3}]:
        cases.append(('salib_sample_sobol', {**sample, 'problem': {**problem, **update}}, True))
    calls(project, cases, 'salib-invalid')

def test_independent_generated_package_excludes_results_and_invocation_entry(plumbing, salib):
    import zipfile
    for label, project in [('plumbing', plumbing[1]), ('salib', salib[1])]:
        package = package_mcp(project)
        save(RUN / f'{label}-package.json', package)
        with zipfile.ZipFile(package['zip_path']) as z:
            names = z.namelist()
        assert any(x.endswith('/call_tool.py') for x in names)
        assert not any('/workspace/' in x or '/verification/' in x or x.endswith('.npz') for x in names)
