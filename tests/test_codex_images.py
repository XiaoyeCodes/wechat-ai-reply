import base64
from io import BytesIO
import json
import time
from types import SimpleNamespace
import uuid

from PIL import Image
import pytest

from wechat_panel.ai import AIService
from wechat_panel.ai_models import Service
from wechat_panel.ai_tools import ToolUncertain
from wechat_panel import codex_images
from wechat_panel.image_generation import reference_for
from wechat_panel.store import Store


def png(color='red',mode='RGB'):
    out=BytesIO();Image.new(mode,(24,24),color).save(out,format='PNG');return out.getvalue()


@pytest.fixture
def ai(tmp_path):
    ai=AIService(Store(tmp_path/'panel.db'),model_call=lambda *args:({'content':'图片处理完成'},{}))
    yield ai
    ai.stop()


def agent_for(ai,**media):
    service=next(s for s in ai.data.list('services') if s['driver']=='codex_image')
    provider=ai.data.save('providers',None,{'name':'test','base_url':'http://localhost:9999','model':'mock'})
    tool=next(t for t in ai.data.list('tools') if t.get('builtin')=='generate_image')
    agent=ai.data.save('agents',None,{'name':'image test','provider_id':provider['id'],'tool_ids':[tool['id']],
        'web_policy':'off','companion':{'media':{'image_service_id':service['id'],**media},
        'safety':{'emotion_enabled':False,'crisis_detection':False}},
        'steps':[{'type':'tool','tool_id':tool['id'],'arguments':{'prompt':'换成蓝色背景','mode':'edit'}},
                 {'type':'reply'}]})
    return agent,tool


def test_service_and_skill_seeded_idempotently_without_binding(ai):
    services=[s for s in ai.data.list('services') if s['driver']=='codex_image']
    assert len(services)==1 and services[0]['timeout']==300 and not services[0]['key_configured']
    assert len([s for s in ai.data.list('skills') if s['name']=='codex-image-generation'])==1
    from wechat_panel.image_generation import install_defaults
    install_defaults(ai.data)
    assert len([s for s in ai.data.list('services') if s['driver']=='codex_image'])==1
    assert ai.data.list('agents')==[]


def test_codex_config_and_other_timeouts():
    service=Service(name='draw',category='image',driver='codex_image')
    assert service.timeout==300 and service.url=='' and service.clear_secret
    with pytest.raises(ValueError):Service(name='draw',category='image',driver='codex_image',api_key='secret')
    with pytest.raises(ValueError):Service(name='draw',category='search',driver='codex_image')
    with pytest.raises(ValueError):Service(name='draw',category='image',driver='image_http',url='http://localhost:99',timeout=300)


def test_login_requires_chatgpt_and_does_not_echo_secrets(monkeypatch):
    monkeypatch.setattr(codex_images.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='',stderr='Logged in using ChatGPT'))
    codex_images.login_status('codex',{})
    monkeypatch.setattr(codex_images.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='API key: private-secret',stderr=''))
    with pytest.raises(ValueError,match='ChatGPT') as error:codex_images.login_status('codex',{})
    assert 'private-secret' not in str(error.value)


@pytest.mark.parametrize('reference',[None,base64.b64encode(png()).decode()])
def test_cli_protocol_and_image_result(monkeypatch,tmp_path,reference):
    root=tmp_path/'generated_images';tid=str(uuid.uuid4());folder=root/tid;folder.mkdir(parents=True)
    payload=png();(folder/'image.png').write_bytes(payload)
    monkeypatch.setattr(codex_images,'executable',lambda:'codex.exe')
    monkeypatch.setattr(codex_images,'environment',lambda:{'CODEX_HOME':str(tmp_path)})
    monkeypatch.setattr(codex_images,'login_status',lambda *args:None)
    def fake(command,prompt,cwd,env,timeout,cancelled):
        assert command[-1]=='-' and '--ignore-user-config' in command and 'read-only' in command
        assert 'shell_tool' in command and 'multi_agent' in command
        assert '换成蓝色背景' in prompt and '--dangerously-bypass-approvals-and-sandbox' not in command
        assert ('--image' in command)==bool(reference)
        if reference:assert (cwd/'reference.png').is_file()
        return 0,json.dumps({'type':'thread.started','thread_id':tid})+'\n'+json.dumps({'type':'turn.completed'})
    monkeypatch.setattr(codex_images,'_run',fake)
    assert codex_images.generate('换成蓝色背景',reference)==payload


def test_result_rejects_old_files_and_fabricated_paths(tmp_path):
    tid=str(uuid.uuid4());folder=tmp_path/tid;folder.mkdir();(folder/'old.png').write_bytes(png())
    output=json.dumps({'type':'thread.started','thread_id':tid})+'\n'+json.dumps({'type':'turn.completed'})
    with pytest.raises(RuntimeError,match='真实图片'):codex_images.result_image(output,tmp_path,time.time()+100)
    with pytest.raises(RuntimeError,match='任务编号'):
        codex_images.result_image(json.dumps({'type':'thread.started','thread_id':'../../elsewhere'}),tmp_path,0)
    with pytest.raises(ToolUncertain):codex_images.result_image(output.replace('turn.completed','turn.failed'),tmp_path,0)


