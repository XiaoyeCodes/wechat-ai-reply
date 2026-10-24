import base64
from datetime import datetime,timedelta,timezone
from io import BytesIO
import json
import threading
import time

from PIL import Image
import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store


def photo(color='red'):
    buf=BytesIO();Image.new('RGB',(40,60),color).save(buf,format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'panel.db');calls=[]
    def model(p,m,t):
        calls.append(m)
        if isinstance(m[-1]['content'],list):return {'content':'红色书籍海报，书名《半小时漫画中国史大全集》，共7册。'},{}
        return {'content':'看到了，是《半小时漫画中国史大全集》，我结合你的问题一起回答。'},{}
    ai=AIService(store,model_call=model)
    p=ai.data.save('providers',None,{'name':'test','model':'vision-test','base_url':'http://localhost:9000'})
    agent=ai.data.save('agents',None,{'name':'Aria','provider_id':p['id'],'web_policy':'off','companion':{
        'media':{'understand_images':True,'vision_provider_id':p['id']},
        'safety':{'emotion_enabled':False,'crisis_detection':False,'require_adult':False}}})
    ai.data.settings({'enabled':True,'debounce_seconds':2})
    c=store.add_contact({'lookup':'Aria','nickname':'Aria','wechat_id':'aria','account_id':'self'})
    store.update_contact(c['id'],ai_agent_id=agent['id'],mode='ai_auto',listening=True)
    store.ingest(c['id'],[])
    yield store,ai,agent,c['id'],calls
    ai.stop()


def add(env,rows,kind,text='图片',data=None):
    store,_,_,cid,_=env
    item={'key':'row-'+str(len(rows)),'kind':kind,'text':text,'direction':'incoming'}
    if data:item['image_data']=data
    rows.append(item);store.ingest(cid,rows)
    return store.messages(cid)[-1]


def ready(env):
    store,ai,_,cid,_=env
    ai._collect()
    with store.connect() as db:db.execute('UPDATE ai_inbox SET due=0 WHERE contact_id=?',(cid,))
    ai._collect()
    runs=ai.data.runs(cid)
    return ai.data.run(runs[0]['id']) if runs else None


def sent(env,rid):
    store,ai,_,cid,_=env
    with store.connect() as db:
        db.execute("UPDATE messages SET status='confirmed' WHERE ai_run_id=? AND status='queued'",(rid,))
        db.execute("UPDATE ai_runs SET status='sent' WHERE id=?",(rid,))


def test_photo_plus_caption_plus_correction_is_one_request(env):
    store,ai,_,cid,calls=env;rows=[]
    ids=[add(env,rows,'image',data=photo())['id'],add(env,rows,'text','这本书帮我看看好看吗？')['id'],
         add(env,rows,'text','适合初学者吗？简短一点')['id']]
    r=ready(env)
    assert r['state']['turn_message_ids']==ids
    ai.execute(r['id']);r=ai.data.run(r['id'])
    assert r['status']=='queued' and len(store.pending())==1
    assert len([m for m in calls if isinstance(m[-1]['content'],list)])==1
    last=calls[-1]
    assert '半小时漫画中国史' in last[0]['content']
    assert '这本书帮我看看好看吗' in last[-1]['content'] and '适合初学者' in last[-1]['content']
    assert len([m for m in last if m['role']=='user'])==1


def test_new_text_during_vision_queues_after_reply_and_preserves_image_observation(env):
    store,ai,_,cid,calls=env;rows=[];entered=threading.Event();release=threading.Event()
    original=ai.model_call
    def slow(p,m,t):
        if isinstance(m[-1]['content'],list):entered.set();assert release.wait(5)
        return original(p,m,t)
    ai.model_call=slow
    add(env,rows,'image',data=photo());first=ready(env)
    thread=threading.Thread(target=ai.execute,args=(first['id'],));thread.start()
    try:
        assert entered.wait(5)
        add(env,rows,'text','这本书适合初学者吗？');second=ready(env)
        assert ai.data.run(first['id'])['status']=='running' and not store.pending()
    finally:release.set();thread.join(5)
    assert ai.data.run(first['id'])['status']=='queued'
    ai.execute(second['id']);assert ai.data.run(second['id'])['status']=='pending'
    sent(env,first['id'])
    ai.execute(second['id']);result=ai.data.run(second['id'])
    assert result['status']=='queued' and len(store.pending())==1
    assert len([m for m in calls if isinstance(m[-1]['content'],list)])==1
    assert '半小时漫画中国史' in calls[-1][0]['content']
    assert any(t['step']=='图片理解缓存' for t in ai.data.detail(second['id'])['trace'])


