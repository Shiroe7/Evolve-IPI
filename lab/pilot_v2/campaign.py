"""Trusted serial controller with 240-call/6M-token whole-campaign limits and fresh sandboxes."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LAB=Path(__file__).resolve().parents[1];REPO=LAB.parent;sys.path.insert(0,str(LAB))
from pilot_v2.protocol import VERSION,ROOTS,MODES,FAMILIES,GLOBAL_CALLS,GLOBAL_TOKENS,CAP,WEB_TASKS,items,document_corpus
from pilot_v2.features import export
from pilot.campaign import wait_socket,stop
from pilot.protocol import save_json,skill_manifest


class Campaign:
    def __init__(self,task,root):
        self.task,self.root=task,root
        self.python=task/'envs/main/bin/python';self.meta=task/'envs/intake-clean-metaclaw/bin/python'
        self.node=task/'node-v22.23.2-linux-arm64';self.data=Path('/data/ljw/evolve-ipi/20261109/data')
        self.env={'PATH':str(self.node/'bin')+':/usr/bin:/bin','HOME':str(Path.home()),'LANG':'C.UTF-8',
                  'PYTHONPATH':str(REPO/'src')+':'+str(LAB),'PYTHONUNBUFFERED':'1'}
        self.prior=task/'pilot-v2/campaign-function-01'
        if not (self.prior/'instances/u01/unit.json').exists():raise RuntimeError('Development controller must finish first')
        prior_ledgers=[json.loads(p.read_text()) for p in (self.prior/'stages').glob('*/broker.json')]
        if any(d['pending'] for d in prior_ledgers):raise RuntimeError('Development has pending paid work')
        prior_usage={'calls':sum(d['calls'] for d in prior_ledgers),'tokens':sum(d['usage_total_tokens'] for d in prior_ledgers)}
        self.protocol={'version':VERSION,'roots':ROOTS,'modes':MODES,'web_tasks':WEB_TASKS,
          'risk_items':{f:items(f) for f in FAMILIES},'utility_items':{f:items(f)[0] for f in FAMILIES},
          'source_commit':subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
          'features_sha256':hashlib.sha256((LAB/'pilot_v2/features.py').read_bytes()).hexdigest(),
          'limits':CAP,'global_calls':GLOBAL_CALLS,'global_tokens':GLOBAL_TOKENS,'attempts_per_stage':1,
          'thinking':{'target':'disabled','evolver':'disabled'},'evolution':False,'new_independent_users':0,
          'cost':None,'cost_status':'laboratory_relay_price_unverified',
          'prior_development_usage_in_global_budget':prior_usage,'prior_development_path':str(self.prior),
          'website_workspace':'original_service_factory_with_instruction_free_placeholder',
          'resource_admission':{'available_gib':24,'full_psi_avg10_below':1,'checks':3,'interval_s':2},
          'corpus':document_corpus(self.data)}
        root.mkdir(parents=True,exist_ok=True)
        p=root/'protocol.json'
        if p.exists() and json.loads(p.read_text())!=json.loads(json.dumps(self.protocol)):raise RuntimeError('Protocol changed')
        save_json(p,self.protocol)
        save_json(Path('/data/ljw/evolve-ipi/pilot-v2')/root.name/'protocol.json',self.protocol)
        self.failed=[]

    def usage(self):
        ledgers=[json.loads(p.read_text()) for base in (self.prior,self.root) for p in (base/'stages').glob('*/broker.json')]
        if any(d['pending'] for d in ledgers):raise RuntimeError('Pending model work requires audit, not restart')
        return sum(d['calls'] for d in ledgers),sum(d['usage_total_tokens'] for d in ledgers)

    def admit(self,name):
        stable=0;began=time.monotonic()
        while time.monotonic()-began<600:
            vals={x.split(':')[0]:int(x.split(':')[1].split()[0]) for x in Path('/proc/meminfo').read_text().splitlines()}
            line=next(x for x in Path('/proc/pressure/memory').read_text().splitlines() if x.startswith('full '))
            psi=float(line.split('avg10=')[1].split()[0]);available=vals['MemAvailable']/1024**2
            stable=stable+1 if available>=24 and psi<1 else 0
            with (self.root/'resource-admission.jsonl').open('a') as f:
                f.write(json.dumps({'stage':name,'at_unix':time.time(),'available_gib':available,'full_psi_avg10':psi,'stable_checks':stable})+'\n')
            if stable>=3:return
            time.sleep(2)
        raise RuntimeError('Resources unavailable; no stage started')

    def stage(self,uid,phase,seed,*,mode=None,task_index=0,family=None,item_index=0):
        suffix=f'{mode}-{task_index}' if phase=='website' else f'{family}-{item_index}'
        directory=self.root/'stages'/f'{uid}-{phase}-{suffix}';outfile=directory/'outcome.json'
        if outfile.exists():
            old=json.loads(outfile.read_text())
            if not old['valid']:raise RuntimeError('Invalid stage cannot be silently retried')
            return directory,old
        if directory.exists():raise RuntimeError('Unfinished stage needs process audit')
        calls,tokens=self.usage()
        if calls>=GLOBAL_CALLS or tokens+CAP['tokens']>GLOBAL_TOKENS:
            raise RuntimeError('Insufficient remaining whole-campaign allowance for another stage')
        call_allowance=min(CAP['calls'],GLOBAL_CALLS-calls)
        self.admit(directory.name);directory.mkdir(parents=True)
        result={'unit':uid,'phase':phase,'mode':mode,'task_index':task_index if phase=='website' else None,
          'family':family,'item_index':item_index if phase!='website' else None,'valid':False,
          'attempts':1,'limits':CAP,'broker_call_allowance':call_allowance,'started_unix':time.time()}
        broker=site=child=None;handles=[]
        try:
            log=(directory/'broker.log').open('w');handles.append(log)
            broker=subprocess.Popen([str(self.python),str(LAB/'broker.py'),'--socket','model.sock',
              '--ledger',str(directory/'broker.json'),'--max-calls',str(call_allowance),'--max-total-tokens',str(CAP['tokens']),
              '--target-thinking','disabled','--evolver-thinking','disabled','--key-file',str(Path.home()/'.config/evolve-ipi/lab_api_key')],
              env=self.env,cwd=directory,stdout=log,stderr=subprocess.STDOUT)
            wait_socket(directory/'model.sock',broker)
            extra=[]
            if phase=='website':
                log=(directory/'site.log').open('w');handles.append(log)
                site=subprocess.Popen([str(self.python),'-m','pilot_v2.site','--mode',mode,'--log',str(directory/'website-events.jsonl'),
                  '--socket','site.sock','--data',str(self.data)],env=self.env,cwd=directory,stdout=log,stderr=subprocess.STDOUT)
                wait_socket(directory/'site.sock',site);extra=['--site-socket',str(directory/'site.sock')]
            driver=['--phase',phase,'--task-index',str(task_index),'--item-index',str(item_index)]
            if family:driver+=['--family',family]
            command=[str(self.python),str(LAB/'isolate.py'),'--conda-root',str(self.task/'envs'),'--node-root',str(self.node),
              '--work',str(directory/'sandbox'),'--data',str(self.data),'--socket',str(directory/'model.sock'),
              '--main-python',str(self.python),'--meta-python',str(self.meta),'--seed',str(seed),*extra,'--',str(self.python),str(LAB/'pilot_v2/driver.py'),*driver]
            log=(directory/'agent.log').open('w');handles.append(log)
            child=subprocess.Popen(command,env=self.env,stdout=log,stderr=subprocess.STDOUT)
            save_json(directory/'live-processes.json',{'controller':os.getpid(),'broker':broker.pid,'site':site.pid if site else None,'agent_supervisor':child.pid})
            code=child.wait(timeout=CAP['seconds'])
            report=directory/'sandbox/outputs/phase.json'
            result.update(exit_code=code,agent_report=json.loads(report.read_text()) if report.exists() else {})
            result['valid']=code==0 and result['agent_report'].get('valid') is True
            if phase=='website' and any(e['status']>=500 for e in export(directory/'website-events.jsonl')):
                result.update(valid=False,error_type='WebsiteServerError')
        except Exception as e:result.update(error_type=type(e).__name__,error=str(e)[:500])
        finally:
            stop(child);stop(site);stop(broker)
            for h in handles:h.close()
            ledger=directory/'broker.json'
            if ledger.exists():
                d=json.loads(ledger.read_text());result.update(broker_calls=d['calls'],reported_tokens=d['usage_total_tokens'],broker_invalid=d['invalid'],broker_pending=d['pending'])
                result['valid']=result['valid'] and not d['invalid'] and not d['pending']
            result['finished_unix']=time.time();save_json(outfile,result)
        print(json.dumps({k:result.get(k) for k in ('unit','phase','mode','family','task_index','item_index','valid','broker_calls','reported_tokens','error_type')}),flush=True)
        if not result['valid']:self.failed.append(directory.name)
        return directory,result

    def seal(self,uid,observations):
        d=self.root/'features'/uid;d.mkdir(parents=True)
        save_json(d/'input.json',{'observations':observations});out=d/'blind-output';out.mkdir()
        argv=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv','--ro-bind','/usr','/usr']
        for path in ('/lib','/lib64','/bin'):
            if Path(path).exists():argv+=['--ro-bind',path,path]
        argv+=['--ro-bind',str(self.task/'envs'),str(self.task/'envs'),'--ro-bind',str(LAB/'pilot_v2/features.py'),'/features.py',
          '--ro-bind',str(d/'input.json'),'/input.json','--bind',str(out),'/output','--tmpfs','/tmp','--proc','/proc','--dev','/dev',
          str(self.python),'/features.py','/input.json','/output/features.json']
        subprocess.run(argv,env=self.env,check=True,timeout=30)
        output=json.loads((out/'features.json').read_text())
        if output['code_sha256']!=self.protocol['features_sha256']:raise RuntimeError('Feature extractor changed')
        save_json(d/'sealed.json',{'before_risk_unix':time.time(),'feature_sha256':hashlib.sha256((out/'features.json').read_bytes()).hexdigest(),
          'input_sha256':hashlib.sha256((d/'input.json').read_bytes()).hexdigest(),'input_mounts':['website_input','extractor','dependencies'],
          'risk_prediction':None,'status':'functionality_no_classifier'})

    def unit(self,uid,oldid):
        d=self.root/'instances'/uid;d.mkdir(parents=True,exist_ok=True)
        if (d/'unit.json').exists():return json.loads((d/'unit.json').read_text())
        if (d/'seed').exists():raise RuntimeError('Partial root requires audit, not re-creation')
        source=self.task/'pilot/campaign-v1/instances'/oldid
        old=json.loads((source/'unit.json').read_text());manifest=old['seed_manifest']
        if skill_manifest(source/'seed')!=manifest:raise RuntimeError('Original frozen library changed')
        shutil.copytree(source/'seed',d/'seed');save_json(d/'source-manifest.json',{'source_root':oldid,'skills':manifest})
        websites=[];observations=[]
        order=MODES if uid!='u02' else tuple(reversed(MODES))
        for mode in order:
            for index in (0,1):
                stage,result=self.stage(uid,'website',d/'seed',mode=mode,task_index=index)
                websites.append(result)
                if not result['valid']:raise RuntimeError('Website stage invalid; inspect before further model work')
                observations.append({'mode':mode,'events':export(stage/'website-events.jsonl')})
        self.seal(uid,observations)
        risks=[]
        for family in FAMILIES:
            for index in (0,1):
                _,result=self.stage(uid,'risk',d/'seed',family=family,item_index=index);risks.append(result)
                if len(self.failed)>=2:raise RuntimeError('Two invalid stages stop batch')
        utilities=[]
        for family in FAMILIES:
            _,result=self.stage(uid,'utility',d/'seed',family=family);utilities.append(result)
            if len(self.failed)>=2:raise RuntimeError('Two invalid stages stop batch')
        if skill_manifest(d/'seed')!=manifest:raise RuntimeError('Copied frozen library changed')
        record={'unit':uid,'source_root':oldid,'condition':old['condition'],'seed_manifest':manifest,
                'websites':websites,'risks':risks,'utilities':utilities,'new_independent_user':False}
        save_json(d/'unit.json',record);return record


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task-root',type=Path,required=True);p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--units',type=int,choices=(1,3),required=True);a=p.parse_args()
    c=Campaign(a.task_root.resolve(),a.campaign.resolve())
    save_json(c.root/'controller.json',{'pid':os.getpid(),'units_requested':a.units,'started_unix':time.time()})
    for uid,oldid in ROOTS[:a.units]:
        record=c.unit(uid,oldid)
        if uid=='u01' and not all(x['valid'] for x in record['websites']+record['risks']+record['utilities']):
            raise RuntimeError('First complete root must validate before expansion')
    print(json.dumps({'status':'completed_requested_roots','roots':a.units,'usage':c.usage(),'invalid_stages':c.failed}),flush=True)
