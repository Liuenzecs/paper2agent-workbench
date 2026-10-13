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

from code_contracts import ContractError, check_contract, validate_value

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


def _type_schema(expression):
    """Translate the supported static annotation grammar without evaluating it."""
    def convert(node):
        if isinstance(node, ast.Constant) and node.value is None:
            return {'type': 'null'}
        if isinstance(node, ast.Name):
            return {'type': {'int': 'integer', 'float': 'number', 'str': 'string', 'bool': 'boolean', 'None': 'null'}[node.id]}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return {'anyOf': [convert(node.left), convert(node.right)]}
        if isinstance(node, ast.Subscript):
            base = ast.unparse(node.value)
            parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            if base == 'Literal':
                values = [ast.literal_eval(x) for x in parts]
                branches = [{'type': {bool: 'boolean', int: 'integer', float: 'number', str: 'string'}[type(v)], 'const': v} for v in values]
                return branches[0] if len(branches) == 1 else {'anyOf': branches}
            if base == 'dict':
                return {'type': 'object', 'properties': {}, 'additionalProperties': convert(parts[1])}
            if base == 'tuple' and not (len(parts) == 2 and isinstance(parts[1], ast.Constant) and parts[1].value is Ellipsis):
                return {'type': 'array', 'prefixItems': [convert(x) for x in parts], 'items': False, 'minItems': len(parts), 'maxItems': len(parts)}
            return {'type': 'array', 'items': convert(parts[0])}
        raise WorkbenchError('无法生成声明类型的 JSON schema。')
    return convert(ast.parse(expression, mode='eval').body)


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
        reasons.append('实例或类方法需要对象生命周期适配')
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
                reasons.append(f'{arg.arg}: 缺注解默认值推断需要审阅')
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
            'assumptions': list(dict.fromkeys(assumptions)), 'source_sha256': _hash(path),
            'is_async': isinstance(node, ast.AsyncFunctionDef), 'is_method': method,
            'requires_manual_runtime_adapter': bool(node.decorator_list or module is None or args.vararg or args.kwarg or args.posonlyargs),
            'contract_status': 'review_required' if reasons or assumptions else 'declared_json',
            'review_required': bool(reasons or assumptions),
            'contract_template': {'reviewed': False, 'source_sha256': _hash(path),
                                  'parameters': {p['name']: {'schema': _type_schema(p['type']) if p['type'] else None} for p in parameters},
                                  'result': {'nonfinite': 'reject'}}}


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
                        c['function'] = n.name + '.' + member.name
                        c['class_name'] = n.name
                        constructor = next((x for x in n.body if isinstance(x, ast.FunctionDef) and x.name == '__init__'), None)
                        c['constructor_parameters'] = _candidate(root, p, constructor, module, method=True)['parameters'] if constructor else []
                        c['constructor_restrictions'] = _reachable_execution_risks(constructor, tree) if constructor else []
                        if constructor and (constructor.args.vararg or constructor.args.kwarg or constructor.args.posonlyargs or constructor.decorator_list):
                            c['constructor_restrictions'].append('构造函数含动态签名或装饰器，需要独立适配。')
                        c['contract_template']['constructor'] = {'parameters': {p['name']: {'schema': None} for p in c['constructor_parameters']}}
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
            'contract_schema_version': 1,
            'supported_reviewed_adapters': ['identity', 'ndarray', 'dataframe', 'path', 'explicit_instance_constructor'],
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
            reasons.append('动态代码或外部进程调用需要人工适配：' + target)
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
from typing import Any
from pydantic import Field, BeforeValidator
from functools import partial
import inspect
import warnings

# Runtime dependency policy.
BLOCKED_MODEL_PACKAGES = {'torch', 'tensorflow', 'transformers', 'jax', 'diffusers', 'ollama',
    'llama_cpp', 'onnxruntime', 'sentence_transformers', 'vllm', 'lmstudio', 'mlx'}
