"""Bounded native entry execution for explicitly reviewed scientific interfaces.

Input validation and source hashes are safeguards, not a security sandbox.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

from code_contracts import ContractError, validate_value, validate_rules


class NativeExecutionError(RuntimeError):
    """Native failure with a retained invocation directory."""


_COMMAND_PYTHON_BOOTSTRAP = '''import importlib.abc, sys, runpy
from pathlib import Path
blocked = {'torch','tensorflow','transformers','jax','diffusers','ollama','llama_cpp','onnxruntime','sentence_transformers','vllm','lmstudio','mlx'}
class ImportPolicy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise ImportError('Model execution framework is outside this native runtime policy: ' + fullname)
sys.meta_path.insert(0, ImportPolicy())
entry = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(entry).resolve().parent))
runpy.run_path(entry, run_name='__main__')
'''


def _command_json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ContractError('Duplicate JSON property: ' + key)
            value[key] = item
        return value

    def constant(value):
        raise ContractError('Nonfinite JSON constant: ' + value)

    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    # JSON's 1e999 is a legal token but its float representation is not finite.
    json.dumps(value, allow_nan=False)
    return value


def _command_path(base, relative, must_exist=True):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or '\x00' in relative:
        raise ContractError('Entry/artifact paths require nonempty POSIX relative paths.')
    rel = Path(relative)
    if rel.is_absolute() or any(x in {'', '.', '..'} for x in relative.split('/')):
        raise ContractError('Entry/artifact paths cannot traverse parent directories.')
    base = base.resolve()
    path = base / rel
    for item in [path, *path.parents]:
        if item == base:
            break
        if item.is_symlink():
            raise ContractError('Entry/artifact paths cannot traverse symbolic links.')
    if base not in path.resolve().parents:
        raise ContractError('Entry/artifact path escapes its managed root.')
    if must_exist and not path.is_file():
        raise ContractError('Required entry/artifact file is missing: ' + relative)
    return path.resolve()


def _command_dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _command_bytes(value):
    return value if isinstance(value, bytes) else (value or '').encode('utf-8', errors='replace')


def _command_process(argv, cwd, environment, timeout, run, label):
    """Run an owned child and terminate its tree before draining timed-out pipes."""
    options = {'cwd': cwd, 'env': environment, 'shell': False,
               'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    process = subprocess.Popen(argv, **options)
    _command_dump(run / (label + '-process.json'), {'pid': process.pid, 'argv': argv,
                  'timeout_seconds': timeout, 'new_process_group': os.name != 'nt'})
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as original:
        # Rscript may spawn R with inherited pipes. Killing only the launcher and
        # then doing unbounded communicate() would wait for that child to finish.
        termination = {'owned_pid': process.pid, 'platform': os.name}
        try:
            if os.name == 'nt' and process.poll() is None:
                killer = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'taskkill.exe'
                command = [str(killer), '/PID', str(process.pid), '/T', '/F']
                killed = subprocess.run(command, capture_output=True, shell=False,
                                        timeout=3, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
                (run / (label + '-termination-stdout.bin')).write_bytes(killed.stdout)
                (run / (label + '-termination-stderr.bin')).write_bytes(killed.stderr)
                termination.update(command=command, exit_code=killed.returncode)
            elif os.name != 'nt':
                os.killpg(process.pid, signal.SIGKILL)
                termination['signal'] = 'SIGKILL owned process group'
            else:
                termination['launcher_already_exited'] = True
        except Exception as exc:
            termination.update(error_type=type(exc).__name__, error=str(exc))
        if process.poll() is None:
            process.kill()
        try:
            stdout, stderr = process.communicate(timeout=3)
            termination['pipes_drained'] = True
        except subprocess.TimeoutExpired as drain:
            # A failed OS tree termination must not turn the configured deadline
            # into an unbounded wait. Keep available output and report the failure.
            stdout = drain.output or original.output or b''
            stderr = drain.stderr or original.stderr or b''
            termination['pipes_drained'] = False
            # Windows reader threads can hold the pipe lock until a surviving
            # child closes it; close() here could itself become an unbounded wait.
            termination['unresolved_pipes_left_to_reader_threads'] = True
        _command_dump(run / (label + '-termination.json'), termination)
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from original


def _command_run(root, tool, values):
    root = Path(root).resolve()
    run = root / 'artifacts' / uuid.uuid4().hex
    run.mkdir(parents=True, exist_ok=False)
    output = run / 'native'
    output.mkdir()
    request = run / 'request.json'
    record = {'tool_name': tool['tool_name'], 'status': 'started', 'algorithm_changes': False,
              'reference': tool['reference'], 'source_files': tool['source_files'],
              'timeout_seconds': tool['timeout_seconds'], 'output_directory': str(output)}
    started = time.monotonic()
    try:
        _command_dump(request, values)
        if set(values) != set(tool['parameters']):
            raise ContractError('Parameters differ from the reviewed native interface.')
        for name, spec in tool['parameters'].items():
            validate_value(values[name], spec['schema'], name)
        validate_rules(values, tool.get('rules', []))
        repo = root / 'vendor' / 'repo'
        for relative, expected in tool['source_files'].items():
            path = _command_path(repo, relative)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ContractError('Reviewed native source hash changed: ' + relative)
        # The manifest and contracts are independently hashed at build time.
        manifest = _command_json((root / 'manifest.json').read_text(encoding='utf-8'))
        for filename, field in [('server.py', 'server_sha256'), ('code_contracts.py', 'runtime_adapter_sha256'),
                                ('contracts.json', 'contracts_sha256')]:
            path = _command_path(root, filename)
            if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[field]:
                raise ContractError('Generated native project hash changed: ' + filename)
        runtime = tool['runtime']
        configured = os.environ.get(runtime['env'], '')
        executable = Path(configured).expanduser()
        if not configured or not executable.is_absolute() or executable.is_symlink() or not executable.is_file():
            raise ContractError('Set ' + runtime['env'] + ' to an absolute native executable path.')
        executable = executable.resolve()
        if executable.stem.lower() in {'cmd', 'powershell', 'pwsh', 'sh', 'bash', 'zsh', 'fish', 'csh', 'wscript', 'cscript', 'rundll32'}:
            raise ContractError('Shell and script-host executables are not scientific native runtimes.')
        if runtime['kind'] in {'python', 'r'} and not executable.name.lower().startswith('python' if runtime['kind'] == 'python' else 'rscript'):
            raise ContractError('Native executable basename does not match its reviewed runtime kind.')
        if runtime.get('sha256') and hashlib.sha256(executable.read_bytes()).hexdigest() != runtime['sha256']:
            raise ContractError('Native executable differs from the reviewed binary hash.')
        environment = os.environ.copy()
        environment.update(CUDA_VISIBLE_DEVICES='', NVIDIA_VISIBLE_DEVICES='none', HF_HUB_OFFLINE='1',
                           TRANSFORMERS_OFFLINE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        probe = _command_process([str(executable), *runtime['version_args']], repo, environment,
                                 min(10, tool['timeout_seconds']), run, 'runtime')
        (run / 'runtime-stdout.bin').write_bytes(probe.stdout)
        (run / 'runtime-stderr.bin').write_bytes(probe.stderr)
        version = (probe.stdout + probe.stderr).decode('utf-8', errors='replace')
        if probe.returncode != 0 or runtime['version_contains'] not in version:
            raise ContractError('Native runtime identity check failed; no scientific call was made.')
        entry = _command_path(repo, tool['entry_file'])
        substitutions = {'{entry_file}': str(entry), '{request_file}': str(request), '{output_dir}': str(output)}
        argv = [str(executable)] + [substitutions.get(item, item) for item in tool['argv']]
        if runtime['kind'] == 'python':
            argv = [str(executable), '-c', _COMMAND_PYTHON_BOOTSTRAP, *argv[1:]]
        record.update(command=argv, working_directory=str(repo), runtime_path=str(executable), runtime_version=version.strip())
        _command_dump(run / 'invocation.json', record)
        completed = _command_process(argv, repo, environment, tool['timeout_seconds'], run, 'native')
        (run / 'stdout.bin').write_bytes(completed.stdout)
        (run / 'stderr.bin').write_bytes(completed.stderr)
        record['exit_code'] = completed.returncode
        if completed.returncode != 0:
            raise NativeExecutionError('Native entry returned exit code ' + str(completed.returncode) + '.')
        result_path = _command_path(output, tool['result_file'])
        if result_path.stat().st_size > 10 * 1024 * 1024:
            raise ContractError('Native JSON result exceeds 10 MB; return scientific arrays as artifacts.')
        result = _command_json(result_path.read_text(encoding='utf-8-sig'))
        if not isinstance(result, dict):
            raise ContractError('Native result must be a JSON object.')
        if 'result_schema' in tool:
            validate_value(result, tool['result_schema'], 'result')
        artifacts = []
        # Preserve native results exactly; include all regular output files, never links.
        for path in sorted(output.rglob('*')):
            if path.is_symlink():
                raise ContractError('Native output contains an unsupported symbolic link.')
            if path.is_file():
                valid = _command_path(output, path.relative_to(output).as_posix())
                artifacts.append({'description': 'Native output ' + path.relative_to(output).as_posix(),
                                  'path': str(valid), 'sha256': hashlib.sha256(valid.read_bytes()).hexdigest()})
        record.update(status='completed', elapsed_seconds=time.monotonic() - started, artifacts=artifacts)
        _command_dump(run / 'invocation.json', record)
        return {'message': 'Original native entry completed.', 'reference': tool['reference'], 'data': result,
                'artifacts': artifacts, 'invocation_path': str(run / 'invocation.json'),
                'runtime': {'path': str(executable), 'version': version.strip()}, 'algorithm_changes': False}
    except Exception as exc:
        if isinstance(exc, subprocess.TimeoutExpired):
            (run / 'stdout.bin').write_bytes(_command_bytes(exc.stdout))
            (run / 'stderr.bin').write_bytes(_command_bytes(exc.stderr))
        record.update(status='timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'execution_error',
                      elapsed_seconds=time.monotonic() - started, error_type=type(exc).__name__, error=str(exc))
        _command_dump(run / 'invocation.json', record)
        raise NativeExecutionError('Native invocation failed (' + type(exc).__name__ + '); retained record: ' + str(run / 'invocation.json')) from exc


async def invoke_command(root, tool, values):
    return await asyncio.to_thread(_command_run, root, tool, values)
