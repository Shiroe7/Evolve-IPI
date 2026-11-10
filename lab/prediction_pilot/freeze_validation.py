"""Freeze forms, predictor and exploration-only base rates before any new validation history is created."""
import argparse
import json
import time
from pathlib import Path
from .protocol import SPECS,FORMS,WELCOME,sha
from .campaign import digest
from pilot.protocol import save_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--nas',type=Path,required=True);a=p.parse_args()
    root=a.root;data=json.loads((root/'exploration-complete.json').read_text())
    protocol=json.loads((root/('effective-protocol.json' if (root/'effective-protocol.json').exists() else 'protocol.json')).read_text())
    if any((root/'instances'/uid).exists() for uid,kind in protocol['roots']['validation']):raise RuntimeError('Validation already started')
    if (root/'frozen-validation.json').exists():raise RuntimeError('Already frozen')
    totals={s['family']:[0,0] for s in SPECS}
    for record in data['outcomes'].values():
        for spec,result in zip(SPECS,record['risks']):
            if not result['valid']:raise RuntimeError('Exploration contains invalid fixed-test outcome')
            totals[spec['family']][0]+=int(result['agent_report']['unauthorized_executed']);totals[spec['family']][1]+=1
    raw={s['key']:totals[s['family']][0]/totals[s['family']][1] for s in SPECS}
    smooth={s['key']:(totals[s['family']][0]+1)/(totals[s['family']][1]+2) for s in SPECS}
    frozen={'frozen_unix':time.time(),'forms_sha256':sha({'forms':FORMS,'welcome':WELCOME}),'predictor_sha256':protocol['predictor_sha256'],
      'form_changes':[],'decision':'retain the prespecified forms; no question edits selected on risk-prediction success',
      'base_rates':raw,'base_rates_smoothed':smooth,'exploration_counts_by_family':totals,
      'exploration_complete_sha256':digest(root/'exploration-complete.json'),'before_validation_history_creation':True,
      'heldout_tests_unchanged':True,'validation_outcomes_not_used':True}
    save_json(root/'frozen-validation.json',frozen);save_json(a.nas/'frozen-validation.json',frozen)
    print(json.dumps(frozen,ensure_ascii=False))

if __name__=='__main__':main()
