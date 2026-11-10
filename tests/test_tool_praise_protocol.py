import subprocess
import pytest
from tool_praise.protocol import DOCUMENTS,GROUPS,PRAISE,NEUTRAL,render_tool,clean_history_fixture

@pytest.mark.parametrize('body',DOCUMENTS)
@pytest.mark.parametrize('group',GROUPS)
def test_synthetic_tool_retains_correct_result_and_origin_control(body,group,tmp_path):
    script,output,extra=render_tool(body,group)
    path=tmp_path/'synthetic_report';path.write_text(script)
    assert subprocess.check_output(['/bin/sh',str(path)],text=True)==output+'\n'
    assert output.startswith(body)
    assert extra==({'neutral':NEUTRAL,'tool_praise':PRAISE}.get(group,''))

def test_neutral_message_is_close_in_length_and_has_no_evaluation():
    assert abs(len(NEUTRAL)-len(PRAISE))<=5
    assert not any(x in NEUTRAL.lower() for x in ('correct','good','excellent','well done'))

def test_background_fixture_has_no_embedded_action_and_does_not_modify_input():
    before={'task':{'meta':{'injection_line':'create {tgt}'},'adaptation':{'a':{'body':'do something'}}}}
    after=clean_history_fixture(before)
    assert before['task']['meta']['injection_line']=='create {tgt}'
    assert after['task']['meta']['injection_line']==''
    assert after['task']['adaptation']['a']['body']=='Synthetic background note. No follow-up action is requested.'
