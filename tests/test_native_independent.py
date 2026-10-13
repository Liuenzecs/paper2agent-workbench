"""Fresh verifier checks public native service/CLI packaging and actual stdio."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import asyncio

import pytest
from command_workbench import build_command_mcp
from code_workbench import package_mcp, test_mcp as explicit_test, WorkbenchError
from lab_service import LabService

ENTRY='''import json, os, sys, time
from pathlib import Path
p=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
out=Path(sys.argv[2])
(out/'native-before.txt').write_text('retained',encoding='utf-8')
if p['fail']: sys.exit(13)
if p['delay']: time.sleep(3)
value={'measurements':p['measurements'], 'credential_forwarded':any(k in os.environ for k in ('PAPERQA_CHAT_API_KEY','PAPERQA_EMBEDDING_API_KEY','OPENAI_API_KEY','P2A_UNREVIEWED_ENV'))}
(out/'result.json').write_text(json.dumps(value),encoding='utf-8')
'''

def reviewed(repo):
    return {'schema_version':1,'route':'command','tools':{'native_measurements':{
      'reviewed':True,'entry_file':'entry.py','source_files':{'entry.py':hashlib.sha256((repo/'entry.py').read_bytes()).hexdigest()},
      'parameters':{'measurements':{'schema':{'type':'array','items':{'type':'number'},'minItems':2},'description':'Supplied measurements.'},
                    'fail':{'schema':{'type':'boolean'},'description':'Retained native error.','default':False},
                    'delay':{'schema':{'type':'boolean'},'description':'Retained native timeout.','default':False}},
      'runtime':{'kind':'python','env':'P2A_NATIVE_RUNTIME','version_args':['--version'],'version_contains':'Python 3.11'},
      'argv':['{entry_file}','{request_file}','{output_dir}'],'result_file':'result.json','timeout_seconds':1,
      'description':'Record measurements through original native code.','reference':'source://independent-verifier/entry.py',
      'native_dependencies':['Python 3.11 standard library']}}}

@pytest.fixture
def prepared(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'entry.py').write_text(ENTRY,encoding='utf-8')
    (repo/'LICENSE.txt').write_text('MIT independent test fixture',encoding='utf-8')
    contract=reviewed(repo)
    build=build_command_mcp(repo,contract,'Independent',tmp_path/'g')
    return repo,contract,Path(build['project_path'])

def run(project,args,native_env=None):
    return explicit_test(project,sys.executable,'native_measurements',args,native_env or {'P2A_NATIVE_RUNTIME':sys.executable})

def test_real_child_does_not_receive_remote_keys(prepared,monkeypatch):
    for key in ('PAPERQA_CHAT_API_KEY','PAPERQA_EMBEDDING_API_KEY','OPENAI_API_KEY','P2A_UNREVIEWED_ENV'):
        monkeypatch.setenv(key,'independent synthetic marker')
    result=run(prepared[2],{'measurements':[2.3,-8.9]})
    assert result['decision']=='completed',result
    assert result['result']['data']['data']=={'measurements':[2.3,-8.9],'credential_forwarded':False}
    runner=Path(result['verification_path'])/'runner.py'
    assert 'independent synthetic marker' not in runner.read_text(encoding='utf-8')

def test_disallowed_environment_is_rejected_before_transport(prepared):
    with pytest.raises(WorkbenchError):
        run(prepared[2],{'measurements':[1.,2.]},{'P2A_NATIVE_RUNTIME':sys.executable,'OPENAI_API_KEY':'synthetic'})

def test_repeat_outputs_and_native_error_retained(prepared):
    project=prepared[2]
    first=run(project,{'measurements':[1.2,8.7]})
    second=run(project,{'measurements':[-1.2,2.7]})
    assert first['decision']==second['decision']=='completed'
    assert first['result']['data']['invocation_path']!=second['result']['data']['invocation_path']
    error=run(project,{'measurements':[1.2,8.7],'fail':True})
    assert error['decision']=='execution_error'
    records=[json.loads(p.read_text(encoding='utf-8')) for p in (project/'artifacts').glob('*/invocation.json')]
    bad=[r for r in records if r.get('exit_code')==13]
    assert len(bad)==1
    assert (Path(bad[0]['output_directory'])/'native-before.txt').read_text(encoding='utf-8')=='retained'

def test_native_timeout_completes_as_error_with_diagnostics(prepared):
    result=run(prepared[2],{'measurements':[1.2,8.7],'delay':True})
    assert result['decision']=='execution_error'
    records=[json.loads(p.read_text(encoding='utf-8')) for p in (prepared[2]/'artifacts').glob('*/invocation.json')]
    assert len(records)==1 and records[0]['status']=='timeout'
    assert (Path(records[0]['output_directory'])/'native-before.txt').is_file()

def test_adapter_and_upstream_license_in_actual_zip(prepared):
    package=package_mcp(prepared[2])
    import zipfile
    with zipfile.ZipFile(package['zip_path']) as archive:
        root=prepared[2].name+'/'
        assert archive.read(root+'LICENSE')==(Path(__file__).parents[1]/'LICENSE').read_bytes()
        assert archive.read(root+'vendor/repo/LICENSE.txt')==b'MIT independent test fixture'
        assert not any('verification/' in p or 'artifacts/' in p for p in archive.namelist())

def test_missing_adapter_license_refuses_packaging(prepared):
    (prepared[2]/'LICENSE').rename(prepared[2]/'LICENSE-kept-original')
    with pytest.raises(WorkbenchError): package_mcp(prepared[2])

def test_actual_public_service_native_route(prepared,tmp_path):
    service=LabService(tmp_path/'service')
    generated=service.build_code_mcp(str(prepared[0]),['native_measurements'],'ServiceNative',prepared[1])
    result=service.test_code_mcp(generated['project_path'],'native_measurements',{'measurements':[3.9,4.1]},python_executable=sys.executable,native_runtime=sys.executable)
    assert result['decision']=='completed',result
    assert result['result']['data']['data']['measurements']==[3.9,4.1]

def test_actual_public_cli_native_route_and_call(prepared,tmp_path):
    contract_file=tmp_path/'contract.json'
    contract_file.write_text(json.dumps(prepared[1]),encoding='utf-8')
    app=Path(__file__).parents[1]
    env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
    process=subprocess.run([sys.executable,str(app/'src/lab_cli.py'),'--data-dir',str(tmp_path/'cli'),'code-mcp',str(prepared[0]),'--name','CLINative','--function','native_measurements','--contracts-file',str(contract_file)],capture_output=True,env=env,timeout=20)
    (tmp_path/'cli-build.stdout.bin').write_bytes(process.stdout)
    (tmp_path/'cli-build.stderr.bin').write_bytes(process.stderr)
    assert process.returncode==0,process.stderr
    generated=json.loads(process.stdout.decode('utf-8'))
    args=tmp_path/'args.json';args.write_text(json.dumps({'measurements':[6.1,-8.0]}),encoding='utf-8')
    process=subprocess.run([sys.executable,str(app/'src/lab_cli.py'),'--data-dir',str(tmp_path/'cli'),'code-test',generated['project_path'],'--python',sys.executable,'--tool','native_measurements','--arguments-file',str(args),'--native-runtime',sys.executable],capture_output=True,env=env,timeout=40)
    (tmp_path/'cli-call.stdout.bin').write_bytes(process.stdout)
    (tmp_path/'cli-call.stderr.bin').write_bytes(process.stderr)
    assert process.returncode==0,process.stderr
    result=json.loads(process.stdout.decode('utf-8'))
    assert result['decision']=='completed',result
    assert result['result']['data']['data']['measurements']==[6.1,-8.0]

def test_original_bound_source_mutation_stops_call(prepared):
    (prepared[2]/'vendor/repo/entry.py').write_text('raise RuntimeError("altered")',encoding='utf-8')
    with pytest.raises(WorkbenchError): run(prepared[2],{'measurements':[2.,3.]})


@pytest.mark.skipif(not os.environ.get('P2A_TEST_RSCRIPT'),reason='Native Rscript must be explicitly configured for process-tree acceptance.')
def test_actual_rscript_child_tree_deadline(tmp_path):
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport
    executable=Path(os.environ['P2A_TEST_RSCRIPT']).resolve(strict=True)
    repo=tmp_path/'r-source';repo.mkdir()
    entry=repo/'entry.R'
    entry.write_text("args<-commandArgs(trailingOnly=TRUE)\nwriteLines('partial native output',file.path(args[[2]],'partial.txt'))\nSys.sleep(10)\nwriteLines('{}',file.path(args[[2]],'result.json'))\n",encoding='utf-8')
    contract={'schema_version':1,'route':'command','tools':{'native_sleep':{
        'reviewed':True,'entry_file':'entry.R','source_files':{'entry.R':hashlib.sha256(entry.read_bytes()).hexdigest()},'parameters':{},
        'argv':['--vanilla','{entry_file}','{request_file}','{output_dir}'],
        'runtime':{'kind':'r','env':'P2A_NATIVE_RUNTIME','version_args':['--version'],'version_contains':'Rscript (R) version ','sha256':hashlib.sha256(executable.read_bytes()).hexdigest()},
        'result_file':'result.json','timeout_seconds':1,'description':'Run a native child timeout check.','reference':'source://independent-native-timeout','native_dependencies':['Configured Rscript base runtime']}}}
    generated=build_command_mcp(repo,contract,'Deadline',tmp_path/'g')
    project=Path(generated['project_path'])
    async def invoke():
        env={'P2A_NATIVE_RUNTIME':str(executable),'LC_ALL':'English_United States.utf8' if os.name=='nt' else 'C.UTF-8'}
        async with Client(PythonStdioTransport(str(project/'server.py'),python_cmd=sys.executable,cwd=str(project),env=env)) as client:
            started=time.monotonic();result=await client.call_tool('native_sleep',{},raise_on_error=False);elapsed=time.monotonic()-started
            assert result.is_error and elapsed<6,elapsed
    asyncio.run(invoke())
    records=list((project/'artifacts').glob('*/invocation.json'));assert len(records)==1
    record=json.loads(records[0].read_text(encoding='utf-8'));assert record['status']=='timeout'
    run_dir=records[0].parent
    assert (run_dir/'native/partial.txt').read_text().strip()=='partial native output'
    assert not (run_dir/'native/result.json').exists()
    termination=json.loads((run_dir/'native-termination.json').read_text(encoding='utf-8'))
    process=json.loads((run_dir/'native-process.json').read_text(encoding='utf-8'))
    assert termination['owned_pid']==process['pid'] and termination['pipes_drained'] is True
    if os.name=='nt':assert termination['exit_code']==0 and termination['command'][1:]==['/PID',str(process['pid']),'/T','/F']
