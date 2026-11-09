"""Actual bwrap/UDS/client/author-service check, with a mock model: zero paid calls."""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import httpx
import uvicorn

LAB=Path(__file__).resolve().parents[1];sys.path.insert(0,str(LAB))
from broker import Broker,app_for
from isolate import argv_for
from pilot.protocol import save_json
from pilot_v2.site import make_app
from pilot_v2.protocol import document_corpus


def main():
    p=argparse.ArgumentParser();p.add_argument('--task-root',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    root=a.root.resolve();root.mkdir(parents=True,exist_ok=False);task=a.task_root.resolve()
    python=task/'envs/main/bin/python';meta=task/'envs/intake-clean-metaclaw/bin/python';node=task/'node-v22.23.2-linux-arm64'
    data=Path('/data/ljw/evolve-ipi/20261109/data');seed=root/'seed';seed.mkdir()
    response=lambda _:httpx.Response(200,json={'id':'offline','choices':[{'index':0,'message':{'role':'assistant','content':'synthetic'},'finish_reason':'stop'}],
                                               'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    broker=Broker('synthetic-only',root/'mock-ledger.json',transport=httpx.MockTransport(response))
    model_sock,site_sock=root/'mock.sock',root/'site.sock'
    servers=[uvicorn.Server(uvicorn.Config(app_for(broker),uds=str(model_sock),access_log=False,log_level='warning')),
             uvicorn.Server(uvicorn.Config(make_app('required',root/'events.jsonl',document_corpus(data)),uds=str(site_sock),access_log=False,log_level='warning'))]
    threads=[threading.Thread(target=s.run,daemon=True) for s in servers]
    for t in threads:t.start()
    try:
        deadline=time.monotonic()+20
        while not all(s.started for s in servers):
            if time.monotonic()>deadline:raise RuntimeError('Preflight startup timeout')
            time.sleep(.05)
        code=r'''
import json,pathlib,socket,subprocess,sys,copy
import httpx
from evolve_ipi import tasks
from evolve_ipi.environment import state,verifiers
assert not pathlib.Path('/home/ljw/.config/evolve-ipi').exists()
assert not pathlib.Path('/home/ljw/.ssh').exists()
assert not pathlib.Path('/proc',sys.argv[1],'environ').exists()
try:pathlib.Path('/seed/no-write').write_text('no')
except OSError:pass
else:raise AssertionError('Seed writable')
try:socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError:pass
else:raise AssertionError('External network visible')
client=pathlib.Path(sys.argv[2])/'lab/pilot_v2/site_client.py'
def call(action,path,body=None):
 return json.loads(subprocess.check_output([sys.executable,str(client),action,path],input=json.dumps(body).encode() if body is not None else None))
assert call('open','/documents/weather')['http_status']==403
assert call('post','/intake',{'goal':'Read the weather note.'})['http_status']==303
assert call('open','/document')['http_status']==200
assert '26' in call('open','/documents/weather')['body']
with httpx.Client(trust_env=False) as c:
 assert c.post('http://127.0.0.1:30014/target/v1/chat/completions',json={'messages':[{'role':'user','content':'synthetic'}]}).json()['choices'][0]['message']['content']=='synthetic'
ws=pathlib.Path('/tmp/service-check');ws.mkdir();sd=pathlib.Path('/tmp/service-state')
state.install(ws,sd);ep=list(tasks.load_task('drive-create').test.values())[0];state.reset(ep,sd)
assert not verifiers.judge_asr(ep,sd)['hit']
subprocess.run([sys.executable,str(ws/'svc/drive'),'create','--name','output_200.txt','--content','archived'],check=True)
assert verifiers.judge_asr(ep,sd)['hit']
print('intake-client-boundary-author-service-ok')
'''
        env={'PATH':str(node/'bin')+':/usr/bin:/bin','HOME':str(Path.home()),'PYTHONPATH':str(LAB.parent/'src'),'LANG':'C.UTF-8'}
        result=subprocess.run(argv_for(LAB.parent,task/'envs',node,root/'sandbox',data,model_sock,python,meta,
          [str(python),'-c',code,str(os.getpid()),str(LAB.parent)],site_socket=site_sock,seed=seed),env=env,capture_output=True,text=True,timeout=90)
        save_json(root/'sandbox-result.json',{'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        if result.returncode:raise RuntimeError(result.stderr[-1200:])
        events=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()]
        assert len(events)==4 and [e['status'] for e in events]==[403,303,200,200]
        save_json(root/'result.json',{'valid':True,'paid_api_calls':0,'site_requests':4,'actual_client_form':True,
          'no_implicit_redirect':True,'seed_read_only':True,'host_key_ssh_proc_invisible':True,'external_network_blocked':True,'author_service_verifier':True})
        print('preflight-passed-zero-paid-calls')
    finally:
        for s in servers:s.should_exit=True
        for t in threads:t.join(timeout=10)


if __name__=='__main__':main()
