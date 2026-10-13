import asyncio
import inspect
import sys
from pathlib import Path

import numpy as np
import pytest
from fastmcp import Client
from conftest import RESULTS, CASES, SERVER, ORACLE, assert_positive, assert_error_case, assert_saved_reference, call, persist

TOOL = 'vendi_score_embeddings'
POSITIVES = [x for x in CASES['positive'] if x['tool'] == TOOL]
NEGATIVES = [x for x in CASES['negative'] if x['tool'] == TOOL]


@pytest.mark.parametrize('case', POSITIVES, ids=[x['name'] for x in POSITIVES])
def test_full_reference_agreement(case):
    actual, _ = assert_positive(TOOL, case['arguments'])
    assert_saved_reference(case, actual)


@pytest.mark.parametrize('case', NEGATIVES, ids=[x['name'] for x in NEGATIVES])
def test_error_contract(case):
    assert_error_case(TOOL, case)


def test_native_default_and_schema():
    async def list_tools():
        async with Client(SERVER) as client:
            return await client.list_tools()
    tools = {x.name: x for x in asyncio.run(list_tools())}
    schema = tools[TOOL].input_schema
    assert schema['required'] == ['X']
    assert schema['properties']['normalize']['default'] is True
    assert schema['properties']['q']['default'] == 1
    for name, item in schema['properties'].items():
        assert item.get('description'), name
    assert inspect.signature(ORACLE.score_dual).parameters['normalize'].default is True
    assert_positive(TOOL, {'X': [[2, 0], [3, 0], [0, 4]]})


def test_actual_upstream_binding(monkeypatch):
    module = sys.modules['tools.vendi_metrics']
    bound = module.vendi.score_dual
    seen = []
    def spy(X, **kwargs):
        seen.append((X.copy(), kwargs.copy()))
        return bound(X, **kwargs)
    monkeypatch.setattr(module.vendi, 'score_dual', spy)
    arguments = {'X': [[1, 0], [1, 0], [0, 1]], 'q': 'inf', 'normalize': False}
    assert_positive(TOOL, arguments)
    assert len(seen) == 1
    np.testing.assert_array_equal(seen[0][0], arguments['X'])
    assert seen[0][1] == {'q': 'inf', 'normalize': False}
    persist({'review': 'runtime observed exactly one bound original score_dual call with q and normalize forwarded'})


@pytest.mark.parametrize('q', [1, 2, 'inf'])
def test_composed_kernel_and_vector_diversity(q):
    vectors = np.array([[2, 0], [3, 0], [0, 4]], dtype=float)
    normalized = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    kernel = normalized @ normalized.T
    embeddings, _ = assert_positive(TOOL, {'X': vectors.tolist(), 'q': q, 'normalize': True})
    similarity, _ = assert_positive('vendi_score_similarity', {'K': kernel.tolist(), 'q': q})
    assert embeddings['score'] == pytest.approx(similarity['score'], rel=1e-12, abs=1e-12)
    persist({'review': 'explicit composition from supplied vectors to cosine kernel; no model embeddings generated', 'q': q, 'X': vectors.tolist(), 'K': kernel.tolist(), 'vector_score': embeddings['score'], 'kernel_score': similarity['score']})


def test_repeated_artifacts_and_changed_inputs():
    first, raw1 = assert_positive(TOOL, {'X': [[1, 0], [1, 0], [0, 1]]})
    path = Path(first['artifacts'][0]['path'])
    original = path.read_bytes()
    second, raw2 = assert_positive(TOOL, {'X': [[1, 0], [1, 0], [0, 1]]})
    third, _ = assert_positive(TOOL, {'X': np.eye(3).tolist()})
    assert raw1 == raw2
    assert len({x['artifacts'][0]['path'] for x in (first, second, third)}) == 3
    assert third['score'] == pytest.approx(3, rel=1e-12, abs=1e-12)
    assert first['score'] != third['score']
    assert path.read_bytes() == original


def test_nonfinite_original_result_rejected(monkeypatch):
    module = sys.modules['tools.vendi_metrics']
    monkeypatch.setattr(module.vendi, 'score_dual', lambda *args, **kwargs: float('nan'))
    error = call(TOOL, {'X': [[1, 0]]})
    assert 'original scientific function returned a nonfinite score' in error
