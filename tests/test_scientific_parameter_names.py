from pathlib import Path
import sys
import pytest
from code_workbench import build_mcp,test_mcp as call_mcp,WorkbenchError

def generated(tmp_path,source,symbol):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'LICENSE').write_text('MIT License\n',encoding='utf-8')
    (repo/'measurements.py').write_text(source,encoding='utf-8')
    build=build_mcp(repo,['measurements:'+symbol],'Measurements',tmp_path/'projects')
    return Path(build['project_path'])

def test_scientific_token_fields_are_real_mcp_inputs(tmp_path):
    project=generated(tmp_path,'def count_tokens(token_ids: list[int], token_count: int):\n    return {"ids": token_ids, "actual": len(token_ids), "expected": token_count}\n','count_tokens')
    result=call_mcp(project,sys.executable,'measurements__count_tokens',{'token_ids':[3,9,12],'token_count':3})
    assert result['decision']=='completed',result
    assert result['result']['data']['native_result']=={'ids':[3,9,12],'actual':3,'expected':3}

def test_actual_credential_field_stays_blocked_before_call(tmp_path):
    project=generated(tmp_path,'def authorization_value(api_key: str):\n    return api_key\n','authorization_value')
    with pytest.raises(WorkbenchError,match='认证密钥'):
        call_mcp(project,sys.executable,'measurements__authorization_value',{'api_key':'synthetic-value'})
    assert not (project/'verification').exists()