def test_two_debounce_batches_remain_distinct_fifo_turns(env):
    store,ai,_,_,calls=env;rows=[]
    add(env,rows,'text','明天帮我安排三件事');first=ready(env)
    add(env,rows,'text','上午读书');add(env,rows,'text','下午散步，晚上看电影');last=ready(env)
    ai.execute(last['id']);assert ai.data.run(last['id'])['status']=='pending'
    ai.execute(first['id']);sent(env,first['id']);ai.execute(last['id'])
    assert ai.data.run(first['id'])['status']=='sent'
    assert all(t in calls[-1][-1]['content'] for t in ('上午读书','下午散步'))
    assert '安排三件事' not in calls[-1][-1]['content']
    assert '安排三件事' in calls[-1][0]['content']
    assert len(store.pending())==1


def test_followup_reuses_recent_image_but_does_not_replay_answered_caption(env):
    store,ai,_,cid,calls=env;rows=[]
    add(env,rows,'image',data=photo());add(env,rows,'text','这是什么书？')
    first=ready(env);ai.execute(first['id']);sent(env,first['id'])
    follow=add(env,rows,'text','刚才那张封面是什么颜色？');second=ready(env);ai.execute(second['id'])
    assert second['input']==follow['text'] and second['state']['turn_message_ids']==[follow['id']]
    assert '半小时漫画中国史' in calls[-1][0]['content']
    assert len([m for m in calls if isinstance(m[-1]['content'],list)])==2
    assert any(t['step']=='图片追问核对' for t in ai.data.detail(second['id'])['trace'])
    scope=ai.data.run(second['id'])['state']['scope']
    with store.connect() as db:
        encrypted=db.execute('SELECT description FROM companion_image_observations WHERE scope=?',(scope,)).fetchone()[0]
    assert '中国史' not in encrypted


def test_multiple_images_keep_order_and_all_reach_vision(env):
    _,ai,_,_,calls=env;rows=[]
    a=add(env,rows,'image',data=photo());b=add(env,rows,'image',data=photo('blue'))
    add(env,rows,'text','比较这两张图片');r=ready(env);ai.execute(r['id'])
    observations=ai.data.run(r['id'])['state']['dynamic_context']['images']
    assert [o['message_id'] for o in observations]==[a['id'],b['id']]
    assert all(o['status']=='ready' for o in observations)
    assert len([m for m in calls if isinstance(m[-1]['content'],list)])==2


@pytest.mark.parametrize('image_first',[True,False])
def test_waits_for_original_image_and_late_ready_event_never_regresses_trigger(env,image_first):
    store,ai,_,cid,_=env;rows=[]
    if not image_first:add(env,rows,'text','这是什么书？')
    image=add(env,rows,'image')
    if image_first:add(env,rows,'text','这是什么书？')
    assert ready(env) is None
    index=0 if image_first else 1
    rows[index]['image_data']=photo();store.ingest(cid,rows)
    r=ready(env)
    assert r and r['trigger_id']==max(m['id'] for m in store.messages(cid))
    ai.execute(r['id'])
    assert ai.data.run(r['id'])['status']=='queued'
    ai._collect()
    assert len(ai.data.runs(cid))==1 and len(store.pending())==1


