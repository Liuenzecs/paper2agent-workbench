"""Static, reviewable Python repository to MCP conversion; never executes a repository."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tokenize
import uuid
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
IGNORED = {'.git', '.hg', '.svn', '.venv', 'venv', 'env', '__pycache__', 'node_modules',
           '.tox', '.pytest_cache', '.mypy_cache', 'dist', 'build', 'tests', 'test'}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_BYTES = 50 * 1024 * 1024
PRIMITIVES = {'int', 'float', 'str', 'bool', 'None', 'NoneType'}
MODEL_PACKAGES = {'torch', 'tensorflow', 'transformers', 'jax', 'diffusers', 'ollama',
                  'llama_cpp', 'onnxruntime', 'sentence_transformers', 'vllm', 'lmstudio', 'mlx'}


class WorkbenchError(ValueError):
    """An actionable unsupported input, never a scientific success."""


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root(path) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise WorkbenchError('研究代码目录不存在。')
    return p


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _files(root: Path):
    warnings = []
    found = []
    total = 0
    for directory, directories, filenames in os.walk(root, followlinks=False):
        d = Path(directory)
        directories[:] = sorted(x for x in directories if x not in IGNORED and not (d / x).is_symlink())
        for filename in sorted(filenames):
            p = d / filename
            if p.is_symlink() or not _inside(p.resolve(), root):
                warnings.append(f'跳过链接：{p.relative_to(root).as_posix()}')
                continue
            if p.suffix != '.py':
                continue
            size = p.stat().st_size
            if size > MAX_FILE_BYTES:
                raise WorkbenchError(f'源码文件超过 10 MB：{p.relative_to(root)}')
            total += size
            if total > MAX_SOURCE_BYTES or len(found) >= 5000:
                raise WorkbenchError('源码过大；请选择更小的研究代码目录。')
            found.append(p)
    return found, warnings


def _annotation(node) -> tuple[str, list[str]]:
    """Return a safe JSON-input type expression, without evaluating annotations."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _annotation(ast.parse(node.value, mode='eval').body)
        except SyntaxError:
            raise WorkbenchError('无法解析字符串类型注解')
    if isinstance(node, ast.Constant) and node.value is None:
        return 'None', []
    if isinstance(node, ast.Name) and node.id in PRIMITIVES:
        return ('None' if node.id == 'NoneType' else node.id), []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, a = _annotation(node.left)
        right, b = _annotation(node.right)
        return f'{left} | {right}', a + b
    if isinstance(node, ast.Subscript):
        base = ast.unparse(node.value).split('.')[-1]
        parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if base == 'Literal':
            vals = []
            for n in parts:
                try:
                    v = ast.literal_eval(n)
                except (ValueError, TypeError):
                    raise WorkbenchError('Literal 必须是静态 JSON 标量')
                if not isinstance(v, (str, int, bool, float)) or isinstance(v, float) and not __import__('math').isfinite(v):
                    raise WorkbenchError('Literal 含非 JSON 标量')
                vals.append(repr(v))
            return f'Literal[{", ".join(vals)}]', []
        if base == 'Annotated':
            return _annotation(parts[0])
        if base in {'Optional', 'Union', 'List', 'list', 'Dict', 'dict', 'Tuple', 'tuple', 'Sequence'}:
            typed, assumptions = [], []
            ndarray = False
            for part in parts:
                if ast.unparse(part).split('.')[-1] == 'ndarray' and base == 'Union':
                    ndarray = True
                    continue
                if isinstance(part, ast.Constant) and part.value is Ellipsis and base in {'Tuple', 'tuple'}:
                    typed.append('...')
                    continue
                t, notes = _annotation(part)
                typed.append(t)
                assumptions.extend(notes)
            if ndarray:
                if not any(t.startswith('list[') for t in typed):
                    raise WorkbenchError('ndarray 仅在源 Union 同时声明 JSON list 时可映射')
                assumptions.append('输入采用源 Union 明确支持的 list 分支，不把 JSON 输入转成 ndarray。')
            if base == 'Union':
                if not typed:
                    raise WorkbenchError('Union 没有可用 JSON 分支')
                return ' | '.join(typed), assumptions
            if base == 'Optional':
                if len(typed) != 1:
                    raise WorkbenchError('Optional 参数数错误')
                return f'{typed[0]} | None', assumptions
            alias = {'List': 'list', 'Dict': 'dict', 'Tuple': 'tuple', 'Sequence': 'list'}.get(base, base)
            if alias == 'dict' and (len(typed) != 2 or typed[0] != 'str'):
                raise WorkbenchError('JSON 字典只支持 str 键')
            if alias == 'list' and len(typed) != 1:
                raise WorkbenchError('list 必须声明元素类型')
            return f'{alias}[{", ".join(typed)}]', assumptions
    raise WorkbenchError(f'未知或非 JSON 输入类型：{ast.unparse(node)}')


