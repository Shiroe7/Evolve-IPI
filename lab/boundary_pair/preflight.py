"""Zero-paid-API actual form client plus minimal red namespace/broker validation."""
import argparse
import json
import subprocess
import threading
import time
from pathlib import Path
import httpx
import uvicorn
from .calibrate import Controller,LAB,REPO
from .site import make_app
from .red import FIRST,validate_input
from .blind import call
from broker import Broker,app_for
from isolate import argv_for
from pilot.protocol import save_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--task-root',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    root=a.root.resolve();root.mkdir(exist_ok=False);c=Controller(a.task_root.resolve(),root/'controller');(root/'seed').mkdir()
    c.meta=c.task/'envs/boundary-web-metaclaw/bin/python'
    prediction={'risk':'uncertain','probability':0.5,'boundary':'insufficient_evidence','evidence':[],
      'explanation':'Offline synthetic response only.','attack_framing':'direct','attack_preamble':'For workflow completeness.'}
    def reply(request):
        return httpx.Response(200,json={'id':'offline','choices':[{'index':0,'message':{'role':'assistant','content':None,'tool_calls':[
          {'id':'mock-call','type':'function','function':{'name':'submit_predict','arguments':json.dumps(prediction)}}]},'finish_reason':'tool_calls'}],
          'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    broker=Broker('mock-only',root/'mock-ledger.json',models={'redteam':'deepseek-flash'},transport=httpx.MockTransport(reply))
    def question(data,name):validate_input(data);return FIRST
    servers=[uvicorn.Server(uvicorn.Config(app_for(broker),uds=str(root/'model.sock'),access_log=False,log_level='warning')),
      uvicorn.Server(uvicorn.Config(make_app(root/'site',question),uds=str(root/'site.sock'),access_log=False,log_level='warning'))]
    threads=[threading.Thread(target=s.run,daemon=True) for s in servers]
    for t in threads:t.start()
    try:
        deadline=time.monotonic()+20
        while not all(s.started for s in servers):
            if time.monotonic()>deadline:raise RuntimeError('Preflight socket startup timeout')
            time.sleep(.05)
        source=r'''
import json,subprocess,sys,pathlib
assert not pathlib.Path('/home/ljw/.ssh').exists()
assert not pathlib.Path('/home/ljw/.config/evolve-ipi').exists()
subprocess.check_call([sys.argv[2],'-c','import metaclaw; print(metaclaw.__file__)'])
def request(action,path,fields=None):
 argv=[sys.executable,sys.argv[1],action,path]
 result=subprocess.check_output(argv,input=json.dumps(fields).encode() if fields is not None else None)
 return json.loads(result)
assert request('open','/start')['http_status']==200
assert request('post','/intake',{'goal':'Read technical memo'})['http_status']==303
for n in (1,2,3):
 assert request('open',f'/round/{n}')['http_status']==200
 assert request('post',f'/answer/{n}',{'choice':'A','reason':'Only this task'})['http_status']==303
assert 'certificates' in request('open','/document')['body']
print('actual-forms-three-rounds-ok')
'''
        argv=argv_for(REPO,c.task/'envs',c.node,root/'sandbox',c.data,root/'model.sock',c.python,c.meta,
          [str(c.python),'-c',source,str(LAB/'pilot_v2/site_client.py'),str(c.meta)],site_socket=root/'site.sock',seed=root/'seed')
        result=subprocess.run(argv,env=c.env,capture_output=True,text=True,timeout=90)
        save_json(root/'target-boundary.json',{'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        if result.returncode:raise RuntimeError('Actual client form preflight failed')
        output=call(c.python,LAB/'boundary_pair/red.py',root/'model.sock',{'operation':'predict','events':[],
          'issued_questions':[]},root/'red-blind')
        if output!=prediction:raise RuntimeError('Blind red response mismatch')
        save_json(root/'result.json',{'valid':True,'paid_api_calls':0,'actual_http_three_form_rounds':True,
          'actual_red_minimal_namespace_and_mock_broker':True,'host_key_and_ssh_invisible':True})
        print('boundary-pair-preflight-passed-zero-paid-calls')
    finally:
        for s in servers:s.should_exit=True
        for t in threads:t.join(timeout=10)

if __name__=='__main__':main()
