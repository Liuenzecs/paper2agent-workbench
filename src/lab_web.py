"""Loopback browser adapter for LabService; no model or research computation here."""
from __future__ import annotations

import base64
import binascii
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 90 * 1024 * 1024
ID = r"[A-Za-z0-9_-]{1,120}"
PRIVATE_FIELDS = {"chat_key", "embedding_key", "api_key", "apikey", "password", "secret",
                  "authorization", "access_token", "refresh_token", "browser_token",
                  "answer_reasoning", "reasoning_content", "chain_of_thought"}
SAFE_ERRORS = {
    "Configure your own remote API and model names before asking": (400, "请先配置自己的远程 API 地址、问答模型和嵌入模型。"),
    "Please wait for the current question before changing provider settings": (409, "请等待当前问答完成，再修改 API 配置。"),
    "Question queue is full; please wait": (409, "问答任务队列已满，请等待已有任务完成。"),
    "Choose existing papers; required citations must be within that selection": (400, "请选择论文库中已有的论文；要求引用的论文必须在选中范围内。"),
    "Question must contain 1..10000 characters": (400, "问题需要包含 1–10000 个字符。"),
    "No text extracted; use OCR-enabled processing for scanned PDFs": (400, "该 PDF 没有可提取的文本。扫描件请先用 OCR 转为可搜索 PDF。"),
    "Password protected PDF is unsupported": (400, "请先解除 PDF 的密码保护，再导入。"),
    "PDF must have 1..500 pages": (400, "当前支持 1–500 页的 PDF；请拆分较长文件。"),
    "Fill in the remote API base address": (400, "请填写远程 API 基础地址。"),
    "Remote API base must be an HTTPS URL without embedded credentials": (400, "远程 API 地址必须使用 HTTPS，且不能在地址中包含密钥。"),
    "Local model endpoints are disabled": (400, "已禁止本地模型地址，请填写远程服务的 HTTPS API 地址。"),
    "Fill in remote model names": (400, "请填写问答和嵌入模型名称。"),
    "Install Git to fetch a repository, or scan an existing local checkout": (400, "下载源码需要安装 Git；也可以直接扫描已经下载的本机仓库。"),
    "Repository download failed; check the public URL, branch and network": (400, "源码下载失败，请检查公开仓库链接、版本和网络。"),
    "Repository download timed out; the partial checkout is retained for inspection": (400, "源码下载超时，未完成目录已保留；请稍后重试或使用本机仓库。"),
    "Persistent keys require Windows DPAPI; use environment variables on this platform": (400, "当前系统不支持 Windows 用户加密保存，请取消勾选并使用环境变量。"),
    "Unable to access this Windows user's protected API keys": (400, "无法读取当前 Windows 用户的加密密钥，请重新配置。"),
    "Unknown question task": (404, "没有找到该问答任务。"),
    "Unknown answer": (404, "没有找到该问答结果。"),
    "Unknown paper": (404, "没有找到该论文。"),
    "源码涉及本地模型或加速框架；本试用版不执行，需人工另行审查。": (400, "所选源码涉及本地模型或加速框架，当前试用版不执行。请改选符合 CPU 环境的工具。"),
    "研究代码目录不存在。": (400, "研究代码目录不存在，请检查本机仓库路径。"),
    "未找到 LICENSE / COPYING / NOTICE；请先核实分发许可，再生成包。": (400, "仓库缺少许可文件，请先核实分发许可再生成项目。"),
    "Choose a code project generated in your personal workspace": (400, "只能测试当前个人工作区中生成的 MCP 项目。"),
    "需要明确指定已安装依赖的 Python 解释器。": (400, "请选择已经安装项目依赖的 Python 解释器。"),
    "复制源码哈希或路径不匹配。": (400, "原源码与生成时的记录不一致，请重新扫描和生成项目。"),
}


