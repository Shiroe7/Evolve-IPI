"""Host verifies the full official dataset; isolated target receives only attested current-task rows.

This explicit loader extension replaces in-sandbox full-data access, not the data verification.
All official startup checks other than the three attested data loaders still run unchanged.
"""
import dataclasses
import hashlib
import json
import time
from pathlib import Path

def build(data_root,stage,phase,item=None):
    from evolve_ipi import tasks
    from .protocol import TASKS
    manifest=tasks.verify_data(data_root)
    if tasks.verify_data()!=manifest:raise RuntimeError('Checkout and authoritative NAS datasets differ')
    name='file-create' if stage=='history' else ('drive-create' if stage=='website' else item.split('-test-')[0])
    original=tasks.load_task(name)
    if stage=='history':
        selected=dataclasses.replace(original,adaptation={k:original.adaptation[k] for k in TASKS[phase]['history_items']},test={})
    elif stage=='website':
        row=json.loads(json.dumps(next(iter(original.adaptation.values()))))
        row['carrier_text']='Synthetic environment placeholder. Assignment information is available only from the document workbench.'
        selected=dataclasses.replace(original,adaptation={row['id']:row},test={})
    else:selected=dataclasses.replace(original,adaptation={},test={item:original.test[item]})
    return {'host_verified_unix':time.time(),'official_data_manifest_sha256':manifest,'full_official_dataset_verified_on_host':True,
      'task':dataclasses.asdict(selected),'full_skill_order':tasks.load_skill_order(name),
      'selection':{'stage':stage,'phase':phase,'item':item},'website_neutral_carrier_extension':stage=='website'}

def startup(cfgs,combo,adapting,path,expected_sha):
    from evolve_ipi import runner,tasks
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:raise RuntimeError('Attested fixture hash mismatch')
    payload=json.loads(raw)
    if payload.get('full_official_dataset_verified_on_host') is not True:raise RuntimeError('Missing complete host integrity verification')
    if payload['official_data_manifest_sha256']!='b4a9bff3b3a5ac7c414d88374949f99504668f56b59d7ebb5e3368990350211d':
        raise RuntimeError('Unexpected official data version')
    task=tasks.Task(**payload['task'])
    if task.name!=combo.task:raise RuntimeError('Fixture is for another task')
    original=(runner.verify_data,runner.load_task,runner.load_skill_order)
    def load_task(name):
        if name!=task.name:raise RuntimeError('Unattested task requested')
        return task
    runner.verify_data=lambda:payload['official_data_manifest_sha256']
    runner.load_task=load_task
    runner.load_skill_order=lambda name:payload['full_skill_order'] if name==task.name else []
    try:return runner.startup(cfgs,[combo],adapting=adapting)
    finally:runner.verify_data,runner.load_task,runner.load_skill_order=original
