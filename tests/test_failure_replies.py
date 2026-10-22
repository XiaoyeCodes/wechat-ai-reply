"""Temporary stores and fake models only; no desktop runtime is started."""
import json

import pytest

from wechat_panel.ai import AIService
from wechat_panel.ai_tools import ToolUncertain
from wechat_panel.failure_replies import REPLY,reason
from wechat_panel.store import Store


DETAIL=('情况是这样的：财经日报依赖本机脚本 run_daily.py，但我现在没有执行命令行脚本的权限。'
        '所以今天这份PDF我没有生成。请运行 py -3.12 scripts/run_daily.py '
        '--workspace C:/Users/other/Documents/reports，再把 run-result.json 和错误日志发给我。')


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'panel.sqlite3');ai=AIService(store,model_call=lambda *a:({'content':DETAIL},{}))
    p=ai.data.save('providers',None,dict(name='fixture',model='fixture',base_url='http://localhost:1',api_key='fixture-secret'))
    agent=ai.data.save('agents',None,dict(name='fixture',provider_id=p['id'],web_policy='model',
        companion={'memory':{'enabled':False},'safety':{'emotion_enabled':False,'crisis_detection':False}}))
    ai.data.settings({'enabled':True})
    def new_run(chat_type='direct',mode='ai_auto',text='请生成财经日报 PDF'):
        from wechat_panel.group_bridge import group_key
        c=store.add_contact(dict(lookup='fixture-'+chat_type,nickname='fixture',account_id='self',
            wechat_id=group_key('self','fixture-group') if chat_type=='group' else 'fixture-direct',
            chat_type=chat_type,group_members=['测试成员'] if chat_type=='group' else [],
            group_mention_only=True,group_self_name='机器人',group_identity_verified=True,
            group_members_verified=True,group_self_name_verified=True))
        store.update_contact(c['id'],listening=True,mode=mode,ai_agent_id=agent['id'])
        store.ingest(c['id'],[])
        incoming=dict(key='question',text=text,kind='text',direction='incoming')
        if chat_type=='group':incoming.update(sender='测试成员',sender_verified=True,mention_me=True,mention_verified=True)
        store.ingest(c['id'],[incoming]);message=store.messages(c['id'])[-1]
        return ai.create_run(agent['id'],text,c['id'],message['id'])
    yield store,ai,agent,new_run
    ai.stop()


@pytest.mark.parametrize('chat_type',['direct','group'])
@pytest.mark.parametrize('mode',['ai_auto','ai_assist'])
def test_detailed_failure_is_one_fixed_response_and_never_a_pdf(env,monkeypatch,chat_type,mode):
    store,ai,_,new_run=env
    monkeypatch.setattr('wechat_panel.financial_pdf.render_report',lambda *a,**kw:pytest.fail('Failure must stay text'))
    r=new_run(chat_type,mode);ai.execute(r['id']);run=ai.data.run(r['id'])
    assert run['reply']==REPLY and run['state']['task_failure'] and not run['state'].get('pdf')
    if mode=='ai_assist':
        assert run['status']=='draft' and not store.pending()
        ai.send_draft(r['id'],DETAIL)
    pending=store.pending()
    assert len(pending)==1 and pending[0]['text']==REPLY and pending[0]['kind']=='text'
    assert store.ai_send_valid(pending[0]) and pending[0]['contact_id']==run['contact_id']
    trace=ai.data.detail(r['id'])['trace']
    assert any(t['step']=='失败回复已收敛' and t['detail']['original_reply']==DETAIL for t in trace)
    # Late errors never generate another notification or replay the request.
    ai._fail_task(r['id'],'late error')
    assert len(store.pending())==1


def test_dry_run_shows_short_failure_and_local_diagnostics_without_outbox(env):
    store,ai,agent,_=env
    r=ai.create_run(agent['id'],'请生成财经日报PDF',dry_run=True)
    ai.execute(r['id']);run=ai.data.run(r['id'])
    assert run['status']=='failed' and run['reply']==REPLY
    assert DETAIL in json.dumps(ai.data.detail(r['id']),ensure_ascii=False)
    assert not store.pending()


