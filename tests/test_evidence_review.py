"""Evidence checks distinguish quoted anchors from source membership alone."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from evidence_review import AnswerReview, QuestionPlan, validate_review, retrieve
import paperqa_bridge as bridge
from test_lab_core import fake_library, remote_settings, FakeRemote

def test_structured_retry_preserves_failed_response_and_is_bounded():
    from evidence_review import remote_json
    class Remote:
        def __init__(self,texts): self.texts=iter(texts);self.calls=0
        async def call_single(self,*args,**kwargs):
            self.calls+=1
            return SimpleNamespace(text=next(self.texts),model='remote',prompt_count=1,completion_count=1)
    async def run():
        valid=json.dumps({'requirements':[{'request':'Question','search':'Question'}]})
        trace=[];remote=Remote(['{"requirements":',valid])
        result=await remote_json(remote,'Plan',{'question':'Q'},QuestionPlan,'plan',trace)
        assert result.requirements[0].request=='Question'
        assert len(trace)==2 and trace[0]['text']=='{"requirements":'
        assert trace[0]['error_type']=='ValidationError'
        from pydantic import ValidationError
        remote=Remote(['{}','{}']);trace=[]
        with pytest.raises(ValidationError):
            await remote_json(remote,'Plan',{'question':'Q'},QuestionPlan,'plan',trace)
        assert remote.calls==2 and len(trace)==2
    asyncio.run(run())

def plan():
    return QuestionPlan(requirements=[{'request':'What is the reported effect under its conditions?',
                                      'search':'reported effect conditions'}])

def review(quote='under the stated conditions', verdict='supported'):
    return AnswerReview(claims=[{'sentence':0,'verdict':verdict,
        'anchors':[{'citation':'pqac-real','quote':quote}] if verdict=='supported' else [],
        'reason':'Source comparison'}], coverage=[{'requirement':0,'status':'answered','reason':'covered'}])

def test_literal_anchor_membership_and_exhaustive_segments():
    contexts=[SimpleNamespace(id='pqac-real',text=SimpleNamespace(text='An effect follows under the stated conditions.'))]
    segments=['An effect follows under the stated conditions (pqac-real).']
    assert not validate_review(review(),segments,contexts,plan())
    assert validate_review(review('outside knowledge invented quote'),segments,contexts,plan())
    # Uncited segments can receive source-checked anchors in a separate reviewed answer.
    assert not validate_review(review(),['Uncited claim.'],contexts,plan())
    assert validate_review(review(),['Wrong source claim (pqac-other).'],contexts,plan())
    assert validate_review(review(),segments+['Additional unsupported assertion.'],contexts,plan())
    bad=review(); bad.claims.append(bad.claims[0])
    assert validate_review(bad,segments,contexts,plan())


def test_segment_sources_cannot_borrow_another_citation():
    from evidence_review import review_payload
    contexts=[SimpleNamespace(id='pqac-a',text=SimpleNamespace(text='First original source.')),
              SimpleNamespace(id='pqac-b',text=SimpleNamespace(text='Second original source.'))]
    answer='First claim (pqac-a).\nSecond claim (pqac-b).\nUncited claim.'
    segments,payload=review_payload('Question',plan(),answer,contexts,[{'text':'Full selected manuscript.'}])
    assert len(segments)==3
    assert [x['citation'] for x in payload['segments'][0]['permitted_claim_sources']]==['pqac-a']
    assert [x['citation'] for x in payload['segments'][1]['permitted_claim_sources']]==['pqac-b']
    assert {x['citation'] for x in payload['segments'][2]['permitted_claim_sources']}=={'pqac-a','pqac-b'}
    assert payload['coverage_sources']==[{'window':0,'text':'Full selected manuscript.'}]


def test_mechanical_review_repair_preserves_answer_and_first_review(monkeypatch):
    import evidence_review
    async def run():
        contexts=[SimpleNamespace(id='pqac-real',text=SimpleNamespace(text='An effect follows under the stated conditions.'))]
        answer='An effect follows under the stated conditions (pqac-real).'
        requests=[]
        async def fake_remote(llm,instructions,payload,schema,name,trace):
            requests.append(payload)
            return review('invented literal anchor' if len(requests)==1 else 'under the stated conditions')
        monkeypatch.setattr(evidence_review,'remote_json',fake_remote)
        result=await evidence_review.review_answer(None,'Question',plan(),answer,contexts,[])
        assert result['passed'] and result['native_answer_unchanged']
        assert len(result['attempts'])==2 and result['attempts'][0]['mechanical_errors']
        assert result['attempts'][0]['review']['claims'][0]['anchors'][0]['quote']=='invented literal anchor'
        assert requests[0]['segments']==requests[1]['segments']
        assert requests[1]['previous_invalid_review']==result['attempts'][0]['review']
        requests.clear()
        async def unsupported_remote(*args):
            requests.append(args[2]);return review(verdict='unsupported')
        monkeypatch.setattr(evidence_review,'remote_json',unsupported_remote)
        rejected=await evidence_review.review_answer(None,'Question',plan(),answer,contexts,[])
        assert not rejected['passed'] and len(requests)==1
    asyncio.run(run())

def test_rrf_order_is_stable_and_required_sources_are_present(tmp_path):
    async def run():
        docs,citations=await bridge.build_paperqa_docs(fake_library(tmp_path),chunk_mode='page_windows')
        a,first=await retrieve(docs,citations,'effects',plan(),FakeRemote(name='fake'),required=['a','b'],limit=4)
        docs.texts.reverse()
        b,second=await retrieve(docs,citations,'effects',plan(),FakeRemote(name='fake'),required=['a','b'],limit=4)
        assert first['selected_names']==second['selected_names']
        assert [c.id for c in a]==[c.id for c in b]
        assert {citations[c.text.name]['paper_id'] for c in a}=={'a','b'}
    asyncio.run(run())

def test_failed_review_does_not_overwrite_native_answer(tmp_path,monkeypatch):
    import evidence_review
    async def run():
        monkeypatch.setattr(bridge,'load_settings',lambda _:remote_settings())
        monkeypatch.setattr(bridge,'embedding_for_settings',lambda _:FakeRemote(name='fake'))
        async def fake_plan(*args): return plan()
        async def fake_query(*args,**kwargs):
            return {'question':'Q','answer':'Unsupported assertion','decision':'answered',
                'native_session':{'raw_answer':'Unsupported assertion'},
                'citation_audit':{'references':[],'citation_identity_pass':True}}
        async def fake_review(*args):
            return {'passed':False,'mechanical_errors':[], 'review':{
                'claims':[{'sentence':0,'verdict':'unsupported','anchors':[],'reason':'Not in evidence'}],
                'coverage':[{'requirement':0,'status':'omitted','reason':'Missing conditions'}]}}
        monkeypatch.setattr(evidence_review,'plan_question',fake_plan)
        monkeypatch.setattr(evidence_review,'review_answer',fake_review)
        monkeypatch.setattr(bridge,'query_paperqa',fake_query)
        result=await bridge.ask_paperqa(fake_library(tmp_path),'Q','unused')
        assert result['decision']=='blocked_evidence_review'
        assert result['counts_as_refusal'] is False
        assert len(result['attempt_paths'])==2
        originals=[json.loads(Path(p).read_text()) for p in result['attempt_paths']]
        assert all(r['answer']=='Unsupported assertion' and r['decision']=='answered' for r in originals)
    asyncio.run(run())

def test_invalid_citations_receive_one_repair_and_both_originals_are_saved(tmp_path,monkeypatch):
    import evidence_review
    async def run():
        monkeypatch.setattr(bridge,'load_settings',lambda _:remote_settings())
        monkeypatch.setattr(bridge,'embedding_for_settings',lambda _:FakeRemote(name='fake'))
        async def fake_plan(*args):return plan()
        calls=[]
        async def fake_query(*args,**kwargs):
            calls.append(kwargs['query_session'])
            return {'question':'Q','answer':'Withheld','decision':'blocked_invalid_citations',
                'native_session':{'raw_answer':'Invented citation (pqac-madeup).'},
                'citation_audit':{'invalid_citations':['pqac-madeup'],'references':[],
                                  'citation_identity_pass':False}}
        monkeypatch.setattr(evidence_review,'plan_question',fake_plan)
        monkeypatch.setattr(bridge,'query_paperqa',fake_query)
        result=await bridge.ask_paperqa(fake_library(tmp_path),'Q','unused')
        assert result['decision']=='blocked_invalid_citations' and len(calls)==2
        assert len(result['attempt_paths'])==2
        assert all(json.loads(Path(p).read_text(encoding='utf-8'))['native_session']['raw_answer']==
                   'Invented citation (pqac-madeup).' for p in result['attempt_paths'])
    asyncio.run(run())

def test_context_failures_are_isolated_between_concurrent_queries():
    import logging
    async def run():
        logger=logging.getLogger('paper2agent-test-context')
        logger.setLevel(logging.ERROR)
        ready=asyncio.Event();started=[]
        async def query(name,count):
            handler=bridge._ContextFailureHandler();logger.addHandler(handler)
            started.append(name)
            if len(started)==2:ready.set()
            await ready.wait()
            try:
                for _ in range(count):logger.error('Context failure in %s',name)
                await asyncio.sleep(0)
                return handler.failures
            finally:logger.removeHandler(handler);handler.reset_context()
        assert await asyncio.gather(query('first',1),query('second',3))==[1,3]
    asyncio.run(run())
