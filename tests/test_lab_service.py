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
