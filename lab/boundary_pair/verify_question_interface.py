"""Two bounded real API question-format checks; then retain failed tool-format session and continue once."""
import json
import subprocess
import time
from pathlib import Path
from .calibrate import Controller,LAB
from .blind import call
from pilot.campaign import stop,wait_socket
from pilot.protocol import save_json

def main():
    task=Path('/home/ljw/evolve-ipi-spark1-20261109');root=task/'boundary-pair/c01';c=Controller(task,root)
    directory=root/'question-interface-check';directory.mkdir(exist_ok=False)
    old=root/'stages/visitor-84-website';old_red=root/'red-question-brokers/visitor-84';archive=root/'invalid-web-api-02'
    ledger=json.loads((old_red/'broker.json').read_text())
    if ledger['pending'] or [x['kind'] for x in ledger['records']]!=['tool_arguments'] or archive.exists():raise RuntimeError('Unexpected failure state')
    pids={pid for p in root.rglob('processes.json') for pid in json.loads(p.read_text()).values() if isinstance(pid,int)}
    if any(Path('/proc',str(pid)).exists() for pid in pids):raise RuntimeError('Recorded processes remain')
    data=json.loads((old/'red-calls/question-2/input.json').read_text())
    broker=None;log=(directory/'broker.log').open('w')
    try:
        broker=subprocess.Popen([str(c.python),str(LAB/'broker.py'),'--socket','red.sock','--ledger',str(directory/'broker.json'),
          '--max-calls','2','--max-total-tokens','20000','--redteam-only','--key-file',str(Path.home()/'.config/evolve-ipi/lab_api_key')],
          env=c.env,cwd=directory,stdout=log,stderr=subprocess.STDOUT)
        save_json(directory/'processes.json',{'controller':__import__('os').getpid(),'red_broker':broker.pid})
        wait_socket(directory/'red.sock',broker)
        for n in (1,2):call(c.python,LAB/'boundary_pair/red.py',directory/'red.sock',data,directory/f'check-{n}')
        status=json.loads((directory/'broker.json').read_text())
        if status['invalid'] or status['pending']:raise RuntimeError('Question interface remains invalid; do not continue')
    finally:stop(broker);log.close()
    for src,dst in ((old,archive/'stages/visitor-84-website'),(old_red,archive/'red-question-brokers/visitor-84')):
        if not src.resolve().is_relative_to(root) or not dst.resolve().is_relative_to(archive):raise RuntimeError('Unsafe archive target')
        dst.parent.mkdir(parents=True,exist_ok=True);src.rename(dst)
    save_json(root/'question-interface-amendment.json',{'at_unix':time.time(),'two_real_question_format_checks_passed':True,
      'change':'question operation uses a single question-only system prompt and JSON text; prediction keeps named native function',
      'reason':'two malformed nested tool-argument JSON responses, retained as invalid; no silent repair',
      'failed_request_usage_unknown':2,'budget_token_reservation_for_unknown_usage':40000,
      'whole_call_cap_unchanged':120,'whole_reported_token_cap_unchanged':2500000,
      'question_checks_are_api_health_only':True,'no_extra_target_or_skill_runs_for_checks':True,
      'completed_empty_state_website_and_predictions_not_rerun':True,'all_other_target_and_research_conditions_unchanged':True,
      'archive':'invalid-web-api-02'})
    print('question-json-interface-two-checks-passed; invalid-tool-session-retained')

if __name__=='__main__':main()
