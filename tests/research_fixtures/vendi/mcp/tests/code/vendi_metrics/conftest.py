"""Independent source oracles and decorated-client evidence; no model calls."""
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import uuid
import warnings

import numpy as np
import pytest
from fastmcp import Client

ROOT = Path(__file__).resolve().parents[3]
RESULTS = Path(os.environ['VENDI_VERIFICATION_OUTPUT'])
RESULTS.mkdir(parents=True, exist_ok=True)
source = ROOT / 'repo' / 'Vendi-Score' / 'vendi_score' / 'vendi.py'
spec = importlib.util.spec_from_file_location('independent_original_vendi_oracle', source)
ORACLE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ORACLE)
SERVER = runpy.run_path(str(ROOT / 'src' / 'Vendi-Score_mcp.py'))['mcp']

CASES = json.loads((ROOT / 'tests' / 'data' / 'vendi_metrics' / 'fixtures.json').read_text(encoding='utf-8'))
SAVED_NATIVE = {case['id']: case for case in json.loads((ROOT / 'notebooks' / 'vendi_reference' / 'reference.json').read_text(encoding='utf-8'))['results']}


def persist(record):
    record['evidence_id'] = uuid.uuid4().hex
    target = RESULTS / (record['evidence_id'] + '.json')
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=True) + '\n', encoding='utf-8')
    return target


def expected(tool, arguments):
    key, symbol = ('K', 'score_K') if tool == 'vendi_score_similarity' else ('X', 'score_dual')
    array = np.asarray(arguments[key], dtype=np.float64)
    before = array.copy()
    options = {k: arguments[k] for k in ('q', 'p', 'normalize') if k in arguments}
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter('always')
        value = getattr(ORACLE, symbol)(array, **options)
    np.testing.assert_array_equal(array, before)
    return float(value), [str(x.message) for x in notices]


async def async_call(tool, arguments):
    async with Client(SERVER) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def call(tool, arguments):
    original = copy.deepcopy(arguments)
    response = asyncio.run(async_call(tool, arguments))
    assert arguments == original, 'User inputs were mutated'
    if response.is_error:
        message = '\n'.join(x.text for x in response.content if hasattr(x, 'text'))
        persist({'tool': tool, 'arguments': original, 'is_error': True, 'native_error': message})
        return message
    data = response.data
    persist({'tool': tool, 'arguments': original, 'is_error': False, 'response': data})
    return data


def assert_error_case(tool, case):
    error = call(tool, case['arguments'])
    assert isinstance(error, str), error
    # FastMCP JSON serialization converts NaN/Infinity to null before dispatch.
    # Check the actual schema rejection, without altering immutable numeric
    # fixtures or any scientific comparator. Attempt 1 records the earlier
    # mistaken expectation that those values would reach the wrapper guard.
    if case['name'] in {'nonfinite_nan', 'nonfinite_inf', 'q_nan', 'q_infinite_numeric', 'weight_nan'}:
        assert 'Input should be a valid number' in error
        expected_parameter = 'q' if case['name'].startswith('q_') else ('p' if case['name'] == 'weight_nan' else ('K' if tool == 'vendi_score_similarity' else 'X'))
        assert expected_parameter in error
        persist({'review': 'nonfinite transport serialization and schema rejection', 'tool': tool, 'case': case['name'], 'native_error': error, 'scientific_comparator_changed': False})
    else:
        assert case['error_contains'] in error


def assert_saved_reference(case, actual):
    if case['name'] in SAVED_NATIVE:
        reference = SAVED_NATIVE[case['name']]
        assert actual['score'] == pytest.approx(reference['value'], rel=1e-12, abs=1e-12)
        assert actual['warnings'] == reference['warnings']
    if case['name'].startswith('identity_'):
        assert actual['score'] == pytest.approx(3, rel=1e-12, abs=1e-12)
    if case['name'].startswith('identical_'):
        assert actual['score'] == pytest.approx(1, rel=1e-12, abs=1e-12)


def assert_positive(tool, arguments):
    options = copy.deepcopy(arguments)
    options['output_dir'] = str(RESULTS / 'artifacts')
    native, native_warnings = expected(tool, arguments)
    actual = call(tool, options)
    assert isinstance(actual, dict), actual
    assert np.isfinite(actual['score']) and np.isfinite(native)
    assert actual['score'] == pytest.approx(native, rel=1e-12, abs=1e-12)
    key = 'K' if tool == 'vendi_score_similarity' else 'X'
    assert actual['num_samples'] == len(arguments[key])
    assert actual['input_shape'] == [len(arguments[key]), len(arguments[key][0])]
    assert isinstance(actual['message'], str) and actual['message']
    assert actual['reference'] == 'https://github.com/vertaix/Vendi-Score/blob/ff1dfdbe6356b98a6087540f215b9a9db6db7c11/README.md'
    assert len(actual['artifacts']) == 1
    path = Path(actual['artifacts'][0]['path'])
    assert path.is_absolute() and path.is_file() and path.is_relative_to(RESULTS / 'artifacts')
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['native_result'] == actual['score']
    assert raw['inputs'][key] == arguments[key]
    assert raw['inputs']['q'] == arguments.get('q', 1)
    assert raw['inputs']['normalize'] == arguments.get('normalize', tool == 'vendi_score_embeddings')
    if key == 'K':
        assert raw['inputs']['p'] == arguments.get('p')
    assert raw['local_models_called'] is False and raw['gpu_called'] is False
    assert raw['provenance'] == actual['provenance']
    assert actual['provenance']['algorithm_changes'] is False
    assert actual['provenance']['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert actual['warnings'] == raw['warnings'] == native_warnings
    persist({'tool': tool, 'source_path': str(source), 'arguments': arguments, 'independent_original_result': native, 'warnings': native_warnings, 'actual_score': actual['score'], 'artifact': str(path), 'artifact_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'absolute_error': abs(actual['score'] - native)})
    return actual, raw
