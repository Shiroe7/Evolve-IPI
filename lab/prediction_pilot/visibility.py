"""Keep trusted data integrity checks, while hiding future fixtures and researcher code from OpenClaw/tools."""
import json
from contextlib import contextmanager
from pathlib import Path
from evolve_ipi.agent import openclaw

def wrapper_source(real,repo,docs,destination):
    hidden=[repo/'data',repo/'.git',repo/'lab',repo/'tests']
    masks=[str(p) for p in hidden if p.exists()]
    code='#!/usr/bin/python3\nimport os,sys\n'
    argv=['/usr/bin/bwrap','--die-with-parent','--unshare-pid','--bind','/','/','--proc','/proc']
    for p in masks:argv+=['--tmpfs',p]
    if destination.exists():argv+=['--ro-bind',str(docs),str(destination)]
    code+='argv='+repr(argv)+'\n'
    code+='os.execv(argv[0],argv+["--",'+repr(str(real))+']+sys.argv[1:])\n'
    return code

@contextmanager
def private_fixture_view(output):
    output=Path(output);docs=output/'public-docs';docs.mkdir()
    original=openclaw._environment
    repo=Path(__file__).resolve().parents[2]
    def environment(profile):
        env,real=original(profile)
        script=profile.dir/'fixture-blind-openclaw'
        script.write_text(wrapper_source(real,repo,docs,profile.workspace/'ipi_docs'));script.chmod(0o755)
        return env,script
    openclaw._environment=environment
    try:yield docs
    finally:openclaw._environment=original
