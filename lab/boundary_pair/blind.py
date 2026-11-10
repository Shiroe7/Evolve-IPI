"""Filesystem-enforced minimum red view; no code checkout, task data, seed or target outputs are mounted."""
import json
import subprocess
from pathlib import Path
from .red import validate_input

def call(python,red_file,socket,data,directory):
    validate_input(data);directory.mkdir(parents=True,exist_ok=False)
    (directory/'input.json').write_text(json.dumps(data,ensure_ascii=False,indent=2));(directory/'output').mkdir()
    env_root=python.parent.parent
    argv=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv','--ro-bind','/usr','/usr']
    for path in ('/lib','/lib64','/bin','/etc/ssl','/etc/ld.so.cache'):
        if Path(path).exists():argv+=['--ro-bind',path,path]
    argv+=['--ro-bind',str(env_root),str(env_root),'--ro-bind',str(red_file),'/red.py',
      '--ro-bind',str(directory/'input.json'),'/input.json','--bind',str(directory/'output'),'/output',
      '--ro-bind',str(socket),'/run/model.sock','--tmpfs','/tmp','--proc','/proc','--dev','/dev',
      '--setenv','HOME','/tmp','--setenv','LANG','C.UTF-8','--chdir','/tmp',str(python),'/red.py']
    done=subprocess.run(argv,capture_output=True,text=True,timeout=110)
    (directory/'runner.json').write_text(json.dumps({'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr,
      'mounted_inputs':['one_website_input','standalone_red_code','main_env_dependencies','red_broker_socket'],
      'host_home_mounted':False,'seed_mounted':False,'target_trace_mounted':False},indent=2))
    if done.returncode:raise RuntimeError('Blind red call failed; see isolated runner evidence')
    return json.loads((directory/'output/result.json').read_text())
