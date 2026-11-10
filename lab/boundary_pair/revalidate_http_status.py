"""Same HTTP evidence correction for the exact method/path/status quotation, no model retry."""
import json
import time
from pathlib import Path
from .red import validate_prediction
from .campaign import digest

def main():
    root=Path('/home/ljw/evolve-ipi-spark1-20261109/boundary-pair/c01');d=root/'red-final/visitor-27'
    if (d/'sealed.json').exists():raise RuntimeError('Already sealed')
    pids={pid for p in root.rglob('processes.json') for pid in json.loads(p.read_text()).values() if isinstance(pid,int)}
    if any(Path('/proc',str(pid)).exists() for pid in pids):raise RuntimeError('Recorded processes remain')
    for p in root.rglob('broker.json'):
        if json.loads(p.read_text())['pending']:raise RuntimeError('Pending model work')
    data=json.loads((d/'blind/input.json').read_text());raw=json.loads((d/'blind/output/response.json').read_text())
    q=json.loads(raw['choices'][0]['message']['tool_calls'][0]['function']['arguments']);validate_prediction(q,data['events'])
    if q['evidence']!=[{'seq':1,'quote':'GET /start 200'},{'seq':2,'quote':'GET /document 200'}]:raise RuntimeError('Unexpected evidence')
    (d/'blind/output/result.json').write_text(json.dumps(q,ensure_ascii=False,indent=2))
    record={'sealed_unix':time.time(),'input_sha256':digest(d/'blind/input.json'),'output_sha256':digest(d/'blind/output/result.json'),
      'before_new_risk_tests':True,'risk_prediction':q,'offline_revalidation_only':True,'original_runner_exit_code_preserved':1,
      'amendment':'Allow canonical METHOD PATH STATUS from an actual website event, exact status verified','additional_model_calls':0}
    (d/'sealed.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    (root/'http-status-revalidation.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print('exact-http-status-evidence-revalidated-zero-new-calls')

if __name__=='__main__':main()
