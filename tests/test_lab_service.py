import json
import os
from pathlib import Path
import pymupdf
import pytest
from lab_service import LabService, _protect


def pdf_bytes(text="An independent paper about adapter weights"):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
        return pdf.tobytes()


def test_personal_library_and_settings_isolation(tmp_path):
    a, b = LabService(tmp_path / "A"), LabService(tmp_path / "B")
    a.ingest_bytes("../../unsafe.pdf", pdf_bytes(), "Paper A")
    assert len(a.inventory()) == 1 and b.inventory() == []
    assert a.envs != b.envs
    a.save_settings({"chat_key": "synthetic-private-value", "embedding_key": "synthetic-private-value"})
    assert a.status()["credential_available"] == {"chat": True, "embedding": True}
    assert "synthetic-private-value" not in a.settings_path.read_text()
    assert "synthetic-private-value" not in json.dumps(a.status())
    assert not (tmp_path / "unsafe.pdf").exists()


def test_repeat_import_preserves_title_url_and_original_snapshot(tmp_path):
    svc=LabService(tmp_path/'workspace');content=pdf_bytes()
    first=svc.ingest_bytes('first.pdf',content,'Original scientific title','https://example.org/paper')
    original=Path(first['bundle_path']).read_bytes()
    svc.ingest_bytes('renamed.pdf',content)
    paper=svc.inventory()[0]
    bundle=svc.library.load(paper['bundle_path'])
    assert paper['title']=='Original scientific title' and bundle['source_url']=='https://example.org/paper'
    assert Path(first['bundle_path']).read_bytes()==original
    local=tmp_path/'local.pdf';local.write_bytes(content)
    svc.ingest_path(local)
    assert svc.inventory()[0]['title']=='Original scientific title'
    svc.ingest_path(local,'Revised title','https://example.org/revised')
    latest=svc.library.load(svc.inventory()[0]['bundle_path'])
    assert latest['title']=='Revised title' and latest['source_url']=='https://example.org/revised'


def test_uploaded_title_uses_pdf_metadata_before_filename(tmp_path):
    with pymupdf.open() as document:
        document.new_page().insert_text((72,72),'Public scientific text')
        document.set_metadata({'title':'A meaningful manuscript title'})
        content=document.tobytes()
    svc=LabService(tmp_path)
    svc.ingest_bytes('opaque-filename.pdf',content)
    assert svc.inventory()[0]['title']=='A meaningful manuscript title'


@pytest.mark.skipif(os.name != "nt", reason="Windows user-bound DPAPI")
def test_protected_credential_roundtrip_and_restart(tmp_path):
    secret = b"synthetic-private-value-for-dpapi"
    encrypted = _protect(secret)
    assert secret not in encrypted and _protect(encrypted, decrypt=True) == secret
    svc = LabService(tmp_path)
    svc.save_settings({"chat_key": secret.decode(), "embedding_key": secret.decode(), "remember_keys": True})
    assert secret.decode() not in (tmp_path / "secrets.dpapi.json").read_text()
    for name in svc.envs.values():
        os.environ.pop(name, None)
    restored = LabService(tmp_path)
    assert restored.status()["credential_available"] == {"chat": True, "embedding": True}


def test_local_model_endpoint_rejected_before_request(tmp_path):
    svc = LabService(tmp_path)
    prior = svc.settings_path.read_bytes()
    with pytest.raises(ValueError):
        svc.save_settings({"api_base": "http://127.0.0.1:1234/v1"})
    assert svc.settings_path.read_bytes() == prior

def test_ark_code_controls_do_not_leak_to_another_provider(tmp_path):
    svc=LabService(tmp_path)
    svc.save_settings({'api_base':'https://ark.cn-beijing.volces.com/api/plan/v3',
                       'chat_model':'ark-code-latest','embedding_model':'remote-embedding'})
    raw=json.loads(svc.settings_path.read_text(encoding='utf-8'))
    for field in ['llm_config','summary_llm_config']:
        assert raw[field]['model_list'][0]['litellm_params']['extra_body']=={'thinking':{'type':'disabled'}}
    svc.save_settings({'api_base':'https://another.example.com/v1','chat_model':'another-chat'})
    raw=json.loads(svc.settings_path.read_text(encoding='utf-8'))
    assert all('extra_body' not in raw[field]['model_list'][0]['litellm_params'] for field in ['llm_config','summary_llm_config'])


def test_credential_update_preserves_separate_embedding_provider(tmp_path):
    svc = LabService(tmp_path)
    svc.save_settings({"embedding_api_base": "https://embedding.example.com/v1"})
    svc.save_settings({"chat_key": "synthetic-session-secret"})
    assert svc.status()["embedding_api_base"] == "https://embedding.example.com/v1"


def test_invalid_paper_and_artifact_paths(tmp_path):
    svc = LabService(tmp_path)
    with pytest.raises(ValueError): svc.ingest_bytes("fake.pdf", b"not pdf")
    with pytest.raises(ValueError): svc.result("../settings")
    with pytest.raises(ValueError): svc.paper_file("../settings")
    with pytest.raises(ValueError): svc.start_question("Question", ["unknown"])
    with pytest.raises(ValueError): svc.start_question(" ")


def test_source_pdf_revalidated_before_serving(tmp_path):
    svc = LabService(tmp_path)
    ingest = svc.ingest_bytes("paper.pdf", pdf_bytes())
    path = svc.paper_file(ingest["paper_id"])
    path.write_bytes(path.read_bytes() + b"\nchanged")
    with pytest.raises(ValueError, match="integrity"):
        svc.paper_file(ingest["paper_id"])


