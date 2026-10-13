"""Paper2Agent command-line interface."""
import argparse
import contextlib
import getpass
import json
import sys
from pathlib import Path
from lab_service import LabService, default_data_dir


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Paper2Agent：论文问答与代码工具")
    p.add_argument("--data-dir", default=str(default_data_dir()), help="Workspace directory for papers, settings and results")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status"); commands.add_parser("list"); commands.add_parser("history")
    add = commands.add_parser("ingest"); add.add_argument("pdf"); add.add_argument("--title", default=""); add.add_argument("--url", default="")
    settings = commands.add_parser("configure")
    settings.add_argument("--api-base"); settings.add_argument("--chat-model"); settings.add_argument("--embedding-model"); settings.add_argument("--embedding-api-base")
    settings.add_argument("--enter-keys", action="store_true", help="Hidden input; keys never go into command arguments")
    settings.add_argument("--remember-keys", action="store_true", help="Protect keys with Windows user DPAPI")
    ask = commands.add_parser("ask"); ask.add_argument("question"); ask.add_argument("--paper-id", action="append"); ask.add_argument("--require-paper-id", action="append"); ask.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    export = commands.add_parser("export"); export.add_argument("answer_id")
    web = commands.add_parser("web"); web.add_argument("--port", type=int, default=8765)
    scan = commands.add_parser("code-scan"); scan.add_argument("repository")
    fetch = commands.add_parser("code-fetch"); fetch.add_argument("url"); fetch.add_argument("--ref", default="")
    build = commands.add_parser("code-mcp"); build.add_argument("repository"); build.add_argument("--function", action="append", required=True); build.add_argument("--name", default="research-tools")
    build.add_argument("--contracts-file", help="Reviewed JSON interface definitions bound to source hashes")
    package = commands.add_parser("code-package"); package.add_argument("project")
    test = commands.add_parser("code-test"); test.add_argument("project"); test.add_argument("--tool", required=True)
    inputs = test.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--arguments", help="JSON object passed to the selected original function")
    inputs.add_argument("--arguments-file", help="UTF-8 JSON file; recommended for Windows shells")
    test.add_argument("--python", default="")
    test.add_argument("--native-runtime",default="",help="Installed native executable path")
    test.add_argument("--r-project",default="",help="R project with its scientific package library")
    args = p.parse_args(argv)
    svc = LabService(args.data_dir)
    if args.command == "status": result = svc.status()
    elif args.command == "list": result = svc.inventory()
    elif args.command == "history": result = svc.history()
    elif args.command == "ingest": result = svc.ingest_path(args.pdf, args.title, args.url)
    elif args.command == "configure":
        data = {key: value for key, value in vars(args).items() if key in {"api_base", "chat_model", "embedding_model", "embedding_api_base"} and value is not None}
        if args.enter_keys:
            data.update(chat_key=getpass.getpass("Chat API key (hidden): "), embedding_key=getpass.getpass("Embedding API key (hidden): "), remember_keys=args.remember_keys)
        result = svc.save_settings(data)
    elif args.command == "ask":
        # Provider SDK notices share the diagnostic stream; stdout is one JSON document.
        with contextlib.redirect_stdout(sys.stderr):
            result = svc.ask(args.question, args.paper_id, args.require_paper_id, args.language, progress=lambda stage: print(stage, file=sys.stderr, flush=True))
    elif args.command == "export": result = svc.export(args.answer_id)
    elif args.command == "code-scan": result = svc.scan_code(args.repository)
    elif args.command == "code-fetch": result = svc.fetch_code(args.url, args.ref)
    elif args.command == "code-mcp":
        contracts = json.loads(Path(args.contracts_file).read_text(encoding="utf-8-sig")) if args.contracts_file else None
        result = svc.build_code_mcp(args.repository, args.function, args.name, contracts)
    elif args.command == "code-test":
        raw_arguments = Path(args.arguments_file).read_text(encoding="utf-8-sig") if args.arguments_file else args.arguments
        result = svc.test_code_mcp(args.project, args.tool, json.loads(raw_arguments), args.python,args.native_runtime,args.r_project)
    elif args.command == "code-package":
        from code_workbench import package_mcp
        result = package_mcp(args.project)
    else:
        from lab_web import serve
        serve(args.data_dir, args.port)
        return
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if args.command == "code-test" and result.get("decision") != "completed":
        sys.exit(1)
    if args.command == "ask" and result.get("decision") in {
        "runtime_error", "blocked_evidence_review", "blocked_invalid_citations", "insufficient_source_coverage"
    }:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # The bridge hides provider detail; never print arbitrary service tracebacks.
        error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else "Operation failed; check settings and saved job details"
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": error}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