def _infer(value):
    if value is None:
        raise WorkbenchError('缺注解且默认 None，无法确定实际输入类型')
    if type(value) in (bool, int, float, str):
        return type(value).__name__
    if isinstance(value, list) and value:
        types = {_infer(x) for x in value}
        if len(types) == 1:
            return 'list[' + next(iter(types)) + ']'
    raise WorkbenchError('缺注解且没有可确定的 JSON 标量或同类型非空 list 默认值')


def _module(root: Path, path: Path):
    rel = path.relative_to(root).with_suffix('')
    parts = list(rel.parts)
    if parts and parts[0] == 'src':
        parts = parts[1:]
    if parts and parts[-1] == '__init__':
        parts.pop()
    if not parts or not all(x.isidentifier() and not __import__('keyword').iskeyword(x) for x in parts):
        return None
    return '.'.join(parts)


def _candidate(root: Path, path: Path, node, module, method=False):
    reasons, assumptions, parameters = [], [], []
    if method:
        reasons.append('实例或类方法需要对象生命周期，第一版不自动封装')
    if isinstance(node, ast.AsyncFunctionDef):
        reasons.append('async 函数需人工异步适配')
    if node.decorator_list:
        reasons.append('带装饰器的函数需人工确认运行时绑定')
    if module is None:
        reasons.append('路径无法对应可导入的 Python 模块')
    args = node.args
    if args.vararg or args.kwarg:
        reasons.append('变长参数不具备明确的 MCP 输入契约')
    if args.posonlyargs:
        reasons.append('仅位置参数需人工适配')
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults))
    for arg, default in pairs:
        if method and arg.arg in {'self', 'cls'}:
            continue
        item = {'name': arg.arg, 'required': default is None, 'kind': 'keyword_only' if arg in args.kwonlyargs else 'positional_or_keyword'}
        try:
            if default is not None:
                item['default'] = ast.literal_eval(default)
                json.dumps(item['default'], allow_nan=False)
            if arg.annotation is not None:
                typ, notes = _annotation(arg.annotation)
                assumptions.extend(notes)
            elif default is not None:
                typ = _infer(item['default'])
                assumptions.append(f'{arg.arg} 缺少注解，按原默认值推断 {typ}；需作者确认。')
            else:
                raise WorkbenchError('必需参数缺少类型注解，不能安全自动推断')
            item['type'] = typ
            item['description'] = f'原函数参数 {arg.arg}；完整含义见源函数文档。'
        except (WorkbenchError, ValueError, TypeError, OverflowError) as exc:
            reasons.append(f'{arg.arg}: {exc}')
            item['type'] = None
        parameters.append(item)
    symbol = node.name
    return {'id': f'{module or path.stem}:{symbol}', 'module': module, 'function': symbol,
            'path': path.relative_to(root).as_posix(), 'line': node.lineno,
            'signature': f'{symbol}({ast.unparse(args)})',
            'docstring': (ast.get_docstring(node) or '')[:5000],
            'return_annotation': ast.unparse(node.returns) if node.returns else None,
            'parameters': parameters, 'eligible': not reasons, 'reasons': reasons,
            'assumptions': list(dict.fromkeys(assumptions)), 'source_sha256': _hash(path)}


