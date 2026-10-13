import json
import pytest
import lab_cli


def test_ask_stdout_remains_machine_readable(monkeypatch, capsys):
    class Service:
        def __init__(self, data_dir):
            pass

        def ask(self, question, *args, progress):
            print("Provider SDK diagnostic")
            progress("正在检索")
            return {"answer": "已核验的答案", "decision": "answered"}

    monkeypatch.setattr(lab_cli, "LabService", Service)
    lab_cli.main(["--data-dir", "unused", "ask", "问题"])
    streams = capsys.readouterr()
    assert json.loads(streams.out) == {"answer": "已核验的答案", "decision": "answered"}
    assert "Provider SDK diagnostic" in streams.err and "正在检索" in streams.err


@pytest.mark.parametrize("decision", ["runtime_error", "blocked_evidence_review", "blocked_invalid_citations", "insufficient_source_coverage"])
def test_ask_failure_keeps_json_and_nonzero_exit(monkeypatch,capsys,decision):
    class Service:
        def __init__(self,data_dir):pass
        def ask(self,*args,**kwargs):return {"answer":"", "decision":decision}
    monkeypatch.setattr(lab_cli,"LabService",Service)
    with pytest.raises(SystemExit) as failure:lab_cli.main(["ask","问题"])
    assert failure.value.code==1 and json.loads(capsys.readouterr().out)["decision"]==decision


@pytest.mark.parametrize("decision", ["refused", "partially_answered"])
def test_ask_valid_abstention_has_zero_exit(monkeypatch,capsys,decision):
    class Service:
        def __init__(self,data_dir):pass
        def ask(self,*args,**kwargs):return {"answer":"Evidence is absent", "decision":decision}
    monkeypatch.setattr(lab_cli,"LabService",Service)
    lab_cli.main(["ask","问题"])
    assert json.loads(capsys.readouterr().out)["decision"]==decision
