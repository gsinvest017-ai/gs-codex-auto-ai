"""Opt-in single paid smoke. Dry run by default; never modifies an existing project."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import signal
import uuid

BASE = Path(__file__).resolve().parent
REPO = BASE.parent


def final_status(task_status, artifacts):
    return 'incomplete' if task_status == 'completed' and not all(artifacts.values()) else task_status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='3d-tiny-triangle')
    parser.add_argument('--execute', action='store_true', help='Explicitly allow this one real provider call')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args(argv)
    if not 30 <= args.timeout <= 300:
        parser.error('--timeout must be between 30 and 300 seconds')
    cases = json.loads((BASE/'cases.json').read_text(encoding='utf-8'))['cases']
    case = next((c for c in cases if c['id'] == args.case), None)
    if not case or not case.get('smoke_eligible'):
        parser.error('only the designated 3d-tiny-triangle case is enabled for paid smoke')
    prompt = (BASE/case['prompt_file']).read_text(encoding='utf-8')
    prompt += '''\n此為單一微型 dispatcher 驗收：讀取本 sandbox 的既有框架說明，依真實工作完成 Phase 0 至 7。
每階段只做本案例需要的最小工作，開始和結束時各留一句簡短可讀活動；用 tools/run_phase.py 在真正階段邊界記錄進度。
不要只為了取得 completed 而寫進度。若任何步驟失敗，誠實回報失敗原因。
本案若需要分工，最多使用一個原生 child agent；禁止 child 再派工，禁止 nested CLI 或第二個 dispatcher。不要新增任務、模型比較或額外功能。
最後實際執行 tests/test_artifact.py，讀回所有產物，再以真實 artifact hash 完成交付。
'''
    plan = {
        'mode': 'execute' if args.execute else 'dry_run', 'case': case['id'],
        'scope': 'ONE_LIVE_CASE_ONLY_NOT_ALL_MATRIX_COMBINATIONS',
        'scenario': '3d_modeling', 'primary_provider': 'codex', 'model': 'CLI default',
        'dispatcher': True, 'requested_nonstop': False, 'runtime_continuation_verified': False, 'retries_per_provider': 1, 'hard_deadline_seconds': args.timeout,
        'native_agent_limit': 1, 'opencode_model': None,
        'sandbox': 'new uniquely named Desktop directory with inherited ACL; never reuse or delete', 'expected_artifacts': case['expected_artifacts'],
        'ui_verified': False, 'warning': 'Production quota fallback policy remains active; actual provider attempts are recorded. No OpenCode model is configured.',
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    sandbox = Path.home()/'Desktop'/('codexautoai-testcase-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    sandbox.mkdir(exist_ok=False)
    for name in ('tools', '.claude', '.githooks', 'src/codexautoai_v2', 'docs/templates'):
        if (REPO/name).is_dir(): shutil.copytree(REPO/name, sandbox/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('AGENTS.md', 'CLAUDE.md', 'usage_gate.toml', 'setup.ps1', 'setup.cmd', 'setup.sh'):
        if (REPO/name).is_file(): shutil.copy2(REPO/name, sandbox/name)
    log = sandbox/'log'; log.mkdir()
    config = {'default': {'provider':'codex','model':None},
              'scenarios': {'3d_modeling': {'provider':'codex','model':None}},
              'fallback': {'provider':'opencode','model':None}, 'quota_policy':'both-primary-exhausted'}
    (log/'model-routing.json').write_text(json.dumps(config),encoding='utf-8')
    (sandbox/'case-prompt.txt').write_text(prompt,encoding='utf-8')
    sys.path.insert(0,str(REPO))
    from tools.model_router import resolve_route
    route=resolve_route(prompt,sandbox,scenario='3d_modeling',provider='codex')
    run_id = str(uuid.uuid4()); started = time.time(); monotonic_start = time.monotonic()
    app = {'source':'testcase_live','run_id':run_id,'prompt':prompt,'status':'running',
           'started_at':started,'updated_at':started,'pid':os.getpid(),'route':route,'requested_nonstop':False}
    def persist():
        temp=log/'app-run.tmp.json';temp.write_text(json.dumps(app,ensure_ascii=False),encoding='utf-8');temp.replace(log/'app-run.json')
    persist()
    env=dict(os.environ);env.update(PYTHONUTF8='1',CODEXAUTOAI_PARENT_RUN_ID=run_id,CODEXAUTOAI_TASK_PROMPT=prompt)
    command=[sys.executable,str(sandbox/'tools/codex_runner.py'),'--dispatcher','--prompt',prompt,
             '--cwd',str(sandbox),'--scenario','3d_modeling','--provider','codex',
             '--retries','1','--timeout',str(args.timeout),'--heartbeat','90','--session-grace','45']
    for artifact in case['expected_artifacts']: command += ['--expect',artifact]
    print(json.dumps({'started':True,'sandbox':str(sandbox),'run_id':run_id,'plan':plan},ensure_ascii=False),flush=True)
    timed_out=False
    with (log/'live-runner-output.log').open('w',encoding='utf-8') as output:
        proc=subprocess.Popen(command,cwd=sandbox,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0), start_new_session=os.name!='nt')
        try:
            while proc.poll() is None:
                if time.monotonic()-monotonic_start >= args.timeout:
                    timed_out=True
                    if os.name=='nt': subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                    else: os.killpg(proc.pid,signal.SIGTERM)
                    break
                app['updated_at']=time.time();persist();time.sleep(1)
            try: exit_code=proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill();exit_code=proc.wait(timeout=5)
        except BaseException:
            if os.name=='nt': subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            else: os.killpg(proc.pid,signal.SIGTERM)
            raise
    task_path=log/f'task-result-{run_id}.json'
    try: task=json.loads(task_path.read_text(encoding='utf-8'))
    except (OSError,ValueError): task={}
    app.update(status='failed' if timed_out else task.get('status') or ('incomplete' if exit_code==0 else 'failed'),updated_at=0,ended_at=time.time())
    persist()
    def query(action):
        result=subprocess.run([sys.executable,str(sandbox/'tools/workbench.py'),'--root',str(sandbox),'--action',action],
            cwd=sandbox,env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        try: return json.loads(result.stdout)
        except ValueError: return {'error':result.stderr or result.stdout}
    status=query('status');activity=query('activity')
    artifacts={name: (sandbox/name).is_file() and (sandbox/name).stat().st_size>0 for name in case['expected_artifacts']}
    report={**plan,'sandbox':str(sandbox),'run_id':run_id,'exit_code':exit_code,'timed_out':timed_out,
            'wall_seconds':round(time.time()-started,3),'task':task,'workbench_status':status,
            'artifact_presence':artifacts,'activity':activity,
            'result':final_status(app['status'],artifacts),
            'semantic_validation':'Provider must run tests/test_artifact.py; file-presence checks alone do not prove GLB semantics. Independent post-run validation is required.'}
    report_path=log/'live-smoke-report.json';report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(report_path),'result':report['result'],'ui_verified':False},ensure_ascii=False))
    return 0 if report['result']=='completed' else 1


if __name__=='__main__':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError: pass
    raise SystemExit(main())
