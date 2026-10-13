"""Real fixed-entry native command checks, with immutable source bindings."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

from command_workbench import build_command_mcp, check_command_contracts, scan_command_repository
from command_runtime import _command_json
from code_workbench import package_mcp


ENTRY = '''import json, sys, time
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
out = Path(sys.argv[2])
if request['mode'] == 'error':
    (out / 'before-error.txt').write_text('native partial result', encoding='utf-8')
    print('native failure', file=sys.stderr)
    sys.exit(7)
if request['mode'] == 'timeout':
    (out / 'before-timeout.txt').write_text('native partial result', encoding='utf-8')
    time.sleep(4)
if request['mode'] == 'nonfinite':
    (out / 'result.json').write_text('{"value":NaN}', encoding='utf-8')
else:
    (out / 'result.json').write_text(json.dumps({'values': request['values'], 'enabled': request['enabled']}), encoding='utf-8')
    (out / 'native-input.json').write_text(json.dumps(request), encoding='utf-8')
'''


def contract(repo):
    entry = repo / 'entry.py'
    return {'schema_version': 1, 'route': 'command', 'tools': {'native_record': {
        'reviewed': True, 'entry_file': 'entry.py',
        'source_files': {'entry.py': hashlib.sha256(entry.read_bytes()).hexdigest()},
        'parameters': {
            'values': {'schema': {'type': 'array', 'items': {'anyOf': [{'type': 'number'}, {'type': 'null'}]}, 'minItems': 2}, 'description': 'Numeric measurements with explicit missing observations.'},
            'enabled': {'schema': {'type': 'boolean'}, 'description': 'Native boolean option.'},
            'mode': {'schema': {'type': 'string', 'enum': ['ok', 'error', 'timeout', 'nonfinite']}, 'description': 'Native diagnostic mode.', 'default': 'ok'}},
        'argv': ['{entry_file}', '{request_file}', '{output_dir}'],
        'runtime': {'kind': 'python', 'env': 'P2A_NATIVE_RUNTIME', 'version_args': ['--version'], 'version_contains': 'Python 3.11'},
        'timeout_seconds': 1, 'result_file': 'result.json', 'description': 'Run the reviewed native measurement entry.',
        'reference': 'source://private-test/entry.py', 'native_dependencies': ['Python 3.11 standard library']}}}


@pytest.fixture
def generated(tmp_path, monkeypatch):
    repo = tmp_path / '代码 with spaces'
    repo.mkdir()
    (repo / 'entry.py').write_text(ENTRY, encoding='utf-8')
    (repo / 'LICENSE').write_text('MIT', encoding='utf-8')
    (repo / 'personal.pdf').write_bytes(b'private')
    (repo / '.env').write_text('SECRET=private', encoding='utf-8')
    monkeypatch.setenv('P2A_NATIVE_RUNTIME', sys.executable)
    result = build_command_mcp(repo, contract(repo), 'NativeMeasurements', tmp_path / 'generated')
    return repo, Path(result['project_path'])


def call(project, values, env=None):
    async def run():
        transport = PythonStdioTransport(str(project / 'server.py'), python_cmd=sys.executable,
                                        cwd=str(project), env=env if env is not None else dict(os.environ))
        async with Client(transport) as client:
            return await client.call_tool('native_record', values, raise_on_error=False)
    return asyncio.run(run())


def test_static_scan_never_reviews_or_executes(tmp_path):
    (tmp_path / 'entry.py').write_text('raise RuntimeError("do not execute")', encoding='utf-8')
    report = scan_command_repository(tmp_path)
    assert report['execution_performed'] is False
    assert report['candidates'][0]['contract_template']['reviewed'] is False


def test_actual_stdio_native_output_and_repeated_fresh_paths(generated):
    _, project = generated
    args = {'values': [3.2, None, -4], 'enabled': True}
    first, second = call(project, args), call(project, args)
    assert not first.is_error and not second.is_error
    assert first.data['data'] == {'values': args['values'], 'enabled': True}
    assert first.data['invocation_path'] != second.data['invocation_path']
    raw = json.loads(Path(first.data['invocation_path']).read_text(encoding='utf-8'))
    assert raw['exit_code'] == 0 and raw['status'] == 'completed'
    assert all(Path(a['path']).is_file() for a in first.data['artifacts'])


@pytest.mark.parametrize('args', [
    {'values': [True, 2], 'enabled': True}, {'values': [1, 2], 'enabled': 1},
    {'values': [1, '2'], 'enabled': True}, {'values': [1], 'enabled': True},
    {'values': [1, 2], 'enabled': True, 'undeclared': 3}])
def test_actual_stdio_strict_inputs(generated, args):
    assert call(generated[1], args).is_error


@pytest.mark.parametrize('mode,status', [('error', 'execution_error'), ('timeout', 'timeout'), ('nonfinite', 'execution_error')])
def test_native_failures_are_mcp_errors_with_retained_artifacts(generated, mode, status):
    project = generated[1]
    result = call(project, {'values': [1, 2], 'enabled': False, 'mode': mode})
    assert result.is_error
    records = list((project / 'artifacts').glob('*/invocation.json'))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding='utf-8'))
    assert record['status'] == status
    assert (records[0].parent / 'request.json').is_file()
    if mode == 'error':
        assert record['exit_code'] == 7 and (records[0].parent / 'native' / 'before-error.txt').is_file()
    if mode == 'timeout':
        assert (records[0].parent / 'native' / 'before-timeout.txt').is_file()


def test_runtime_must_be_explicit_and_correct(generated, monkeypatch):
    project = generated[1]
    monkeypatch.delenv('P2A_NATIVE_RUNTIME')
    assert call(project, {'values': [1, 2], 'enabled': True}).is_error
    monkeypatch.setenv('P2A_NATIVE_RUNTIME', str(project / 'server.py'))
    assert call(project, {'values': [1, 2], 'enabled': True}).is_error


def test_source_tampering_rejected_and_package_contains_only_bound_sources(generated):
    _, project = generated
    package = package_mcp(project)
    assert 'vendor/repo/entry.py' in package['files']
    assert not any('.env' in name or 'personal.pdf' in name or 'artifacts' in name for name in package['files'])
    (project / 'vendor' / 'repo' / 'entry.py').write_text('raise RuntimeError("changed")', encoding='utf-8')
    assert call(project, {'values': [1, 2], 'enabled': True}).is_error


@pytest.mark.parametrize('change', ['unreviewed', 'source', 'argv', 'timeout', 'path', 'dependency', 'version_code', 'boolean_version'])
def test_invalid_contracts_rejected(generated, change):
    repo, _ = generated
    value = contract(repo)
    tool = value['tools']['native_record']
    if change == 'unreviewed': tool['reviewed'] = False
    if change == 'source': tool['source_files']['entry.py'] = '0' * 64
    if change == 'argv': tool['argv'].append('{values}')
    if change == 'timeout': tool['timeout_seconds'] = 181
    if change == 'path': tool['result_file'] = '../result.json'
    if change == 'dependency': tool['native_dependencies'] = []
    if change == 'version_code': tool['runtime']['version_args'] = ['-c', 'print(1)']
    if change == 'boolean_version': value['schema_version'] = True
    with pytest.raises(ValueError):
        build_command_mcp(repo, value, 'Rejected', repo.parent / 'rejected')


@pytest.mark.parametrize('text', ['{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":1,"x":2}'])
def test_nonfinite_duplicate_json_rejected(text):
    with pytest.raises((ValueError, ContractError)):
        _command_json(text)


def test_model_source_rejected_before_copy(generated):
    repo, _ = generated
    (repo / 'entry.py').write_text('import torch\n', encoding='utf-8')
    with pytest.raises(ValueError):
        build_command_mcp(repo, contract(repo), 'Rejected', repo.parent / 'rejected')


def test_dynamic_model_import_blocked_inside_native_process(generated):
    repo, _ = generated
    (repo / 'entry.py').write_text("__import__('tor' + 'ch')\n", encoding='utf-8')
    made = build_command_mcp(repo, contract(repo), 'DynamicPolicy', repo.parent / 'dynamic')
    project = Path(made['project_path'])
    assert call(project, {'values': [1, 2], 'enabled': True}).is_error
    diagnostics = next((project / 'artifacts').glob('*/stderr.bin')).read_text(encoding='utf-8')
    assert 'Model execution framework is outside this native runtime policy: torch' in diagnostics


from code_contracts import ContractError