def test_unreadable_image_is_acknowledged_and_late_capture_does_not_cancel_task(env):
    store,ai,_,cid,calls=env;rows=[]
    image=add(env,rows,'image');add(env,rows,'text','看看这张图')
    with store.connect() as db:db.execute('UPDATE messages SET created_at=? WHERE id=?',
        ((datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat(),image['id']))
    r=ready(env);ai.execute(r['id'])
    assert ai.data.run(r['id'])['state']['dynamic_context']['images'][0]['status']=='pending'
    assert '已收到图片消息' in calls[-1][0]['content']
    rows[0]['image_data']=photo();store.ingest(cid,rows);ai._collect()
    assert ai.data.run(r['id'])['status']=='queued' and len(ai.data.runs(cid))==1


def test_manual_reply_closes_batch_and_side_effect_task_is_not_replayed(env):
    store,ai,_,cid,_=env;rows=[]
    add(env,rows,'text','旧请求');old=ready(env)
    s=old['state'];s['effect_started']=True;ai.data.update_run(old['id'],state=s,status='running')
    new=add(env,rows,'text','补充说明');r=ready(env)
    assert r['input']==new['text'] and '旧请求' not in r['input']
    rows.append({'key':'manual','kind':'text','text':'人工回复','direction':'outgoing'});store.ingest(cid,rows)
    follow=add(env,rows,'text','下一件事');last=ready(env)
    assert last['input']==follow['text']


def test_agent_switch_and_forget_do_not_reuse_other_image_memory(env):
    store,ai,agent,cid,calls=env;rows=[]
    add(env,rows,'image',data=photo());r=ready(env);ai.execute(r['id']);sent(env,r['id'])
    scope=ai.data.run(r['id'])['state']['scope']
    ai.companion.memory.forget(scope)
    with store.connect() as db:assert not db.execute('SELECT 1 FROM companion_image_observations WHERE scope=?',(scope,)).fetchone()
    add(env,rows,'text','我发过什么图？');r=ready(env);ai.execute(r['id'])
    assert not ai.data.run(r['id'])['state'].get('input_images')
    other=ai.data.save('agents',None,{k:v for k,v in {**agent,'name':'另一个角色'}.items() if k not in ('id','key_configured')})
    store.update_contact(cid,ai_agent_id=other['id'])
    add(env,rows,'text','你好');r=ready(env);ai.execute(r['id'])
    assert not ai.data.run(r['id'])['state'].get('input_images')
    assert ai.data.run(r['id'])['state']['scope']!=scope


def test_other_contact_cannot_receive_image_observation(env):
    store,ai,agent,cid,calls=env;rows=[]
    add(env,rows,'image',data=photo());r=ready(env);ai.execute(r['id']);sent(env,r['id'])
    other=store.add_contact({'lookup':'乙','nickname':'乙','wechat_id':'b','account_id':'self'})
    store.update_contact(other['id'],ai_agent_id=agent['id'],listening=True,mode='ai_auto');store.ingest(other['id'],[])
    store.ingest(other['id'],[{'key':'other','text':'这本书叫什么','kind':'text','direction':'incoming'}])
    trigger=store.messages(other['id'])[-1]
    r=ai.create_run(agent['id'],trigger['text'],other['id'],trigger['id']);ai.execute(r['id'])
    assert '半小时漫画中国史' not in calls[-1][0]['content']
    assert not ai.data.run(r['id'])['state'].get('input_images')


def test_pending_image_does_not_cancel_reply_of_the_running_queue_turn(env):
    store,ai,_,_,_=env;rows=[]
    add(env,rows,'text','你好');r=ready(env);ai.execute(r['id'])
    queued=store.pending()[0]
    assert store.ai_send_valid(queued)
    add(env,rows,'image')  # Capture has not completed and _collect has not run.
    assert store.ai_send_valid(queued)
    assert ai.data.valid(ai.data.run(r['id']))


def test_forget_during_vision_does_not_restore_image_cache(env):
    store,ai,_,_,_=env;rows=[]
    add(env,rows,'image',data=photo());r=ready(env)
    original=ai.model_call
    scope=r['state']['scope']
    def model(p,m,t):
        if isinstance(m[-1]['content'],list):ai.companion.memory.forget(scope)
        return original(p,m,t)
    ai.model_call=model;ai.execute(r['id'])
    with store.connect() as db:
        assert not db.execute('SELECT 1 FROM companion_image_observations WHERE scope=?',(scope,)).fetchone()


def test_dispatch_waits_for_previous_delivery_and_reuses_its_vision(env):
    store,ai,_,cid,calls=env;rows=[]
    entered=threading.Event();release=threading.Event();original=ai.model_call
    def model(p,m,t):
        if isinstance(m[-1]['content'],list):entered.set();assert release.wait(8)
        return original(p,m,t)
    ai.model_call=model
    ai.start()
    try:
        add(env,rows,'image',data=photo())
        assert entered.wait(5)
        add(env,rows,'text','补充：只告诉我书名')
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            runs=ai.data.runs(cid)
            if len(runs)==2:break
            time.sleep(.05)
        assert len(runs)==2
        assert sorted(r['status'] for r in runs)==['pending','running']
        assert not store.pending()
        release.set()
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and not store.pending():time.sleep(.05)
        assert len(store.pending())==1
        first=next(r for r in ai.data.runs(cid) if r['status']=='queued')
        assert any(r['status']=='pending' for r in ai.data.runs(cid))
        sent(env,first['id'])
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and not store.pending():time.sleep(.05)
        assert len(store.pending())==1
        assert len([m for m in calls if isinstance(m[-1]['content'],list)])==1
        assert '半小时漫画中国史' in calls[-1][0]['content']
    finally:
        release.set();ai.stop()
