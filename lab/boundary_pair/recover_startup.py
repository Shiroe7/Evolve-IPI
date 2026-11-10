"""One explicit audited zero-call startup repair; never retries model work or behavior."""
import json
import os
import time
from pathlib import Path

def main():
    root=Path('/home/ljw/evolve-ipi-spark1-20261109/boundary-pair/c01').resolve()
    stage=root/'stages/visitor-27-website';red=root/'red-question-brokers/visitor-27'
    if (root/'startup-recovery.json').exists():raise RuntimeError('Already recovered once')
    outcomes=json.loads((stage/'outcome.json').read_text())
    if outcomes['valid'] or outcomes['calls'] or outcomes['tokens']:raise RuntimeError('Not the known zero-call invalid startup')
    if 'MetaClaw proxy' not in outcomes['agent_report'].get('error',''):raise RuntimeError('Unexpected failure')
    for path in (stage/'broker.json',red/'broker.json'):
        ledger=json.loads(path.read_text())
        if ledger['calls'] or ledger['pending'] or ledger['usage_total_tokens']:raise RuntimeError('Cannot replace paid/pending work')
    processes=json.loads((stage/'processes.json').read_text())
    if any(Path('/proc',str(pid)).exists() for pid in processes.values() if pid):raise RuntimeError('Recorded processes still exist')
    # No red broker is still running with this unique campaign-specific command path.
    for directory in Path('/proc').iterdir():
        if not directory.name.isdecimal() or directory.stat().st_uid!=os.getuid():continue
        try:text=(directory/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
        except (OSError,ProcessLookupError):continue
        if 'broker.py' in text and str(red/'broker.json') in text:raise RuntimeError('Unrecorded red broker still exists')
    moves=[(stage,stage.with_name('visitor-27-website-invalid-env-1')),
      (red,red.with_name('visitor-27-invalid-env-1')),
      (root/'web-protocol.json',root/'web-protocol-invalid-env-1.json')]
    for src,dst in moves:
        if not src.resolve().is_relative_to(root) or not dst.resolve().is_relative_to(root) or dst.exists():raise RuntimeError('Unsafe archive target')
    for src,dst in moves:src.rename(dst)
    (root/'startup-recovery.json').write_text(json.dumps({'at_unix':time.time(),'reason':'editable MetaClaw import pointed outside new checkout mount',
      'paid_calls':0,'tokens':0,'recorded_processes_gone':processes,'action':'dedicated web MetaClaw environment rebound to web checkout; preserve invalid records; replacement only',
      'calibration_not_repeated':True,'history_or_skill_changes':False,'archives':[str(dst.relative_to(root)) for src,dst in moves]},indent=2))
    print('audited-zero-call-startup-archive-complete')

if __name__=='__main__':main()
