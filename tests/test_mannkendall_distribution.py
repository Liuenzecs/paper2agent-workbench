import hashlib
import json
from pathlib import Path
import pytest

from lab_service import LabService
import mannkendall_example


def test_mannkendall_generation_preserves_source_and_personal_isolation(tmp_path):
    a = mannkendall_example.build_example(LabService(tmp_path / 'a'))
    b = mannkendall_example.build_example(LabService(tmp_path / 'b'))
    assert a != b
    for project in (a, b):
        assert hashlib.sha256((project / 'vendor/repo/pymannkendall.py').read_bytes()).hexdigest() == '537e3b1234a0de0297223729c8aa079978e0ec3d77401c5b8592794e512d5fd5'
        assert 'strict_input_validation=True' in (project / 'server.py').read_text()


def test_mannkendall_tamper_refused_before_generation(tmp_path, monkeypatch):
    source = tmp_path / 'app/examples/mannkendall/source'
    source.mkdir(parents=True)
    (source / 'pymannkendall.py').write_text('changed')
    (source.parent / 'provenance.json').write_text(json.dumps({'source_files': {'pymannkendall.py': '0' * 64}}))
    monkeypatch.setattr(mannkendall_example, 'APP_ROOT', tmp_path / 'app')
    service = LabService(tmp_path / 'person')
    with pytest.raises(ValueError, match='hash mismatch'):
        mannkendall_example.build_example(service)
    assert not list((service.root / 'code-projects').glob('mcp-*'))
