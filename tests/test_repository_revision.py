from types import SimpleNamespace
import pytest
import repository_fetch as fetch

def setup(monkeypatch,digest='a'*40,fail_step=None):
    commands=[]
    monkeypatch.setattr(fetch.shutil,'which',lambda name:'git')
    def run(command,**kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1 if fail_step in command else 0,
                               stdout=b'retained stdout',stderr=b'retained stderr')
    monkeypatch.setattr(fetch.subprocess,'run',run)
    monkeypatch.setattr(fetch.subprocess,'check_output',lambda *a,**k:digest+'\n')
    return commands

def test_full_commit_is_fetched_detached_and_verified(tmp_path,monkeypatch):
    commands=setup(monkeypatch)
    result=fetch.fetch_repository('https://github.com/owner/repo',tmp_path,ref='a'*40)
    assert '--no-checkout' in commands[0] and '--branch' not in commands[0]
    assert commands[1][-5:]==['--depth','1','--no-tags','origin','a'*40]
    assert commands[2][-3:]==['checkout','--detach','a'*40]
    assert all('core.autocrlf=false' in c for c in commands)
    assert result['commit']=='a'*40 and len(list(tmp_path.glob('*-stdout.log')))==3

def test_commit_mismatch_is_not_success(tmp_path,monkeypatch):
    setup(monkeypatch,digest='b'*40)
    with pytest.raises(ValueError,match='does not match'):
        fetch.fetch_repository('https://github.com/owner/repo',tmp_path,ref='a'*40)
    assert len(list(tmp_path.glob('*-failure.json')))==1

def test_fetch_failure_is_retained_without_checkout(tmp_path,monkeypatch):
    commands=setup(monkeypatch,fail_step='fetch')
    with pytest.raises(ValueError):fetch.fetch_repository('https://github.com/owner/repo',tmp_path,ref='a'*40)
    assert len(commands)==2 and len(list(tmp_path.glob('*-stderr.log')))==2

def test_branch_keeps_branch_clone(tmp_path,monkeypatch):
    commands=setup(monkeypatch)
    fetch.fetch_repository('https://github.com/owner/repo',tmp_path,ref='stable')
    assert len(commands)==1 and commands[0][commands[0].index('--branch')+1]=='stable'
