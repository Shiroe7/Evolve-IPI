import subprocess
import pytest
from tool_praise.protocol import DOCUMENTS,GROUPS,PRAISE,NEUTRAL,render_tool

@pytest.mark.parametrize('body',DOCUMENTS)
@pytest.mark.parametrize('group',GROUPS)
def test_synthetic_tool_retains_correct_result_and_origin_control(body,group,tmp_path):
    script,output,extra=render_tool(body,group)
    path=tmp_path/'synthetic_report';path.write_text(script)
    assert subprocess.check_output(['/bin/sh',str(path)],text=True)==output+'\n'
    assert output.startswith(body)
    assert extra==({'neutral':NEUTRAL,'tool_praise':PRAISE}.get(group,''))
