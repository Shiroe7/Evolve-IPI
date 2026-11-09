"""Website-only export. No arbitrary question-to-risk scoring or fitted predictor in this pilot."""
import argparse
import hashlib
import json
from pathlib import Path

FIELDS={'seq','elapsed_s','method','path','status','goal','query','action','validation'}
PATHS={'/start','/intake','/decline','/exit','/public','/document','/lookup','/help',
       '/documents/weather','/documents/project','/other'}


def export(path):
    if not path.is_file(): raise RuntimeError('Missing log is not a nonvisit')
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        if set(row)-FIELDS or row['path'] not in PATHS: raise ValueError('Forbidden feature field/path')
    return rows


def summarize(observations):
    pages=[]
    for obs in observations:
        if set(obs) != {'mode','events'}:raise ValueError('Unexpected observation metadata')
        events=obs['events']
        for e in events:
            if set(e)-FIELDS:raise ValueError('Forbidden event metadata')
        pages.append({'mode':obs['mode'],
          'requests':len(events),'visited':bool(events),'single_request':len(events)==1,
          'goal_submitted':any(e['path']=='/intake' and e['status']==303 for e in events),
          'explicit_decline':any(e.get('action')=='explicit_decline' for e in events),
          'explicit_exit':any(e.get('action')=='explicit_exit' for e in events),
          'searches':sum(e['path']=='/lookup' for e in events),
          'goals':[e['goal'] for e in events if 'goal' in e],
          'paths':[e['path'] for e in events]})
    return {'pages':pages,'risk_prediction':None,'status':'functionality_only_no_fitted_classifier'}


if __name__=='__main__':
    import sys
    src,out=map(Path,sys.argv[1:])
    data=json.loads(src.read_text())
    if set(data)!={'observations'}:raise ValueError('Unexpected metadata')
    result=summarize(data['observations'])
    result['input_sha256']=hashlib.sha256(src.read_bytes()).hexdigest()
    result['code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
