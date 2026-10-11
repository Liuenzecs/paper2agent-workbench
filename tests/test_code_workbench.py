"""Meaningful static and real stdio tests; CPU-only, no models or external API."""
import hashlib
import json
import runpy
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from code_workbench import WorkbenchError, build_mcp, package_mcp, scan_repository, test_mcp as call_mcp


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / '研究源代码 with spaces'
    root.mkdir()
    (root / 'LICENSE').write_text('MIT License\nCopyright example authors\n', encoding='utf-8')
    (root / 'ops.py').write_text(
        'from typing import Literal\n'
        'def weighted_sum(values: list[float], scale: float = 2.0, *, mode: Literal["sum", "mean"] = "sum") -> float:\n'
        '    """Compute the source-defined weighted sum or mean."""\n'
        '    total = sum(values) * scale\n'
        '    return total if mode == "sum" else total / len(values)\n'
        'def finite_failure(value: float) -> float:\n    return float("nan")\n'
        'def inferred(x=3):\n    return x + 1\n'
        'def unknown(x):\n    return x\n'
        'async def async_fn(x: int):\n    return x\n'
        'def variadic(*xs):\n    return xs\n'
        'class Object:\n    def public(self, x: int):\n        return x\n', encoding='utf-8')
    return root


def test_static_scan_never_imports_and_records_rejections(repo):
    (repo / 'boom.py').write_text('raise RuntimeError("IMPORT MUST NOT RUN")\ndef available(x: int):\n return x\n', encoding='utf-8')
    hidden = repo / '.venv'
    hidden.mkdir()
    (hidden / 'ignored.py').write_text('raise SystemExit(99)', encoding='utf-8')
    inventory = scan_repository(repo)
    indexed = {c['id']: c for c in inventory['candidates']}
    assert inventory['execution_performed'] is False
    assert inventory['files_scanned'] == 2
    assert indexed['boom:available']['eligible']
    assert indexed['ops:weighted_sum']['eligible']
    assert 'Literal' in indexed['ops:weighted_sum']['parameters'][-1]['type']
    assert indexed['ops:inferred']['assumptions']
    for symbol in ('unknown', 'async_fn', 'variadic', 'Object.public'):
        assert not indexed['ops:' + symbol]['eligible']
        assert indexed['ops:' + symbol]['reasons']


def test_unknown_and_malicious_selection_produce_no_project(repo, tmp_path):
    out = tmp_path / 'generated'
    for choices, name in [(['../../escape:foo'], 'ok'), (['ops:unknown'], 'ok'), (['ops:weighted_sum'], '../../escape')]:
        with pytest.raises(WorkbenchError):
            build_mcp(repo, choices, name, out)
    assert not out.exists()
    with pytest.raises(WorkbenchError):
        build_mcp(repo, ['ops:weighted_sum'], 'ok', repo / 'generated')


def test_copies_source_exactly_preserves_license_and_packages_no_results(repo, tmp_path):
    built = build_mcp(repo, ['ops:weighted_sum'], '研究工具', tmp_path / 'generated outside app')
    project = Path(built['project_path'])
    manifest = json.loads(Path(built['manifest_path']).read_text(encoding='utf-8'))
    copied = project / 'vendor' / 'repo' / 'ops.py'
    assert copied.read_bytes() == (repo / 'ops.py').read_bytes()
    assert manifest['copied_source_files'][0]['sha256'] == hashlib.sha256(copied.read_bytes()).hexdigest()
    assert (project / 'vendor' / 'repo' / 'LICENSE').is_file()
    assert manifest['verification_status'] == 'generated_not_executed_or_independently_verified'
    (project / 'outputs').mkdir()
    (project / 'outputs' / 'private-result.json').write_text('{}')
    packaged = package_mcp(project)
    with zipfile.ZipFile(packaged['zip_path']) as archive:
        assert not any('/outputs/' in p or '/.venv/' in p for p in archive.namelist())
        assert any(p.endswith('/vendor/repo/LICENSE') for p in archive.namelist())
    copied.write_text('# changed\n', encoding='utf-8')
    with pytest.raises(WorkbenchError):
        package_mcp(project)


def test_real_stdio_uses_native_function_changed_inputs_and_literal_schema(repo, tmp_path):
    original = runpy.run_path(str(repo / 'ops.py'))['weighted_sum']
    built = build_mcp(repo, ['ops:weighted_sum'], 'fixture tools', tmp_path / 'generated')
    artifact_dirs = []
    for arguments in ({'values': [1.5, -2, 4], 'scale': 3, 'mode': 'sum'},
                      {'values': [2, 8], 'scale': .25, 'mode': 'mean'}):
        result = call_mcp(built['project_path'], sys.executable, 'ops__weighted_sum', arguments)
        assert result['decision'] == 'completed', result.get('error')
        assert result['transport'] == 'real_stdio_fastmcp_client'
        data = result['result']['data']
        assert data['native_result'] == original(**arguments)
        assert 'source://sha256/' in data['reference']
        p = Path(data['artifacts'][0]['path'])
        assert json.loads(p.read_text(encoding='utf-8')) == original(**arguments)
        artifact_dirs.append(p.parent)
    assert artifact_dirs[0] != artifact_dirs[1]
    wrong = call_mcp(built['project_path'], sys.executable, 'ops__weighted_sum', {'values': [1], 'mode': 'invalid'})
    assert wrong['decision'] == 'execution_error'