def public_data(value):
    """Defense in depth; service public methods must themselves omit credentials."""
    if isinstance(value, dict):
        return {str(k): public_data(v) for k, v in value.items()
                if str(k).lower() not in PRIVATE_FIELDS
                and not str(k).lower().endswith(("_api_key", "_password", "_secret", "_token"))}
    if isinstance(value, (list, tuple)):
        return [public_data(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


class LabHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_server(service, host="127.0.0.1", port=8765):
    """Create the loopback-only server; accepts a fake service in offline tests."""
    if host != "127.0.0.1":
        raise ValueError("Web server must bind 127.0.0.1")
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Port must be 0..65535")
    session_token = secrets.token_urlsafe(32)
    page_cache = {}
    page_lock = threading.Lock()

    def page_data(paper_id, page):
        with page_lock:
            key = (paper_id, page)
            if key not in page_cache:
                result = dict(service.page(paper_id, page))
                image = Path(result.get("source_page_image") or result.get("image_path") or "").resolve(strict=True)
                image_root = (service.library.root / "page_views").resolve(strict=True)
                if not image.is_relative_to(image_root) or image.suffix.lower() != ".png" or not image.is_file():
                    raise ValueError("Invalid bound page image")
                page_cache[key] = (result, image)
            return page_cache[key]

    class Handler(BaseHTTPRequestHandler):
        server_version = "Paper2AgentLab/1"

        def log_message(self, format, *args):
            # Body, question, API key and query strings never enter access logs.
            pass

        def _safe_request(self, mutation=False):
            actual_port = self.server.server_address[1]
            allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
            if self.headers.get("Host", "") not in allowed_hosts:
                self._error(403, "invalid_host", "请使用本机启动地址访问。")
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                self._error(403, "invalid_origin", "只允许本机网页请求。")
                return False
            if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
                self._error(403, "cross_site", "请从本机网页发起操作。")
                return False
            if mutation and not hmac.compare_digest(self.headers.get("X-Lab-Token", ""), session_token):
                self._error(403, "invalid_token", "页面会话已失效，请刷新后重试。")
                return False
            return True

        def _headers(self, status, mime, length, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _json(self, status, data, include_token=False):
            body = public_data(data)
            if include_token:
                if not isinstance(body, dict):
                    body = {"status": body}
                body["browser_token"] = session_token
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(encoded))
            self.wfile.write(encoded)

        def _error(self, status, code, message):
            self._json(status, {"error": code, "message": message})

        def _file(self, path, mime, download=None):
            path = Path(path)
            if not path.is_file():
                raise FileNotFoundError()
            headers = {"Content-Disposition": f'attachment; filename="{download}"'} if download else None
            self._headers(200, mime, path.stat().st_size, headers)
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 128):
                    self.wfile.write(chunk)

        def _body(self):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Transfer encoding unsupported")
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise TypeError("JSON required")
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                length = -1
            if length < 0:
                raise ValueError("Content length required")
            route_limit = MAX_JSON_BYTES if urlsplit(self.path).path == "/api/upload" else 256 * 1024
            if length > route_limit:
                raise OverflowError("Request too large")
            self.connection.settimeout(30)
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise ValueError("Incomplete request")
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Object required")
            return data

        def _dispatch_error(self, error):
            # Provider errors can contain credentials: never return their raw str().
            if isinstance(error, ValueError) and str(error) in SAFE_ERRORS:
                status, message = SAFE_ERRORS[str(error)]
                self._error(status, "input_or_state_error", message)
            elif isinstance(error, (FileNotFoundError, KeyError)):
                self._error(404, "not_found", "没有找到记录或文件。")
            elif isinstance(error, OverflowError):
                self._error(413, "too_large", "PDF 不能超过 64 MB；请减少单次上传内容。")
            elif isinstance(error, TypeError):
                self._error(415, "invalid_format", "请求格式不正确，请刷新页面后重试。")
            elif isinstance(error, (ValueError, UnicodeError, binascii.Error)):
                self._error(400, "invalid_input", "输入无效。请检查 PDF、论文选择和远程 API 配置。")
            else:
                self._error(500, "service_error", "操作失败。请检查配置与本机可用空间后重试；此错误不是缺证据拒答。")

        def do_GET(self):
            if not self._safe_request():
                return
            try:
                split = urlsplit(self.path)
                path = split.path
                static = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/index.html": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8")}
                if path in static:
                    filename, mime = static[path]
                    return self._file(WEB_ROOT / filename, mime)
                if path == "/api/status":
                    return self._json(200, service.status(), include_token=True)
                if path == "/api/papers":
                    return self._json(200, service.inventory())
                if path == "/api/history":
                    return self._json(200, service.history())
                if match := re.fullmatch(rf"/api/jobs/({ID})", path):
                    return self._json(200, service.job(match[1]))
                if match := re.fullmatch(rf"/api/answers/({ID})", path):
                    return self._json(200, service.result(match[1]))
                if match := re.fullmatch(rf"/papers/({ID})\.pdf", path):
                    return self._file(service.paper_file(match[1]), "application/pdf")
                if match := re.fullmatch(rf"/api/pages/({ID})/([1-9][0-9]{{0,3}})", path):
                    result = dict(page_data(match[1], int(match[2]))[0])
                    for field in ("image_path", "source_page_image", "pdf_path"):
                        result.pop(field, None)
                    result["image_url"] = f"/api/page-images/{match[1]}/{match[2]}"
                    return self._json(200, result)
                if match := re.fullmatch(rf"/api/page-images/({ID})/([1-9][0-9]{{0,3}})", path):
                    return self._file(page_data(match[1], int(match[2]))[1], "image/png")
                if match := re.fullmatch(rf"/api/exports/({ID})", path):
                    format_name = parse_qs(split.query).get("format", ["markdown"])[0]
                    if format_name not in {"markdown", "json"}:
                        raise ValueError("Unknown export format")
                    export = service.export(match[1])
                    key, mime, ext = (("markdown_path", "text/markdown; charset=utf-8", "md")
                                      if format_name == "markdown" else ("json_path", "application/json; charset=utf-8", "json"))
                    return self._file(export[key], mime, f"paperqa-{match[1]}.{ext}")
                return self._error(404, "not_found", "页面不存在。")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                self._dispatch_error(error)

        def do_POST(self):
            if not self._safe_request(mutation=True):
                self.close_connection = True
                return
            try:
                path = urlsplit(self.path).path
                known = {"/api/settings", "/api/ask", "/api/upload", "/api/code/scan", "/api/code/build", "/api/code/test", "/api/code/fetch", "/api/examples/vendi"}
                if path not in known:
                    return self._error(404, "not_found", "操作不存在。")
                data = self._body()
                if path == "/api/examples/vendi":
                    if set(data) != {"python_executable"} or not isinstance(data["python_executable"], str) or not data["python_executable"].strip():
                        raise ValueError("Provide the installed Vendi Python executable")
                    return self._json(200, service.run_vendi_example(data["python_executable"]))
                if path == "/api/settings":
                    allowed = {"api_base", "chat_model", "embedding_model", "embedding_api_base", "chat_key", "embedding_key", "remember_keys"}
                    if (set(data) - allowed or any(not isinstance(v, str) for k, v in data.items() if k != "remember_keys")
                            or ("remember_keys" in data and not isinstance(data["remember_keys"], bool))):
                        raise ValueError("Unknown settings fields")
                    return self._json(200, service.save_settings(data))
                if path == "/api/ask":
                    question = data.get("question", "")
                    if not isinstance(question, str) or not question.strip() or len(question) > 10000:
                        raise ValueError("Invalid question")
                    for field in ("paper_ids", "required_paper_ids"):
                        ids = data.get(field)
                        if ids is not None and (not isinstance(ids, list) or len(ids) > 100
                                                or any(not isinstance(i, str) or not re.fullmatch(ID, i) for i in ids)):
                            raise ValueError("Invalid paper selection")
                    language = data.get("answer_language", "auto")
                    if language not in {"auto", "zh", "en"}:
                        raise ValueError("Invalid language")
                    return self._json(202, service.start_question(question.strip(), paper_ids=data.get("paper_ids"),
                                      required_paper_ids=data.get("required_paper_ids"), answer_language=language))
                if path == "/api/upload":
                    filename = data.get("filename", "")
                    if not isinstance(filename, str) or len(filename) > 240 or not filename.lower().endswith(".pdf"):
                        raise ValueError("PDF filename required")
                    if any(c in filename for c in ("/", "\\", "\x00", ":")) or filename in {".", ".."}:
                        raise ValueError("Invalid filename")
                    payload = data.get("content_base64", "")
                    if not isinstance(payload, str):
                        raise ValueError("Base64 required")
                    if len(payload) > ((MAX_PDF_BYTES + 2) // 3) * 4:
                        raise OverflowError()
                    raw = base64.b64decode(payload, validate=True)
                    if len(raw) > MAX_PDF_BYTES:
                        raise OverflowError()
                    if not raw.startswith(b"%PDF-"):
                        raise ValueError("Real PDF required")
                    title, source_url = data.get("title", ""), data.get("source_url", "")
                    if not isinstance(title, str) or not isinstance(source_url, str) or len(title) > 1000 or len(source_url) > 3000:
                        raise ValueError("Invalid PDF metadata")
                    return self._json(201, service.ingest_bytes(filename, raw, title=title, source_url=source_url))
                if path == "/api/code/fetch":
                    repository_url, ref = data.get("repository_url", ""), data.get("ref", "")
                    if (not isinstance(repository_url, str) or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", repository_url)
                            or not isinstance(ref, str) or len(ref) > 200 or any(c in ref for c in ("\x00", "\n", "\r"))):
                        raise ValueError("Public GitHub HTTPS repository URL required")
                    return self._json(201, service.fetch_code(repository_url, ref=ref))
                if path == "/api/code/test":
                    project_path = data.get("project_path", "")
                    tool_name = data.get("tool_name", "")
                    arguments = data.get("arguments", {})
                    executable = data.get("python_executable", "")
                    if (not isinstance(project_path, str) or not project_path.strip() or len(project_path) > 4096
                            or not isinstance(tool_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", tool_name)
                            or not isinstance(arguments, dict) or not isinstance(executable, str) or len(executable) > 4096):
                        raise ValueError("Invalid test selection")
                    return self._json(200, service.test_code_mcp(project_path, tool_name, arguments, python_executable=executable))
                repository_path = data.get("repository_path", "")
                if not isinstance(repository_path, str) or not repository_path.strip() or len(repository_path) > 4096:
                    raise ValueError("Repository path required")
                if path == "/api/code/scan":
                    return self._json(200, service.scan_code(repository_path))
                selections, name = data.get("selections", []), data.get("name", "")
                if not isinstance(selections, list) or not selections or len(selections) > 100 or any(not isinstance(s, str) for s in selections):
                    raise ValueError("Select existing functions")
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
                    raise ValueError("Invalid MCP name")
                return self._json(201, service.build_code_mcp(repository_path, selections=selections, name=name))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                self.close_connection = True
                self._dispatch_error(error)

        def do_OPTIONS(self):
            self._error(405, "method_not_allowed", "只允许本机同源请求。")

    return LabHTTPServer((host, port), Handler)


def serve(data_dir, port=8765):
    from lab_service import LabService
    server = build_server(LabService(data_dir), port=port)
    print(f"Paper2Agent 网页：http://127.0.0.1:{server.server_address[1]}")
    print("此网页只在本机开放；按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
