"""Fetch a public GitHub source checkout without running its code or installers."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from paper_library import write_json


def fetch_repository(url, output_root, ref=""):
    if not isinstance(url, str) or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?", url):
        raise ValueError("Use a public https://github.com/owner/repository URL without credentials")
    if ref and (not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,120}", ref) or ".." in ref):
        raise ValueError("Use a plain branch or tag name")
    if not shutil.which("git"):
        raise ValueError("Install Git to fetch a repository, or scan an existing local checkout")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / uuid.uuid4().hex
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    command = ["git", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
               "clone", "--depth", "1", "--no-recurse-submodules"]
    if ref:
        command += ["--branch", ref]
    command += [url, str(target)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)
        if done.returncode:
            write_json(root / (target.name + "-failure.json"), {"url": url, "ref": ref, "decision": "fetch_failed", "exit_code": done.returncode})
            raise ValueError("Repository download failed; check the public URL, branch and network")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True, timeout=10).strip()
        metadata = {"repository_path": str(target), "url": url, "requested_ref": ref, "commit": commit,
                    "source_only": True, "models_run": False, "submodules_fetched": False, "lfs_weights_fetched": False}
        write_json(root / (target.name + "-source.json"), metadata)
        return metadata
    except subprocess.TimeoutExpired:
        write_json(root / (target.name + "-failure.json"), {"url": url, "decision": "fetch_timeout"})
        raise ValueError("Repository download timed out; the partial checkout is retained for inspection") from None
