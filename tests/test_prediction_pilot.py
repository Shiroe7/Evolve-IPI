import asyncio
import copy
import json
import sys
from pathlib import Path
import httpx
import pytest
from prediction_pilot.site import make_app
from prediction_pilot.protocol import plan,FORMS,FOOTER,TASKS,NOTICE,with_notice
from prediction_pilot.predictor import validate_input,validate_result
from broker import Broker

def input_data():
    return {'events':[{'seq':1,'method':'POST','path':'/answer/1','status':303,'note':'Only the requested summary.'}],
      'issued_pages':[],'test_specs':plan()['test_specs_public'],'base_rates':{k:.5 for k in ('local-a','local-b','drive','membership')}}

@pytest.mark.skipif(sys.platform=='win32',reason='Official task loader uses Linux fcntl; run in pinned server environment')
def test_future_tests_and_histories_are_disjoint_and_policy_identical():
    from evolve_ipi import tasks
    assert set(TASKS['exploration']['tests']).isdisjoint(TASKS['validation']['tests'])
    assert set(TASKS['exploration']['history_items']).isdisjoint(TASKS['validation']['history_items'])
    for name in ('file-create','drive-create','chat-invite'):
        task=with_notice(tasks.load_task(name))
        if task.kind=='file':assert all(task.meta['requests'][k].endswith(NOTICE) for k in ('test','authorized'))
        else:assert all(x['request_test'].endswith(NOTICE) and x['request_authorized'].endswith(NOTICE) for x in task.test.values())

@pytest.mark.parametrize('condition',list(FORMS))
def test_actual_matched_forms_keep_skips_declines_and_only_visible_values(tmp_path,condition):
    async def run():
        app=make_app(condition,'exploration',tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://desk') as c:
            assert FOOTER in (await c.get('/start')).text
            assert (await c.get('/document')).status_code==200
            assert (await c.post('/intake',data={'goal':'read memo','history':'hidden-canary'})).status_code==422
            assert (await c.post('/intake',data={'goal':'read memo'})).status_code==303
            for n in (1,2):
                assert FOOTER in (await c.get(f'/step/{n}')).text
                r=await c.post(f'/answer/{n}',data={'choice':'decline','note':'No relevant experience.'})
                assert r.status_code==303
            assert (await c.post('/answer/2',data={'choice':'decline'})).status_code==422
        text=(tmp_path/'website-events.jsonl').read_text();assert 'hidden-canary' not in text
        rows=[json.loads(x) for x in text.splitlines()]
        assert len([e for e in rows if e.get('choice')=='decline'])==2
        assert all('condition' not in e and 'kind' not in e for e in rows)
    asyncio.run(run())

def test_predictor_rejects_hidden_information_and_unobserved_citations():
    data=input_data();validate_input(data)
    for name in ('condition','skill','history','root_id','outcomes','test_content'):
        with pytest.raises(ValueError):validate_input({**data,name:'hidden'})
        other=copy.deepcopy(data);other['events'][0][name]='hidden'
        with pytest.raises(ValueError):validate_input(other)
    result={'history_class':'uncertain','history_confidence':.3,'execution_probabilities':data['base_rates'],
      'evidence':[{'seq':1,'quote':'Only the requested summary.'}],'explanation':'A task-level statement, not observed execution.'}
    validate_result(result,data);result['evidence'][0]['quote']='private memory'
    with pytest.raises(ValueError):validate_result(result,data)

def test_invalid_tool_generation_still_accounts_billable_usage(tmp_path):
    response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[
      {'id':'bad','type':'function','function':{'name':'f','arguments':'not JSON'}}]},'finish_reason':'tool_calls'}],
      'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
    broker=Broker('mock-only',tmp_path/'ledger.json',transport=httpx.MockTransport(lambda _:httpx.Response(200,json=response)))
    r=asyncio.run(broker.complete('target',{'messages':[{'role':'user','content':'synthetic'}],
      'tools':[{'type':'function','function':{'name':'f','parameters':{'type':'object'}}}]}))
    assert r.status_code==502 and broker.state['invalid']
    assert broker.state['usage_total_tokens']==15 and broker.state['usage_missing']==0
    assert broker.state['records'][0]['kind']=='tool_arguments'

def test_unknown_usage_for_paid_failure_is_explicit(tmp_path):
    broker=Broker('mock-only',tmp_path/'ledger.json',transport=httpx.MockTransport(lambda _:httpx.Response(500,json={})))
    asyncio.run(broker.complete('target',{'messages':[{'role':'user','content':'synthetic'}]}))
    assert broker.state['usage_missing']==1 and broker.state['calls']==1 and broker.state['invalid']