def test_failed_question_retained_in_history_after_restart(tmp_path, monkeypatch):
    svc = LabService(tmp_path)
    paper = svc.ingest_bytes("paper.pdf", pdf_bytes())
    def fail(*args, **kwargs):
        raise ValueError("Fill in remote model names")
    monkeypatch.setattr(svc, "ask", fail)
    job = svc.start_question("A saved failed request", [paper["paper_id"]])
    svc.tasks.join()
    assert svc.job(job["job_id"])["status"] == "failed"
    history = LabService(tmp_path).history()
    assert history[0]["job_id"] == job["job_id"]
    assert history[0]["question"] == "A saved failed request"
    assert history[0]["decision"] == "runtime_error" and not history[0]["native_answer"]


def test_running_job_visible_to_another_service_and_records_transitions(tmp_path, monkeypatch):
    import threading
    gate = threading.Event()
    entered = threading.Event()
    svc = LabService(tmp_path)
    paper = svc.ingest_bytes("paper.pdf", pdf_bytes())
    def waiting(*args):
        entered.set()
        gate.wait(5)
        raise ValueError("Controlled failure")
    monkeypatch.setattr(svc, "ask", waiting)
    job = svc.start_question("Retain running request", [paper["paper_id"]])
    try:
        assert entered.wait(3)
        reader = LabService(tmp_path)
        assert reader.job(job["job_id"])["status"] == "running"
        records = list((tmp_path / "job-events" / job["job_id"]).glob("*.json"))
        before = {p: p.read_bytes() for p in records}
        assert {json.loads(v)["status"] for v in before.values()} == {"queued", "running"}
    finally:
        gate.set()
        svc.tasks.join()
    assert reader.job(job["job_id"])["status"] == "failed"
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("owner,current", [({}, None), ({"pid": 123, "identity": "old-birth"}, None),
                                         ({"pid": 123, "identity": "old-birth"}, "new-birth")])
def test_interrupted_job_is_failure_without_rewriting_raw_or_retrying(tmp_path, monkeypatch, owner, current):
    from paper_library import write_json
    svc = LabService(tmp_path)
    job = {"job_id": "a" * 32, "question": "Interrupted question", "status": "running", "owner": owner}
    path = tmp_path / "jobs" / (job["job_id"] + ".json")
    write_json(path, job)
    raw = path.read_bytes()
    monkeypatch.setattr("lab_service._process_identity", lambda pid: current)
    projected = svc.job(job["job_id"])
    assert projected["status"] == "failed" and projected["error_type"] == "InterruptedTask"
    assert svc.history()[0]["decision"] == "runtime_error"
    assert path.read_bytes() == raw and svc.tasks.empty() and not svc.jobs


def test_provider_change_requires_new_keys_even_after_restart(tmp_path):
    svc = LabService(tmp_path)
    svc.save_settings({"chat_key": "synthetic-chat-key", "embedding_key": "synthetic-embedding-key", "remember_keys": True})
    svc.save_settings({"api_base": "https://other-provider.example.com/v1"})
    assert svc.status()["credential_available"] == {"chat": False, "embedding": False}
    restarted = LabService(tmp_path)
    assert restarted.status()["credential_available"] == {"chat": False, "embedding": False}
    assert list(tmp_path.glob("secrets-backup-*.dpapi.json"))


def test_nonremembered_replacement_does_not_restore_old_persisted_key(tmp_path):
    svc = LabService(tmp_path)
    svc.save_settings({"chat_key": "synthetic-old", "remember_keys": True})
    svc.save_settings({"chat_key": "synthetic-session", "remember_keys": False})
    os.environ.pop(svc.envs["chat"], None)
    restarted = LabService(tmp_path)
    assert not restarted.status()["credential_available"]["chat"]


def test_fullwidth_citation_display_does_not_modify_raw_answer(tmp_path):
    svc = LabService(tmp_path)
    original = {"answer": "条件成立（pqac-abcd1234）。", "decision": "answered", "citation_audit": {"references": [
        {"paper_id": "a" * 24, "title": "Paper", "context_id": "pqac-abcd1234", "locations": [{"page": 2}],
         "source_text": "Original evidence", "source_sha256": "sha", "source_url": "https://example.com/paper"}]}}
    public = svc._public_answer(original)
    assert public["answer"] == "条件成立（引用 1）。"
    assert original["answer"] == "条件成立（pqac-abcd1234）。"
    assert public["citations"][0]["number"] == 1


def test_python_contract_import_allows_selecting_one_reviewed_tool(tmp_path):
    import hashlib
    repo = tmp_path / 'repo'
    repo.mkdir()
    source = repo / 'module.py'
    source.write_text('def first(x: float) -> float:\n    return x * 2\n\ndef second(x: float) -> float:\n    return x + 1\n', encoding='utf-8')
    (repo / 'LICENSE').write_text('MIT License\nCopyright 2026 Test authors\n', encoding='utf-8')
    contract = {'reviewed': True, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'parameters': {'x': {'schema': {'type': 'number'}, 'adapter': {'kind': 'identity'}}}}
    contracts = {"schema_version": 1, "tools": {"module:first": contract, "module:second": contract}}
    svc = LabService(tmp_path / 'workspace')
    built = svc.build_code_mcp(str(repo), ["module:second"], "chosen-tool", contracts)
    project = Path(built['project_path'])
    manifest = json.loads((project / 'manifest.json').read_text(encoding='utf-8'))
    assert len(manifest['selected_tools']) == 1 and manifest['selected_tools'][0]['id'] == 'module:second'
    assert set(json.loads((project / 'contracts.json').read_text(encoding='utf-8'))['tools']) == {'module:second'}
    assert set(contracts["tools"]) == {"module:first", "module:second"}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == contract['source_sha256']