@pytest.mark.parametrize('chat_type',['direct','group'])
def test_raised_operational_failure_never_leaks_details_or_secret(env,chat_type):
    store,ai,_,new_run=env
    def fail(*a):raise RuntimeError('HTTP 403: fixture-secret C:/private/run_daily.py 缺少权限')
    ai.model_call=fail;r=new_run(chat_type);ai.execute(r['id'])
    run=ai.data.run(r['id'])
    assert run['status']=='queued' and run['reply']==REPLY
    assert store.pending()[0]['text']==REPLY
    assert 'HTTP 403' in run['error'] and 'fixture-secret' not in json.dumps(ai.data.detail(r['id']))


def test_failed_reply_suppresses_existing_generated_media_and_pdf(env):
    store,ai,_,new_run=env;r=new_run();run=ai.data.run(r['id']);state=run['state']
    state.update(artifacts=[{'id':'generated','kind':'image'}],pdf={'name':'partial.pdf'},output_format='pdf')
    ai.data.update_run(r['id'],state=state)
    ai.finish(r['id'],DETAIL);run=ai.data.run(r['id'])
    assert not run['state']['artifacts'] and not run['state'].get('pdf')
    assert len(store.pending())==1 and store.pending()[0]['text']==REPLY


def test_old_unsent_technical_draft_is_also_checked_at_manual_send(env):
    store,ai,_,new_run=env;r=new_run(mode='ai_assist')
    ai.data.update_run(r['id'],status='draft',reply=DETAIL)
    ai.send_draft(r['id'],DETAIL)
    assert len(store.pending())==1 and store.pending()[0]['text']==REPLY


def test_user_requesting_preview_only_does_not_receive_even_a_failure_notice(env):
    store,ai,_,new_run=env
    r=new_run(text='请生成财经日报 PDF，先不要发送')
    ai.execute(r['id']);run=ai.data.run(r['id'])
    assert run['status']=='draft' and run['reply']==REPLY and not store.pending()


@pytest.mark.parametrize('uncertain',[False,True])
def test_cancelled_or_uncertain_work_does_not_add_a_failure_message(env,uncertain):
    store,ai,_,new_run=env;r=new_run()
    def fail(*a):
        if uncertain:raise ToolUncertain('结果待核对，不可重试')
        store.update_contact(r['contact_id'],mode='manual');raise RuntimeError('late failure')
    ai.model_call=fail;ai.execute(r['id'])
    assert ai.data.run(r['id'])['status']==('uncertain' if uncertain else 'cancelled')
    assert not store.pending()


def test_successful_fallback_source_keeps_its_answer(env):
    store,ai,agent,_=env;r=ai.create_run(agent['id'],'读取指定内容',dry_run=True)
    state=ai.data.run(r['id'])['state']
    state.update(reply_failure_reason='之前未取到数据',web_evidence=[{'status':'error','sources':[]},
        {'status':'ok','sources':['https://example.org/source']}])
    ai.data.update_run(r['id'],state=state)
    answer='主接口失败，备用来源已成功读取。正文：这是完整的原始内容。'
    ai.model_call=lambda *a:({'content':answer},{})
    ai.execute(r['id']);run=ai.data.run(r['id'])
    assert run['status']=='completed' and run['reply']==answer and not run['state'].get('task_failure')
    assert not store.pending()


@pytest.mark.parametrize('text',[
    DETAIL,'我这次无法生成图片，接口报错 HTTP 500。','本次任务未能完成，请检查 API Key。',
    '我暂时没有获取到行情数据，请稍后再试。','图片生成失败，暂时没做出来。',
    '这次查询超时了，日志里是403。',
    "I cannot complete this task because the script is unavailable.",
])
def test_explicit_operational_failure_is_detected(text):
    assert reason(text,{})


@pytest.mark.parametrize('text',[
    '脚本执行失败通常有三种原因：路径、依赖和权限，可以逐项检查。',
    '图片生成失败通常与服务配置有关。这里介绍排查步骤。',
    'HTTP 403 表示服务器拒绝访问。下面是排查方法。',
    '使用命令 `py -3.12 scripts/run_daily.py`，参数含义如下。',
    '这份报告分析了失败案例，并列出了完整数据。',
    '引用中的“我无法执行脚本”描述的是旧消息。正确解释如下。',
    '> 我没有执行脚本的权限\n\n以上是引用，下面解释其含义。',
    '请告诉我你要查询的城市。',
    '我遇到的问题已经修好了，报告已完整生成。',
])
def test_successful_answers_clarifications_and_quoted_errors_remain_unchanged(text):
    assert not reason(text,{})
