"""Native stdio verification against fresh executions of pinned original source."""
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import warnings

import numpy as np
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'tests' / 'results' / 'vendi_metrics' / 'stdio-v1'
OUT.mkdir(parents=True, exist_ok=False)
spec = importlib.util.spec_from_file_location('independent_stdio_vendi_oracle', ROOT / 'repo' / 'Vendi-Score' / 'vendi_score' / 'vendi.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
fixtures_path = ROOT / 'tests' / 'data' / 'vendi_metrics' / 'fixtures.json'
fixture_bytes = fixtures_path.read_bytes()
fixtures = json.loads(fixture_bytes)
report = {'runner': 'local test runner', 'transport': 'real_stdio', 'started_at': datetime.now(timezone.utc).isoformat(), 'python': sys.executable, 'server': str(ROOT / 'server.py'), 'fixtures_sha256': hashlib.sha256(fixture_bytes).hexdigest(), 'cases': [], 'success': False}


async def main():
    log = ROOT / 'tests' / 'logs' / 'vendi-verify-stdio-v1-server.log'
    transport = PythonStdioTransport(ROOT / 'server.py', python_cmd=sys.executable, cwd=str(ROOT), env={'CUDA_VISIBLE_DEVICES': '-1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}, log_file=log)
    async with Client(transport, timeout=30) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {'vendi_score_similarity', 'vendi_score_embeddings'}
        report['schemas'] = {name: tool.input_schema for name, tool in tools.items()}
        report['output_schemas'] = {name: tool.output_schema for name, tool in tools.items()}
        previous_paths = set()
        for case in fixtures['positive'] + fixtures['positive'][:2]:
            arguments = copy.deepcopy(case['arguments'])
            options = {name: arguments[name] for name in ('q', 'p', 'normalize') if name in arguments}
            key, function = ('K', 'score_K') if case['tool'] == 'vendi_score_similarity' else ('X', 'score_dual')
            with warnings.catch_warnings(record=True) as notices:
                warnings.simplefilter('always')
                original = float(getattr(oracle, function)(np.asarray(arguments[key], dtype=np.float64), **options))
            arguments['output_dir'] = str(OUT / 'artifacts')
            immutable = copy.deepcopy(arguments)
            result = await client.call_tool(case['tool'], arguments, raise_on_error=False)
            assert arguments == immutable
            data = result.data
            record = {'name': case['name'], 'tool': case['tool'], 'arguments': arguments, 'is_error': result.is_error, 'native_response': data, 'independent_original_score': original, 'independent_original_warnings': [str(x.message) for x in notices], 'passed': False}
            report['cases'].append(record)
            assert not result.is_error
            assert math.isfinite(data['score']) and math.isclose(data['score'], original, rel_tol=1e-12, abs_tol=1e-12)
            assert data['num_samples'] == len(arguments[key])
            assert data['input_shape'] == [len(arguments[key]), len(arguments[key][0])]
            assert data['warnings'] == record['independent_original_warnings']
            artifact = Path(data['artifacts'][0]['path'])
            assert artifact.is_absolute() and artifact.is_file() and artifact.is_relative_to(OUT / 'artifacts')
            assert str(artifact) not in previous_paths
            previous_paths.add(str(artifact))
            raw = json.loads(artifact.read_text(encoding='utf-8'))
            assert raw['native_result'] == data['score']
            assert raw['inputs'] == {key: arguments[key], 'q': arguments.get('q', 1), **({'p': arguments.get('p')} if key == 'K' else {}), 'normalize': arguments.get('normalize', key == 'X')}
            assert raw['provenance'] == data['provenance']
            assert data['provenance']['source_sha256'] == hashlib.sha256((ROOT / 'repo' / 'Vendi-Score' / 'vendi_score' / 'vendi.py').read_bytes()).hexdigest()
            assert raw['local_models_called'] is False and raw['gpu_called'] is False
            record['artifact_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            record['passed'] = True
        for case in fixtures['negative']:
            result = await client.call_tool(case['tool'], case['arguments'], raise_on_error=False)
            message = '\n'.join(x.text for x in result.content if hasattr(x, 'text'))
            record = {'name': case['name'], 'tool': case['tool'], 'arguments': case['arguments'], 'is_error': result.is_error, 'native_error': message, 'passed': False}
            report['cases'].append(record)
            assert result.is_error
            if case['name'] in {'nonfinite_nan', 'nonfinite_inf', 'q_nan', 'q_infinite_numeric', 'weight_nan'}:
                assert 'Input should be a valid number' in message
            else:
                assert case['error_contains'] in message
            record['passed'] = True
    assert fixtures_path.read_bytes() == fixture_bytes
    report['success'] = True


try:
    asyncio.run(main())
finally:
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    (OUT / 'raw-stdio-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True) + '\n', encoding='utf-8')
print(json.dumps({'success': report['success'], 'positive_calls': sum(not x['is_error'] for x in report['cases']), 'negative_calls': sum(x['is_error'] for x in report['cases']), 'report': str(OUT / 'raw-stdio-results.json')}))
