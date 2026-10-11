"""Import the reviewed CPU example into a fresh personal project and call real MCP."""
import hashlib
import json
import math
from pathlib import Path
import uuid

APP_ROOT = Path(__file__).resolve().parents[1]


def import_example(data_root):
    source = APP_ROOT / 'examples/vendi/mcp'
    inventory = json.loads((source / 'reports/publication-provenance.json').read_text(encoding='utf-8'))['runtime_files_byte_identical_to_validated_zip']
    verified = {}
    for relative, expected in inventory.items():
        path = source / relative
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('Invalid Vendi example path')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Vendi example hash mismatch')
        verified[relative] = raw
    project = Path(data_root).resolve() / 'code-projects' / ('vendi-' + uuid.uuid4().hex)
    project.mkdir(parents=True, exist_ok=False)
    for relative, raw in verified.items():
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    return project


def run_demo(service, python_executable):
    if not Path(python_executable).is_file():
        raise ValueError('Install the independent Vendi environment and provide its Python executable')
    project = import_example(service.root)
    reference = json.loads((APP_ROOT / 'examples/vendi/mcp/notebooks/vendi_reference/reference.json').read_text(encoding='utf-8'))
    expected = {x['id']: x['value'] for x in reference['results']}
    kernel = [[1, .9, 0], [.9, 1, 0], [0, 0, 1]]
    vectors = [[1, 0], [1, 0], [0, 1]]
    cases = [
        ('readme_q1', 'vendi_score_similarity', {'K': kernel, 'q': 1}),
        ('readme_q2', 'vendi_score_similarity', {'K': kernel, 'q': 2}),
        ('vectors_q1', 'vendi_score_embeddings', {'X': vectors, 'q': 1}),
        ('vectors_q2', 'vendi_score_embeddings', {'X': vectors, 'q': 2}),
        ('q0_rejected', 'vendi_score_similarity', {'K': kernel, 'q': 0}),
    ]
    records = []
    for name, tool, arguments in cases:
        native = service.test_code_mcp(str(project), tool, arguments, python_executable)
        if name == 'q0_rejected':
            passed = native.get('decision') == 'execution_error' and 'q=0' in json.dumps(native)
            score = None
        else:
            score = (native.get('result') or {}).get('data', {}).get('score')
            passed = native.get('decision') == 'completed' and isinstance(score, (float, int)) and math.isclose(score, expected[name], rel_tol=1e-12, abs_tol=1e-12)
        records.append({'case': name, 'tool': tool, 'passed': passed, 'score': score,
                        'expected_original_score': expected.get(name), 'native_record': native})
    report = {'success': all(x['passed'] for x in records), 'project_path': str(project),
              'transport': 'real_stdio_fastmcp_client', 'local_model_called': False,
              'vector_inputs': 'explicit synthetic numeric arrays; no embedding generation', 'cases': records}
    destination = project / 'demo-result.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return {'success': report['success'], 'project_path': str(project), 'report_path': str(destination),
            'cases': [{k: x[k] for k in ('case', 'passed', 'score', 'expected_original_score')} for x in records]}
