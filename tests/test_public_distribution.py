"""Public template and personal example boundaries; no API or model execution."""
import hashlib
import json
from pathlib import Path
import pytest

from lab_service import LabService
from remote_config import is_remote_configured, prepare_remote_settings
import vendi_example


def test_unconfigured_template_never_reuses_global_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERQA_CHAT_API_KEY', 'synthetic-global-chat')
    monkeypatch.setenv('PAPERQA_EMBEDDING_API_KEY', 'synthetic-global-embedding')
    service = LabService(tmp_path)
    assert not service.status()['ready']
    assert not service.status()['provider_configured']
    assert service.status()['credential_available'] == {'chat': False, 'embedding': False}
    with pytest.raises(ValueError, match='Configure your own'):
        prepare_remote_settings(service._settings())


def test_configuring_one_person_does_not_configure_another(tmp_path):
    a, b = LabService(tmp_path / 'a'), LabService(tmp_path / 'b')
    a.save_settings({'api_base': 'https://provider.example.org/v1', 'chat_model': 'chat-a',
                     'embedding_model': 'embed-a', 'chat_key': 'synthetic-person-a',
                     'embedding_key': 'synthetic-person-a'})
    assert a.status()['ready'] and is_remote_configured(a._settings())
    assert not b.status()['ready'] and not is_remote_configured(b._settings())
    assert 'synthetic-person-a' not in a.settings_path.read_text(encoding='utf-8')
    assert 'synthetic-person-a' not in json.dumps(a.status())


def test_example_imports_are_unique_and_byte_identical(tmp_path):
    first = vendi_example.import_example(tmp_path)
    before = (first / 'vendor/repo/vendi_score/vendi.py').read_bytes()
    second = vendi_example.import_example(tmp_path)
    assert first != second and first.parent == tmp_path / 'code-projects'
    assert (first / 'vendor/repo/vendi_score/vendi.py').read_bytes() == before
    assert (second / 'vendor/repo/vendi_score/vendi.py').read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == '8ee71e09e475c82f65262174505c88a2a119da5fb62a9e0ef4f2c12864441ac6'


def test_changed_example_is_refused_before_project_creation(tmp_path, monkeypatch):
    fake_app = tmp_path / 'app'
    fake_source = fake_app / 'examples/vendi/mcp'
    (fake_source / 'reports').mkdir(parents=True)
    (fake_source / 'server.py').write_text('changed source', encoding='utf-8')
    (fake_source / 'reports/publication-provenance.json').write_text(json.dumps(
        {'runtime_files_byte_identical_to_validated_zip': {'server.py': '0' * 64}}), encoding='utf-8')
    monkeypatch.setattr(vendi_example, 'APP_ROOT', fake_app)
    with pytest.raises(ValueError, match='hash mismatch'):
        vendi_example.import_example(tmp_path / 'personal')
    assert not (tmp_path / 'personal').exists()
