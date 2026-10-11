import json
from io import BytesIO

from PIL import Image,ImageDraw
import pytest
from fastapi.testclient import TestClient

from wechat_panel.ai import AIService
from wechat_panel.app import create_app
from wechat_panel.store import Store
from wechat_panel.skills import import_package,run_tool
from wechat_panel.web_reader import bilibili,ReadUnavailable


def service(tmp_path,model):
    ai=AIService(Store(tmp_path/'panel.db'),model_call=model)
    p=ai.data.save('providers',None,{'name':'test','model':'test','base_url':'http://localhost:9000'})
    return ai,p


@pytest.mark.parametrize('mode,expected',[('on_demand',False),('always',True),('off',False)])
def test_knowledge_only_runs_when_requested(tmp_path,mode,expected):
    ai,p=service(tmp_path,lambda *a:({'content':'你好','role':'assistant'},{}))
    try:
        k=ai.data.save('knowledge',None,{'name':'售后','documents':[{'title':'退货','content':'七天退货'}]})
        a=ai.data.save('agents',None,{'name':'agent','provider_id':p['id'],'knowledge_ids':[k['id']],'knowledge_mode':mode})
        r=ai.create_run(a['id'],'你好',dry_run=True);ai.execute(r['id'])
        trace=ai.data.detail(r['id'])['trace']
        assert any(t['step']=='知识检索' for t in trace)==expected
        assert ai.data.run(r['id'])['status']=='completed'
    finally:ai.stop()


def test_query_failure_can_be_explained_instead_of_dropping_reply(tmp_path,monkeypatch):
    def model(p,m,t):
        if m[-1]['role']=='tool':
            assert json.loads(m[-1]['content'])['ok'] is False
            return {'role':'assistant','content':'网页暂时读取不到，你可以把正文发给我。'},{}
        return {'role':'assistant','content':'','tool_calls':[{'id':'read1','type':'function','function':{'name':'read_web','arguments':json.dumps({'url':'https://example.com'})}}]},{}
    ai,p=service(tmp_path,model)
    try:
        tool=next(t for t in ai.data.list('tools') if t['builtin']=='read_web')
        a=ai.data.save('agents',None,{'name':'agent','provider_id':p['id'],'tool_ids':[tool['id']]})
        def fail(*a):raise ReadUnavailable('HTTP 401：网页不可读取')
        monkeypatch.setattr(ai.companion.services,'read_web',fail)
        r=ai.create_run(a['id'],'读这个网页',dry_run=True);ai.execute(r['id'])
        assert ai.data.run(r['id'])['status']=='completed'
        assert '正文' in ai.data.run(r['id'])['reply']
    finally:ai.stop()


