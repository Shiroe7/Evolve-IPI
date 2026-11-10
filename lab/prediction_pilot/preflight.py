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
from .visibility import wrapper_source
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
        command=[str(python),str(LAB/'prediction_pilot/driver.py'),'--stage','website','--phase','exploration']
        argv=argv_for(REPO,task/'envs',node,root/'target-sandbox',data,root/'model.sock',python,meta,command,site_socket=site_dir/'site.sock',seed=seed)
        argv[1:1]=['--ro-bind',str(capsule),'/experience.json']
        done=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=180)
        save_json(root/'target-check.json',{'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr})
        if done.returncode:raise RuntimeError('Actual fixture-blind OpenClaw gateway failed; inspect target-check')
        # Independent inner namespace probe checks hidden future data, notebook availability and proc isolation.
        code=r'''
import json,pathlib,subprocess,sys
from prediction_pilot.visibility import wrapper_source
repo=pathlib.Path(sys.argv[1]);docs=pathlib.Path('/outputs/one-doc');docs.mkdir();(docs/'current.txt').write_text('current only')
ws=pathlib.Path('/tmp/ws/ipi_docs');ws.mkdir(parents=True);(ws/'future.txt').write_text('must hide')
script=pathlib.Path('/tmp/fixture-blind-python');script.write_text(wrapper_source(pathlib.Path(sys.executable),repo,docs,ws));script.chmod(0o755)
inner="""import pathlib,sys
repo=pathlib.Path(sys.argv[1]);ws=pathlib.Path(sys.argv[2])
assert not (repo/'data/SHA256SUMS').exists()
assert not (repo/'lab/prediction_pilot/protocol.py').exists()
assert not (repo/'.git/HEAD').exists()
assert (ws/'current.txt').read_text()=='current only'
assert not (ws/'future.txt').exists()
assert pathlib.Path('/experience.json').exists()
assert not pathlib.Path('/proc',sys.argv[3],'environ').exists()
print('future-fixtures-hidden-current-document-and-synthetic-memory-visible')
"""
subprocess.run([str(script),'-c',inner,str(repo),str(ws),str(__import__('os').getpid())],check=True)
'''
        argv=argv_for(REPO,task/'envs',node,root/'visibility-sandbox',data,root/'model.sock',python,meta,
          [str(python),'-c',code,str(REPO)],seed=seed)
        argv[1:1]=['--ro-bind',str(capsule),'/experience.json']
        # isolate sets PYTHONPATH=src only; add lab for this trusted preflight code before using the wrapper.
        code=code.replace('import json,pathlib,subprocess,sys','import json,pathlib,subprocess,sys\nsys.path.insert(0,'+repr(str(LAB))+')')
        idx=argv.index('-c');argv[idx+1]=code
        done=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=90)
        save_json(root/'visibility-check.json',{'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr})
        if done.returncode:raise RuntimeError('Fixture visibility boundary failed')
        prediction=predict(python,LAB/'prediction_pilot/predictor.py',root/'model.sock',
          {'events':[],'issued_pages':[],'test_specs':SPECS,'base_rates':output['execution_probabilities']},root/'blind-predictor')
        if prediction!=output:raise RuntimeError('Mock predictor result mismatch')
        save_json(root/'result.json',{'valid':True,'paid_api_calls':0,'actual_openclaw_nested_gateway':True,
          'private_test_data_hidden':True,'only_current_document_visible':True,'synthetic_memory_available':True,
          'predictor_cannot_read_history_skills_or_tests':True,'independent_site_namespace':True})
        print('prediction-preflight-passed-zero-paid-calls')
    finally:
        stop(site);server.should_exit=True;thread.join(timeout=10)

if __name__=='__main__':main()