def scan_repository(path) -> dict:
    """Inspect Python AST only; imports, decorators and scientific code are never run."""
    root = _root(path)
    files, warnings = _files(root)
    candidates, source_manifest, model_dependencies = [], [], []
    for p in files:
        source_manifest.append({'path': p.relative_to(root).as_posix(), 'sha256': _hash(p), 'bytes': p.stat().st_size})
        try:
            with tokenize.open(p) as f:
                tree = ast.parse(f.read(), filename=str(p))
        except (SyntaxError, UnicodeError, OSError) as exc:
            warnings.append(f'{p.relative_to(root)} 无法静态解析：{type(exc).__name__}')
            continue
        module = _module(root, p)
        for n in ast.walk(tree):
            imports = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ''] if isinstance(n, ast.ImportFrom) else []
            model_dependencies.extend(x.split('.')[0] for x in imports if x.split('.')[0] in MODEL_PACKAGES)
        global_risks = []
        for n in tree.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                global_risks.extend(_reachable_execution_risks(n, tree))
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith('_'):
                c = _candidate(root, p, n, module)
                c['execution_restrictions'] = list(dict.fromkeys(global_risks + _reachable_execution_risks(n, tree)))
                candidates.append(c)
            elif isinstance(n, ast.ClassDef):
                for member in n.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and not member.name.startswith('_'):
                        c = _candidate(root, p, member, module, method=True)
                        c['id'] = f'{module or p.stem}:{n.name}.{member.name}'
                        c['execution_restrictions'] = list(dict.fromkeys(global_risks + _reachable_execution_risks(member, tree)))
                        candidates.append(c)
    model_dependencies = sorted(set(model_dependencies))
    for c in candidates:
        if model_dependencies:
            c['execution_restrictions'].append('研究仓库声明本地模型或加速依赖：' + ', '.join(model_dependencies))
        c['runnable'] = c['eligible'] and not c['execution_restrictions']
    return {'repository_path': str(root), 'files_scanned': len(files),
            'candidates': candidates, 'warnings': warnings, 'source_manifest': source_manifest,
            'execution_performed': False, 'validation_status': 'static_only_not_verified',
            'local_model_execution_allowed': False, 'model_dependencies': model_dependencies}


def _execution_risks(node):
    reasons = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        target = ast.unparse(n.func)
        tail = target.split('.')[-1]
        if tail == 'from_pretrained' or tail in {'load_model', 'train_model', 'run_inference', 'init_model', 'generate_text'}:
            reasons.append('检测到模型加载、训练或推理调用：' + target)
        elif target in {'exec', 'eval', '__import__', 'compile', 'os.system', 'os.popen', 'runpy.run_path', 'runpy.run_module'} or target.startswith('subprocess.'):
            reasons.append('动态代码或外部进程调用需人工审阅，本轮不执行：' + target)
    return list(dict.fromkeys(reasons))


def _reachable_execution_risks(node, tree):
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    visited, pending, risks = set(), [node], []
    while pending:
        current = pending.pop()
        risks.extend(_execution_risks(current))
        for n in ast.walk(current):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in functions and n.func.id not in visited:
                visited.add(n.func.id)
                pending.append(functions[n.func.id])
    return list(dict.fromkeys(risks))


def _secret_check(text: str, path: str):
    if re.search(r'\b(?:sk-[A-Za-z0-9_-]{16,}|ark-[A-Za-z0-9_-]{24,})\b', text):
        raise WorkbenchError(f'源码可能包含密钥，不能复制或打包：{path}')