def test_skill_import_binding_reference_and_real_tool_execution(tmp_path):
    package=import_package([{'path':'sum/SKILL.md','content':'---\nname: sum-skill\ndescription: 按参考计算\n---\n先读取 references/math.md，再调用 calculate。'},
        {'path':'sum/references/math.md','content':'计算 7*8，并给出结果。'}])
    def call(name,args,cid):return {'role':'assistant','content':'','tool_calls':[{'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
    def model(p,m,t):
        results=[x for x in m if x['role']=='tool']
        if not results:return call('use_skill',{'name':'sum-skill'},'s1'),{}
        if len(results)==1:return call('read_skill_file',{'name':'sum-skill','path':'references/math.md'},'s2'),{}
        if len(results)==2:return call('calculate',{'expression':'7*8'},'s3'),{}
        assert results[-1]['content']=='56'
        return {'role':'assistant','content':'结果是 56。'},{}
    ai,p=service(tmp_path,model)
    try:
        s=ai.data.save('skills',None,package);t=next(t for t in ai.data.list('tools') if t['builtin']=='calculator')
        a=ai.data.save('agents',None,{'name':'计算','provider_id':p['id'],'skill_ids':[s['id']],'tool_ids':[t['id']]})
        r=ai.create_run(a['id'],'按技能计算',dry_run=True);ai.execute(r['id'])
        result=ai.data.detail(r['id']);assert result['status']=='completed' and '56' in result['reply']
        assert any(t['step']=='加载技能' for t in result['trace'])
        assert any(t['step']=='读取技能附件' for t in result['trace'])
        with pytest.raises(ValueError):run_tool('use_skill',{'name':'unbound'},ai.data.run(r['id'])['state'])
        with pytest.raises(ValueError):ai.data.delete('skills',s['id'])
    finally:ai.stop()


def test_skill_cannot_traverse_package_or_run_yaml_constructors():
    with pytest.raises(ValueError):import_package([{'path':'../SKILL.md','content':'test'}])
    with pytest.raises(Exception):import_package([{'path':'SKILL.md','content':'---\n!!python/object/apply:os.system [echo unsafe]\n---\ntest'}])


def test_bilibili_uses_actual_subtitles_and_marks_missing_content(monkeypatch):
    def cli(name,args,timeout):
        if name=='bili':return {'ok':True,'data':{'video':{'title':'探店','duration_seconds':100}}}
        assert args[-1]=='background'
        return [{'content':'今天来到青岛餐厅。'},{'content':'点了松叶蟹卷煎饼。'}]
    monkeypatch.setattr('wechat_panel.web_reader.cli_json',cli)
    data=bilibili('https://www.bilibili.com/video/BV19cHY6TEhs?track=test')
    assert data['coverage']=='subtitles' and '松叶蟹' in data['transcript']
    def unavailable(name,args,timeout):
        if name=='bili':return cli(name,args,timeout)
        raise ReadUnavailable('no subtitles')
    monkeypatch.setattr('wechat_panel.web_reader.cli_json',unavailable)
    from types import SimpleNamespace
    monkeypatch.setattr('wechat_panel.web_reader.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=1,stdout=''))
    data=bilibili('https://www.bilibili.com/video/BV19cHY6TEhs')
    assert data['coverage']=='metadata_only' and not data['transcript']


def test_no_stale_clipboard_or_placeholder_picture(monkeypatch):
    from wechat_panel.wechat_images import fresh_clipboard_image,ready_thumbnail
    monkeypatch.setattr('win32clipboard.GetClipboardSequenceNumber',lambda:1)
    def stale():raise AssertionError('must not read stale clipboard')
    monkeypatch.setattr('wechat_panel.wechat_images.ImageGrab.grabclipboard',stale)
    with pytest.raises(ValueError):fresh_clipboard_image(1,timeout=.12)
    placeholder=Image.new('RGB',(300,180),'#fafafa');ImageDraw.Draw(placeholder).rectangle((140,70,160,90),fill='#eeeeee')
    assert not ready_thumbnail(placeholder)
    ImageDraw.Draw(placeholder).rectangle((0,0,160,180),fill='blue');assert ready_thumbnail(placeholder)


def test_skill_preview_api(tmp_path):
    with TestClient(create_app(tmp_path,demo=True)) as c:
        r=c.post('/api/ai/skills/preview',headers={'X-Panel-Request':'1'},json={'files':[{'path':'SKILL.md','content':'---\nname: demo\ndescription: 测试\n---\n调用天气工具。'}]})
        assert r.status_code==200 and r.json()['name']=='demo'
        saved=c.post('/api/ai/config/skills',headers={'X-Panel-Request':'1'},json=r.json())
        assert saved.status_code==201
        assert c.get('/api/ai/bootstrap').json()['skills'][0]['name']=='demo'


def test_image_turn_is_latest_user_message_even_after_unanswered_history(tmp_path):
    ai,p=service(tmp_path,lambda *a:({'content':'图片是市场仪表盘','role':'assistant'},{}))
    try:
        a=ai.data.save('agents',None,{'name':'vision','provider_id':p['id']})
        r=ai.create_run(a['id'],'图片',dry_run=True);state=ai.data.run(r['id'])['state']
        state.update(input_media_id='media',context=[{'role':'user','content':'昨天发的链接帮我总结'}],
                     dynamic_context={'image_description':'全球宏观经济终端'})
        messages=ai._base_messages(r,state,'')
        assert messages[-1]['role']=='user' and '全球宏观经济终端' in messages[-1]['content']
        assert '不自动补办历史' in messages[0]['content']
    finally:ai.stop()


def test_deepseek_short_tasks_disable_reasoning_to_preserve_answer_budget(monkeypatch):
    from wechat_panel.ai_tools import complete
    seen=[]
    def request(url,body,**kw):
        seen.append(body);return {'choices':[{'finish_reason':'stop','message':{'content':'可以'}}]}
    monkeypatch.setattr('wechat_panel.ai_tools.request_json',request)
    p={'model':'deepseek-flash','base_url':'https://api.deepseek.com','max_tokens':1000,'timeout':30}
    complete(p,[{'role':'user','content':'你好'}],[])
    assert seen[-1]['thinking']=={'type':'disabled'}
    complete({**p,'thinking':'enabled','_structured':True},[{'role':'user','content':'JSON'}],[])
    assert seen[-1]['thinking']=={'type':'disabled'} and seen[-1]['response_format']=={'type':'json_object'}
