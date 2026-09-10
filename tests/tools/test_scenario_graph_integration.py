"""Controlled executable provider subprocesses; never invoke installed model CLIs."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import codex_runner as runner, scenario_graph, events_model


@pytest.mark.parametrize('last_outcome', ['ok', 'error'])
def test_real_runner_graph_quota_recovery_and_js_metrics(tmp_path, monkeypatch, last_outcome):
    fake = tmp_path / 'provider.py'
    fake.write_text('''import json,sys
provider,outcome=sys.argv[1:3]
if outcome=='quota':
 print(json.dumps({'type':'error','error':{'code':'usage_limit_reached','message':'usage quota exhausted'}}));sys.exit(1)
if outcome=='error':
 print(json.dumps({'type':'error','error':{'message':'authentication failed'}}));sys.exit(1)
print(json.dumps({'type':'result','is_error':False,'result':'Controlled review complete','usage':{'input_tokens':17,'output_tokens':5}}))
''', encoding='utf-8')
    original_which = shutil.which
    monkeypatch.setattr(runner.shutil, 'which', lambda name: sys.executable if name in ('codex', 'claude') else original_which(name))
    monkeypatch.setattr(runner, 'resolve_codex', lambda provider: [sys.executable, str(fake), provider, 'quota' if provider == 'codex' else last_outcome])
    monkeypatch.setenv('CODEXAUTOAI_PARENT_RUN_ID', 'controlled-run')
    graph = {'version':1,'id':'recovery','scenario':'review','entry_node':'first',
             'nodes':[{'id':'first','provider':'codex','kind':'review','model':None},
                      {'id':'backup','provider':'claude','kind':'review','model':None}],
             'edges':[{'id':'quota','source':'first','target':'backup','condition':'quota_exhausted'}]}
    args = SimpleNamespace(dispatcher=False, codex_cmd=None, retries=1, timeout=8, session_grace=1, heartbeat=1)
    ok, reason, metadata, _ = scenario_graph.execute(graph,'Controlled only',tmp_path,args,'controlled-run',[0],runner.execute_with_fallback,runner.record_attempt)
    assert ok == (last_outcome == 'ok'), reason
    assert metadata['activated_edges'] == ['quota']
    assert metadata['graph_states']['first'] == 'quota_exhausted'
    events = [json.loads(line) for line in (tmp_path/'log/events.jsonl').read_text(encoding='utf-8').splitlines()]
    stats = events_model.routing_stats(events, parent_run_id='controlled-run')
    assert len(stats['attempts']) == 2  # graph_node_result must not count as calls.
    assert sum(e.get('outcome') == 'graph_node_result' for e in events) == 2
    assert not list((tmp_path/'log').glob('task-result-*.json'))
    assert not any(e.get('event_type') == 'phase_end' for e in events)
    module = Path(__file__).resolve().parents[2]/'vscode-extension/dashboard.js'
    script = 'const fs=require("fs"),d=require(process.argv[1]);console.log(JSON.stringify(d.summarizeRoutingAttempts(fs.readFileSync(0,"utf8").split(/\\r?\\n/),null,"controlled-run")))'
    result = subprocess.run(['node','-e',script,str(module)], input='\n'.join(map(json.dumps,events)), text=True, encoding='utf-8', capture_output=True, check=True)
    assert json.loads(result.stdout) == stats


@pytest.mark.parametrize('policy,digest,violation', [('explicit-primary','a'*64,False),('explicit-graph','b'*64,False),('explicit-primary',None,True),('quota-only','c'*64,True)])
def test_explicit_opencode_evidence_not_misreported_as_quota_violation(policy, digest, violation):
    event = {'type':'model_attempt','run_id':'r','attempt_id':'r:1','actual_provider':'opencode','outcome':'ok',
             'routing_policy':policy,'config_digest':digest,'graph_digest':digest,'graph_id':'g','graph_node_id':'n'}
    stats=events_model.routing_stats([event],run_id='r')
    assert bool(stats['violations']) == violation
    module=Path(__file__).resolve().parents[2]/'vscode-extension/dashboard.js'
    result=subprocess.run(['node','-e','const d=require(process.argv[1]);console.log(JSON.stringify(d.summarizeRoutingAttempts([JSON.parse(process.argv[2])],"r")))',str(module),json.dumps(event)],text=True,encoding='utf-8',capture_output=True,check=True)
    assert json.loads(result.stdout)==stats
