"""Trusted serial controller: independent histories, matched websites, sealed predictions, fixed tests."""
import argparse
import hashlib
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

LAB=Path(__file__).resolve().parents[1];REPO=LAB.parent;sys.path.insert(0,str(LAB))
from prediction_pilot.protocol import plan,sha,canonical,CONDITIONS,TASKS,SPECS,CAPS,GLOBAL_CALLS,GLOBAL_TOKENS,FORMS,WELCOME
from prediction_pilot.blind import predict
from prediction_pilot.predictor import validate_input
from prediction_pilot.attestation import build as build_fixture
from pilot.protocol import save_json,skill_manifest
from pilot.campaign import wait_socket,stop
from isolate import argv_for

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def minimal_site_argv(python,condition,phase,directory):
    env_root=python.parent.parent;source=LAB/'prediction_pilot'
    argv=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv','--ro-bind','/usr','/usr']
    for path in ('/lib','/lib64','/bin'):
        if Path(path).exists():argv+=['--ro-bind',path,path]
    argv+=['--ro-bind',str(env_root),str(env_root),'--dir','/code/prediction_pilot']
    for name in ('__init__.py','protocol.py','site.py'):argv+=['--ro-bind',str(source/name),'/code/prediction_pilot/'+name]
    return argv+['--bind',str(directory),'/site-out','--tmpfs','/tmp','--proc','/proc','--dev','/dev','--chdir','/site-out',
      '--setenv','HOME','/tmp','--setenv','PYTHONPATH','/code','--setenv','LANG','C.UTF-8',str(python),'-m','prediction_pilot.site',
      '--condition',condition,'--phase',phase,'--directory','/site-out']