def _dependency_declarations(root: Path):
    declared, warnings = [], []
    setup = root / 'setup.py'
    if setup.is_file():
        try:
            tree = ast.parse(setup.read_text(encoding='utf-8'))
            for n in ast.walk(tree):
                if isinstance(n, ast.keyword) and n.arg == 'install_requires':
                    declared.extend(ast.literal_eval(n.value))
        except (SyntaxError, ValueError, TypeError):
            warnings.append('setup.py 的动态依赖未执行，需人工审阅。')
    pp = root / 'pyproject.toml'
    if pp.is_file():
        try:
            import tomllib
            declared.extend(tomllib.loads(pp.read_text(encoding='utf-8')).get('project', {}).get('dependencies', []))
        except (ValueError, TypeError):
            warnings.append('pyproject.toml 依赖未成功静态读取。')
    req = root / 'requirements.txt'
    if req.is_file():
        for line in req.read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                declared.append(line.strip())
    safe = []
    for item in declared:
        if isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~][A-Za-z0-9.*,<>=!~+-]+)?', item):
            if not item.lower().startswith('fastmcp'):
                safe.append(item)
        else:
            warnings.append('非简单 PyPI 依赖（路径、URL、marker 或动态声明）需人工审阅，未自动安装。')
    return list(dict.fromkeys(safe)), list(dict.fromkeys(warnings))


def _git_identity(root: Path):
    """Read only standard local Git identity files; never run Git or expose credentials."""
    git = root / '.git'
    identity = {'commit': None, 'public_repository_url': None}
    if not git.is_dir() or git.is_symlink():
        return identity
    head = git / 'HEAD'
    if head.is_file():
        text = head.read_text(encoding='utf-8').strip()
        if re.fullmatch('[0-9a-f]{40,64}', text):
            identity['commit'] = text
        elif text.startswith('ref: '):
            ref = git / text[5:]
            if _inside(ref.resolve(), git.resolve()) and ref.is_file():
                value = ref.read_text(encoding='utf-8').strip()
                if re.fullmatch('[0-9a-f]{40,64}', value):
                    identity['commit'] = value
            packed = git / 'packed-refs'
            if identity['commit'] is None and packed.is_file():
                for line in packed.read_text(encoding='utf-8').splitlines():
                    bits = line.split()
                    if len(bits) == 2 and bits[1] == text[5:] and re.fullmatch('[0-9a-f]{40,64}', bits[0]):
                        identity['commit'] = bits[0]
    config = git / 'config'
    if config.is_file():
        # Restrict exported origin to a credential-free public GitHub URL.
        match = re.search(r'(?m)^\s*url\s*=\s*(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*$', config.read_text(encoding='utf-8'))
        if match:
            identity['public_repository_url'] = match.group(1).removesuffix('.git')
    return identity


_SERVER_HEADER = '''"""Generated direct bindings. Source provenance and assumptions are in manifest.json."""
from __future__ import annotations
import importlib
import hashlib
import json
import math
import os
import sys
import uuid
from pathlib import Path
from typing import Annotated, Literal

# User constraint: this trial must never load a local model or use GPU execution.
BLOCKED_MODEL_PACKAGES = {'torch', 'tensorflow', 'transformers', 'jax', 'diffusers', 'ollama',
    'llama_cpp', 'onnxruntime', 'sentence_transformers', 'vllm', 'lmstudio', 'mlx'}
class _NoLocalModels:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in BLOCKED_MODEL_PACKAGES:
            raise ImportError('Local model/framework imports are disabled in this trial: ' + fullname)
        return None
sys.meta_path.insert(0, _NoLocalModels())
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['HIP_VISIBLE_DEVICES'] = ''
os.environ['NVIDIA_VISIBLE_DEVICES'] = 'void'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
from fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor' / 'repo'))
sys.path.insert(0, str(ROOT / 'vendor' / 'repo' / 'src'))

def _json_native(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('Upstream returned a non-finite number; no successful result recorded.')
        return value
    if isinstance(value, (list, tuple)):
        return [_json_native(x) for x in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise TypeError('Upstream result has non-string JSON dictionary keys.')
        return {k: _json_native(v) for k, v in value.items()}
    if type(value).__module__.split('.')[0] == 'numpy' and hasattr(value, 'tolist'):
        return _json_native(value.tolist())
    raise TypeError('Upstream result cannot be safely represented as JSON: ' + type(value).__name__)

def _invoke(module, symbol, kwargs, reference, source_path, source_sha256):
    # Loading source happens only when the recipient explicitly calls an MCP tool.
    pinned = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    vendor = (ROOT / 'vendor' / 'repo').resolve()
    for item in pinned['copied_source_files']:
        path = (vendor / item['path']).resolve()
        if vendor not in path.parents or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Pinned source dependency changed; no call performed.')
    expected = (ROOT / 'vendor' / 'repo' / source_path).resolve()
    if hashlib.sha256(expected.read_bytes()).hexdigest() != source_sha256:
        raise ValueError('Pinned upstream source changed; regenerate and verify before calling.')
    source_module = importlib.import_module(module)
    if not getattr(source_module, '__file__', None) or Path(source_module.__file__).resolve() != expected:
        raise ValueError('Imported module origin differs from pinned source; no call performed.')
    function = getattr(source_module, symbol)
    result = _json_native(function(**kwargs))
    output = ROOT / 'outputs' / uuid.uuid4().hex
    output.mkdir(parents=True)
    result_path = output / 'native-result.json'
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\\n', encoding='utf-8')
    return {'message': 'Upstream function completed; source result preserved without algorithm changes.',
            'reference': reference, 'native_result': result,
            'artifacts': [{'description': 'JSON serialization of the native upstream result', 'path': str(result_path.resolve())}]}

'''


