"""Zero-paid checks: matched site, real nested OpenClaw gateway and minimum predictor namespace."""
import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path
import httpx
import uvicorn
from .campaign import Campaign,minimal_site_argv,LAB,REPO,digest
from .protocol import SPECS
from .blind import predict
from .attestation import build as build_fixture
from broker import Broker,app_for
from isolate import argv_for
from pilot.campaign import wait_socket,stop
from pilot.protocol import save_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--task-root',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    task=a.task_root.resolve();root=a.root.resolve();root.mkdir(exist_ok=False)
    python=task/'envs/main/bin/python';meta=task/'envs/prediction-metaclaw/bin/python';node=task/'node-v22.23.2-linux-arm64'
    data=Path('/data/ljw/evolve-ipi/20261109/data');seed=root/'seed';seed.mkdir();capsule=root/'experience.json'
    capsule.write_text(json.dumps({'synthetic_only':True,'previous_tasks':[]}))
    output={'history_class':'uncertain','history_confidence':.2,'execution_probabilities':{s['key']:.5 for s in SPECS},
      'evidence':[],'explanation':'Synthetic offline preflight response only.'}
    def response(request):
        body=json.loads(request.content);content=json.dumps(output) if body['model']=='offline-predictor' else 'Certificates rotate next Tuesday; restart long-running jobs afterwards. Source: synthetic technical memo.'
        return httpx.Response(200,json={'id':'offline','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],
          'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    broker=Broker('synthetic-only',root/'mock-ledger.json',models={'target':'offline-target','evolver':'offline-evolver','predictor':'offline-predictor'},
      transport=httpx.MockTransport(response))
    server=uvicorn.Server(uvicorn.Config(app_for(broker),uds=str(root/'model.sock'),access_log=False,log_level='warning'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start();site=None
    env={'PATH':str(node/'bin')+':/usr/bin:/bin','HOME':str(Path.home()),'LANG':'C.UTF-8','PYTHONPATH':str(REPO/'src')+':'+str(LAB)}
    try:
        deadline=time.monotonic()+20
        while not server.started:
            if time.monotonic()>deadline:raise RuntimeError('Mock server startup')
            time.sleep(.05)
        site_dir=root/'site-view';site_dir.mkdir();log=(root/'site.log').open('w')
        site=subprocess.Popen(minimal_site_argv(python,'information','exploration',site_dir),env=env,stdout=log,stderr=subprocess.STDOUT)
        wait_socket(site_dir/'site.sock',site)
        public_data=root/'public-data';public_data.mkdir();fixture=root/'fixture.json'
        save_json(fixture,build_fixture(data,'website','exploration'))
        command=[str(python),str(LAB/'prediction_pilot/driver.py'),'--stage','website','--phase','exploration','--fixture-sha256',digest(fixture)]
        argv=argv_for(REPO,task/'envs',node,root/'target-sandbox',public_data,root/'model.sock',python,meta,command,site_socket=site_dir/'site.sock',seed=seed)
        argv[1:1]=['--ro-bind',str(capsule),'/experience.json','--ro-bind',str(fixture),'/fixture.json','--tmpfs',str(REPO/'.git'),'--tmpfs',str(REPO/'tests')]
        done=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=180)
        save_json(root/'target-check.json',{'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr})
        if done.returncode:raise RuntimeError('Actual fixture-blind OpenClaw gateway failed; inspect target-check')
        # Independent current-row fixture probe checks hidden future data, notebook availability and proc isolation.
        code=r'''
import json,pathlib,sys
from evolve_ipi import tasks
from evolve_ipi.paths import RuntimePaths
from evolve_ipi.agent.episode import prepare_environment
from evolve_ipi.config import load_configs
repo=pathlib.Path(sys.argv[1])
assert not (repo/'data/SHA256SUMS').exists()
assert not (repo/'.git/HEAD').exists()
assert not (repo/'tests/test_prediction_pilot.py').exists()
assert pathlib.Path('/experience.json').exists()
assert not pathlib.Path('/proc',sys.argv[2],'environ').exists()
payload=json.loads(pathlib.Path('/fixture.json').read_text());task=tasks.Task(**payload['task'])
cfgs=load_configs(repo/'lab/configs');paths=RuntimePaths()
ctx=prepare_environment(task,'skills',paths,cfgs.ports,cfgs.ports.proxy,300)
assert sorted(x.name for x in (ctx.profile.workspace/'ipi_docs').iterdir())==['report_200.txt']
assert set(task.test)=={'file-create-test-200'} and not task.adaptation
try:pathlib.Path('/fixture.json').write_text('modified')
except OSError:pass
else:raise AssertionError('Attested fixture writable')
print('future-fixtures-hidden-current-document-and-synthetic-memory-visible')
'''
        risk_fixture=root/'risk-fixture.json';save_json(risk_fixture,build_fixture(data,'risk','exploration','file-create-test-200'))
        argv=argv_for(REPO,task/'envs',node,root/'visibility-sandbox',public_data,root/'model.sock',python,meta,
          [str(python),'-c',code,str(REPO),str(os.getpid())],seed=seed)
        argv[1:1]=['--ro-bind',str(capsule),'/experience.json','--ro-bind',str(risk_fixture),'/fixture.json','--tmpfs',str(REPO/'.git'),'--tmpfs',str(REPO/'tests')]
        done=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=90)
        save_json(root/'visibility-check.json',{'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr})
        if done.returncode:raise RuntimeError('Fixture visibility boundary failed')
        prediction=predict(python,LAB/'prediction_pilot/predictor.py',root/'model.sock',
          {'events':[],'issued_pages':[],'test_specs':SPECS,'base_rates':output['execution_probabilities']},root/'blind-predictor')
        if prediction!=output:raise RuntimeError('Mock predictor result mismatch')
        save_json(root/'result.json',{'valid':True,'paid_api_calls':0,'actual_openclaw_gateway':True,
          'private_test_data_hidden':True,'only_current_document_visible':True,'synthetic_memory_available':True,
          'predictor_cannot_read_history_skills_or_tests':True,'independent_site_namespace':True})
        print('prediction-preflight-passed-zero-paid-calls')
    finally:
        stop(site);server.should_exit=True;thread.join(timeout=10)

if __name__=='__main__':main()
