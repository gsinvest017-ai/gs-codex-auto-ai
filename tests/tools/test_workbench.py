"""Real stdio MCP interoperability and workspace evidence boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from workbench import Workbench
import workbench as module
from workbench_mcp import Server
import codex_runner as runner


def write_events(root, events):
    (root / 'log').mkdir(exist_ok=True)
    (root / 'log/events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')


def attempt(run='current', **extra):
    return {'type':'model_attempt', 'run_id':run, 'parent_run_id':run,
            'attempt_id':run+':1','actual_provider':'codex','outcome':'started', **extra}


def test_current_run_activity_and_unknown_usage(tmp_path):
    log = tmp_path / 'log/output.log'
    write_events(tmp_path, [attempt('old', result_path=str(tmp_path/'outside.log')), attempt(result_path=str(log))])
    log.write_text('\n'.join(json.dumps(e) for e in [
        {'type':'item.completed','item':{'type':'agent_message','text':'hello'}},
        {'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE_REASONING'}},
        {'type':'item.started','item':{'type':'command_execution','command':'python test.py','status':'in_progress'}},
        {'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':4}},
    ])+'\n',encoding='utf-8')
    data = Workbench(tmp_path).activity()
    assert data['run_id'] == 'current'
    assert len(data['items']) == 2 and data['last_update'] > 0
    assert 'PRIVATE_REASONING' not in json.dumps(data)
    assert data['items'][0]['timestamp'] is None
    assert data['usage_by_attempt']['current:1']['usage'] == {'input_tokens':12,'output_tokens':4,'cached_input_tokens':None}
    assert data['items'][0]['source_line'] == 1
    assert data['items'][0]['id'] == Workbench(tmp_path).activity()['items'][0]['id']


def test_cli_ok_is_not_task_completion(tmp_path):
    write_events(tmp_path, [attempt(outcome='ok')])
    data=Workbench(tmp_path).status()
    assert data['call_status']=='ok' and data['task_status']=='unknown'
    (tmp_path/'log/task-result-current.json').write_text(json.dumps({'run_id':'current','status':'incomplete'}))
    assert Workbench(tmp_path).status()['task_status']=='incomplete'


def test_app_run_scope_does_not_leak_previous_attempts(tmp_path):
    write_events(tmp_path,[attempt('old',outcome='ok')])
    (tmp_path/'log/app-run.json').write_text(json.dumps({'run_id':'new','status':'running'}))
    data=Workbench(tmp_path).status()
    assert data['run_id']=='new' and data['attempts']==[]
    assert data['call_status']=='unknown' and data['task_status']=='running'


def test_artifact_registry_no_snapshot_and_confined_paths(tmp_path):
    (tmp_path/'log').mkdir()
    (tmp_path/'log/model.glb').write_bytes(b'glTF test')
    board=Workbench(tmp_path)
    record=board.register_artifact('log/model.glb',label='model')
    assert len(record['sha256'])==64 and record['snapshot'] is False
    data=board.artifacts()
    assert data['items'][0]['relative_path']=='log/model.glb'
    with pytest.raises(ValueError,match='outside'):
        board.register_artifact(str(tmp_path.parent))
    (tmp_path/'secret.txt').write_text('not an artifact')
    with pytest.raises(ValueError):
        board.register_artifact('secret.txt')


def test_symlink_escape_is_never_read(tmp_path):
    outside=tmp_path.parent/'workbench-outside.glb'
    outside.write_bytes(b'secret')
    try:
        (tmp_path/'escape.glb').symlink_to(outside)
    except OSError:
        pytest.skip('symlink privilege unavailable')
    assert Workbench(tmp_path).artifacts()['items']==[]
    with pytest.raises(ValueError):
        Workbench(tmp_path).register_artifact('escape.glb')


def test_scan_has_unmatched_file_budget(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'MAX_SCAN_FILES',2)
    for i in range(3):
        (tmp_path/f'{i}.txt').write_text('x')
    assert Workbench(tmp_path).artifacts()['limited'] is True


def test_reports_are_explicitly_unverified(tmp_path):
    result=Workbench(tmp_path).report_progress('building mesh','r',35)
    assert result['verified'] is False and result['source']=='caller_reported'
    assert Workbench(tmp_path).status('r')['progress']==[result]
    assert Workbench(tmp_path).status('r')['task_status']=='unknown'


def test_stdout_path_escape_is_not_read(tmp_path):
    external=tmp_path.parent/'external-output.log'
    external.write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'secret'}}))
    write_events(tmp_path,[attempt(result_path=str(external))])
    assert Workbench(tmp_path).activity()['items']==[]


def rpc(method,ident=None,params=None):
    value={'jsonrpc':'2.0','method':method}
    if ident is not None: value['id']=ident
    if params is not None: value['params']=params
    return value


def test_real_mcp_stdio_roundtrip(tmp_path):
    messages=[rpc('initialize',1,{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}}),
              rpc('notifications/initialized'),rpc('tools/list',2),
              rpc('tools/call',3,{'name':'route_preview','arguments':{'prompt':'建立3D模型'}}),
              rpc('resources/list',4),rpc('resources/read',5,{'uri':'workbench://status'}),
              rpc('tools/call',6,{'name':'register_artifact','arguments':{'path':'x.glb'}}),
              rpc('ping',7)]
    result=subprocess.run([sys.executable,str(ROOT/'tools/workbench_mcp.py'),'--root',str(tmp_path)],
                          input=''.join(json.dumps(m,ensure_ascii=False)+'\n' for m in messages),
                          text=True,encoding='utf-8',capture_output=True,timeout=15)
    assert result.returncode==0 and result.stderr==''
    responses=[json.loads(line) for line in result.stdout.splitlines()]
    assert [r['id'] for r in responses]==[1,2,3,4,5,6,7]
    assert responses[0]['result']['protocolVersion']=='2025-06-18'
    assert len(responses[1]['result']['tools'])==5
    assert responses[2]['result']['structuredContent']['scenario']=='3d_modeling'
    assert len(responses[3]['result']['resources'])==4
    assert json.loads(responses[4]['result']['contents'][0]['text'])['task_status']=='unknown'
    assert responses[5]['error']['code']==-32602


def test_mcp_reports_require_explicit_server_flag(tmp_path):
    server=Server(tmp_path,allow_reports=True)
    server.handle(rpc('initialize',1,{'protocolVersion':'2025-06-18'}))
    server.handle(rpc('notifications/initialized'))
    result=server.handle(rpc('tools/call',2,{'name':'report_progress','arguments':{'run_id':'r','message':'working'}}))
    assert result['result']['structuredContent']['verified'] is False
    invalid=server.handle(rpc('resources/read',3,{'uri':[]}))
    assert invalid['error']['code']==-32002
    bad=server.handle(rpc('tools/call',4,{'name':'run_activity','arguments':{'limit':False}}))
    assert bad['error']['code']==-32602


def test_live_output_keeps_own_attempt_alive_and_publishes_path(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'_latest_session_mtime',lambda *_: pytest.fail('global heartbeat used'))
    script="import time,json;\nfor i in range(8):\n print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':str(i)}}),flush=True); time.sleep(.05)"
    metadata={}
    event=attempt()
    ok,_=runner.run_once([sys.executable,'-c',script],tmp_path,[],.2,.15,poll=.02,timeout=3,
                         result_metadata=metadata,started_event=event)
    assert ok
    events=[json.loads(line) for line in (tmp_path/'log/events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events[0]['outcome']=='started' and Path(events[0]['result_path']).exists()


def test_owned_heartbeat_excludes_unrelated_and_accepts_child(tmp_path,monkeypatch):
    now=time.time(); stamp=datetime.fromtimestamp(now,timezone.utc).isoformat()
    folder=tmp_path/datetime.fromtimestamp(now,timezone.utc).strftime('%Y/%m/%d'); folder.mkdir(parents=True)
    monkeypatch.setattr(runner,'_sessions_dir',lambda:tmp_path)
    def session(name,parent):
        file=folder/f'rollout-{name}.jsonl'
        meta={'id':name,'timestamp':stamp,'source':{'subagent':{'thread_spawn':{'parent_thread_id':parent}}}}
        file.write_text(json.dumps({'type':'session_meta','payload':meta})+'\n')
        return file
    unrelated=session('stranger','another-root')
    assert runner._owned_session_mtime('root',now-1,now+1) is None
    child=session('child','root')
    assert runner._owned_session_mtime('root',now-1,now+1)==child.stat().st_mtime
    grandchild=session('grandchild','child')
    assert runner._owned_session_mtime('root',now-1,now+1)==max(child.stat().st_mtime,grandchild.stat().st_mtime)


def test_activity_merges_item_lifecycle_and_marks_terminal_history(tmp_path):
    log=tmp_path/'log/out.log'
    write_events(tmp_path,[attempt(outcome='ok',result_path=str(log))])
    log.write_text('\n'.join(json.dumps(e) for e in [
        {'type':'item.started','item':{'id':'tool1','type':'command_execution','command':'pytest','status':'in_progress'}},
        {'type':'item.completed','item':{'id':'tool1','type':'command_execution','command':'pytest','status':'completed'}},
        {'type':'item.started','item':{'id':'tool2','type':'command_execution','command':'python','status':'in_progress'}},
    ]),encoding='utf-8')
    items=Workbench(tmp_path).activity()['items']
    assert len(items)==2
    assert items[0]['event_type']=='item.completed' and items[0]['status']=='completed'
    assert items[1]['status']=='historical' and items[1]['reported_status']=='in_progress'
    assert items[1]['historical'] is True


@pytest.mark.parametrize('linked',[True,False])
def test_child_heartbeat_real_process_matches_only_owned_root(tmp_path,monkeypatch,linked):
    sessions=tmp_path/'sessions'; sessions.mkdir()
    monkeypatch.setattr(runner,'_sessions_dir',lambda:sessions)
    parent='owned-root' if linked else 'different-root'
    script=("import pathlib,datetime,json,time,os\n"
            f"base=pathlib.Path({str(sessions)!r})\n"
            "now=datetime.datetime.now(datetime.timezone.utc)\n"
            "folder=base/now.strftime('%Y/%m/%d');folder.mkdir(parents=True)\n"
            f"meta={{'id':'child','timestamp':now.isoformat(),'source':{{'subagent':{{'thread_spawn':{{'parent_thread_id':{parent!r}}}}}}}}}\n"
            "file=folder/'rollout-child.jsonl';file.write_text(json.dumps({'type':'session_meta','payload':meta})+'\\n')\n"
            "print(json.dumps({'type':'thread.started','thread_id':'owned-root'}),flush=True)\n"
            "for i in range(14):\n os.utime(file,None);time.sleep(.05)\n")
    ok,reason=runner.run_once([sys.executable,'-c',script],tmp_path,[],.5,.25,poll=.025,timeout=3)
    assert ok is linked,reason
    if not linked:
        assert 'heartbeat stalled' in reason


def test_unknown_native_completion_does_not_mark_finished_cli_running(tmp_path):
    write_events(tmp_path,[attempt(outcome='ok'),attempt('native',parent_run_id='current',role='native_worker')])
    data=Workbench(tmp_path).status('current')
    assert data['call_status']=='ok'
    assert data['task_status']=='unknown'


@pytest.mark.parametrize('change', ['valid','missing_result','wrong_run','wrong_schema','stale_start','bad_end','wrong_phase','missing_artifacts','bad_hash','wrong_evidence_run','stale_evidence'])
def test_task_completion_matches_dashboard_contract(tmp_path,change):
    import shutil
    if not shutil.which('node'):
        pytest.skip('Node required for Python/JS completion parity')
    (tmp_path/'log').mkdir()
    app={'run_id':'r','started_at':100,'status':'completed'}
    evidence={'event_type':'phase_end','phase':'phase7','run_id':'r','timestamp':'1970-01-01T00:02:30Z','status':'success','artifacts':[{'path':'model.glb','sha256':'a'*64}]}
    result={'schema_version':1,'run_id':'r','started_at':100,'ended_at':200,'status':'completed','completion_evidence':evidence}
    if change=='wrong_run': result['run_id']='other'
    if change=='wrong_schema': result['schema_version']=2
    if change=='stale_start': result['started_at']=99
    if change=='bad_end': result['ended_at']=99
    if change=='wrong_phase': evidence['phase']='phase6'
    if change=='missing_artifacts': evidence['artifacts']=[]
    if change=='bad_hash': evidence['artifacts'][0]['sha256']='fake'
    if change=='wrong_evidence_run': evidence['run_id']='other'
    if change=='stale_evidence': evidence['timestamp']='1970-01-01T00:00:01Z'
    (tmp_path/'log/app-run.json').write_text(json.dumps(app))
    if change!='missing_result':
        (tmp_path/'log/task-result-r.json').write_text(json.dumps(result))
    js='const fs=require("fs");const d=JSON.parse(fs.readFileSync(0,"utf8"));const r=require('+json.dumps(str(ROOT/'vscode-extension/routing.js'))+');console.log(JSON.stringify(r.taskResult(d.root,d.run)));'
    completed=subprocess.run(['node','-e',js],input=json.dumps({'root':str(tmp_path),'run':app}),text=True,encoding='utf-8',capture_output=True,check=True,timeout=10)
    expected=json.loads(completed.stdout)['status']
    assert Workbench(tmp_path).status()['task_status']==expected


def test_historical_delivery_does_not_rehash_user_edits(tmp_path):
    (tmp_path/'log').mkdir()
    (tmp_path/'model.glb').write_bytes(b'edited after delivery')
    app={'run_id':'r','started_at':100,'status':'completed'}
    result={'schema_version':1,'run_id':'r','started_at':100,'ended_at':200,'status':'completed',
            'completion_evidence':{'event_type':'phase_end','phase':'phase7','run_id':'r','timestamp':'1970-01-01T00:02:30Z','status':'success','artifacts':[{'path':'model.glb','sha256':'a'*64}]}}
    (tmp_path/'log/app-run.json').write_text(json.dumps(app))
    (tmp_path/'log/task-result-r.json').write_text(json.dumps(result))
    data=Workbench(tmp_path).status()
    assert data['task_status']=='completed'
    assert data['task_result']['evidence_validation']=='valid_delivery_envelope'


def test_metrics_preserve_original_terminal_event_order(tmp_path):
    events=[attempt('parent',parent_run_id='scope'),
            attempt('child',parent_run_id='scope',role='native_worker',outcome='ok'),
            attempt('parent',parent_run_id='scope',outcome='ok')]
    write_events(tmp_path,events)
    result=Workbench(tmp_path).status('scope')
    assert [a['run_id'] for a in result['metrics']['attempts']]==['child','parent']
    assert result['metrics']==module.events_model.routing_stats(events,parent_run_id='scope')


def test_gif_artifact_is_discovered_and_registered(tmp_path):
    (tmp_path / 'walk.gif').write_bytes(b'GIF89a' + b'\0' * 20)
    bench = Workbench(tmp_path)
    assert any(item['relative_path'] == 'walk.gif' for item in bench.artifacts()['items'])
    bench.register_artifact('walk.gif')
    assert any(item['relative_path'] == 'walk.gif' for item in bench.artifacts()['registrations'])


def test_running_graph_without_terminal_result_stays_running_until_scoped_exit(tmp_path):
    (tmp_path / 'log').mkdir()
    exit_file = tmp_path / 'log' / 'run.exit'
    app = {'run_id':'active', 'status':'running', 'started_at':100, 'route':{'mode':'graph'}, 'exit_file':str(exit_file)}
    (tmp_path / 'log' / 'app-run.json').write_text(json.dumps(app), encoding='utf8')
    bench = Workbench(tmp_path)
    assert bench._task_result('active')['status'] == 'running'
    exit_file.write_text('1')
    assert bench._task_result('active')['status'] == 'failed'
