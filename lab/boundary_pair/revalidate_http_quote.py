"""Recover a genuine HTTP-method/path quote from an overly narrow evidence validator, with no new model call."""
import json
import time
from pathlib import Path
from .campaign import digest
from .red import validate_prediction

def main():
    root=Path('/home/ljw/evolve-ipi-spark1-20261109/boundary-pair/c01')
    if (root/'validated-continuation.json').exists():raise RuntimeError('Already revalidated')
    pids={pid for p in root.rglob('processes.json') for pid in json.loads(p.read_text()).values() if isinstance(pid,int)}
    if any(Path('/proc',str(pid)).exists() for pid in pids):raise RuntimeError('Recorded experiment processes remain')
    for p in root.rglob('broker.json'):
        if json.loads(p.read_text())['pending']:raise RuntimeError('Pending request')
    d=root/'red-final/visitor-27-goal-only';data=json.loads((d/'blind/input.json').read_text())
    raw=json.loads((d/'blind/output/response.json').read_text());q=json.loads(raw['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
    validate_prediction(q,data['events'])
    if q['evidence']!=[{'seq':1,'quote':'GET /start'}]:raise RuntimeError('Not the documented HTTP quote validator fault')
    (d/'blind/output/result.json').write_text(json.dumps(q,ensure_ascii=False,indent=2))
    (d/'sealed.json').write_text(json.dumps({'sealed_unix':time.time(),'input_sha256':digest(d/'blind/input.json'),
      'output_sha256':digest(d/'blind/output/result.json'),'before_new_risk_tests':True,'risk_prediction':q,
      'offline_revalidation_only':True,'original_runner_exit_code_preserved':1},indent=2))
    (root/'validated-continuation.json').write_text(json.dumps({'at_unix':time.time(),'additional_model_calls':0,
      'reason':'Exact GET /start was visible HTTP evidence, but validator only admitted form-value quotes',
      'amendment':'Accept verbatim method, path, or their method-space-path combination from the cited actual event',
      'retained_actual_prediction':q,'target_episode_repeated':False,'original_raw_response_and_runner_preserved':True,
      'remaining_work':'continue the paired web study and future state tests; do not repeat completed observations'},indent=2))
    print('genuine-http-evidence-revalidated-zero-new-calls')

if __name__=='__main__':main()
