"""Offline HTTP adapter contracts; fake service means no model/API execution."""
import base64
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab_web import build_server


class FakeService:
    def __init__(self, root):
        self.root = Path(root)
        self.library = SimpleNamespace(root=self.root / "library")
        (self.library.root / "page_views").mkdir(parents=True)
        self.calls = []
        (self.root / "paper.pdf").write_bytes(b"%PDF-1.4 test")
        (self.library.root / "page_views" / "page.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (self.root / "answer.md").write_text("# Test", encoding="utf-8")
        (self.root / "answer.json").write_text('{"answer":"test"}', encoding="utf-8")

    def status(self):
        return {"ready": True, "settings": {"api_base": "https://example.test/v1", "chat_key": "HIDDEN-KEY", "chat_key_available": True}}

    def run_vendi_example(self, python_executable):
        self.calls.append(("vendi", python_executable))
        return {"success": True, "cases": [], "report_path": "personal/demo-result.json"}

    def run_mannkendall_example(self, python_executable):
        self.calls.append(("mannkendall", python_executable))
        return {"success": True, "cases": [], "report_path": "personal/demo-result.json"}

    def inventory(self):
        return {"papers": [{"paper_id": "paper1", "title": "Paper", "page_count": 1}]}

    def history(self):
        return {"answers": [{"answer_id": "answer1", "question": "Test", "decision": "answered"}]}

    def save_settings(self, data):
        self.calls.append(("settings", data))
        return {"saved": True, "chat_key": data.get("chat_key")}

    def start_question(self, question, **kwargs):
        self.calls.append(("ask", question, kwargs))
        return {"job_id": "job1"}

    def job(self, job_id):
        if job_id != "job1":
            raise KeyError(job_id)
        return {"status": "completed", "answer_id": "answer1"}

    def result(self, answer_id):
        if answer_id == "error":
            raise RuntimeError("provider exposed HIDDEN-KEY")
        if answer_id != "answer1":
            raise FileNotFoundError()
        return {"answer_id": answer_id, "decision": "answered", "answer": "safe", "native_session": {"answer_reasoning": "PRIVATE-COT"}}

    def ingest_bytes(self, filename, content, **kwargs):
        self.calls.append(("upload", filename, content, kwargs))
        return {"paper_id": "paper2"}

    def paper_file(self, paper_id):
        if paper_id != "paper1":
            raise KeyError(paper_id)
        return self.root / "paper.pdf"

    def page(self, paper_id, page):
        if paper_id != "paper1" or page != 1:
            raise FileNotFoundError()
        self.calls.append(("page", paper_id, page))
        return {"page": page, "source_page_image": str(self.library.root / "page_views" / "page.png"), "source_sha256": "hash"}

    def export(self, answer_id):
        if answer_id != "answer1":
            raise KeyError(answer_id)
        return {"markdown_path": str(self.root / "answer.md"), "json_path": str(self.root / "answer.json")}

    def scan_code(self, repository_path):
        self.calls.append(("scan", repository_path))
        return {"functions": [{"module": "utils", "function": "scale"}]}

    def fetch_code(self, repository_url, ref=""):
        self.calls.append(("fetch", repository_url, ref))
        return {"repository_path": "downloaded", "commit": "abc", "source_only": True}

    def build_code_mcp(self, repository_path, **kwargs):
        self.calls.append(("build", repository_path, kwargs))
        return {"output_dir": "test-project"}

    def test_code_mcp(self, project_path, tool_name, arguments, python_executable=""):
        self.calls.append(("code_test", project_path, tool_name, arguments, python_executable))
        return {"decision": "completed", "result": {"value": 4}}


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.service = FakeService(cls.temp.name)
        cls.server = build_server(cls.service, port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        _, payload, _ = cls.request("GET", "/api/status")
        cls.token = json.loads(payload)["browser_token"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)
        cls.temp.cleanup()

    @classmethod
    def request(cls, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        base = {"Content-Type": "application/json"}
        if hasattr(cls, "token"):
            base["X-Lab-Token"] = cls.token
        base.update(headers or {})
        conn.request(method, path, body=data, headers=base)
        response = conn.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return result

    def test_bind_is_loopback_only(self):
        with self.assertRaises(ValueError):
            build_server(self.service, host="0.0.0.0")

    def test_explicit_vendi_example_route(self):
        code, raw, _ = self.request("POST", "/api/examples/vendi", {"python_executable": "python.exe"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(raw)["success"])
        self.assertEqual(self.service.calls[-1], ("vendi", "python.exe"))
        for data in [{}, {"python_executable": ""}, {"python_executable": 1}, {"python_executable": "python.exe", "unexpected": True}]:
            self.assertEqual(self.request("POST", "/api/examples/vendi", data)[0], 400)

    def test_explicit_mannkendall_example_route(self):
        code, raw, _ = self.request("POST", "/api/examples/mannkendall", {"python_executable": "python.exe"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(raw)["success"])
        self.assertEqual(self.service.calls[-1], ("mannkendall", "python.exe"))
        for data in [{}, {"python_executable": ""}, {"python_executable": 1}, {"python_executable": "python.exe", "unexpected": True}]:
            self.assertEqual(self.request("POST", "/api/examples/mannkendall", data)[0], 400)

    def test_status_omits_key_and_provides_browser_token(self):
        code, raw, _ = self.request("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertNotIn(b"HIDDEN-KEY", raw)
        self.assertTrue(json.loads(raw)["browser_token"])

    def test_dns_rebinding_host_rejected(self):
        code, _, _ = self.request("GET", "/api/status", headers={"Host": "attacker.example"})
        self.assertEqual(code, 403)

    def test_cross_origin_read_and_mutation_rejected(self):
        for method, path, body in [("GET", "/api/status", None), ("POST", "/api/settings", {"chat_model": "x"})]:
            with self.subTest(method=method):
                code, _, _ = self.request(method, path, body, {"Origin": "https://attacker.example"})
                self.assertEqual(code, 403)

    def test_wrong_token_rejected_before_service_call(self):
        before = len(self.service.calls)
        code, _, _ = self.request("POST", "/api/settings", {}, {"X-Lab-Token": "wrong"})
        self.assertEqual(code, 403)
        self.assertEqual(len(self.service.calls), before)

    def test_same_origin_request_and_content_security(self):
        code, _, headers = self.request("POST", "/api/settings", {"chat_model": "remote"}, {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(code, 200)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_settings_echo_filters_secret(self):
        code, raw, _ = self.request("POST", "/api/settings", {"chat_key": "PRIVATE-API"})
        self.assertEqual(code, 200)
        self.assertNotIn(b"PRIVATE-API", raw)
        self.assertEqual(self.request("POST", "/api/settings", {"remember_keys": True})[0], 200)
        self.assertEqual(self.request("POST", "/api/settings", {"remember_keys": "true"})[0], 400)

    def test_ask_preserves_paper_and_language_selection(self):
        code, raw, _ = self.request("POST", "/api/ask", {"question": " Q ", "paper_ids": ["paper1"], "required_paper_ids": ["paper1"], "answer_language": "zh"})
        self.assertEqual(code, 202)
        self.assertEqual(json.loads(raw)["job_id"], "job1")
        _, question, kwargs = self.service.calls[-1]
        self.assertEqual(question, "Q")
        self.assertEqual(kwargs, {"paper_ids": ["paper1"], "required_paper_ids": ["paper1"], "answer_language": "zh"})

    def test_invalid_selection_and_empty_question_rejected(self):
        for payload in [{"question": ""}, {"question": "q", "paper_ids": ["../../escape"]}, {"question": "q", "paper_ids": "all"}, {"question": "q", "answer_language": "xx"}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("POST", "/api/ask", payload)[0], 400)

    def test_upload_bytes_and_filename_are_bounded(self):
        content = b"%PDF-1.4 source"
        payload = {"filename": "paper.pdf", "content_base64": base64.b64encode(content).decode()}
        self.assertEqual(self.request("POST", "/api/upload", payload)[0], 201)
        self.assertEqual(self.service.calls[-1][2], content)
        for filename in ["../escape.pdf", "C:\\escape.pdf", "file.txt"]:
            self.assertEqual(self.request("POST", "/api/upload", {**payload, "filename": filename})[0], 400)

    def test_fake_pdf_and_malformed_base64_rejected(self):
        for content in ["!!!!", base64.b64encode(b"not a PDF").decode()]:
            self.assertEqual(self.request("POST", "/api/upload", {"filename": "a.pdf", "content_base64": content})[0], 400)

    def test_large_request_rejected_without_reading_body(self):
        code, _, _ = self.request("POST", "/api/ask", {"question": "q"}, {"Content-Length": str(256 * 1024 + 1)})
        self.assertEqual(code, 413)

    def test_unknown_paths_and_encoded_traversal_do_not_read_files(self):
        for path in ["/../src/lab_web.py", "/%2e%2e/src/lab_web.py", "/api/answers/../settings", "/papers/%2e%2e.pdf", "/api/pages/paper1/0"]:
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_result_omits_cot_and_provider_errors(self):
        code, raw, _ = self.request("GET", "/api/answers/answer1")
        self.assertEqual(code, 200)
        self.assertNotIn(b"PRIVATE-COT", raw)
        code, raw, _ = self.request("GET", "/api/answers/error")
        self.assertEqual(code, 500)
        self.assertNotIn(b"HIDDEN-KEY", raw)

    def test_pdf_pages_and_export_only_use_bound_records(self):
        code, raw, _ = self.request("GET", "/papers/paper1.pdf")
        self.assertEqual((code, raw[:5]), (200, b"%PDF-"))
        code, raw, _ = self.request("GET", "/api/pages/paper1/1")
        self.assertEqual(code, 200)
        self.assertNotIn(b"image_path", raw)
        self.assertEqual(json.loads(raw)["image_url"], "/api/page-images/paper1/1")
        self.assertNotIn(b"source_page_image", raw)
        self.assertEqual(self.request("GET", "/api/page-images/paper1/1")[0], 200)
        code, raw, headers = self.request("GET", "/api/exports/answer1?format=markdown")
        self.assertEqual((code, raw), (200, b"# Test"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(self.request("GET", "/api/exports/answer1?format=exe")[0], 400)

    def test_static_assets_allowlist(self):
        for path in ["/", "/app.js", "/style.css"]:
            self.assertEqual(self.request("GET", path)[0], 200)
        self.assertEqual(self.request("GET", "/src/lab_web.py")[0], 404)

    def test_code_workbench_delegates_without_executing_source(self):
        self.assertEqual(self.request("POST", "/api/code/scan", {"repository_path": "repo"})[0], 200)
        self.assertEqual(self.service.calls[-1], ("scan", "repo"))
        self.assertEqual(self.request("POST", "/api/code/build", {"repository_path": "repo", "selections": ["utils:scale"], "name": "paper-tools"})[0], 201)
        self.assertEqual(self.service.calls[-1], ("build", "repo", {"selections": ["utils:scale"], "name": "paper-tools"}))
        self.assertEqual(self.request("POST", "/api/code/build", {"repository_path": "repo", "selections": [], "name": "paper-tools"})[0], 400)

    def test_code_explicit_test_delegates_valid_json_inputs(self):
        code, raw, _ = self.request("POST", "/api/code/test", {"project_path": "generated", "tool_name": "utils__scale", "arguments": {"x": 2}, "python_executable": "python.exe"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["decision"], "completed")
        self.assertEqual(self.service.calls[-1], ("code_test", "generated", "utils__scale", {"x": 2}, "python.exe"))
        self.assertEqual(self.request("POST", "/api/code/test", {"project_path": "generated", "tool_name": "utils__scale", "arguments": []})[0], 400)

    def test_code_fetch_public_https_source_only_contract(self):
        code, raw, _ = self.request("POST", "/api/code/fetch", {"repository_url": "https://github.com/owner/repo", "ref": "v1"})
        self.assertEqual(code, 201)
        self.assertTrue(json.loads(raw)["source_only"])
        self.assertEqual(self.service.calls[-1], ("fetch", "https://github.com/owner/repo", "v1"))
        for url in ["http://github.com/owner/repo", "https://token@github.com/owner/repo", "https://github.com/owner/repo?key=secret", "https://127.0.0.1/repo"]:
            self.assertEqual(self.request("POST", "/api/code/fetch", {"repository_url": url})[0], 400)

    def test_page_image_bound_path_rejects_arbitrary_files(self):
        original = self.service.page
        self.service.page = lambda paper_id, page: {"source_page_image": str(self.service.root / "paper.pdf")}
        try:
            self.assertEqual(self.request("GET", "/api/page-images/paper1/2")[0], 400)
        finally:
            self.service.page = original


if __name__ == "__main__":
    unittest.main()
