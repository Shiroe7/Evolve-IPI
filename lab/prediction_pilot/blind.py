import json
import subprocess
from pathlib import Path
from .predictor import validate_input

def predict(python,source,socket,data,directory):
    validate_input(data);directory.mkdir(parents=True,exist_ok=False);(directory/'output').mkdir()
    (directory/'input.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    argv=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv','--ro-bind','/usr','/usr']
    for path in ('/lib','/lib64','/bin','/etc/ssl','/etc/ld.so.cache'):
        if Path(path).exists():argv+=['--ro-bind',path,path]
    env=python.parent.parent
    argv+=['--ro-bind',str(env),str(env),'--ro-bind',str(source),'/predictor.py','--ro-bind',str(directory/'input.json'),'/input.json',
      '--bind',str(directory/'output'),'/output','--ro-bind',str(socket),'/run/model.sock','--tmpfs','/tmp','--proc','/proc','--dev','/dev',
      '--setenv','HOME','/tmp','--setenv','LANG','C.UTF-8','--chdir','/tmp',str(python),'/predictor.py']
    done=subprocess.run(argv,capture_output=True,text=True,timeout=110)
    (directory/'runner.json').write_text(json.dumps({'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr,
      'input_mounts':['website_input','standalone_predictor','dependencies','role_specific_broker_socket'],
      'history_mounted':False,'skills_mounted':False,'test_content_or_labels_mounted':False},indent=2))
    if done.returncode:raise RuntimeError('Blind predictor invalid; preserve run, do not retry')
    return json.loads((directory/'output/result.json').read_text())