class _NoLocalModels:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in BLOCKED_MODEL_PACKAGES:
            raise ImportError('Unsupported runtime dependency in Paper2Agent: ' + fullname)
        return None
sys.meta_path.insert(0, _NoLocalModels())
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['HIP_VISIBLE_DEVICES'] = ''
os.environ['NVIDIA_VISIBLE_DEVICES'] = 'void'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
from fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
# Load this package's pinned adapter by path; never substitute an installed name.
import importlib.util
_helper_name = '_paper2agent_adapter_' + hashlib.sha256((ROOT / 'code_contracts.py').read_bytes()).hexdigest()
_helper_spec = importlib.util.spec_from_file_location(_helper_name, ROOT / 'code_contracts.py')
_helper = importlib.util.module_from_spec(_helper_spec)
sys.modules[_helper_name] = _helper
_helper_spec.loader.exec_module(_helper)
adapt_inputs, serialize_result = _helper.adapt_inputs, _helper.serialize_result
validate_rules, before_validate = _helper.validate_rules, _helper.before_validate
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

async def _invoke(module, symbol, kwargs, reference, source_path, source_sha256, contract=None, constructor=None):
    # Loading source happens only when the recipient explicitly calls an MCP tool.
    pinned = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    for filename, field in [('code_contracts.py', 'runtime_adapter_sha256'), ('contracts.json', 'contracts_sha256')]:
        if hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != pinned[field]:
            raise ValueError('Pinned runtime adapter or interface contract changed; no call performed.')
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
    workspace = ROOT / 'workspace'
    output = workspace / 'outputs' / uuid.uuid4().hex if contract else ROOT / 'outputs' / uuid.uuid4().hex
    if contract:
        for managed in (workspace, workspace / 'inputs', workspace / 'outputs'):
            if managed.is_symlink() or ROOT not in managed.resolve().parents:
                raise ValueError('Managed workspace root escapes the generated project.')
    # Validating inputs and binding precede any upstream call or successful artifact.
    if contract:
        validate_rules(kwargs, contract.get('rules', []))
        kwargs = adapt_inputs(kwargs, contract['parameters'], workspace, output)
    else:
        _json_native(kwargs)  # reject nonfinite input before the scientific function
    if '.' in symbol:
        class_name, method = symbol.split('.')
        if contract is None or 'constructor' not in contract or constructor is None:
            raise ValueError('Instance methods require an explicitly reviewed constructor contract.')
        init_kwargs = adapt_inputs(constructor, contract['constructor']['parameters'], workspace, output)
        instance = getattr(source_module, class_name)(**init_kwargs)
        function = getattr(instance, method)
    else:
        function = getattr(source_module, symbol)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        native = function(**kwargs)
        if inspect.isawaitable(native):
            native = await native
    result, nonfinite_mask, returned_artifacts = serialize_result(native, contract.get('result') if contract else None, output)
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / 'native-result.json'
    if result_path.exists() or contract and (output / 'serialization-audit.json').exists():
        raise ValueError('Upstream used a reserved call-record filename; original files preserved.')
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\\n', encoding='utf-8')
    artifacts = [{'description': 'JSON serialization of the native upstream result', 'path': str(result_path.resolve())}]
    if contract:
        audit_path = output / 'serialization-audit.json'
        audit_path.write_text(json.dumps({'nonfinite_policy': contract.get('result', {'nonfinite': 'reject'}), 'nonfinite_mask': nonfinite_mask}, ensure_ascii=False, indent=2, allow_nan=False) + '\\n', encoding='utf-8')
        artifacts.append({'description': 'Explicit serialization policy and JSON-pointer missingness mask', 'path': str(audit_path.resolve())})
        for path in output.rglob('*'):
            if path.is_symlink() or output.resolve() not in path.resolve().parents:
                raise ValueError('Upstream output escapes the managed call directory.')
            if path.is_file() and path not in (result_path, audit_path):
                artifacts.append({'description': 'Upstream output file', 'path': str(path.resolve())})
    return {'message': 'Upstream function completed; source result preserved without algorithm changes.',
            'reference': reference, 'native_result': result,
            'nonfinite_mask': nonfinite_mask,
            'warnings': [{'category': type(item.message).__name__, 'message': str(item.message)} for item in captured],
            'artifacts': artifacts}