def build_mcp(repository_path, selections: list[str], name: str, output_root: Path) -> dict:
    """Generate a standalone server without importing or running selected research code."""
    root = _root(repository_path)
    out = Path(output_root).expanduser().resolve()
    if _inside(out, root):
        raise WorkbenchError('输出目录不能位于研究源码目录中。')
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(x in name for x in '/\\\x00\r\n'):
        raise WorkbenchError('服务器名必须为 1–80 字符，不能包含路径分隔符。')
    if not isinstance(selections, list) or not selections or not all(isinstance(s, str) for s in selections) or len(set(selections)) != len(selections):
        raise WorkbenchError('请选择至少一个不重复的 module:function。')
    scan = scan_repository(root)
    lookup = {}
    for c in scan['candidates']:
        if c['id'] in lookup:
            lookup[c['id']] = None
        else:
            lookup[c['id']] = c
    selected = []
    for identifier in selections:
        if identifier not in lookup or lookup[identifier] is None:
            raise WorkbenchError(f'函数不存在或模块身份有歧义：{identifier}')
        c = lookup[identifier]
        if not c['eligible']:
            raise WorkbenchError(f'暂不能自动封装 {identifier}：' + '; '.join(c['reasons']))
        if not c['runnable']:
            raise WorkbenchError(f'本轮不执行或封装 {identifier}：' + '; '.join(c['execution_restrictions']))
        selected.append(c)
    licenses = [p for p in root.iterdir() if p.is_file() and not p.is_symlink() and p.name.lower().startswith(('license', 'copying', 'notice'))]
    if not licenses:
        raise WorkbenchError('未找到 LICENSE / COPYING / NOTICE；请先核实分发许可，再生成包。')
    dependencies, dependency_warnings = _dependency_declarations(root)
    # Review secrets before creating any deliverable directory.
    source_files, _ = _files(root)
    for p in source_files + licenses:
        _secret_check(p.read_text(encoding='utf-8', errors='replace'), p.relative_to(root).as_posix())
    project = out / ('mcp-' + uuid.uuid4().hex)
    if project.parent.resolve() != out or not _inside(project.resolve(), out):
        raise WorkbenchError('生成路径超出指定输出目录。')
    project.mkdir(parents=True)
    copied = []
    vendor = project / 'vendor' / 'repo'
    vendor.mkdir(parents=True)
    extras = [p for p in root.iterdir() if p.is_file() and not p.is_symlink() and (p.name.lower().startswith('readme') or p.name in {'requirements.txt', 'pyproject.toml', 'setup.py', 'setup.cfg'})]
    for p in dict.fromkeys(source_files + licenses + extras):
        if p.stat().st_size > MAX_FILE_BYTES:
            raise WorkbenchError(f'分发文件超过 10 MB：{p.name}')
        _secret_check(p.read_text(encoding='utf-8', errors='replace'), p.name)
        rel = p.relative_to(root)
        target = vendor / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        copied.append({'path': rel.as_posix(), 'sha256': _hash(target), 'bytes': target.stat().st_size})
    # Preserve the caller's JSON types: coercion can bypass scientific guards.
    server = _SERVER_HEADER + f'mcp = FastMCP(name={name!r}, strict_input_validation=True)\n\n'
    tool_names = []
    for c in selected:
        tool_name = re.sub(r'[^a-zA-Z0-9_]', '_', c['module']) + '__' + c['function']
        if tool_name in tool_names:
            raise WorkbenchError('规范化后的工具名称冲突，请分别生成。')
        tool_names.append(tool_name)
        arguments, bindings = [], []
        keyword_started = False
        for p in c['parameters']:
            if p['kind'] == 'keyword_only' and not keyword_started:
                arguments.append('*')
                keyword_started = True
            text = f"{p['name']}: Annotated[{p['type']}, {p['description']!r}]"
            if not p['required']:
                text += ' = ' + repr(p['default'])
            arguments.append(text)
            bindings.append(f"{p['name']!r}: {p['name']}")
        reference = f"source://sha256/{c['source_sha256']}/{c['path']}#{c['function']}:L{c['line']}"
        doc = f"Call the original {c['module']}.{c['function']} research function.\nJSON-compatible inputs → native result and a source-traceable JSON artifact."
        server += f"@mcp.tool()\ndef {tool_name}({', '.join(arguments)}) -> dict:\n    {doc!r}\n    return _invoke({c['module']!r}, {c['function']!r}, {{{', '.join(bindings)}}}, {reference!r}, {c['path']!r}, {c['source_sha256']!r})\n\n"
        c['tool_name'] = tool_name
    server += "if __name__ == '__main__':\n    mcp.run()\n"
    (project / 'server.py').write_text(server, encoding='utf-8')
    requirements = ['fastmcp==4.0.3'] + dependencies
    (project / 'requirements.txt').write_text('\n'.join(requirements) + '\n', encoding='utf-8')
    manifest = {'schema_version': 1, 'name': name, 'repository_path_at_build': str(root),
                'repository_identity': _git_identity(root),
                'license_files': [p.name for p in licenses],
                'selected_tools': selected, 'copied_source_files': copied,
                'declared_dependencies': dependencies, 'dependency_warnings': dependency_warnings,
                'server_sha256': _hash(project / 'server.py'), 'execution_performed': False,
                'verification_status': 'generated_not_executed_or_independently_verified',
                'input_validation': 'strict_json_types',
                'algorithm_changes': False, 'local_model_execution_allowed': False,
                'execution_policy': {'blocked_model_packages': sorted(MODEL_PACKAGES),
                                     'gpu_visibility': 'disabled', 'hf_offline': True,
                                     'not_a_security_sandbox': True},
                'source_data_files_included': False,
                'limits': ['Python public synchronous module functions with explicit JSON contracts.',
                           'External data, native libraries and dynamic dependencies require separate review.',
                           'Generated source runs with caller permissions only on explicit MCP invocation; static review is not a sandbox.',
                           'No claim of scientific correctness until upstream comparison and independent acceptance.']}
    (project / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (project / 'USAGE.md').write_text(
        '# ' + name + '\n\n本包直接调用 vendor/repo 中保留的研究代码，不重写算法。来源、SHA256、参数假设和限制见 manifest.json。上游许可原文保留在 vendor/repo。\n\n'
        '## 安装与启动\n\n在本目录内创建独立环境，然后人工审阅 requirements.txt 和源码，再执行：\n\n'
        '```powershell\npython -m venv .venv\n.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n.\\.venv\\Scripts\\python.exe server.py\n```\n\n'
        '自动生成阶段未导入研究代码、未安装依赖、未运行模型。MCP 调用会执行原始函数及其模块导入；只使用可信、已审阅的代码。\n\n'
        '该生成包尚未通过独立验收；安装成功或工具列表可见不等于科学结果已核实。请保存原实现直接运行的参考结果，然后运行 example_call.py，与 native_result 对比。不能调用本地模型。\n\n'
        '## 测试入口\n\n`python example_call.py module__function arguments.json` 使用 FastMCP 真实客户端调用服务器；参数 JSON 需自行准备，不提供虚构科学数据。每次调用的原生结果保存在 outputs/ 的独立目录。\n\n'
        '外部数据文件、动态依赖、未知类型、实例方法、异步函数及变长参数需人工适配。\n', encoding='utf-8')
    (project / 'example_call.py').write_text(
        "\"\"\"Explicit recipient acceptance entry point, never run during conversion.\"\"\"\n"
        "import asyncio, json, sys\nfrom pathlib import Path\nfrom fastmcp import Client\n"
        "async def main():\n    arguments = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "    async with Client(Path(__file__).with_name('server.py')) as client:\n"
        "        result = await client.call_tool(sys.argv[1], arguments)\n        print(json.dumps(result.data, ensure_ascii=False, indent=2, allow_nan=False))\n"
        "if __name__ == '__main__':\n    asyncio.run(main())\n", encoding='utf-8')
    return {'project_path': str(project.resolve()), 'manifest_path': str((project / 'manifest.json').resolve()),
            'server_path': str((project / 'server.py').resolve()), 'requirements_path': str((project / 'requirements.txt').resolve()),
            'selected_tools': selected, 'source_manifest': copied, 'warnings': dependency_warnings,
            'verification_status': manifest['verification_status']}


def package_mcp(project_path) -> dict:
    """Package only generated production files and original source, never results or secrets."""
    project = _root(project_path)
    if not (project / 'manifest.json').is_file():
        raise WorkbenchError('只能打包具备生成来源清单的 MCP 项目。')
    manifest = json.loads((project / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or manifest.get('server_sha256') != _hash(project / 'server.py'):
        raise WorkbenchError('服务器来源清单不匹配，需重新生成和核对。')
    allowed = [project / p for p in ('server.py', 'requirements.txt', 'manifest.json', 'USAGE.md', 'example_call.py')]
    for item in manifest['copied_source_files']:
        p = project / 'vendor' / 'repo' / item['path']
        if not _inside(p.resolve(), project / 'vendor' / 'repo') or p.is_symlink() or not p.is_file() or _hash(p) != item['sha256']:
            raise WorkbenchError('复制源码哈希或路径不匹配。')
        allowed.append(p)
    for p in allowed:
        if p.is_symlink():
            raise WorkbenchError('生成项目不能包含符号链接。')
        _secret_check(p.read_text(encoding='utf-8', errors='replace'), p.name)
    dist = project.parent / 'dist'
    dist.mkdir(exist_ok=True)
    zip_path = dist / ('mcp-' + uuid.uuid4().hex + '.zip')
    with zipfile.ZipFile(zip_path, 'x', zipfile.ZIP_DEFLATED) as archive:
        for p in allowed:
            archive.write(p, project.name + '/' + p.relative_to(project).as_posix())
    return {'zip_path': str(zip_path.resolve()), 'sha256': _hash(zip_path),
            'files': [p.relative_to(project).as_posix() for p in allowed],
            'verification_status': manifest['verification_status']}


def test_mcp(project_path, python_executable, tool_name: str, arguments: dict) -> dict:
    """Explicitly test one selected upstream function via real stdio MCP; no installation."""
    project = _root(project_path)
    interpreter = Path(python_executable).expanduser().resolve()
    if not interpreter.is_file() or not interpreter.name.lower().startswith('python'):
        raise WorkbenchError('需要明确指定已安装依赖的 Python 解释器。')
    manifest_path = project / 'manifest.json'
    if not manifest_path.is_file():
        raise WorkbenchError('缺少 MCP 生成来源清单。')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    candidates = {t['tool_name']: t for t in manifest.get('selected_tools', [])}
    if tool_name not in candidates:
        raise WorkbenchError('测试工具不在生成清单中。')
    if manifest.get('server_sha256') != _hash(project / 'server.py'):
        raise WorkbenchError('服务器已改变，不能沿用原生成来源身份。')
    if not isinstance(arguments, dict):
        raise WorkbenchError('工具参数必须为 JSON 对象。')
    serialized = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    _secret_check(serialized, '测试参数')
    if any(re.search(r'api[_-]?key|secret|password|token', key, re.I) for key in arguments):
        raise WorkbenchError('测试参数不能包含认证密钥；请在独立远程配置中设置。')
    # Conservative static gate, no model/GPU probe or dependency import.
    for item in manifest.get('copied_source_files', []):
        p = project / 'vendor' / 'repo' / item['path']
        if not _inside(p.resolve(), project / 'vendor' / 'repo') or p.is_symlink() or not p.is_file() or _hash(p) != item['sha256']:
            raise WorkbenchError('复制源码哈希或路径不匹配。')
        if p.suffix == '.py':
            with tokenize.open(p) as source:
                tree = ast.parse(source.read())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or '']
                if any(n.split('.')[0] in MODEL_PACKAGES for n in names):
                    raise WorkbenchError('源码涉及本地模型或加速框架；本试用版不执行，需人工另行审查。')
    result_dir = project / 'verification' / uuid.uuid4().hex
    result_dir.mkdir(parents=True)
    (result_dir / 'arguments.json').write_text(serialized + '\n', encoding='utf-8')
    runner = result_dir / 'runner.py'
    response = result_dir / 'response.json'
    runner.write_text(
        'import asyncio, json\nfrom pathlib import Path\nfrom fastmcp import Client\nfrom fastmcp.client.transports import PythonStdioTransport\n'
        f'SERVER = {str(project / "server.py")!r}\nPYTHON = {str(interpreter)!r}\nTOOL = {tool_name!r}\n'
        f'ARGS = {str(result_dir / "arguments.json")!r}\nRESPONSE = {str(response)!r}\n'
        'async def invoke():\n    transport = PythonStdioTransport(SERVER, python_cmd=PYTHON, cwd=str(Path(SERVER).parent))\n'
        '    async with Client(transport) as client:\n        inventory = await client.list_tools()\n'
        '        result = await client.call_tool(TOOL, json.loads(Path(ARGS).read_text(encoding="utf-8")))\n'
        '        Path(RESPONSE).write_text(json.dumps({"tools": [t.name for t in inventory], "data": result.data}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")\n'
        'async def main():\n    await asyncio.wait_for(invoke(), timeout=25)\nasyncio.run(main())\n', encoding='utf-8')
    record = {'tool_name': tool_name, 'arguments': arguments, 'source': candidates[tool_name],
              'python_executable': str(interpreter), 'transport': 'real_stdio_fastmcp_client',
              'scientific_validation': 'not_established_by_smoke_call', 'local_model_called': False,
              'verification_path': str(result_dir.resolve())}
    try:
        completed = subprocess.run([str(interpreter), str(runner)], cwd=str(project),
                                   capture_output=True, text=True, encoding='utf-8', errors='replace',
                                   timeout=30, check=False)
        record['exit_code'] = completed.returncode
        if completed.returncode == 0 and response.is_file():
            record['decision'] = 'completed'
            record['result'] = json.loads(response.read_text(encoding='utf-8'))
        else:
            record['decision'] = 'execution_error'
            message = completed.stderr[-4000:]
            message = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|ark-[A-Za-z0-9_-]{24,})\b', '[REDACTED]', message)
            record['error'] = message
    except subprocess.TimeoutExpired:
        record.update(decision='timeout', error='MCP 调用超过 30 秒；不计成功。')
    report = result_dir / 'test-result.json'
    report.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    record['report_path'] = str(report.resolve())
    return record
