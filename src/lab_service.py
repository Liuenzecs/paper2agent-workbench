"""Paper2Agent workspace shared by the CLI and browser service."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import queue
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from paper_library import PaperLibrary, write_json
from remote_config import prepare_remote_settings, read_credential, is_remote_configured

APP_ROOT = Path(__file__).resolve().parents[1]


def default_data_dir():
    override = os.environ.get("PAPER2AGENT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "Paper2AgentLab"


def _protect(data: bytes, decrypt=False) -> bytes:
    """Windows user-bound DPAPI; never stores a plaintext API key."""
    if os.name != "nt":
        raise ValueError("Persistent keys require Windows DPAPI; use environment variables on this platform")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.restype = wintypes.BOOL
    if decrypt:
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "Paper2Agent", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ValueError("Unable to access this Windows user's protected API keys")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)


class LabService:
    def __init__(self, data_dir=None):
        self.root = Path(data_dir or default_data_dir()).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.library = PaperLibrary(self.root / "library")
        self.settings_path = self.root / "settings.json"
        self.lock = threading.RLock()
        self.jobs = {}
        self.tasks = queue.Queue(maxsize=8)
        identity = hashlib.sha256(str(self.root).encode()).hexdigest()[:12].upper()
        self.envs = {role: f"PAPERQA_LAB_{identity}_{role.upper()}_API_KEY" for role in ("chat", "embedding")}
        if not self.settings_path.exists():
            raw = json.loads((APP_ROOT / "configs/remote-default.json").read_text(encoding="utf-8"))
            raw["credential_envs"] = self.envs
            write_json(self.settings_path, raw)
        else:
            raw = self._settings()
            self.envs = raw["credential_envs"]
        secrets_path = self.root / "secrets.dpapi.json"
        if secrets_path.exists():
            values = json.loads(secrets_path.read_text(encoding="utf-8"))
            for role in self.envs:
                if values.get(role):
                    os.environ[self.envs[role]] = _protect(base64.b64decode(values[role]), decrypt=True).decode()
        else:
            # An existing user-configured provider can be reused without copying its key to disk.
            defaults = json.loads((APP_ROOT / "configs/remote-default.json").read_text(encoding="utf-8"))
            current_bases = self._provider_bases(raw)
            default_bases = self._provider_bases(defaults)
            for role, name in {"chat": "PAPERQA_CHAT_API_KEY", "embedding": "PAPERQA_EMBEDDING_API_KEY"}.items():
                if is_remote_configured(raw) and current_bases[role] == default_bases[role] and not os.environ.get(self.envs[role]) and (value := read_credential(name)):
                    os.environ[self.envs[role]] = value
        self.worker = threading.Thread(target=self._work, name="paperqa-remote-queue", daemon=True)
        self.worker.start()

    def _settings(self):
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    @staticmethod
    def _provider_bases(raw):
        return {"chat": raw["llm_config"]["model_list"][0]["litellm_params"]["api_base"],
                "embedding": raw["embedding_config"]["kwargs"]["api_base"]}

    def _busy(self):
        return any(j["status"] in {"queued", "running"} for j in self.jobs.values())

    def status(self):
        raw = self._settings()
        _, available = prepare_remote_settings(raw, require_credentials=False)
        models = raw["llm_config"]["model_list"][0]["litellm_params"]
        return {"data_dir": str(self.root), "remote_only": True, "credential_available": available,
                "busy": self._busy(), "api_base": models["api_base"], "chat_model": models["model"].removeprefix("openai/"),
                "embedding_model": raw["embedding"].removeprefix("openai/"),
                "embedding_api_base": raw["embedding_config"]["kwargs"]["api_base"],
                "provider_configured": is_remote_configured(raw),
                "ready": is_remote_configured(raw) and all(available.values()),
                "knowledge_status": "unreviewed", "version": "1.0.0"}

    def save_settings(self, data):
        with self.lock:
            if self._busy():
                raise ValueError("Please wait for the current question before changing provider settings")
            raw = self._settings()
            prior_bases = self._provider_bases(raw)
            base = str(data.get("api_base", self.status()["api_base"])).strip()
            chat = str(data.get("chat_model", self.status()["chat_model"])).strip().removeprefix("openai/")
            embedding = str(data.get("embedding_model", self.status()["embedding_model"])).strip().removeprefix("openai/")
            for name in ("llm_config", "summary_llm_config"):
                params = raw[name]["model_list"][0]["litellm_params"]
                params.update(api_base=base, model="openai/" + chat)
            raw["embedding"] = "openai/" + embedding
            raw["embedding_config"]["kwargs"]["api_base"] = str(data.get("embedding_api_base") or
                (base if "api_base" in data else raw["embedding_config"]["kwargs"]["api_base"])).strip()
            prepare_remote_settings(raw, require_credentials=False)
            changed_roles = {role for role, address in self._provider_bases(raw).items() if address != prior_bases[role]}
            values = {}
            for role in self.envs:
                value = data.get(role + "_key")
                if value is not None and str(value).strip():
                    values[role] = str(value).strip()
            secrets_path = self.root / "secrets.dpapi.json"
            stored = json.loads(secrets_path.read_text(encoding="utf-8")) if secrets_path.exists() else {}
            for role in changed_roles | (set(values) if not data.get("remember_keys", False) else set()):
                stored.pop(role, None)
            if values and data.get("remember_keys", False):
                for role in self.envs:
                    value = values.get(role) or (read_credential(self.envs[role]) if role not in changed_roles else "")
                    if value:
                        stored[role] = base64.b64encode(_protect(value.encode())).decode()
            if (secrets_path.exists() and (changed_roles or values)) or (values and data.get("remember_keys", False)):
                if secrets_path.exists():
                    (self.root / ("secrets-backup-" + uuid.uuid4().hex + ".dpapi.json")).write_bytes(secrets_path.read_bytes())
                write_json(secrets_path, stored)
            for role in changed_roles:
                os.environ.pop(self.envs[role], None)
            for role, value in values.items():
                os.environ[self.envs[role]] = value
            write_json(self.settings_path, raw)
            return self.status()

    def inventory(self):
        return self.library.inventory("pymupdf")

    def ingest_bytes(self, filename, content, title="", source_url=""):
        if not isinstance(content, bytes) or not 0 < len(content) <= 64 * 1024 * 1024:
            raise ValueError("PDF must be between 1 byte and 64 MB")
        if content[:5] != b"%PDF-":
            raise ValueError("Please choose a real PDF file")
        incoming = self.root / "incoming"
        incoming.mkdir(exist_ok=True)
        path = incoming / (uuid.uuid4().hex + ".pdf")
        path.write_bytes(content)
        try:
            return self.library.ingest(str(path), title or Path(filename).stem, source_url, "pymupdf")
        finally:
            path.unlink(missing_ok=True)

    def ingest_path(self, pdf, title="", source_url=""):
        return self.library.ingest(str(Path(pdf).expanduser().resolve()), title, source_url, "pymupdf")

    def ask(self, question, paper_ids=None, required_paper_ids=None, answer_language="auto", progress=None):
        import asyncio
        from paperqa_bridge import ask_paperqa
        if not isinstance(question, str) or not question.strip() or len(question) > 10000:
            raise ValueError("Question must contain 1..10000 characters")
        return asyncio.run(ask_paperqa(self.library, question, str(self.settings_path), paper_ids=paper_ids,
                           required_paper_ids=required_paper_ids, answer_language=answer_language, progress=progress))

    def start_question(self, question, paper_ids=None, required_paper_ids=None, answer_language="auto"):
        if not isinstance(question, str) or not question.strip() or len(question) > 10000:
            raise ValueError("Question must contain 1..10000 characters")
        # Validate selection before charging a remote API call.
        known = {p["paper_id"] for p in self.inventory()}
        chosen = known if paper_ids is None else set(paper_ids)
        if not chosen or not chosen <= known or not set(required_paper_ids or []) <= chosen:
            raise ValueError("Choose existing papers; required citations must be within that selection")
        job_id = uuid.uuid4().hex
        job = {"job_id": job_id, "question": question, "status": "queued", "stage": "等待远程问答", "created_at": datetime.now(timezone.utc).isoformat()}
        with self.lock:
            if self.tasks.full():
                raise ValueError("Question queue is full; please wait")
            self.jobs[job_id] = job
            self.tasks.put_nowait((job_id, question, paper_ids, required_paper_ids, answer_language))
        return dict(job)

    def _work(self):
        while True:
            job_id, question, selected, required, language = self.tasks.get()
            def progress(stage):
                with self.lock:
                    self.jobs[job_id]["stage"] = str(stage)
            with self.lock:
                self.jobs[job_id]["status"] = "running"
            try:
                answer = self.ask(question, selected, required, language, progress)
                public = self._public_answer(answer)
                with self.lock:
                    self.jobs[job_id].update(status="completed", stage="完成", result=public)
            except Exception as exc:
                # Never relay a provider traceback that could contain credentials.
                message = str(exc) if isinstance(exc, ValueError) else "远程服务请求失败，请检查连接、模型名称和密钥。"
                with self.lock:
                    self.jobs[job_id].update(status="failed", stage="服务或配置错误", error=message, error_type=type(exc).__name__)
            finally:
                write_json(self.root / "jobs" / (job_id + ".json"), self.job(job_id))
                self.tasks.task_done()

    def job(self, job_id):
        self._id(job_id)
        with self.lock:
            if job_id not in self.jobs:
                path = self.root / "jobs" / (job_id + ".json")
                if not path.is_file():
                    raise ValueError("Unknown question task")
                return json.loads(path.read_text(encoding="utf-8"))
            return json.loads(json.dumps(self.jobs[job_id], ensure_ascii=False))

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{24}|[a-f0-9]{32}", value):
            raise ValueError("Invalid artifact identifier")

    def _public_answer(self, data):
        result_path = Path(data.get("result_path", ""))
        answer_id = result_path.parent.name if result_path.name == "result.json" else data.get("answer_id")
        refs = data.get("citation_audit", {}).get("references", [])
        citations = [{"paper_id": r["paper_id"], "title": r["title"], "context_id": r["context_id"],
                      "pages": sorted({loc["page"] for loc in r["locations"]}),
                      "page": r["locations"][0]["page"], "text": r["source_text"],
                      "locations": r["locations"], "source_sha256": r["source_sha256"], "source_url": r["source_url"]} for r in refs]
        answer = data.get("answer")
        for index, citation in enumerate(citations, 1):
            citation["number"] = index
            if isinstance(answer, str):
                answer = answer.replace(citation["context_id"], f"引用 {index}")
        return {"answer_id": answer_id, "question": data.get("question"), "answer": answer,
                "decision": data["decision"], "citations": citations, "seconds": data.get("seconds"),
                "coverage": data.get("source_coverage", data.get("coverage", {})), "cache": data.get("cache", {}),
                "citation_identity_checked": data.get("citation_audit", {}).get("citation_identity_pass", False),
                "claim_support_status": "requires_source_review", "local_models_used": False}

    def result(self, answer_id):
        self._id(answer_id)
        path = self.library.root / "paperqa-answers" / answer_id / "result.json"
        if not path.is_file():
            raise ValueError("Unknown answer")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["result_path"] = str(path)
        return self._public_answer(data)

    def history(self):
        paths = sorted((self.library.root / "paperqa-answers").glob("*/result.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        entries = [(p.stat().st_mtime, self.result(p.parent.name)) for p in paths[:100]]
        for path in (self.root / "jobs").glob("*.json"):
            job = json.loads(path.read_text(encoding="utf-8"))
            if job.get("status") == "failed":
                entries.append((path.stat().st_mtime, {"job_id": job["job_id"],
                    "question": job.get("question", ""), "decision": "runtime_error",
                    "answer": "", "citations": [], "error": job.get("error", "服务或配置错误"),
                    "native_answer": False, "local_models_used": False}))
        return [entry for _, entry in sorted(entries, key=lambda item: item[0], reverse=True)[:100]]

    def paper_file(self, paper_id):
        self._id(paper_id)
        for path, bundle in self.library.bundles("pymupdf"):
            if bundle["paper_id"] == paper_id:
                return path.parent / "source.pdf"
        raise ValueError("Unknown paper")

    def page(self, paper_id, page):
        self._id(paper_id)
        return self.library.read_page(paper_id, int(page), "pymupdf", render=True)

    def export(self, answer_id):
        public = self.result(answer_id)
        directory = self.root / "exports" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        write_json(directory / "answer.json", public)
        text = f'# {public["question"]}\n\n{public["answer"]}\n\n状态：{public["decision"]}\n\n'
        for r in public["citations"]:
            text += f'## {r["title"]} · PDF 页 {", ".join(map(str,r["pages"]))}\n\n{r["text"]}\n\n'
        text += '引用已定位到原文；论断是否成立仍需核对原论文。\n'
        (directory / "answer.md").write_text(text, encoding="utf-8")
        return {"markdown_path": str(directory / "answer.md"), "json_path": str(directory / "answer.json")}

    def scan_code(self, repository_path):
        from code_workbench import scan_repository
        return scan_repository(repository_path)

    def fetch_code(self, repository_url, ref=""):
        from repository_fetch import fetch_repository
        return fetch_repository(repository_url, self.root / "repositories", ref)

    def build_code_mcp(self, repository_path, selections, name):
        from code_workbench import build_mcp
        return build_mcp(repository_path, selections, name, self.root / "code-projects")

    def test_code_mcp(self, project_path, tool_name, arguments, python_executable=""):
        import sys
        from code_workbench import test_mcp
        project = Path(project_path).expanduser().resolve(strict=True)
        allowed = (self.root / "code-projects").resolve()
        if not project.is_relative_to(allowed):
            raise ValueError("Choose a code project generated in your personal workspace")
        return test_mcp(str(project), python_executable or sys.executable, tool_name, arguments)