'''


def build_mcp(repository_path, selections: list[str], name: str, output_root: Path, contracts: dict | None = None) -> dict:
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
    if contracts is None:
        reviewed = {}
    elif not isinstance(contracts, dict) or set(contracts) != {'schema_version', 'tools'} or contracts.get('schema_version') != 1 or not isinstance(contracts.get('tools'), dict):
        raise WorkbenchError('接口契约必须包含 schema_version:1 和 tools 对象。')
    else:
        reviewed = contracts['tools']
        if set(reviewed) - set(selections):
            raise WorkbenchError('接口契约包含未选中的函数。')
        try:
            contract_text = json.dumps(contracts, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise WorkbenchError('接口契约必须是有限值的 JSON 数据。') from exc
        _secret_check(contract_text, '接口契约')
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
        contract = reviewed.get(identifier)
        if contract is not None:
            try:
                contract = check_contract(contract)
                if contract['source_sha256'] != c['source_sha256']:
                    raise ContractError('Reviewed source SHA-256 differs from the scanned source.')
                if set(contract['parameters']) != {p['name'] for p in c['parameters']}:
                    raise ContractError('Reviewed parameters must exactly match all source parameters.')
                if c['requires_manual_runtime_adapter']:
                    raise ContractError('; '.join(c['reasons']))
                for p in c['parameters']:
                    if not p['required']:
                        if 'default' not in p:
                            raise ContractError('A source default cannot be represented as reviewed JSON: ' + p['name'])
                        validate_value(p['default'], contract['parameters'][p['name']]['schema'], p['name'] + '.default')
                if c.get('is_method'):
                    if c.get('constructor_restrictions'):
                        raise ContractError('; '.join(c['constructor_restrictions']))
                    if 'constructor' not in contract:
                        raise ContractError('Instance methods require explicit constructor parameters.')
                    if 'constructor' in contract['parameters']:
                        raise ContractError('Method parameter constructor conflicts with the explicit lifecycle argument.')
                    init_params = c['constructor_parameters']
                    declared = contract['constructor']['parameters']
                    if set(declared) != {p['name'] for p in init_params}:
                        raise ContractError('Constructor contract must match all source constructor parameters.')
                elif 'constructor' in contract:
                    raise ContractError('Module functions cannot declare constructor parameters.')
            except (ContractError, TypeError, ValueError) as exc:
                raise WorkbenchError(f'接口契约无效 {identifier}：{exc}') from exc
            c['reviewed_contract'] = contract
            c['contract_status'] = 'reviewed_source_bound'
        elif not c['eligible']:
            raise WorkbenchError(f'暂不能自动封装 {identifier}：' + '; '.join(c['reasons']))
        if c['execution_restrictions']:
            raise WorkbenchError(f'函数需要人工适配 {identifier}：' + '; '.join(c['execution_restrictions']))
        selected.append(c)
    licenses = [p for p in root.iterdir() if p.is_file() and not p.is_symlink() and p.name.lower().startswith(('license', 'copying', 'notice'))]
    if not licenses:
        raise WorkbenchError('未找到 LICENSE / COPYING / NOTICE；请先核实分发许可，再生成包。')
    dependencies, dependency_warnings = _dependency_declarations(root)
    adapter_kinds = {spec.get('adapter', {}).get('kind') for c in selected for spec in c.get('reviewed_contract', {}).get('parameters', {}).values()}
    adapter_kinds |= {spec.get('adapter', {}).get('kind') for c in selected for spec in c.get('reviewed_contract', {}).get('constructor', {}).get('parameters', {}).values()}
    declared_names = {re.split(r'[\[<>=!~]', x, 1)[0].lower().replace('_', '-') for x in dependencies}
    if ('ndarray' in adapter_kinds or any(rule['op'] == 'finite_moments' for c in selected for rule in c.get('reviewed_contract', {}).get('rules', []))) and 'numpy' not in declared_names:
        dependencies.append('numpy>=1.24,<3')
    if 'dataframe' in adapter_kinds and 'pandas' not in declared_names:
        dependencies.append('pandas>=2,<4')
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
        contract = c.get('reviewed_contract')
        tool_name = contract.get('tool_name') if contract and contract.get('tool_name') else re.sub(r'[^a-zA-Z0-9_]', '_', c['module']) + '__' + c['function'].replace('.', '__')
        if tool_name in tool_names:
            raise WorkbenchError('规范化后的工具名称冲突，请分别生成。')
        tool_names.append(tool_name)
        arguments, bindings = [], []
        keyword_started = False
        for p in c['parameters']:
            if p['kind'] == 'keyword_only' and not keyword_started:
                arguments.append('*')
                keyword_started = True
            schema = contract['parameters'][p['name']]['schema'] if contract else _type_schema(p['type'])
            description = schema.get('description') or p.get('description') or f"Original source parameter {p['name']}."
            text = (f"{p['name']}: Annotated[Any, BeforeValidator(partial(before_validate, schema={schema!r})), Field(description={description!r}, json_schema_extra={schema!r})]"
                    if contract else f"{p['name']}: Annotated[{p['type']}, BeforeValidator(partial(before_validate, schema={schema!r})), {p['description']!r}]")
            if not p['required']:
                text += ' = ' + repr(p['default'])
            arguments.append(text)
            bindings.append(f"{p['name']!r}: {p['name']}")
        reference = f"source://sha256/{c['source_sha256']}/{c['path']}#{c['function']}:L{c['line']}"
        doc = f"Call the original {c['module']}.{c['function']} research function.\nJSON-compatible inputs → native result and a source-traceable JSON artifact."
        if contract and contract.get('description'):
            doc += ' ' + ' '.join(contract['description'].split())
        constructor_argument = 'None'
        if contract and 'constructor' in contract:
            ctor_schema = {'type': 'object', 'properties': {k: v['schema'] for k, v in contract['constructor']['parameters'].items()},
                           'required': list(contract['constructor']['parameters']), 'additionalProperties': False}
            # Add a required keyword-only lifecycle input even after source defaults.
            if not keyword_started:
                arguments.append('*')
            arguments.append(f"constructor: Annotated[Any, BeforeValidator(partial(before_validate, schema={ctor_schema!r})), Field(description='Reviewed constructor arguments for a new instance per invocation.', json_schema_extra={ctor_schema!r})]")
            constructor_argument = 'constructor'
        server += f"@mcp.tool()\nasync def {tool_name}({', '.join(arguments)}) -> dict:\n    {doc!r}\n    return await _invoke({c['module']!r}, {c['function']!r}, {{{', '.join(bindings)}}}, {reference!r}, {c['path']!r}, {c['source_sha256']!r}, {contract!r}, {constructor_argument})\n\n"
        c['tool_name'] = tool_name
    server += "if __name__ == '__main__':\n    mcp.run()\n"
    (project / 'server.py').write_text(server, encoding='utf-8')
    helper = project / 'code_contracts.py'
    shutil.copyfile(Path(__file__).with_name('code_contracts.py'), helper)
    shutil.copyfile(APP_ROOT/'LICENSE',project/'LICENSE')
    (project / 'contracts.json').write_text(json.dumps({'schema_version': 1, 'tools': reviewed}, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    requirements = ['fastmcp==4.0.3'] + dependencies
    (project / 'requirements.txt').write_text('\n'.join(requirements) + '\n', encoding='utf-8')
    manifest = {'schema_version': 1, 'name': name, 'repository_path_at_build': str(root),
                'repository_identity': _git_identity(root),
                'license_files': [p.name for p in licenses],
                'selected_tools': selected, 'copied_source_files': copied,
                'declared_dependencies': dependencies, 'dependency_warnings': dependency_warnings,
                'server_sha256': _hash(project / 'server.py'), 'execution_performed': False,
                'runtime_adapter_sha256': _hash(helper), 'contracts_sha256': _hash(project / 'contracts.json'),
                'invocation_script': 'call_tool.py','adapter_license':'AGPL-3.0-only',
                'license_sha256':_hash(project/'LICENSE'),
                'verification_status': 'generated_not_executed_or_independently_verified',
                'input_validation': 'strict_json_types',
                'algorithm_changes': False, 'local_model_execution_allowed': False,
                'execution_policy': {'blocked_model_packages': sorted(MODEL_PACKAGES),
                                     'gpu_visibility': 'disabled', 'hf_offline': True,
                                     'not_a_security_sandbox': True},
                'source_data_files_included': False,
                'limits': ['Python public sync/async functions; reviewed ndarray, DataFrame, managed Path, and instance-method contracts.',
                           'External data, native libraries and dynamic dependencies require separate review.',
                           'Generated source runs with caller permissions only on explicit MCP invocation; static review is not a sandbox.',
                           'No claim of scientific correctness until upstream comparison and independent acceptance.']}
    (project / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    usage_template = (Path(__file__).resolve().parents[1] / 'docs/MCP_USAGE.md').read_text(encoding='utf-8')
    (project / 'USAGE.md').write_text(usage_template.replace('# MCP 工具使用指南', '# ' + name, 1), encoding='utf-8')
    (project / 'call_tool.py').write_text(
        "\"\"\"Explicit recipient acceptance entry point, never run during conversion.\"\"\"\n"
        "import asyncio, json, sys\nfrom pathlib import Path\nfrom fastmcp import Client\n"
        "async def main():\n    arguments = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "    async with Client(Path(__file__).with_name('server.py')) as client:\n"
        "        result = await client.call_tool(sys.argv[1], arguments)\n        print(json.dumps(result.data, ensure_ascii=False, indent=2, allow_nan=False))\n"
        "if __name__ == '__main__':\n    asyncio.run(main())\n", encoding='utf-8')
    manifest['invocation_script_sha256'] = _hash(project / 'call_tool.py')
    (project / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
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
    invocation = manifest.get('invocation_script', 'call_tool.py' if (project / 'call_tool.py').is_file() else 'example_call.py')
    if invocation not in {'call_tool.py', 'example_call.py'}:
        raise WorkbenchError('调用入口文件不在支持清单中。')
    if manifest.get('invocation_script_sha256') and _hash(project / invocation) != manifest['invocation_script_sha256']:
        raise WorkbenchError('调用入口哈希不匹配。')
    allowed = [project / p for p in ('server.py', 'requirements.txt', 'manifest.json', 'USAGE.md', invocation)]
    if 'license_sha256' in manifest:
        license_file=project/'LICENSE'
        if license_file.is_symlink() or not license_file.is_file() or _hash(license_file)!=manifest['license_sha256']:
            raise WorkbenchError('适配器许可文件哈希不匹配。')
        allowed.append(license_file)
    for filename, field in [('code_contracts.py', 'runtime_adapter_sha256'), ('contracts.json', 'contracts_sha256')]:
        if field in manifest:
            path = project / filename
            if path.is_symlink() or not path.is_file() or _hash(path) != manifest[field]:
                raise WorkbenchError('运行适配器或接口契约哈希不匹配。')
            allowed.append(path)
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


def test_mcp(project_path, python_executable, tool_name: str, arguments: dict, native_environment: dict | None = None) -> dict:
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
    for filename, field in [('code_contracts.py', 'runtime_adapter_sha256'), ('contracts.json', 'contracts_sha256')]:
        if field in manifest:
            p = project / filename
            if p.is_symlink() or not p.is_file() or _hash(p) != manifest[field]:
                raise WorkbenchError('运行适配器或接口契约哈希不匹配。')
    if not isinstance(arguments, dict):
        raise WorkbenchError('工具参数必须为 JSON 对象。')
    native_names={'P2A_NATIVE_RUNTIME','P2A_R_PROJECT','LC_ALL'}
    if native_environment is not None and (not isinstance(native_environment,dict) or
        set(native_environment)-native_names or any(not isinstance(v,str) or '\x00' in v for v in native_environment.values())):
        raise WorkbenchError('原生环境只接受程序路径、R 项目路径和区域设置。')
    native_env={key:os.environ[key] for key in native_names if key in os.environ}
    native_env.update(native_environment or {})
    source_timeout=candidates[tool_name].get('timeout_seconds',180) if manifest.get('route')=='command' else 25
    if type(source_timeout) is not int or not 1<=source_timeout<=180:
        raise WorkbenchError('原生调用时限超出支持范围。')
    transport_timeout=source_timeout+30 if manifest.get('route')=='command' else 25
    process_timeout=transport_timeout+5
    serialized = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    _secret_check(serialized, '测试参数')
    credential_fields={'apikey','secret','password','token','accesstoken','refreshtoken','authorization','clientsecret'}
    if any(re.sub(r'[_\s-]','',key).lower() in credential_fields for key in arguments):
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
                    raise WorkbenchError('源码依赖不受支持的执行框架，请选择符合运行要求的函数。')
    result_dir = project / 'verification' / uuid.uuid4().hex
    result_dir.mkdir(parents=True)
    (result_dir / 'arguments.json').write_text(serialized + '\n', encoding='utf-8')
    runner = result_dir / 'runner.py'
    response = result_dir / 'response.json'
    runner.write_text(
        'import asyncio, json\nfrom pathlib import Path\nfrom fastmcp import Client\nfrom fastmcp.client.transports import PythonStdioTransport\n'
        f'SERVER = {str(project / "server.py")!r}\nPYTHON = {str(interpreter)!r}\nTOOL = {tool_name!r}\n'
        f'ARGS = {str(result_dir / "arguments.json")!r}\nRESPONSE = {str(response)!r}\nNATIVE_ENV = {native_env!r}\n'
        'async def invoke():\n    transport = PythonStdioTransport(SERVER, python_cmd=PYTHON, cwd=str(Path(SERVER).parent), env=NATIVE_ENV)\n'
        '    async with Client(transport) as client:\n        inventory = await client.list_tools()\n'
        '        result = await client.call_tool(TOOL, json.loads(Path(ARGS).read_text(encoding="utf-8")))\n'
        '        Path(RESPONSE).write_text(json.dumps({"tools": [t.name for t in inventory], "data": result.data}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")\n'
        f'async def main():\n    await asyncio.wait_for(invoke(), timeout={transport_timeout})\nasyncio.run(main())\n', encoding='utf-8')
    record = {'tool_name': tool_name, 'arguments': arguments, 'source': candidates[tool_name],
              'python_executable': str(interpreter), 'transport': 'real_stdio_fastmcp_client',
              'scientific_validation': 'not_established_by_smoke_call', 'local_model_called': False,
              'verification_path': str(result_dir.resolve())}
    try:
        completed = subprocess.run([str(interpreter), str(runner)], cwd=str(project),
                                   capture_output=True, text=True, encoding='utf-8', errors='replace',
                                   timeout=process_timeout, check=False)
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
        record.update(decision='timeout', error=f'MCP 调用超过 {process_timeout} 秒；不计成功。')
    report = result_dir / 'test-result.json'
    report.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    record['report_path'] = str(report.resolve())
    return record
