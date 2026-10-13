"""Generate reviewed, hash-bound native command entry adapters without execution."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import keyword
import re
import shutil
import uuid
from pathlib import Path

from code_contracts import ContractError, check_rules, check_schema, validate_value
from command_runtime import _command_path
from code_workbench import MODEL_PACKAGES, WorkbenchError, _secret_check, _git_identity

SOURCE_SUFFIXES = {'.py', '.r', '.sh', '.c', '.cpp', '.h', '.hpp', '.rs', '.jl', '.m', '.toml', '.json', '.yaml', '.yml', '.ini', '.cfg', '.txt', '.md'}
EXCLUDED_PARTS = {'.git', '.venv', 'venv', 'env', '__pycache__', 'node_modules', 'results', 'artifacts', 'papers', 'data', 'dist', 'build', 'tests', 'test', 'notebooks', '.pytest_cache'}
RESERVED = {'ROOT', 'CONTRACTS', 'invoke_command'}


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_allowed(relative):
    path = Path(relative)
    return (not any(p.lower() in EXCLUDED_PARTS for p in path.parts)
            and not any(re.search(r'(?i)(?:^\.env|credential|password|secret|api[-_]?key|private[-_]?key)', p) for p in path.parts)
            and (path.suffix.lower() in SOURCE_SUFFIXES or path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE'))))


def _review_source(path, relative):
    if not _source_allowed(relative):
        raise WorkbenchError('Only reviewed code, source configuration and licenses may be copied: ' + relative)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise WorkbenchError('Reviewed source exceeds 10 MB: ' + relative)
    text = path.read_text(encoding='utf-8-sig', errors='strict')
    _secret_check(text, relative)
    if path.suffix.lower() == '.py':
        tree = ast.parse(text)
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
            if any(name.split('.')[0] in MODEL_PACKAGES for name in names):
                raise WorkbenchError('Native entry source declares an unsupported model framework: ' + relative)
    elif path.suffix.lower() == '.r' and re.search(r'(?i)(?:library|require|requireNamespace)\s*\(\s*[\"\']?(?:torch|keras|tensorflow|reticulate)\b', text):
        raise WorkbenchError('Native R entry declares an unsupported model execution framework: ' + relative)
    return text


def scan_command_repository(repository):
    root = Path(repository).expanduser().resolve()
    if not root.is_dir():
        raise WorkbenchError('研究代码目录不存在。')
    entries = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and not path.is_symlink() and _source_allowed(relative) and path.suffix.lower() in {'.py', '.r', '.sh'}:
            _command_path(root, relative)
            entries.append({'id': relative, 'path': relative, 'source_sha256': _hash(path),
                            'eligible': False, 'review_required': True,
                            'contract_template': {'reviewed': False, 'entry_file': relative,
                                                  'source_files': {relative: _hash(path)}, 'parameters': {},
                                                  'argv': ['{entry_file}', '{request_file}', '{output_dir}']}})
            if len(entries) > 5000:
                raise WorkbenchError('请选择更小的研究代码目录。')
    return {'repository_path': str(root), 'route': 'command', 'candidates': entries,
            'execution_performed': False, 'validation_status': 'static_only_not_verified', 'contract_schema_version': 1}


def check_command_contracts(contracts):
    if not isinstance(contracts, dict) or set(contracts) != {'schema_version', 'route', 'tools'} or type(contracts['schema_version']) is not int or contracts['schema_version'] != 1 or contracts['route'] != 'command':
        raise ContractError('Native contracts require schema_version:1, route:command and tools.')
    if not isinstance(contracts['tools'], dict) or not 1 <= len(contracts['tools']) <= 32:
        raise ContractError('Native contracts require 1–32 reviewed tools.')
    checked = copy.deepcopy(contracts)
    for name, tool in checked['tools'].items():
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name in {'mcp', 'ROOT', 'CONTRACTS', 'invoke_command', 'Path', 'json', 'subprocess', 'time', 'uuid', 'hashlib', 'os', 'asyncio', 'ContractError', 'NativeExecutionError', 'validate_value', 'validate_rules', 'partial', 'Field', 'BeforeValidator', 'Any', 'Annotated', 'before_validate'} or name.startswith('_'):
            raise ContractError('Native tool name must be an identifier.')
        required = {'reviewed', 'entry_file', 'source_files', 'parameters', 'argv', 'runtime', 'result_file', 'timeout_seconds', 'description', 'reference', 'native_dependencies'}
        if not isinstance(tool, dict) or set(tool) - required - {'rules', 'result_schema'} or required - set(tool) or tool['reviewed'] is not True:
            raise ContractError('Native tool requires an explicitly reviewed, complete interface.')
        if not isinstance(tool['source_files'], dict) or not 1 <= len(tool['source_files']) <= 5000 or tool['entry_file'] not in tool['source_files']:
            raise ContractError('Native entry and all source dependencies need SHA-256 bindings.')
        for relative, sha in tool['source_files'].items():
            _command_path(Path.cwd(), relative, False)
            if not _source_allowed(relative) or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                raise ContractError('Native source requires an allowed path and exact SHA-256.')
        if not isinstance(tool['parameters'], dict) or len(tool['parameters']) > 64:
            raise ContractError('Native parameters must be a bounded object.')
        for param, spec in tool['parameters'].items():
            if not isinstance(param, str) or not param.isidentifier() or keyword.iskeyword(param) or param in RESERVED or re.search(r'(?i)secret|password|token|api_?key', param):
                raise ContractError('Native parameter name is reserved or unsupported.')
            if not isinstance(spec, dict) or set(spec) - {'schema', 'description', 'default'} or not {'schema', 'description'} <= set(spec) or not isinstance(spec['description'], str) or not spec['description'].strip():
                raise ContractError('Native parameters require a schema and description.')
            check_schema(spec['schema'])
            if 'default' in spec:
                validate_value(spec['default'], spec['schema'], param)
        argv = tool['argv']
        if not isinstance(argv, list) or not 3 <= len(argv) <= 64 or any(not isinstance(x, str) or not x or len(x) > 1024 or '\x00' in x for x in argv):
            raise ContractError('Native argv must be a bounded literal list.')
        for placeholder in ['{entry_file}', '{request_file}', '{output_dir}']:
            if argv.count(placeholder) != 1:
                raise ContractError('Native argv needs each entry/request/output placeholder exactly once.')
        if any(('{' in x or '}' in x) and x not in {'{entry_file}', '{request_file}', '{output_dir}'} for x in argv):
            raise ContractError('Native argv does not interpolate scientific values or other placeholders.')
        runtime = tool['runtime']
        if not isinstance(runtime, dict) or set(runtime) - {'kind', 'env', 'version_args', 'version_contains', 'sha256'} or not {'kind', 'env', 'version_args', 'version_contains'} <= set(runtime):
            raise ContractError('Native runtime requires kind, env and an explicit version identity check.')
        if runtime['kind'] not in {'python', 'r', 'executable'} or runtime['env'] != 'P2A_NATIVE_RUNTIME':
            raise ContractError('Native runtime kind or dedicated runtime environment is unsupported.')
        if runtime['kind'] == 'python' and argv[0] != '{entry_file}':
            raise ContractError('Python native argv begins with the reviewed entry_file.')
        if runtime['kind'] == 'executable' and 'sha256' not in runtime:
            raise ContractError('Native binary commands require a reviewed executable SHA-256.')
        if not isinstance(runtime['version_args'], list) or not 1 <= len(runtime['version_args']) <= 8 or any(not isinstance(x, str) or not x or '\x00' in x or len(x) > 256 for x in runtime['version_args']):
            raise ContractError('Native version check requires bounded literal arguments.')
        if runtime['kind'] in {'python', 'r'} and runtime['version_args'] not in ([['--version'], ['-V']] if runtime['kind'] == 'python' else [['--version']]):
            raise ContractError('Interpreter identity checks use version flags, never code expressions.')
        if not isinstance(runtime['version_contains'], str) or not runtime['version_contains'].strip() or len(runtime['version_contains']) > 256:
            raise ContractError('Native version identity must be a nonempty literal substring.')
        if runtime['kind'] == 'python' and not runtime['version_contains'].startswith('Python ') or runtime['kind'] == 'r' and not runtime['version_contains'].startswith('Rscript (R) version '):
            raise ContractError('Interpreter identity requires its native version prefix.')
        if 'sha256' in runtime and (not isinstance(runtime['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', runtime['sha256'])):
            raise ContractError('Native executable SHA-256 must be exact.')
        if type(tool['timeout_seconds']) is not int or not 1 <= tool['timeout_seconds'] <= 180:
            raise ContractError('Native timeout must be 1–180 seconds.')
        _command_path(Path.cwd(), tool['result_file'], False)
        if not tool['result_file'].endswith('.json'):
            raise ContractError('Native result_file must name a JSON output.')
        for field in ['description', 'reference']:
            if not isinstance(tool[field], str) or not tool[field].strip() or len(tool[field]) > 4000:
                raise ContractError('Native tool requires scientific description and source reference.')
        if not isinstance(tool['native_dependencies'], list) or not tool['native_dependencies'] or any(not isinstance(x, str) or not x.strip() or len(x) > 1024 for x in tool['native_dependencies']):
            raise ContractError('Recipient native dependencies must be explicitly declared.')
        check_rules(tool.get('rules', []))
        if 'result_schema' in tool:
            check_schema(tool['result_schema'])
            if tool['result_schema'].get('type') != 'object':
                raise ContractError('Native result schema must be an object.')
        tool['tool_name'] = name
    # No JSON NaN, Infinity, secrets or nonserializable contract values.
    _secret_check(json.dumps(checked, allow_nan=False), 'native contracts')
    return checked


def build_command_mcp(repository, contracts, name, output_root):
    """Bind explicitly reviewed JSON-file native entries to MCP without execution.
    A source repository and command contracts produce a portable server project.
    """
    root = Path(repository).expanduser().resolve()
    if not root.is_dir():
        raise WorkbenchError('研究代码目录不存在。')
    if not isinstance(name, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,63}', name):
        raise WorkbenchError('MCP 名称需要 1–64 位字母、数字、下划线或连字符。')
    checked = check_command_contracts(contracts)
    sources = {}
    for tool in checked['tools'].values():
        for relative, sha in tool['source_files'].items():
            if relative in sources and sources[relative] != sha:
                raise ContractError('Conflicting native source identity: ' + relative)
            path = _command_path(root, relative)
            if _hash(path) != sha:
                raise WorkbenchError('Native source differs from the reviewed hash: ' + relative)
            _review_source(path, relative)
            sources[relative] = sha
    # Licenses are code provenance, and are always included when present at root.
    for path in sorted(root.iterdir()):
        if path.is_file() and not path.is_symlink() and path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
            relative = path.name
            _review_source(path, relative)
            sources[relative] = _hash(path)
    if sum((root / relative).stat().st_size for relative in sources) > 50 * 1024 * 1024:
        raise WorkbenchError('Reviewed native sources exceed 50 MB.')
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = output / (name + '-' + uuid.uuid4().hex)
    project.mkdir()
    copied = []
    for relative, sha in sorted(sources.items()):
        destination = project / 'vendor' / 'repo' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, destination)
        copied.append({'path': relative, 'sha256': sha, 'bytes': destination.stat().st_size})
    shutil.copyfile(Path(__file__).with_name('code_contracts.py'), project / 'code_contracts.py')
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'LICENSE', project / 'LICENSE')
    (project / 'contracts.json').write_text(json.dumps(checked, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    runtime_source = Path(__file__).with_name('command_runtime.py').read_text(encoding='utf-8')
    server = runtime_source + '\nfrom typing import Annotated, Any\nfrom functools import partial\nfrom pydantic import BeforeValidator, Field\nfrom fastmcp import FastMCP\nfrom code_contracts import before_validate\n'
    server += f'ROOT = Path(__file__).resolve().parent\nmcp = FastMCP({name!r})\n'
    server += "CONTRACTS = _command_json((ROOT / 'contracts.json').read_text(encoding='utf-8'))\n\n"
    for tool_name, tool in checked['tools'].items():
        arguments, bindings = [], []
        for param, spec in tool['parameters'].items():
            expression = f"{param}: Annotated[Any, BeforeValidator(partial(before_validate, schema={spec['schema']!r})), Field(description={spec['description']!r}, json_schema_extra={spec['schema']!r})]"
            if 'default' in spec:
                expression += '=' + repr(spec['default'])
            arguments.append(expression)
            bindings.append(repr(param) + ': ' + param)
        doc = ' '.join(tool['description'].split()) + '\nReviewed JSON inputs → original native outputs and retained invocation artifacts.'
        server += f"@mcp.tool()\nasync def {tool_name}(*{', ' if arguments else ''}{', '.join(arguments)}) -> dict:\n    {doc!r}\n    return await invoke_command(ROOT, CONTRACTS['tools'][{tool_name!r}], {{{', '.join(bindings)}}})\n\n" if arguments else f"@mcp.tool()\nasync def {tool_name}() -> dict:\n    {doc!r}\n    return await invoke_command(ROOT, CONTRACTS['tools'][{tool_name!r}], {{}})\n\n"
    server += "if __name__ == '__main__':\n    mcp.run()\n"
    (project / 'server.py').write_text(server, encoding='utf-8')
    dependencies = ['fastmcp==4.0.3']
    if any(rule['op'] == 'finite_moments' for tool in checked['tools'].values() for rule in tool.get('rules', [])):
        dependencies.append('numpy==2.4.6')
    (project / 'requirements.txt').write_text('\n'.join(dependencies) + '\n', encoding='utf-8')
    (project / 'call_tool.py').write_text("import asyncio, json, os, sys\nfrom pathlib import Path\nfrom fastmcp import Client\nfrom fastmcp.client.transports import PythonStdioTransport\nasync def main():\n    arguments = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n    native_env = {key: os.environ[key] for key in ('P2A_NATIVE_RUNTIME', 'P2A_R_PROJECT', 'LC_ALL') if key in os.environ}\n    transport = PythonStdioTransport(str(Path(__file__).with_name('server.py')), python_cmd=sys.executable, cwd=str(Path(__file__).parent), env=native_env)\n    async with Client(transport) as client:\n        result = await client.call_tool(sys.argv[1], arguments)\n        print(json.dumps(result.data, ensure_ascii=False, indent=2, allow_nan=False))\nif __name__ == '__main__':\n    asyncio.run(main())\n", encoding='utf-8')
    usage = '# ' + name + '\n\nInstall Python 3.11 and this server runtime:\n\n```powershell\npython -m venv .venv\n.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n```\n\nConfigure the reviewed native runtime and install its scientific dependencies separately. `manifest.json` records their requirements and source bindings. The server does not install or alter native runtimes.\n\n```powershell\n$env:P2A_NATIVE_RUNTIME = "C:/absolute/path/to/native/executable"\n.venv\\Scripts\\python.exe server.py\n```\n\nEach invocation writes a new directory under `artifacts`, retaining request, native output, diagnostics and execution status. Native failures are MCP errors. Scientific nonfinite values require an explicit JSON representation from the reviewed entry and preserved native artifacts. Repository code executes with the caller\'s permissions; source checks do not provide a security sandbox.\n'
    for tool in checked['tools'].values():
        usage += '\n' + tool['tool_name'] + ': ' + '; '.join(tool['native_dependencies']) + '.\n'
    (project / 'USAGE.md').write_text(usage, encoding='utf-8')
    manifest = {'schema_version': 1, 'name': name, 'route': 'command', 'repository_identity': _git_identity(root),
                'repository_path_at_build': str(root), 'selected_tools': list(checked['tools'].values()),
                'copied_source_files': copied, 'license_files': [x['path'] for x in copied if Path(x['path']).name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE'))],
                'server_sha256': _hash(project / 'server.py'), 'runtime_adapter_sha256': _hash(project / 'code_contracts.py'),
                'adapter_license': 'AGPL-3.0-only', 'license_sha256': _hash(project / 'LICENSE'),
                'contracts_sha256': _hash(project / 'contracts.json'), 'invocation_script': 'call_tool.py',
                'invocation_script_sha256': _hash(project / 'call_tool.py'), 'execution_performed': False,
                'verification_status': 'generated_not_executed_or_independently_verified', 'algorithm_changes': False,
                'local_model_execution_allowed': False, 'native_dependencies': {name: tool['native_dependencies'] for name, tool in checked['tools'].items()},
                'declared_dependencies': dependencies, 'source_data_files_included': False,
                'limits': ['Requires an explicitly reviewed JSON-file entry; ordinary arbitrary CLI arguments are not automatically converted.',
                           'Recipient must install the recorded native scientific dependencies and set the explicit executable path.',
                           'Version checks and source hashes establish reviewed identity, not scientific correctness or a security sandbox.']}
    (project / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return {'project_path': str(project), 'manifest_path': str(project / 'manifest.json'), 'server_path': str(project / 'server.py'),
            'requirements_path': str(project / 'requirements.txt'), 'selected_tools': manifest['selected_tools'],
            'source_manifest': copied, 'warnings': ['Native scientific dependencies must be installed in the recipient runtime.'],
            'verification_status': manifest['verification_status']}