def test_edit_uses_uploaded_image_without_vision_and_preserves_approval(ai,monkeypatch):
    agent,tool=agent_for(ai)
    scope=ai.companion.memory.scope(agent['id'],session_id='edit')
    image=ai.companion.memory.media(scope,png())
    calls=[]
    monkeypatch.setattr(codex_images,'generate',lambda prompt,reference,timeout,cancelled:
        calls.append(reference) or png('blue'))
    run=ai.create_run(agent['id'],'把背景换成蓝色',dry_run=True,session_id='edit',input_media_id=image['id'])
    ai.execute(run['id']);saved=ai.data.run(run['id'])
    assert saved['status']=='waiting_approval' and not calls
    with ai.store.connect() as db:approval=db.execute('SELECT id FROM ai_approvals WHERE run_id=?',(run['id'],)).fetchone()[0]
    ai.approve(approval,True);ai.execute(run['id']);saved=ai.data.run(run['id'])
    assert saved['status']=='completed',saved['error']
    assert calls==[base64.b64encode(ai.companion.memory.read_media(image['id'])['data']).decode()]
    artifact=saved['state']['artifacts'][0]
    assert ai.companion.memory.read_media(artifact['id'],scope)['kind']=='image'
    assert saved['state']['dynamic_context']['images'][0]['media_id']==image['id']


def test_reference_scoping_and_modes(ai):
    agent,_=agent_for(ai);memory=ai.companion.memory
    scope=memory.scope(agent['id'],session_id='one');other=memory.scope(agent['id'],session_id='two')
    first=memory.media(scope,png());foreign=memory.media(other,png('blue'))
    state={'scope':scope,'input_media_id':first['id']};run={'contact_id':None}
    assert reference_for(ai.companion,run,state,{}, {'mode':'generate'}) is None
    assert reference_for(ai.companion,run,state,{}, {'mode':'edit'})
    with pytest.raises(ValueError,match='当前会话'):
        reference_for(ai.companion,run,state,{}, {'mode':'edit','reference_media_id':foreign['id']})
    with pytest.raises(ValueError,match='上传图片'):
        reference_for(ai.companion,run,{'scope':scope},{},{'mode':'edit'})
    with pytest.raises(ValueError,match='edit'):
        reference_for(ai.companion,run,state,{}, {'mode':'generate','reference_media_id':first['id']})
    with pytest.raises(KeyError):
        reference_for(ai.companion,run,{'scope':scope,'input_media_id':foreign['id']},{},{'mode':'edit'})


def test_multiple_current_images_require_choice(ai):
    agent,_=agent_for(ai);scope=ai.companion.memory.scope(agent['id'],session_id='multiple')
    images=[ai.companion.memory.media(scope,png()) for _ in range(2)]
    state={'scope':scope,'input_images':[{'media_id':i['id'],'media_scope':scope,'current':True} for i in images]}
    with pytest.raises(ValueError,match='多张'):
        reference_for(ai.companion,{'contact_id':None},state,{}, {'mode':'edit'})


def test_transparent_output_is_preserved(ai):
    image=ai.companion.memory.media('test',png((255,0,0,0),'RGBA'))
    stored=Image.open(BytesIO(ai.companion.memory.read_media(image['id'])['data']))
    assert stored.mode=='RGBA' and stored.getpixel((0,0))[3]==0


def test_api_key_environment_is_not_inherited(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private');monkeypatch.setenv('CODEX_API_KEY','private')
    assert 'OPENAI_API_KEY' not in codex_images.environment() and 'CODEX_API_KEY' not in codex_images.environment()


@pytest.mark.parametrize('cancelled',[False,True])
def test_timeout_and_cancellation_kill_process_without_retry(tmp_path,monkeypatch,cancelled):
    calls=[]
    class Process:
        stdin=BytesIO()
        stopped=False
        def poll(self):return 1 if self.stopped else None
        def kill(self):self.stopped=True;calls.append('kill')
        def wait(self,timeout):calls.append('wait');return 1
    process=Process()
    monkeypatch.setattr(codex_images.subprocess,'Popen',lambda *args,**kw:calls.append('start') or process)
    error=RuntimeError if cancelled else ToolUncertain
    with pytest.raises(error):
        codex_images._run(['codex'],'test',tmp_path,{},0,lambda:cancelled)
    assert calls==['start','kill','wait']


def test_tool_upgrade_keeps_existing_permissions(ai):
    tool=next(t for t in ai.data.list('tools') if t.get('builtin')=='generate_image')
    old={k:v for k,v in tool.items() if k not in ('id','key_configured')}
    old.update(approval='auto',enabled=False,description='old description',
        parameters={'type':'object','properties':{'prompt':{'type':'string'}},'required':['prompt']})
    ai.data.save('tools',tool['id'],old)
    from wechat_panel.ai_store import AIStore
    fresh=AIStore(ai.store).get('tools',tool['id'])
    assert fresh['approval']=='auto' and fresh['enabled'] is False
    assert fresh['parameters']['properties']['mode']['enum']==['generate','edit']


def test_image_capability_can_be_added_to_agent_with_thirty_tools(ai):
    agent,image_tool=agent_for(ai)
    existing=[t['id'] for t in ai.data.list('tools') if t['id']!=image_tool['id']][:30]
    while len(existing)<30:
        t=ai.data.save('tools',None,{'name':'extra_'+str(len(existing)),'description':'test clock','kind':'builtin','builtin':'clock'})
        existing.append(t['id'])
    body={k:v for k,v in agent.items() if k not in ('id','key_configured')}
    body['tool_ids']=existing+[image_tool['id']]
    saved=ai.data.save('agents',agent['id'],body)
    assert saved['tool_ids']==existing+[image_tool['id']]
    assert saved['companion']['media']['image_service_id']
