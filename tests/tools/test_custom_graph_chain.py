"""Three controlled subprocesses, real runner/Workbench/JS; no paid providers."""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from tools import codex_runner as runner, scenario_graph
from tools.workbench import Workbench


def test_astra_opus_sol_graph_argv_handoff_and_evidence(tmp_path, monkeypatch):
    root=Path(__file__).resolve().parents[2]
    fake=tmp_path/'controlled_provider.py'
    fake.write_text('''import json,sys,os
from pathlib import Path
provider=sys.argv[1];args=sys.argv[2:];node=os.environ['CODEXAUTOAI_NODE_ID']
with open('observed.jsonl','a',encoding='utf-8') as f:f.write(json.dumps({'provider':provider,'node':node,'args':args})+'\\n')
text='CONTROLLED_HANDOFF_'+node
if provider=='claude':
 print(json.dumps({'type':'result','is_error':False,'result':text,'usage':{'input_tokens':20,'output_tokens':2}}))
else:
 print(json.dumps({'type':'item.completed','item':{'id':node,'type':'agent_message','text':text}}))
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':1,'cached_input_tokens':0}}))
''',encoding='utf-8')
    old_which=shutil.which
    monkeypatch.setattr(runner.shutil,'which',lambda name:sys.executable if name in ('codex','claude') else old_which(name))
    monkeypatch.setattr(runner,'resolve_codex',lambda provider:[sys.executable,str(fake),provider])
    for key in ('CODEXAUTOAI_ROUTED_WORKER','CODEXAUTOAI_ROUTED_PROVIDER','CODEXAUTOAI_ROUTED_ROLE'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('CODEXAUTOAI_PARENT_RUN_ID','custom-chain')
    nodes=[{'id':'astra','kind':'review','provider':'codex','model':'gpt-6-astra'},
           {'id':'opus','kind':'task','provider':'claude','model':'opus'},
           {'id':'sol','kind':'review','provider':'codex','model':'gpt-5.6-sol'}]
    graph=scenario_graph.save(tmp_path,{'version':1,'id':'custom','scenario':'coding','entry_node':'astra','nodes':nodes,'edges':[
        {'id':'a-o','source':'astra','target':'opus','condition':'success'},
        {'id':'o-s','source':'opus','target':'sol','condition':'success'}]})
    run={'run_id':'custom-chain','prompt':'Controlled original request','started_at':time.time()-1,'updated_at':time.time(),'status':'running','route':{'mode':'graph','graph_id':'custom','graph_digest':scenario_graph.digest(graph),'graph_snapshot':graph}}
    (tmp_path/'log/app-run.json').write_text(json.dumps(run),encoding='utf-8')
    assert runner.main(['--graph-id','custom','--graph-digest',scenario_graph.digest(graph),'--cwd',str(tmp_path),'--prompt',run['prompt'],'--retries','1','--timeout','10'])==0
    calls=[json.loads(s) for s in (tmp_path/'observed.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [(c['provider'],c['node']) for c in calls]==[('codex','astra'),('claude','opus'),('codex','sol')]
    for call,model in zip(calls,['gpt-6-astra','opus','gpt-5.6-sol']):
        flag='-m' if call['provider']=='codex' else '--model'
        assert call['args'][call['args'].index(flag)+1]==model
        assert any('saved explicit-primary or explicit-graph selection takes precedence' in arg for arg in call['args'])
    assert any('CONTROLLED_HANDOFF_astra' in arg for arg in calls[1]['args'])
    assert any('CONTROLLED_HANDOFF_opus' in arg for arg in calls[2]['args'])
    assert calls[0]['args'][calls[0]['args'].index('--sandbox')+1]=='read-only'
    assert calls[2]['args'][calls[2]['args'].index('--sandbox')+1]=='read-only'
    run.update(status='completed',ended_at=time.time(),updated_at=0)
    (tmp_path/'log/app-run.json').write_text(json.dumps(run),encoding='utf-8')
    wb=Workbench(tmp_path);status=wb.status();activity=wb.activity()
    assert status['task_status']=='completed'
    assert status['task_result']['task_delivery_verified'] is False
    assert len(status['metrics']['attempts'])==3
    assert status['metrics']['providers']['codex']['inTok']==20
    assert status['metrics']['providers']['claude']['inTok']==20
    assert all(any('CONTROLLED_HANDOFF_'+node in item['text'] for item in activity['items']) for node in ('astra','opus','sol'))
    assert [(item['graph_node_id'],item['configured_model']) for item in activity['items']]==[('astra','gpt-6-astra'),('opus','opus'),('sol','gpt-5.6-sol')]
    js='const d=require(process.argv[1]);console.log(JSON.stringify(d.computeState(process.argv[2],{includeHistory:false})))'
    ui=json.loads(subprocess.run(['node','-e',js,str(root/'vscode-extension/dashboard.js'),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',check=True).stdout)
    assert ui['routingStats']==status['metrics']
    assert ui['graphResult']['graph_states']=={'astra':'ok','opus':'ok','sol':'ok'}
    assert ui['graphResult']['task_delivery_verified'] is False
    assert not ui['summary']['completed']
    assert not list((tmp_path/'log').glob('task-result-*.json'))
