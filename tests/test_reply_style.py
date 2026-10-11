import copy
import json

import pytest

from wechat_panel.reply_style import expression, prompt
from wechat_panel.web_policy import WebPolicy


def state(**persona):
    return {'snapshot':{'agent':{'companion':{'persona':{'enabled':True,**persona}}}},'context':[]}


def test_expression_is_stable_on_resume_varies_across_turns_and_respects_opt_out():
    s=state();first=expression({'id':'run-a'},s)
    assert expression({'id':'run-a'},copy.deepcopy(s))==first
    choices=[expression({'id':str(i)},state()) for i in range(100)]
    assert 10<sum(c['emoji_allowed'] for c in choices)<45
    assert len({c['rhythm'] for c in choices})==3
    assert not expression({'id':'off'},state(emoji_probability=0))['emoji_allowed']
    assert prompt({'id':'off'},state(natural_rhythm=False))==''
    assert prompt({'id':'disabled'},state(enabled=False))==''


@pytest.mark.parametrize('previous,risk,input_text',[
    ('好呀 😊','none','你好'),('好呀','support','你好'),('好呀','none','我有点难过'),
],ids=['recent-emoji','support','sad'])
def test_emojis_yield_to_recent_usage_and_emotion(previous,risk,input_text):
    s=state(emoji_probability=1);s.update(context=[{'role':'assistant','content':previous}],analysis={'risk':risk})
    assert not expression({'id':'turn','input':input_text},s)['emoji_allowed']


def test_recent_questions_do_not_become_a_required_template():
    s=state();s['context']=[{'role':'assistant','content':'你呢？'}]
    text=prompt({'id':'turn'},s)
    assert '近期已经用问题结尾' in text and '不把“一至三句”写成固定三段' in text


@pytest.mark.parametrize('policy,input_text,material,reply,attached',[
    ('auto','这个词是什么意思？','资料','这是一个简短解释。',False),
    ('auto','这个词是什么意思？','资料'*3000,'这是一个简短解释。',False),
    ('auto','帮我总结这篇长文章','正文'*3000,'这是文章的要点。',True),
    ('auto','给我来源链接','资料','这是解释。',True),
    ('requested','总结文章','正文'*3000,'简短摘要。',False),
    ('always','解释这个词','资料','简短解释。',True),
    ('always','解释这个词，不要参考链接','资料','简短解释。',False),
],ids=['short','short-large-payload','summary','requested-source','requested-only','always','user-no-links'])
def test_citation_timing_keeps_evidence_without_forcing_links(policy,input_text,material,reply,attached):
    from types import SimpleNamespace
    traces=[];web=WebPolicy(SimpleNamespace(data=SimpleNamespace(trace=lambda *args:traces.append(args))))
    source='https://example.com/article'
    s=state();s['snapshot']['agent']['source_policy']=policy
    s.update(web_evidence=[{'status':'ok','sources':[source]}],web_context=[{'result':material}])
    assert web.check({'id':'run','input':input_text},s,reply)=='done'
    assert (source in s['web_checked_reply']) is attached
    assert source in traces[-1][2]['sources']


def test_short_answer_drops_model_reference_footer_but_preserves_delivery_link():
    from types import SimpleNamespace
    web=WebPolicy(SimpleNamespace(data=SimpleNamespace(trace=lambda *args:None)))
    s=state();s['web_evidence']=[{'status':'ok','sources':['https://example.com/source']}]
    web.check({'id':'x','input':'歌曲链接'},s,'歌曲在这里：https://example.com/song\n\n参考：https://example.com/source')
    assert s['web_checked_reply']=='歌曲在这里：https://example.com/song'
