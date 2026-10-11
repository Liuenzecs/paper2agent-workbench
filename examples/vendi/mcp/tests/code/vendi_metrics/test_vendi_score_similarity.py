import asyncio
import ast
import hashlib
import inspect
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from fastmcp import Client
from conftest import ROOT, RESULTS, CASES, SERVER, ORACLE, assert_positive, assert_error_case, assert_saved_reference, call, persist

TOOL = 'vendi_score_similarity'
POSITIVES = [x for x in CASES['positive'] if x['tool'] == TOOL]
NEGATIVES = [x for x in CASES['negative'] if x['tool'] == TOOL]


@pytest.mark.parametrize('case', POSITIVES, ids=[x['name'] for x in POSITIVES])
def test_full_reference_agreement(case):
    actual, _ = assert_positive(TOOL, case['arguments'])
    assert_saved_reference(case, actual)


@pytest.mark.parametrize('case', NEGATIVES, ids=[x['name'] for x in NEGATIVES])
def test_error_contract(case):
    assert_error_case(TOOL, case)


def test_defaults_schema_and_source_bytes():
    async def get_tools():
        async with Client(SERVER) as client:
            return await client.list_tools()
    tools = {x.name: x for x in asyncio.run(get_tools())}
    assert set(tools) == {'vendi_score_similarity', 'vendi_score_embeddings'}
    schema = tools[TOOL].input_schema
    assert schema['required'] == ['K']
    assert schema['properties']['q']['default'] == 1
    assert schema['properties']['normalize']['default'] is False
    assert schema['properties']['p']['default'] is None
    for name, item in schema['properties'].items():
        assert item.get('description'), name
    assert inspect.signature(ORACLE.score_K).parameters['normalize'].default is False
    for item in json.loads((ROOT / 'manifest.json').read_text())['copied_source_files']:
        original = ROOT / 'repo' / 'Vendi-Score' / item['path']
        bundled = ROOT / 'vendor' / 'repo' / item['path']
        assert original.read_bytes() == bundled.read_bytes()
        assert hashlib.sha256(bundled.read_bytes()).hexdigest() == item['sha256']
    persist({'review': 'inventory/schema/source byte identity', 'schemas': {name: tool.input_schema for name, tool in tools.items()}})


def test_upstream_exception_remains_mcp_error(monkeypatch):
    module = sys.modules['tools.vendi_metrics']
    def original_failure(*args, **kwargs):
        raise np.linalg.LinAlgError('Independent injected upstream eigensolver failure')
    monkeypatch.setattr(module.vendi, 'score_K', original_failure)
    error = call(TOOL, {'K': [[1]]})
    assert 'Independent injected upstream eigensolver failure' in error


def test_actual_upstream_binding_and_parameters(monkeypatch):
    module = sys.modules['tools.vendi_metrics']
    bound = module.vendi.score_K
    seen = []
    def spy(K, **kwargs):
        seen.append((K.copy(), kwargs.copy()))
        return bound(K, **kwargs)
    monkeypatch.setattr(module.vendi, 'score_K', spy)
    case = {'K': [[4, 5.4, 0], [5.4, 9, 0], [0, 0, 16]], 'q': 2, 'p': [0.25, 0.25, 0.5], 'normalize': True}
    result, _ = assert_positive(TOOL, case)
    assert len(seen) == 1
    np.testing.assert_array_equal(seen[0][0], case['K'])
    np.testing.assert_array_equal(seen[0][1]['p'], case['p'])
    assert seen[0][1]['q'] == 2 and seen[0][1]['normalize'] is True
    assert Path(module.vendi.__file__).resolve() == (ROOT / 'vendor' / 'repo' / 'vendi_score' / 'vendi.py').resolve()
    tree = ast.parse((ROOT / 'src' / 'tools' / 'vendi_metrics.py').read_text())
    scientific = [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {'score_K', 'score_dual', 'eigvalsh', 'entropy_q'}]
    assert scientific == ['score_K', 'score_dual']
    persist({'review': 'runtime monkeypatch observed one original scientific call with unmodified input and q,p,normalize forwarded', 'actual_score': result['score']})


def test_repeated_calls_artifact_isolation():
    arguments = {'K': [[1, 0.9, 0], [0.9, 1, 0], [0, 0, 1]]}
    first, first_raw = assert_positive(TOOL, arguments)
    path = Path(first['artifacts'][0]['path'])
    old = path.read_bytes()
    second, second_raw = assert_positive(TOOL, arguments)
    third, third_raw = assert_positive(TOOL, {'K': np.eye(3).tolist()})
    assert len({first['artifacts'][0]['path'], second['artifacts'][0]['path'], third['artifacts'][0]['path']}) == 3
    assert first_raw == second_raw
    assert first['score'] != third['score']
    assert path.read_bytes() == old


def test_native_q0_issue_is_preserved_not_replaced():
    kernel = np.ones((3, 3))
    native = float(ORACLE.score_K(kernel, q=0))
    eigenvalues = ORACLE.scipy.linalg.eigvalsh(kernel / 3)
    # q=0 counts every strictly positive eigenvalue, including roundoff.
    # The historical local observation was 2; GitHub Windows observed 3.
    # Check the unchanged upstream behavior without imposing one BLAS result.
    np.testing.assert_allclose(eigenvalues, [0, 0, 1], atol=1e-12, rtol=1e-12)
    assert np.linalg.matrix_rank(kernel) == 1
    assert native == pytest.approx(int(np.count_nonzero(eigenvalues > 0)), abs=1e-12)
    error = call(TOOL, {'K': kernel.tolist(), 'q': 0})
    assert 'q=0 is deferred' in error
    persist({'review': 'upstream numerical rank issue explicit exclusion', 'original_q0_identical_3_score': native,
             'observed_eigenvalues': eigenvalues.tolist(), 'historical_local_score': 2,
             'historical_github_windows_score': 3.0000000000000004, 'mathematical_rank': 1, 'mcp_error': error})
