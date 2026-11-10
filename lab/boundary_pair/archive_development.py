"""Audit and retain the incomplete web-schema development batch before one clean paired run."""
import json
import time
from pathlib import Path

def main():
    root=Path('/home/ljw/evolve-ipi-spark1-20261109/boundary-pair/c01').resolve()
    destination=root/'development-web-01'
    if destination.exists():raise RuntimeError('Development archive already exists')
    if (root/'all-predictions-sealed.json').exists() or (root/'results.json').exists():raise RuntimeError('Cannot reset completed paired experiment')
    error=root/'stages/visitor-27-website/site-invalid.json'
    if json.loads(error.read_text())['kind']!='red_question_error':raise RuntimeError('Unexpected failure')
    pids={pid for p in root.rglob('processes.json') for pid in json.loads(p.read_text()).values() if isinstance(pid,int)}
    if any(Path('/proc',str(pid)).exists() for pid in pids):raise RuntimeError('Recorded processes remain')
    for p in root.rglob('broker.json'):
        if json.loads(p.read_text())['pending']:raise RuntimeError('Pending model work')
    destination.mkdir();(destination/'stages').mkdir()
    stages=[p for p in (root/'stages').iterdir() if not p.name.startswith('cal-')]
    paths=[root/x for x in ('red-final','red-question-brokers','web-protocol.json','web-protocol-invalid-env-1.json','resource-scope.txt','startup-recovery.json')]
    moves=[(p,destination/'stages'/p.name) for p in stages]+[(p,destination/p.name) for p in paths if p.exists()]
    for src,dst in moves:
        if not src.resolve().is_relative_to(root) or not dst.resolve().is_relative_to(destination) or dst.exists():raise RuntimeError('Unsafe move target')
    usage={'calls':0,'tokens':0}
    for src,dst in moves:src.rename(dst)
    for p in destination.rglob('broker.json'):
        ledger=json.loads(p.read_text());usage['calls']+=ledger['calls'];usage['tokens']+=ledger['usage_total_tokens']
    (root/'repair-history.json').write_text(json.dumps({'at_unix':time.time(),'development_usage_retained':usage,
      'invalid_runs':['zero-call MetaClaw import startup','paid red question returned prediction schema; site 503'],
      'valid_development_websites':1,'development_website_records_pooled_into_clean_pair':False,
      'new_paid_model_retries_inside_a_stage':0,'calibration_repeated':False,
      'repair':'distinct forced function schemas for red question and prediction, no change to Skills, public task or first question',
      'archive':'development-web-01','recorded_process_count_gone':len(pids)},indent=2))
    print(json.dumps({'archived_development_web_usage':usage,'calibration_preserved':True}))

if __name__=='__main__':main()
