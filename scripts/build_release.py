"""Build only tracked public source, preserving byte hashes and excluding personal data."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'Paper2Agent-Workbench-0.3.2'
raw_names = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0')
names = sorted(filter(None, raw_names))
if not names:
    raise ValueError('Stage the reviewed public source first')
blocked_dirs = {'personal', 'library', 'incoming', 'outputs', 'verification', 'exports', 'code-projects', 'repositories', 'dist', 'tmp', '__pycache__', '.git'}
secrets = re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|ark-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9_-]{24,})|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
files = {}
for name in names:
    path = ROOT / name
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
        raise ValueError('Linked/outside source is not publishable: ' + name)
    if any(p in blocked_dirs or p.startswith('.venv') for p in path.relative_to(ROOT).parts):
        raise ValueError('Personal/generated source is not publishable: ' + name)
    if path.suffix.lower() in {'.pdf', '.pem', '.key', '.sqlite', '.db', '.bin', '.safetensors', '.pt', '.pth'} or path.name.startswith(('.env', 'secrets')):
        raise ValueError('Private document/key/model is not publishable: ' + name)
    raw = path.read_bytes()
    if secrets.search(raw):
        raise ValueError('Credential pattern detected; value not displayed: ' + name)
    files[name] = raw
target = ROOT / 'dist' / (NAME + '.zip')
target.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
    for name, raw in files.items():
        archive.writestr(NAME + '/' + name, raw)
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
digest = hashlib.sha256(target.read_bytes()).hexdigest()
manifest = {'archive': target.name, 'sha256': digest, 'top_level': NAME,
            'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
            'source_commit_at_build': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip(),
            'personal_documents_included': False, 'credentials_included': False}
(ROOT / 'dist/0.3.2-package-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
(ROOT / 'dist/0.3.2-SHA256SUMS.txt').write_text(digest + '  ' + target.name + '\n', encoding='utf-8')
print(json.dumps({'archive': str(target), 'sha256': digest, 'file_count': len(files)}))
