"""Current/history graph completion parity across actual filesystem adapters."""
import copy
import json
import subprocess
from pathlib import Path
import pytest
from tools.workbench import Workbench


@pytest.mark.parametrize('mutation', [None,'digest','start','end','state','exit','schema','old_run'])
@pytest.mark.parametrize('historical',[False,True])
def test_graph_status_history_and_js_have_same_scope(tmp_path, mutation, historical):
    log=tmp_path/'log';log.mkdir()
    graph={'id':'g','nodes':[{'id':'n'}],'edges':[]}
    run={'run_id':'r','started_at':100,'status':'completed','route':{'mode':'graph','graph_id':'g','graph_digest':'a'*64,'graph_snapshot':graph},'exit_file':str(log/'r.exit')}
    result={'schema_version':1,'run_id':'r','graph_id':'g','graph_digest':'a'*64,'started_at':101,'ended_at':110,'status':'completed','task_delivery_verified':False,'graph_states':{'n':'ok'},'activated_edges':[]}
    if mutation=='digest': result['graph_digest']='b'*64
    if mutation=='start': result['started_at']=99
    if mutation=='end': result['ended_at']=99
    if mutation=='state': result['graph_states']={'n':'failed'}
    if mutation=='schema': result['schema_version']=True
    if mutation=='old_run': result['run_id']='old'
    (log/'r.exit').write_text('1' if mutation=='exit' else '0')
    (log/'graph-result-r.json').write_text(json.dumps(result))
    (log/'app-run.json').write_text(json.dumps({'run_id':'new','started_at':200,'status':'running'} if historical else run))
    (log/'vscode-sessions.jsonl').write_text(json.dumps(run))
    (log/'events.jsonl').write_text(json.dumps({'type':'model_attempt','run_id':'r','attempt_id':'r:1','actual_provider':'codex','outcome':'ok'}))
    expected='completed' if mutation is None else 'failed' if mutation=='exit' else 'incomplete'
    status=Workbench(tmp_path).status('r')
    assert status['task_status']==expected
    assert status['task_result']['task_delivery_verified'] is False
    base=Path(__file__).resolve().parents[2]/'vscode-extension'
    script='const fs=require("fs"),r=require(process.argv[1]+"/routing"),d=require(process.argv[1]+"/dashboard");const root=process.argv[2],run=JSON.parse(fs.readFileSync(root+"/log/vscode-sessions.jsonl","utf8"));d.loadProjectHistory(root).then(h=>console.log(JSON.stringify({current:r.taskResult(root,run),history:h.runs[0]})))'
    js=json.loads(subprocess.run(['node','-e',script,str(base),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',check=True).stdout)
    assert js['current']['status']==expected
    assert js['history']['task_status']==expected
    assert js['history']['execution_mode']=='graph'
    assert js['history']['task_delivery_verified'] is False
