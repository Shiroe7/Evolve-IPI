"""Adapt using real toolResult messages from a fixed local synthetic command."""
import json
from pathlib import Path
from tool_praise.protocol import DOCUMENTS,REQUEST,FOLLOWUP,PRAISE,render_tool
from pilot.driver import agent,check_turn
from pilot.protocol import save_json,skill_manifest
from evolve_ipi.adapt import skills
from evolve_ipi.adapt.library import lib_sha
from evolve_ipi.agent import openclaw
from evolve_ipi.environment import workspace

def evolve_history(start,out,kind,phase,refs,result):
    library=out/'library';library.mkdir();records=[]
    with agent(start,out,library,True) as (ctx,watch):
        close=skills._closure(library/skills.HISTORY_FILE,start.cfgs.ports.proxy,
          start.cfgs.models['deepseek-flash-lab'].skill_served_name,max_tokens=2048)
        for number,body in enumerate(DOCUMENTS,1):
            profile=ctx.profile
            workspace.restore(profile.workspace,ctx.workspace_snapshot);workspace.guard(profile.workspace)
            openclaw.delete_session(profile);openclaw.archive_deleted_transcripts(profile,ctx.transcript_archive)
            script,output,extra=render_tool(body,kind)
            tool=profile.workspace/'synthetic_report'
            tool.write_text(script)
            tool.chmod(0o755)
            first=openclaw.run_turn(profile,REQUEST,180);check_turn(first)
            calls=openclaw.transcript_tool_calls(profile,first.session_id)
            used=any('synthetic_report' in str(c.get('arguments',{})) for c in calls)
            feedback=(PRAISE+' ' if kind=='user_praise' else '')+FOLLOWUP
            second=openclaw.run_turn(profile,feedback,180);check_turn(second)
            if first.session_id!=second.session_id:raise RuntimeError('Feedback changed session')
            records.append({'item':number,'user_request':REQUEST,'tool_output':output,'tool_extra':extra,
              'user_followup':feedback,'tool_called_candidate':used,'first_reply':first.reply,'final_reply':second.reply,
              'tool_calls':openclaw.transcript_tool_calls(profile,first.session_id),'closure':close(first.session_id)})
            save_json(out/'history.json',records);watch.check()
            if skills.drain(library/skills.HISTORY_FILE,number,poll_s=1,stable_polls=2,max_polls=90,check=watch.check)!=number:
                raise RuntimeError('Evolution count mismatch')
    skills.run_guards(library/skills.HISTORY_FILE,out/'proxy/conversations.jsonl',out/'guards',paths=start.paths)
    save_json(out/'experience.json',{'synthetic_only':True,'previous_tasks':[]})
    result.update(history=records,skill_sha=lib_sha(library),skill_manifest=skill_manifest(library),
      evolutions=2,tool_called_count=sum(x['tool_called_candidate'] for x in records))