class Campaign:
    def __init__(self,task,root,nas):
        self.task,self.root,self.nas=task,root,nas
        self.python=task/'envs/main/bin/python';self.meta=task/'envs/prediction-metaclaw/bin/python'
        self.node=task/'node-v22.23.2-linux-arm64';self.data=Path('/data/ljw/evolve-ipi/20261109/data')
        self.env={'PATH':str(self.node/'bin')+':/usr/bin:/bin','HOME':str(Path.home()),'LANG':'C.UTF-8',
          'PYTHONPATH':str(REPO/'src')+':'+str(LAB),'PYTHONUNBUFFERED':'1'}
        root.mkdir(parents=True,exist_ok=True);nas.mkdir(parents=True,exist_ok=True)
        protocol=plan();protocol.update(source_commit=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
          predictor_sha256=digest(LAB/'prediction_pilot/predictor.py'),dataset_manifest_sha256=digest(self.data/'SHA256SUMS'),
          data_visibility='host verifies full official data, hash-attested current rows enter isolated target; repo data/git/tests masked; official remaining startup checks unchanged',
          website_visibility='separate minimal namespace with own page code and log directory only, no history, Skills, test data or outcomes')
        path=root/'protocol.json'
        if path.exists() and json.loads(path.read_text())!=json.loads(canonical(protocol)):
            original=json.loads(path.read_text());amendment=root/'citation-format-amendment.json'
            changed={k for k in set(original)|set(protocol) if original.get(k)!=json.loads(canonical(protocol)).get(k)}
            if not amendment.exists() or not changed<={'source_commit','predictor_sha256'}:raise RuntimeError('Protocol changed; do not silently resume')
            approved=json.loads(amendment.read_text())
            if approved['original_protocol_sha256']!=digest(path) or approved['new_source_commit']!=protocol['source_commit'] or approved['new_predictor_sha256']!=protocol['predictor_sha256']:
                raise RuntimeError('Format-only protocol amendment does not match current source')
            save_json(root/'effective-protocol.json',protocol)
        self.protocol=protocol
        if not path.exists():
            save_json(path,protocol);save_json(nas/'protocol.json',protocol)
            self.pin_tests()
    def pin_tests(self):
        from evolve_ipi import tasks
        pinned={}
        for phase,task in TASKS.items():
            pinned[phase]=[]
            for ident in task['tests']:
                family=ident.split('-test-')[0];data=tasks.load_task(family);item=data.test[ident]
                pinned[phase].append({'id':ident,'item':item,'task_meta':data.meta,'item_sha256':sha(item)})
        save_json(self.root/'fixed-tests-researcher-only.json',pinned)
        save_json(self.nas/'fixed-tests-researcher-only.json',pinned)
        save_json(self.root/'preregistration-seal.json',{'at_unix':time.time(),'protocol_sha256':digest(self.root/'protocol.json'),
          'fixed_tests_sha256':digest(self.root/'fixed-tests-researcher-only.json'),'before_any_paid_call':True})
    def usage(self):
        ledgers=[json.loads(p.read_text()) for p in self.root.rglob('broker.json')]
        if any(x['pending'] for x in ledgers):raise RuntimeError('Pending API work requires audit')
        return {'calls':sum(x['calls'] for x in ledgers),'tokens':sum(x['usage_total_tokens'] for x in ledgers),
          'unknown_usage':sum(x['usage_missing'] for x in ledgers)}
    def allowance(self,calls,tokens):
        u=self.usage()
        if u['unknown_usage']:raise RuntimeError('Unknown usage: stop paid work and audit')
        if u['calls']+calls>GLOBAL_CALLS or u['tokens']+tokens>GLOBAL_TOKENS:raise RuntimeError('Insufficient whole-study allowance')
    def admit(self,name):
        for check in range(3):
            memory=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))/1024**2
            psi=float(next(x for x in Path('/proc/pressure/memory').read_text().splitlines() if x.startswith('full ')).split('avg10=')[1].split()[0])
            with (self.root/'resource-admission.jsonl').open('a') as f:f.write(json.dumps({'stage':name,'available_gib':memory,'psi_full_avg10':psi,'check':check+1,'at_unix':time.time()})+'\n')
            if memory<24 or psi>=1:raise RuntimeError('Resource admission failed before starting a stage')
            time.sleep(2)
    def broker(self,directory,cap,only_predictor=False):
        log=(directory/'broker.log').open('w')
        argv=[str(self.python),str(LAB/'broker.py'),'--socket','model.sock','--ledger',str(directory/'broker.json'),
          '--max-calls',str(cap['calls']),'--max-total-tokens',str(cap['tokens']),'--target-thinking','disabled','--evolver-thinking','disabled',
          '--key-file',str(Path.home()/'.config/evolve-ipi/lab_api_key')]
        if only_predictor:argv+=['--predictor-only']
        child=subprocess.Popen(argv,cwd=directory,env=self.env,stdout=log,stderr=subprocess.STDOUT)
        return child,log
    def stage(self,uid,stage,phase,*,kind=None,condition=None,item=None,refs=None):
        suffix=condition or item or kind
        d=self.root/'stages'/f'{uid}-{stage}-{suffix}';outfile=d/'outcome.json'
        if outfile.exists():
            old=json.loads(outfile.read_text())
            if old['valid']:return d,old
            raise RuntimeError('Invalid stage retained; cannot silently rerun')
        if d.exists():raise RuntimeError('Incomplete stage requires process audit')
        cap=CAPS[stage];self.allowance(cap['calls'],cap['tokens']);self.admit(d.name);d.mkdir(parents=True)
        fixture=d/'fixture.json';save_json(fixture,build_fixture(self.data,stage,phase,item));public_data=d/'public-data';public_data.mkdir()
        result={'uid':uid,'stage':stage,'study_phase':phase,'website_condition':condition,'item':item,
          'started_unix':time.time(),'valid':False,'attempts':1,'caps':cap}
        broker=child=site=None;handles=[]
        try:
            broker,log=self.broker(d,cap);handles.append(log);wait_socket(d/'model.sock',broker)
            extra=[]
            if stage=='website':
                site_dir=d/'site-view';site_dir.mkdir();log=(d/'site.log').open('w');handles.append(log)
                site=subprocess.Popen(minimal_site_argv(self.python,condition,phase,site_dir),env=self.env,stdout=log,stderr=subprocess.STDOUT)
                wait_socket(site_dir/'site.sock',site);extra=['--site-socket',str(site_dir/'site.sock')]
            args=['--stage',stage,'--phase',phase,'--fixture-sha256',digest(fixture)]
            if kind:args+=['--kind',kind]
            if item:args+=['--item',item]
            if refs:args+=['--references',*refs]
            seed=None if stage=='history' else self.root/'instances'/uid/'seed'
            argv=argv_for(REPO,self.task/'envs',self.node,d/'sandbox',public_data,d/'model.sock',self.python,self.meta,
              [str(self.python),str(LAB/'prediction_pilot/driver.py'),*args],
              site_socket=d/'site-view/site.sock' if stage=='website' else None,seed=seed)
            at=argv.index(str(self.python))
            argv[at:at]=['--ro-bind',str(fixture),'/fixture.json','--tmpfs',str(REPO/'.git'),'--tmpfs',str(REPO/'tests')]
            if stage!='history':argv[1:1]=['--ro-bind',str(self.nas/'capsules'/f'{uid}.json'),'/experience.json']
            log=(d/'agent.log').open('w');handles.append(log)
            child=subprocess.Popen(argv,env=self.env,stdout=log,stderr=subprocess.STDOUT)
            save_json(d/'processes.json',{'controller':os.getpid(),'broker':broker.pid,'target_supervisor':child.pid,'site':site.pid if site else None})
            result['exit_code']=child.wait(timeout=cap['seconds'])
            p=d/'sandbox/outputs/stage.json';result['agent_report']=json.loads(p.read_text()) if p.exists() else {}
            result['valid']=result['exit_code']==0 and result['agent_report'].get('valid') is True
            if stage=='website':
                events=[json.loads(x) for x in (d/'site-view/website-events.jsonl').read_text().splitlines()]
                if any(x['status']>=500 for x in events):result['valid']=False
        except Exception as e:result.update(error_type=type(e).__name__,error=str(e)[:600])
        finally:
            stop(child);stop(site);stop(broker)
            for h in handles:h.close()
            if (d/'broker.json').exists():
                ledger=json.loads((d/'broker.json').read_text());result.update(calls=ledger['calls'],tokens=ledger['usage_total_tokens'])
                result['valid']=result['valid'] and not ledger['invalid'] and not ledger['pending'] and not ledger['usage_missing']
                if stage!='history' and any(x['role']!='target' for x in ledger['records']):result['valid']=False
            result['finished_unix']=time.time();save_json(outfile,result)
        print(json.dumps({'stage':d.name,'valid':result['valid'],'calls':result.get('calls'),'tokens':result.get('tokens'),
          'executed':result.get('agent_report',{}).get('unauthorized_executed')}),flush=True)
        if not result['valid']:raise RuntimeError('Invalid stage stops paid study: '+d.name)
        return d,result
    def unit(self,uid,kind,phase):
        d=self.root/'instances'/uid
        if (d/'unit.json').exists():return json.loads((d/'unit.json').read_text())
        if d.exists():raise RuntimeError('Partial unit requires audit')
        d.mkdir(parents=True);seed=d/'seed';seed.mkdir();refs=[]
        if kind=='empty':capsule={'synthetic_only':True,'previous_tasks':[]}
        else:
            refs=['SYN-'+secrets.token_hex(4).upper() for _ in range(2)]
            stage,result=self.stage(uid,'history',phase,kind=kind,refs=refs)
            for p in (stage/'sandbox/outputs/library').rglob('SKILL.md'):
                dst=seed/p.relative_to(stage/'sandbox/outputs/library');dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
            capsule=json.loads((stage/'sandbox/outputs/experience.json').read_text())
        path=self.nas/'capsules'/f'{uid}.json';save_json(path,capsule)
        record={'uid':uid,'kind_researcher_only':kind,'study_phase':phase,'references_researcher_only':refs,
          'seed_manifest':skill_manifest(seed),'capsule_sha256':digest(path),'created_unix':time.time(),'new_independent_history':kind!='empty'}
        save_json(d/'unit.json',record);return record
    def predict_website(self,uid,condition,stage_dir,priors):
        directory=self.root/'predictions'/f'{uid}-{condition}';seal=directory/'sealed.json'
        if seal.exists():return json.loads(seal.read_text())
        if directory.exists():raise RuntimeError('Incomplete prediction requires audit, not retry')
        self.allowance(1,16000);directory.mkdir(parents=True)
        data={'events':[json.loads(x) for x in (stage_dir/'site-view/website-events.jsonl').read_text().splitlines()],
          'issued_pages':json.loads((stage_dir/'site-view/issued-pages.json').read_text()),'test_specs':SPECS,'base_rates':priors}
        validate_input(data);broker=log=None
        try:
            broker,log=self.broker(directory,{'calls':1,'tokens':16000},True);wait_socket(directory/'model.sock',broker)
            save_json(directory/'processes.json',{'controller':os.getpid(),'predictor_broker':broker.pid})
            result=predict(self.python,LAB/'prediction_pilot/predictor.py',directory/'model.sock',data,directory/'blind')
            ledger=json.loads((directory/'broker.json').read_text())
            if ledger['invalid'] or ledger['pending'] or ledger['usage_missing']:raise RuntimeError('Predictor broker invalid')
            sealed={'sealed_unix':time.time(),'result':result,'input_sha256':digest(directory/'blind/input.json'),
              'output_sha256':digest(directory/'blind/output/result.json'),'predictor_sha256':self.protocol['predictor_sha256']}
            save_json(seal,sealed);return sealed
        finally:stop(broker);log.close() if log else None
    def phase(self,phase):
        summary=self.root/(phase+'-complete.json')
        if summary.exists():return json.loads(summary.read_text())
        if phase=='validation':
            frozen=json.loads((self.root/'frozen-validation.json').read_text())
            if frozen['forms_sha256']!=sha({'forms':FORMS,'welcome':WELCOME}) or frozen['predictor_sha256']!=self.protocol['predictor_sha256']:raise RuntimeError('Frozen validation changed')
            priors=frozen['base_rates']
        else:priors={s['key']:.5 for s in SPECS}
        units=[self.unit(uid,kind,phase) for uid,kind in self.protocol['roots'][phase]]
        observations={};orders={}
        for index,unit in enumerate(units):
            uid=unit['uid'];order=list(CONDITIONS[index%3:]+CONDITIONS[:index%3]);orders[uid]=order;observations[uid]={}
            for condition in order:
                directory,_=self.stage(uid,'website',phase,condition=condition)
                observations[uid][condition]=self.predict_website(uid,condition,directory,priors)
        from .sealing import seal_predictions
        seal_predictions(self.root/(phase+'-predictions-sealed.json'),observations,orders,priors)
        outcomes={}
        for unit in units:
            uid=unit['uid'];risks=[];utilities=[]
            for ident in TASKS[phase]['tests']:
                _,result=self.stage(uid,'risk',phase,item=ident);risks.append(result)
            for family in ('file-create','drive-create','chat-invite'):
                ident=next(x for x in TASKS[phase]['tests'] if x.split('-test-')[0]==family)
                _,result=self.stage(uid,'utility',phase,item=ident);utilities.append(result)
            if skill_manifest(self.root/'instances'/uid/'seed')!=unit['seed_manifest']:raise RuntimeError('Root frozen library changed')
            if digest(self.nas/'capsules'/f'{uid}.json')!=unit['capsule_sha256']:raise RuntimeError('Root synthetic capsule changed')
            outcomes[uid]={'risks':risks,'utilities':utilities}
        result={'phase':phase,'units':units,'predictions':observations,'outcomes':outcomes,'base_rates':priors,'usage':self.usage(),'completed_unix':time.time()}
        save_json(summary,result);save_json(self.nas/summary.name,result)
        print(json.dumps({'completed_phase':phase,'usage':self.usage()}),flush=True)
        return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--task-root',type=Path,required=True);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--nas',type=Path,required=True);p.add_argument('--phase',choices=TASKS,required=True);a=p.parse_args()
    c=Campaign(a.task_root.resolve(),a.root.resolve(),a.nas.resolve())
    save_json(c.root/(a.phase+'-controller.json'),{'pid':os.getpid(),'started_unix':time.time()})
    c.phase(a.phase)

if __name__=='__main__':main()
