"""Exercise saved companion prompts and model in an isolated store; never send WeChat."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wechat_panel.ai import AIService
from wechat_panel.ai_models import Agent,Provider
from wechat_panel.ai_store import unprotect
from wechat_panel.store import Store


def main():
    source=sqlite3.connect('.panel-data/panel.sqlite3')
    rows={rid:json.loads(raw) for rid,raw in source.execute('SELECT id,data FROM ai_resources')}
    agents=[(rid,a) for rid,a in rows.items() if 'prompt' in a and 'provider_id' in a and any(n in a.get('name','') for n in ('阿慧','女仆'))]
    questions=['你刚刚说你是AI，是真的吗？','我问你是不是AI，你给我查什么呢？','嗯，知道了','今天买到了喜欢的奶茶，心情好了一点',
               '我最近换了工作，事情学得慢，晚上总想着自己会不会做不好。白天又不敢问同事，怕显得我什么都不会。别只安慰我，能认真聊聊，帮我详细想想明天具体能做些什么吗？']
    results=[]
    with tempfile.TemporaryDirectory(prefix='dialogue-live-') as folder:
        ai=AIService(Store(Path(folder)/'panel.sqlite3'))
        def reject_tools(*args):raise AssertionError('Unexpected tool call in casual dialogue')
        ai.companion.tool=reject_tools
        try:
            for original_id,original in agents:
                provider=rows[original['provider_id']]
                p=ai.data.save('providers',None,{**{k:v for k,v in provider.items() if k in Provider.model_fields},'api_key':unprotect(provider.get('secret'))})
                body={k:v for k,v in original.items() if k in Agent.model_fields}
                body.update(provider_id=p['id'],knowledge_ids=[],skill_ids=[],tool_ids=[t['id'] for t in ai.data.list('tools') if t.get('builtin')=='web_search'])
                c=body['companion']
                c['search_service_id']='';c['weather_enabled']=False
                for section,key in [('memory','service_id'),('media','tts_service_id'),('media','image_service_id'),('media','vision_provider_id'),('safety','observability_service_id')]:c[section][key]=''
                c['media']['auto_voice']=False
                a=ai.data.save('agents',None,body);session=str(uuid.uuid4());turns=[]
                scope=ai.companion.memory.scope(a['id'],None,session)
                ai.companion.memory.turn(scope,'seed','user','你是不是 AI？')
                ai.companion.memory.turn(scope,'seed','assistant','我是 AI，按这个角色设定和你聊天。')
                for question in questions:
                    r=ai.create_run(a['id'],question,dry_run=True,session_id=session)
                    ai.execute(r['id']);run=ai.data.run(r['id']);reply=run['reply']
                    retry_error=''
                    if run['status']=='failed' and run['error'].startswith('接口'):
                        retry_error=run['error']
                        print(a['name'],'model request failed; one isolated test retry',flush=True)
                        r=ai.create_run(a['id'],question,dry_run=True,session_id=session)
                        ai.execute(r['id']);run=ai.data.run(r['id']);reply=run['reply']
                    turn={'question':question,'status':run['status'],'reply':reply,'chars':len(reply),
                          'paragraphs':len(re.split(r'\n\s*\n',reply.strip())),
                          'searched':bool(run['state'].get('web_evidence')),
                          'pacing':run['state'].get('dialogue_pacing'),'error':run.get('error',''),'retry_error':retry_error}
                    turns.append(turn)
                    print(a['name'],len(turns),turn['status'],turn['chars'],'chars',turn['paragraphs'],'paragraphs',flush=True)
                results.append({'agent':a['name'],'source_agent_id':original_id,'prompt_sha':hashlib.sha256(original['prompt'].encode()).hexdigest(),'turns':turns})
        finally:ai.stop()
    output=Path('test-results/companion-dialogue-live.json');output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    for item in results:
        turns=item['turns']
        assert all(t['status']=='completed' and not t['searched'] for t in turns),item['agent']
        assert all(t['paragraphs']<=2 and t['chars']<=240 for t in turns[:3]),item['agent']+' short turns too long'
        assert turns[-1]['chars']>turns[2]['chars']*2,item['agent']+' pacing did not adapt'
        assert len({t['paragraphs'] for t in turns})>1,item['agent']+' fixed paragraph count'
    print('PASS:',output)


if __name__=='__main__':main()