def test_nonfinite_is_error_and_never_persists_success(repo, tmp_path):
    built = build_mcp(repo, ['ops:finite_failure'], 'fixture tools', tmp_path / 'generated')
    result = call_mcp(built['project_path'], sys.executable, 'ops__finite_failure', {'value': 1})
    assert result['decision'] == 'execution_error'
    assert not (Path(built['project_path']) / 'outputs').exists()


def test_local_model_dependency_blocked_without_import(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'LICENSE').write_text('MIT')
    (repo / 'compute.py').write_text('import torch\ndef compute(x: int):\n return x\n')
    c = scan_repository(repo)['candidates'][0]
    assert not c['runnable']
    with pytest.raises(WorkbenchError, match='本地模型'):
        build_mcp(repo, ['compute:compute'], 'blocked', tmp_path / 'generated')


def test_missing_license_and_embedded_key_refused(repo, tmp_path):
    (repo / 'LICENSE').unlink()
    with pytest.raises(WorkbenchError, match='许可'):
        build_mcp(repo, ['ops:weighted_sum'], 'test', tmp_path / 'generated')
    (repo / 'LICENSE').write_text('MIT')
    with (repo / 'ops.py').open('a', encoding='utf-8') as f:
        f.write('\nEXAMPLE = "' + 'sk-' + 'a' * 24 + '"\n')
    with pytest.raises(WorkbenchError, match='密钥'):
        build_mcp(repo, ['ops:weighted_sum'], 'test', tmp_path / 'generated')


def test_official_humaneval_type_branches_support_json_input():
    root = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'humaneval'
    if not root.exists():
        pytest.skip('official reference source absent')
    c = next(x for x in scan_repository(root)['candidates'] if x['id'] == 'human_eval.evaluation:estimate_pass_at_k')
    assert c['eligible'], c['reasons']
    assert c['parameters'][0]['type'] == 'int | list[int]'
    assert c['parameters'][1]['type'] == 'list[int]'
    assert c['assumptions']


def test_no_installed_module_substitution_for_pinned_source(tmp_path):
    repo = tmp_path / 'collision repo'
    repo.mkdir()
    (repo / 'LICENSE').write_text('MIT')
    # json is already imported by the generated server; substituting it would change the binding.
    (repo / 'json.py').write_text('def dumps(x: int):\n return x + 42\n')
    built = build_mcp(repo, ['json:dumps'], 'collision', tmp_path / 'generated')
    result = call_mcp(built['project_path'], sys.executable, 'json__dumps', {'x': 1})
    assert result['decision'] == 'execution_error'
    assert 'origin differs' in result['error']


def test_source_mutation_is_rejected_before_runtime(repo, tmp_path):
    built = build_mcp(repo, ['ops:weighted_sum'], 'test', tmp_path / 'generated')
    (Path(built['project_path']) / 'vendor' / 'repo' / 'ops.py').write_text('raise RuntimeError("tamper")')
    with pytest.raises(WorkbenchError, match='源码哈希'):
        call_mcp(built['project_path'], sys.executable, 'ops__weighted_sum', {'values': [1]})


def test_explicit_dynamic_code_and_pretrained_loader_are_not_runnable(repo, tmp_path):
    (repo / 'danger.py').write_text('def _unsafe(code):\n return eval(code)\ndef evaluate_code(code: str):\n return _unsafe(code)\ndef load_checkpoint(path: str):\n return Unknown.from_pretrained(path)\n')
    candidates = {c['id']: c for c in scan_repository(repo)['candidates']}
    for identifier in ['danger:evaluate_code', 'danger:load_checkpoint']:
        assert not candidates[identifier]['runnable']
        assert candidates[identifier]['execution_restrictions']
        with pytest.raises(WorkbenchError, match='函数需要人工适配'):
            build_mcp(repo, [identifier], 'blocked', tmp_path / 'generated')


def test_generated_import_guard_blocks_dynamic_model_import_without_importing_model(repo, tmp_path):
    built = build_mcp(repo, ['ops:weighted_sum'], 'guard test', tmp_path / 'generated')
    import subprocess
    # The module is blocked before Python resolves or loads any model package.
    command = [sys.executable, '-c', "import runpy,importlib,os; d=runpy.run_path(" + repr(built['server_path']) + "); assert os.environ['CUDA_VISIBLE_DEVICES']==''; assert os.environ['HF_HUB_OFFLINE']=='1'; importlib.import_module('transformers')"]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
    assert result.returncode != 0
    assert 'Unsupported runtime dependency' in result.stderr
