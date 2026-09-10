"""One explicitly opted-in Astra→Opus→Sol smoke, isolated Desktop workspace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from tools import scenario_graph
from tools.workbench import Workbench


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');args=ap.parse_args()
    if not args.execute:
        print('Plan only: 3 nodes, gpt-6-astra → opus → gpt-5.6-sol; 1 attempt each, 300s total.');return 0
    run_id='custom-live-'+uuid.uuid4().hex
    root=Path.home()/'Desktop'/('codexautoai-'+run_id);root.mkdir()
    for name in ('tools','.claude','.githooks','src/codexautoai_v2','docs/templates'):
        if (REPO/name).is_dir():shutil.copytree(REPO/name,root/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('AGENTS.md','CLAUDE.md','usage_gate.toml'):
        if (REPO/name).is_file():shutil.copy2(REPO/name,root/name)
    graph=scenario_graph.save(root,{'version':1,'id':'live-three-node','scenario':'coding','entry_node':'plan','nodes':[
        {'id':'plan','label':'規劃 Astra','kind':'task','provider':'codex','model':'gpt-6-astra','task_text':'Write plan.txt with exactly: Build result.txt containing GRAPH_CHAIN_OK. Then finish. Do not create result.txt. No subagents, nested CLI, or phase pipeline.','expects':['plan.txt']},
        {'id':'build','label':'實作 Opus','kind':'task','provider':'claude','model':'opus','task_text':'Read plan.txt and predecessor evidence. Write result.txt with exactly one line GRAPH_CHAIN_OK. Finish immediately. Do not call subagents, another CLI, or phase pipeline.','expects':['result.txt']},
        {'id':'verify','label':'驗證 Sol','kind':'review','provider':'codex','model':'gpt-5.6-sol','task_text':'Read plan.txt and result.txt. Without writing files, check result.txt.strip() equals GRAPH_CHAIN_OK. Reply VERIFIED_GRAPH_CHAIN_OK only if correct; otherwise report failure. No subagents, nested CLI or phase pipeline.'}],
        'edges':[{'id':'plan-build','source':'plan','target':'build','condition':'success'},{'id':'build-verify','source':'build','target':'verify','condition':'success'}]})
    start=time.time();log=root/'log';app={'run_id':run_id,'prompt':'Run only the three explicitly selected tiny graph tasks.','status':'running','started_at':start,'updated_at':start,'route':{'mode':'graph','graph_id':graph['id'],'graph_digest':scenario_graph.digest(graph),'graph_snapshot':graph}}
    app_path=log/'app-run.json';app_path.write_text(json.dumps(app),encoding='utf-8')
    env=dict(os.environ,CODEXAUTOAI_PARENT_RUN_ID=run_id,PYTHONUTF8='1')
    for key in ('CODEXAUTOAI_ROUTED_WORKER','CODEXAUTOAI_ROUTED_ROLE','CODEXAUTOAI_ROUTED_PROVIDER'):env.pop(key,None)
    cmd=[sys.executable,str(root/'tools/codex_runner.py'),'--graph-id',graph['id'],'--graph-digest',scenario_graph.digest(graph),'--cwd',str(root),'--prompt',app['prompt'],'--retries','1','--timeout','85','--session-grace','15','--heartbeat','50']
    print(json.dumps({'root':str(root),'run_id':run_id,'source':'live_provider_invocations'}),flush=True)
    timed_out=False
    with (log/'live-runner-output.jsonl').open('w',encoding='utf-8') as output:
        proc=subprocess.Popen(cmd,cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        while proc.poll() is None:
            if time.time()-start>=300:
                timed_out=True
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
                else:proc.kill()
                proc.wait();break
            app['updated_at']=time.time();app_path.write_text(json.dumps(app),encoding='utf-8');time.sleep(1)
    exit_code=proc.returncode;exit_file=log/'live.exit';exit_file.write_text(str(exit_code))
    app.update(status='failed' if exit_code or timed_out else 'completed',updated_at=0,ended_at=time.time(),exit_file=str(exit_file));app_path.write_text(json.dumps(app),encoding='utf-8')
    wb=Workbench(root);status=wb.status();activity=wb.activity();observed=status['metrics']['attempts']
    content=(root/'result.txt').read_text(encoding='utf-8-sig').strip() if (root/'result.txt').exists() else None
    checks={'graph_completed':status['task_status']=='completed','three_calls':len(observed)==3,'configured_sequence':[(a['actual_provider'],a['configured_model']) for a in observed]==[('codex','gpt-6-astra'),('claude','opus'),('codex','gpt-5.6-sol')],'exact_result':content=='GRAPH_CHAIN_OK','readonly_verdict':any('VERIFIED_GRAPH_CHAIN_OK' in item['text'] and item.get('graph_node_id')=='verify' for item in activity['items']),'within_deadline':not timed_out}
    hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('tools/codex_runner.py','tools/scenario_graph.py','tools/enforce_build_codex.py')}
    report={'schema_version':1,'source':'live_three_node_graph','root':str(root),'run_id':run_id,'exit_code':exit_code,'timed_out':timed_out,'wall_seconds':time.time()-start,'checks':checks,'passed':all(checks.values()),'status':status,'activity':activity,'source_sha256':hashes,'ui_verified':False,'seven_phase_delivery_verified':False}
    (log/'live-custom-graph-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'root':str(root),'checks':checks,'passed':report['passed'],'exit_code':exit_code}),flush=True)
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
