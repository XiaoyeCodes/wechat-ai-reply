"""Bounded adapters; credentials stay in saved service connections."""
import base64
from datetime import datetime, timezone
import ipaddress
import json
import re
import time
from pathlib import Path
import shutil
import socket
import subprocess
from urllib.parse import quote, urlsplit, urlencode
import uuid

import httpx

from .ai_store import unprotect
from .ai_tools import request_json, redact


def public_url(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只支持公开 HTTP/HTTPS 网页')
    try:addresses={r[4][0] for r in socket.getaddrinfo(parsed.hostname,parsed.port or 443)}
    except OSError:raise ValueError('网页域名无法解析') from None
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('网页读取不能访问本机、内网或特殊地址')
    return url


def binary_request(url,body=None,headers=None,timeout=60,method='POST',limit=8_000_000):
    try:
        with httpx.Client(timeout=timeout,follow_redirects=False) as client:
            with client.stream(method,url,json=body,headers=headers) as response:
                if response.status_code>=300:raise RuntimeError(f'媒体服务返回 HTTP {response.status_code}')
                value=bytearray()
                for chunk in response.iter_bytes():
                    value.extend(chunk)
                    if len(value)>limit:raise RuntimeError('服务返回文件超过 8 MB')
                return bytes(value),response.headers.get('content-type','').split(';')[0]
    except httpx.HTTPError:raise RuntimeError('媒体服务连接失败，请检查服务地址、配额和密钥') from None


class Services:
    def __init__(self,ai):self.ai=ai

    def get(self,sid,category):
        if not sid:raise ValueError('尚未配置'+{'memory':'外部记忆','search':'联网搜索','tts':'语音','image':'图像生成','observability':'观测'}[category]+'服务')
        service=self.ai.data.get('services',sid,private=True)
        if service['category']!=category or not service['enabled']:raise ValueError('服务类别不匹配或服务已停用')
        return service

    @staticmethod
    def headers(s):
        key=unprotect(s.get('secret'))
        if s['driver']=='elevenlabs':return {'xi-api-key':key} if key else {}
        if s['driver']=='qdrant':return {'api-key':key} if key else {}
        if s['driver']=='mem0':return {'Authorization':'Token '+key} if key else {}
        if s['driver']=='exa':return {'x-api-key':key} if key else {}
        if s['driver']=='langsmith':return {'x-api-key':key} if key else {}
        if s['driver']=='langfuse':return {'Authorization':'Basic '+base64.b64encode((s['public_key']+':'+key).encode()).decode()}
        return {'Authorization':'Bearer '+key} if key else {}

    def call(self,s,path,body=None,method='POST'):
        # Base64 images and hexadecimal speech need a larger JSON envelope than
        # plain tool responses. Decoded media still has an 8 MB storage limit.
        limit=17_000_000 if s['category']=='tts' else 12_000_000 if s['category']=='image' else 2_000_000
        result=request_json(s['url'].rstrip('/')+path,body,method=method,headers=self.headers(s),timeout=s['timeout'],max_bytes=limit)
        return redact(result,unprotect(s.get('secret')))

    def search(self,sid,query,limit=5):
        s=self.get(sid,'search');query=query[:1000]
        if s['driver']=='deepseek':
            query=query.strip()[:128]
            if not query:raise ValueError('搜索内容不能为空')
            data=self.deepseek_call(s,'search',{'query':query,'count':min(max(limit,1),50),'need_content':False})
            if not isinstance(data,dict) or not isinstance(data.get('results'),list):raise RuntimeError('DeepSeek 搜索返回格式无效')
            results=[]
            for item in data['results'][:limit]:
                if not isinstance(item,dict):continue
                results.append({k:str(item.get(k) or '')[:n] for k,n in
                    [('title',300),('url',2000),('site_name',150),('date',80),('snippet',800),('summary',1800)]} |
                    {k:item.get(k) for k in ('authority','rerank_score')})
            return json.dumps({'engine':'deepseek','query':query,'results':results},ensure_ascii=False)
        if s['driver']=='agent_reach':
            cli=shutil.which('mcporter');node=shutil.which('node')
            script=Path(cli).parent/'node_modules/mcporter/dist/cli.js' if cli else None
            if not node or not script or not script.is_file():raise RuntimeError('未找到 Agent Reach 的 mcporter；请按接入文档安装并配置 Exa')
            # Invoke Node directly: .cmd launchers interpret shell metacharacters.
            try:
                result=subprocess.run([node,str(script),'call','exa.web_search_exa','query='+query,
                    'numResults='+str(min(limit,5)),'--output','json'],capture_output=True,timeout=s['timeout'],
                    encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except (OSError,subprocess.TimeoutExpired):
                raise RuntimeError('Agent Reach 搜索超时或不可用，请稍后重试') from None
            if result.returncode or len(result.stdout)>200000:raise RuntimeError('Agent Reach 搜索失败，请检查 Exa 连接')
            try:data=json.loads(result.stdout)
            except ValueError:raise RuntimeError('Agent Reach 返回格式无效') from None
            if not isinstance(data,dict):raise RuntimeError('Agent Reach 返回格式无效')
            if data.get('_meta',{}).get('ai.exa/rateLimited') or "hit Exa's free MCP rate limit" in result.stdout:
                raise RuntimeError('Exa 搜索额度已用尽或被限流，请稍后重试，或切换到 Exa API 连接并配置可用密钥')
            if data.get('error') or data.get('isError'):raise RuntimeError('Agent Reach 搜索不可用，请检查 Exa 配置')
            return json.dumps(data,ensure_ascii=False)[:16000]
        if s['driver']=='exa':body={'query':query,'numResults':min(limit,5),'contents':{'text':{'maxCharacters':2000}}};path='/search'
        else:body={'query':query,'max_results':min(limit,5),'include_answer':False};path='/search'
        return json.dumps(self.call(s,path,body),ensure_ascii=False)[:16000]

    @staticmethod
    def read_web(url):
        from .web_reader import read_page
        return json.dumps(read_page(url),ensure_ascii=False)

    def deepseek_call(self,service,operation,body):
        # Search / Fetch are read-only. Retry transient failures once, never
        # credentials/balance errors, and never expose the response body/key.
        for attempt in range(2):
            try:return self.call(service,'/v1/tools/'+operation,body)
            except RuntimeError as exc:
                match=re.search(r'HTTP (\d+)',str(exc));code=int(match[1]) if match else None
                if attempt==0 and code in (408,429,500,502,503,504):
                    time.sleep(.8);continue
                reason={401:'API Key 无效',402:'账户余额不足',403:'页面访问被限制',404:'未取得页面正文',
                        408:'请求超时',429:'请求被限流'}.get(code,'接口暂不可用')
                raise RuntimeError(f'DeepSeek {operation}：{reason}'+(f'（HTTP {code}）' if code else '')) from None

    def fetch(self,sid,url):
        from .news_cards import read_source
        original=read_source(url)
        if original is not None:return json.dumps(original,ensure_ascii=False)
        # Keep transcript/platform readers for media links; Search/Fetch is
        # the generic article reader, not a substitute for video subtitles.
        from .platform_reader import platform_for_url
        if platform_for_url(url):return self.read_web(url)
        if not sid:return self.read_web(url)
        service=self.get(sid,'search')
        if service['driver']!='deepseek':return self.read_web(url)
        public_url(url)
        from .web_reader import page_content,ReadUnavailable
        try:
            data=self.deepseek_call(service,'fetch',{'url':url})
            if not isinstance(data,dict) or not isinstance(data.get('content'),str) or not data['content'].strip():
                raise ReadUnavailable('DeepSeek Fetch 未取得页面正文，不能用搜索摘要代替原文')
            content=page_content(data['content'],str(data.get('title') or ''))
            return json.dumps({'engine':'deepseek_fetch','url':url,'title':str(data.get('title') or '')[:300],
                               'content':content[:18000],'truncated':len(content)>18000},ensure_ascii=False)
        except RuntimeError:
            # Same public URL, using the existing Jina/direct reader. No search
            # snippets, alternate article, paid provider or changed source.
            try:
                page=json.loads(self.read_web(url))
                if not isinstance(page,dict) or page.get('coverage') in ('metadata_only','unavailable'):
                    raise ReadUnavailable('公共网页读取未取得正文')
                content=page_content(page.get('content'),str(page.get('title') or ''))
                result={'engine':'public_page_fallback','fallback_from':'deepseek_fetch','url':url,
                        'coverage':page.get('coverage','unknown'),'title':str(page.get('title') or '')[:300],
                        'content':content[:24000],'truncated':bool(page.get('truncated')) or len(content)>24000}
                if page.get('url') and page['url']!=url:result['resolved_url']=page['url']
                return json.dumps(result,ensure_ascii=False)
            except (RuntimeError,ValueError,TypeError):
                raise ReadUnavailable('网页读取暂不可用，未取得可用正文') from None

    @staticmethod
    def weather(city):
        data=request_json('https://geocoding-api.open-meteo.com/v1/search',{'name':city,'count':1,'language':'zh','format':'json'},method='GET',timeout=15)
        places=data.get('results',[])
        if not places:return '没有找到该城市，请提供更明确的城市名称。'
        place=places[0]
        weather=request_json('https://api.open-meteo.com/v1/forecast',{'latitude':place['latitude'],'longitude':place['longitude'],
            'current':'temperature_2m,apparent_temperature,precipitation,weather_code',
            'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max',
            'forecast_days':7,'timezone':'auto'},method='GET',timeout=15)
        return json.dumps({'city':place['name'],'country':place.get('country'),'weather':weather.get('current'),
            'units':weather.get('current_units'),'daily':weather.get('daily'),'daily_units':weather.get('daily_units'),
            'timezone':weather.get('timezone'),'source':'https://open-meteo.com/'},ensure_ascii=False)

    def tts(self,sid,text,voice_id='',speed=1):
        s=self.get(sid,'tts');voice_id=voice_id or s['voice_id']
        if not voice_id:raise ValueError('请配置固定音色 ID')
        text=text[:1999];driver=s['driver']
        if driver=='elevenlabs':
            data,mime=binary_request(s['url']+'/v1/text-to-speech/'+quote(voice_id,safe=''),
                {'text':text,'model_id':s['model'] or 'eleven_multilingual_v2','voice_settings':{'speed':speed}},self.headers(s),s['timeout'])
            return data,'audio/mpeg'
        if driver=='minimax':
            value=self.call(s,'/v1/t2a_v2',{'model':s['model'] or 'speech-2.8-hd','text':text,'stream':False,
                'output_format':'hex','voice_setting':{'voice_id':voice_id,'speed':speed},'audio_setting':{'format':'mp3','sample_rate':32000}})
            if value.get('base_resp',{}).get('status_code',0)!=0:raise RuntimeError('MiniMax 未能合成语音，请检查模型和音色 ID')
            try:return bytes.fromhex(value['data']['audio']),'audio/mpeg'
            except (KeyError,ValueError):raise RuntimeError('语音服务未返回有效音频') from None
        body={'model':s['model'],'input':text,'voice':voice_id,'speed':speed,'response_format':'mp3'}
        data,mime=binary_request(s['url']+('/audio/speech' if driver=='openai_speech' else ''),body,self.headers(s),s['timeout'])
        if mime=='application/json':
            try:
                value=json.loads(data);data=base64.b64decode(value['audio_base64'],validate=True);mime=value.get('mime','audio/mpeg')
            except (ValueError,KeyError):raise RuntimeError('语音网关应返回音频或 audio_base64') from None
        if mime not in ('audio/mpeg','audio/wav','audio/flac'):raise RuntimeError('语音接口没有返回受支持的音频格式')
        return data,mime

    def generate_image(self,sid,prompt,media,reference=None,cancelled=None):
        s=self.get(sid,'image');body={'model':s['model'],'prompt':prompt[:5000],'n':1}
        if s['driver']=='codex_image':
            if media.get('lora'):raise ValueError('Codex 生图不支持 LoRA，请清空 LoRA 设置')
            from .codex_images import generate
            return generate(prompt,reference,s['timeout'],cancelled)
        if s['driver']=='image_http':
            body.update(lora=media.get('lora',''),lora_scale=media.get('lora_scale',.8),reference_image=reference)
            value=self.call(s,'',body)
            encoded=value.get('image_base64');url=value.get('url')
        else:
            if reference or media.get('lora'):raise ValueError('参考图/LoRA 需要图像网关协议，当前连接仅支持文字生图')
            value=self.call(s,'/images/generations',body)
            item=value.get('data',[{}])[0];encoded=item.get('b64_json');url=item.get('url')
        if encoded:
            try:return base64.b64decode(encoded,validate=True)
            except ValueError:raise RuntimeError('图像服务返回了无效图片') from None
        if url:
            public_url(url);data,_=binary_request(url,method='GET');return data
        raise RuntimeError('图像服务未返回图片')

    def embedding(self,s,text):
        p=self.ai.data.get('providers',s['embedding_provider_id'],private=True)
        key=unprotect(p.get('secret'))
        value=request_json(p['base_url']+'/embeddings',{'model':p['model'],'input':text[:6000]},headers={'Authorization':'Bearer '+key} if key else {},timeout=p['timeout'])
        try:
            vector=value['data'][0]['embedding']
            if not isinstance(vector,list) or not 1<=len(vector)<=8192 or any(type(n) not in (float,int) for n in vector):raise ValueError()
            return vector
        except (ValueError,KeyError,IndexError):raise RuntimeError('Embedding 服务未返回有效向量') from None

    def memory(self,sid,operation,scope,**params):
        s=self.get(sid,'memory');driver=s['driver']
        if driver=='memory_http':return self.call(s,'',{'operation':operation,'namespace':scope,**params})
        if driver=='mem0':
            if operation=='search':return self.call(s,'/v2/memories/search/',{'query':params['query'],'filters':{'user_id':scope},'top_k':params.get('limit',6)})
            if operation=='upsert':return self.call(s,'/v1/memories/',{'messages':[{'role':'user','content':params['content']}],'user_id':scope,'infer':False,'metadata':{'local_id':params['id']}})
            # Delete only this namespace; avoid deleting a remote memory by arbitrary ID.
            if operation=='clear':return self.call(s,'/v1/memories/?'+urlencode({'user_id':scope}),None,method='DELETE')
            raise ValueError('mem0 的单条遗忘请先清除当前用户远端记忆，再同步保留的本地记录')
        path='/collections/'+s['collection'];flt={'must':[{'key':'namespace','match':{'value':scope}}]}
        if operation=='search':
            vector=self.embedding(s,params['query'])
            return self.call(s,path+'/points/query',{'query':vector,'filter':flt,'limit':params.get('limit',6),'with_payload':True})
        if operation=='upsert':
            vector=self.embedding(s,params['content'])
            # Collection creation is done explicitly by the connection test.
            point=str(uuid.uuid5(uuid.NAMESPACE_URL,scope+params['id']))
            return self.call(s,path+'/points?wait=true',{'points':[{'id':point,'vector':vector,'payload':{'namespace':scope,'text':params['content'],'local_id':params['id']}}]},method='PUT')
        if operation=='clear':return self.call(s,path+'/points/delete?wait=true',{'filter':flt})
        if operation=='delete':return self.call(s,path+'/points/delete?wait=true',{'points':[str(uuid.uuid5(uuid.NAMESPACE_URL,scope+params['id']))]})
        raise ValueError('未知记忆操作')

    def trace(self,sid,run,metadata,include_content=False):
        s=self.get(sid,'observability');rid=run['id'];stamp=datetime.now(timezone.utc).isoformat()
        meta={'agent_id':run['agent_id'],'dry_run':bool(run['dry_run']),**metadata}
        # Identifiers are pseudonymous. Text export is separately opt-in.
        payload={'id':rid,'name':'Relay companion','metadata':meta}
        if include_content:payload.update(input=run['input'],output=run['reply'])
        if s['driver']=='langfuse':return self.call(s,'/api/public/ingestion',{'batch':[{'id':str(uuid.uuid4()),'type':'trace-create','timestamp':stamp,'body':payload}]})
        if s['driver']=='langsmith':
            payload.update(run_type='chain',start_time=run['created_at'],end_time=stamp,inputs={'text':run['input']} if include_content else {},outputs={'text':run['reply']} if include_content else {})
            payload.pop('input',None);payload.pop('output',None)
            return self.call(s,'/runs',payload)
        return self.call(s,'',payload)

    def test(self,sid):
        s=self.ai.data.get('services',sid,private=True)
        if not s['enabled']:raise ValueError('服务已停用')
        if s['category']=='search':return {'detail':'搜索连接成功','sample':self.search(sid,'天气查询服务',2)[:1600]}
        if s['category']=='tts':
            audio,mime=self.tts(sid,'你好，我在这里。')
            return {'detail':'语音合成成功','media':self.ai.companion.memory.media('service-test:'+sid,audio,'audio',mime)}
        if s['driver']=='qdrant':
            vector=self.embedding(s,'连接测试')
            try:self.call(s,'/collections/'+s['collection'],None,method='GET')
            except RuntimeError:
                self.call(s,'/collections/'+s['collection'],{'vectors':{'size':len(vector),'distance':'Cosine'}},method='PUT')
            return {'detail':'Embedding 与 Qdrant 集合连接成功'}
        if s['category']=='memory':return {'detail':'记忆检索接口连接成功','sample':self.memory(sid,'search','connection-test',query='test',limit=1)}
        if s['category']=='image':
            image=self.generate_image(sid,'A single red apple on a white background',{})
            return {'detail':'图片生成成功','media':self.ai.companion.memory.media('service-test:'+sid,image)}
        self.trace(sid,{'id':str(uuid.uuid4()),'agent_id':'connection-test','dry_run':1,'created_at':datetime.now(timezone.utc).isoformat(),'input':'','reply':''},{'connection_test':True})
        return {'detail':'观测事件已提交'}
