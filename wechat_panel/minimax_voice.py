"""MiniMax native voice adapter. Keys never leave the local backend except as auth."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

import httpx

from .ai_store import protect, unprotect
from .store import now

BASE_URL = 'https://api.minimax.cn'
MODELS = ('speech-2.8-turbo', 'speech-2.8-hd')
MAX_UPLOAD = 20_000_000
SYSTEM_VOICES = (
    ('Chinese (Mandarin)_ExplorativeGirl', '漫游少女', '甜美温柔，充满情感张力，迷茫纯真。'),
    ('Chinese (Mandarin)_Warm_HeartedGirl', '热心女孩', '轻柔且略带气声，舒缓流畅，具有疗愈感。'),
    ('Chinese (Mandarin)_IntellectualGirl', '智性少女', '清冷通透，平稳有序，严谨高智感。'),
)
# Keep resolving saved selections without adding them back to the preset menu.
LEGACY_SYSTEM_VOICES = (
    ('Chinese (Mandarin)_Sweet_Lady', '甜美女声', '甜美亲切，推荐先试听阿慧的日常短句。'),
    ('Chinese (Mandarin)_Warm_Bestie', '温暖闺蜜', '温暖的陪伴女声，适合轻松聊天。'),
    ('Chinese (Mandarin)_Soft_Girl', '柔和少女', '柔和轻声，适合晚安和安静陪伴。'),
    ('tianxin_xiaoling', '甜心小玲', '甜美角色音色，适合活泼的聊天语气。'),
)


def voice_item(vid, name, description='', kind='system', **extra):
    return dict(id='minimax:'+vid, voice=vid, name='MiniMax · '+name,
                description=description, engine='minimax', kind=kind, **extra)


class MiniMaxError(RuntimeError):
    def __init__(self, message, *, rejected=False):
        super().__init__(message)
        # True only when the provider explicitly rejects this attempt. Timeouts,
        # server errors and duplicate voice IDs still require reconciliation.
        self.rejected = rejected


def http_client():
    return httpx.Client(timeout=httpx.Timeout(90, connect=8), follow_redirects=False)


def remote_call(key, endpoint, *, body=None, files=None):
    """One bounded request; never retry a potentially billed operation."""
    if endpoint not in ('/v1/get_voice', '/v1/files/upload', '/v1/voice_clone', '/v1/t2a_v2'):
        raise ValueError('不支持的 MiniMax 操作')
    try:
        with http_client() as client:
            args = {'data': {'purpose': 'voice_clone'}, 'files': files} if files else {'json': body}
            with client.stream('POST', BASE_URL+endpoint, headers={'Authorization':'Bearer '+key}, **args) as response:
                if response.status_code in (401, 403):
                    raise MiniMaxError('MiniMax 认证失败，请检查 API Key、实名认证和接口权限', rejected=True)
                if response.status_code == 429:
                    raise MiniMaxError('MiniMax 请求过于频繁，请稍后再试', rejected=True)
                if response.status_code >= 300:
                    raise MiniMaxError(f'MiniMax 返回 HTTP {response.status_code}，请检查服务状态',
                                       rejected=300<=response.status_code<500 and response.status_code!=408)
                content = bytearray()
                deadline = time.monotonic()+90
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content)>8_000_000 or time.monotonic()>deadline:
                        raise MiniMaxError('MiniMax 响应过大或超时，请稍后检查结果')
        value = json.loads(content)
        if not isinstance(value, dict) or not isinstance(value.get('base_resp'), dict):
            raise ValueError()
        code = value['base_resp'].get('status_code')
        if code != 0:
            reasons = {1002:'请求过于频繁，请稍后再试',
                       1004:'认证失败，请检查 API Key 和接口权限',
                       1008:'API 账户余额不足，请在该 Key 所属的 MiniMax 开放平台账户补足余额',
                       1026:'样音或输入内容未通过平台审核，请更换内容',
                       1043:'样音与校验文本不匹配，请检查录音和文本',
                       1044:'示例音频与提示文本不匹配，请检查录音和文本',
                       2013:'请求参数无效，请检查样音和音色配置',
                       20132:'样音或音色 ID 无效，请检查录音和音色配置',
                       2037:'样音时长不符合要求，请使用 10 秒至 5 分钟的清晰录音',
                       2038:'当前账户未开通声音克隆权限，请完成个人或企业认证',
                       2042:'当前账户无权使用该音色，请检查音色所属账号',
                       2048:'克隆提示音频过长，请使用短于 8 秒的示例录音',
                       2049:'API Key 无效，请重新配置'}
            reason = reasons.get(code, {1000:'服务发生未知错误',1001:'请求超时',
                1024:'服务内部错误',1033:'下游服务异常',
                2039:'音色编号已存在，请先核对原音色的创建结果'}.get(code,'请求失败，请核对平台错误码'))
            safe_code = str(code) if isinstance(code, int) else '未知'
            raise MiniMaxError(f'MiniMax {reason}（{safe_code}）', rejected=code in reasons)
        return value
    except MiniMaxError:
        raise
    except (httpx.HTTPError, ValueError, UnicodeError):
        raise MiniMaxError('MiniMax 网络请求失败或返回格式无效；未自动重试') from None


def validate_sample(data, extension):
    """Decode bounded local audio. Never trust a filename or remote media URL."""
    import soundfile as sf
    import numpy as np
    if not data or len(data)>MAX_UPLOAD:
        raise ValueError('样音不能为空且不能超过 20 MB')
    if extension not in ('wav', 'mp3', 'm4a'):
        raise ValueError('样音仅支持 WAV、MP3、M4A')
    if extension == 'm4a':
        if data[4:8]!=b'ftyp':raise ValueError('M4A 文件头无效，请上传真实的音频文件')
        executable = shutil.which('ffmpeg')
        if not executable:
            raise ValueError('本机未安装 FFmpeg，请先将 M4A 转为 WAV 或 MP3 再上传')
        with tempfile.TemporaryDirectory(prefix='relay-voice-sample-') as folder:
            source=Path(folder)/'sample.m4a'; target=Path(folder)/'sample.wav'
            source.write_bytes(data)
            try:
                result=subprocess.run([executable, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                    '-f', 'mov', '-i', str(source), '-map', '0:a:0', '-vn', '-t', '301', '-ac', '1', '-ar', '24000',
                    '-c:a', 'pcm_s16le', str(target)], capture_output=True, timeout=25,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode or not target.exists():raise ValueError()
                data=target.read_bytes();extension='wav'
            except (OSError, subprocess.TimeoutExpired, ValueError):
                raise ValueError('无法读取 M4A 音频，请换用有效的 WAV 或 MP3') from None
    try:
        with sf.SoundFile(BytesIO(data)) as source:
            duration=len(source)/source.samplerate
            if source.format not in ('WAV','WAVEX','MP3') or source.channels not in (1,2):
                raise ValueError('请使用单声道或双声道 WAV / MP3 样音')
            if not 10<=duration<=300:raise ValueError('样音时长需要在 10 秒至 5 分钟之间')
            if not 8000<=source.samplerate<=192000:raise ValueError('样音采样率不受支持')
            peak=0.
            for block in source.blocks(blocksize=65536, dtype='float32'):
                if not np.isfinite(block).all():raise ValueError('样音包含无效数据')
                peak=max(peak,float(np.abs(block).max()))
            if peak<.01:raise ValueError('样音声音过小或没有声音，请换一段清晰录音')
            extension='mp3' if source.format=='MP3' else 'wav'
        return data,extension,round(duration,2)
    except (RuntimeError, OSError, ZeroDivisionError):
        raise ValueError('无法解析样音，请上传有效的 WAV、MP3 或 M4A 音频') from None


class MiniMaxVoice:
    def __init__(self, store):
        self.store=store

    def _config(self):
        with self.store.connect() as db:
            row=db.execute("SELECT value FROM settings WHERE key='minimax_voice'").fetchone()
        return json.loads(row[0]) if row else {}

    def config(self):
        config=self._config()
        return {'key_configured':bool(config.get('secret')), 'revision':config.get('revision',''),
                'endpoint':BASE_URL, 'models':list(MODELS)}

    def save_config(self, api_key='', clear=False):
        api_key=api_key.strip()
        if api_key and clear:raise ValueError('填写 Key 和清除 Key 不能同时进行')
        if len(api_key)>4096 or any(c.isspace() or ord(c)<32 or ord(c)>126 for c in api_key):
            raise ValueError('API Key 格式不正确，请粘贴完整密钥')
        with self.store.connect() as db:
            row=db.execute("SELECT value FROM settings WHERE key='minimax_voice'").fetchone()
            old=json.loads(row[0]) if row else {}
            if api_key or clear:
                scope=hashlib.sha256(api_key.encode()).hexdigest() if api_key else ''
                old.update(secret=protect(api_key) if api_key else '', scope=scope, revision=uuid.uuid4().hex)
                db.execute("INSERT OR REPLACE INTO settings VALUES('minimax_voice',?)",(json.dumps(old),))
                self.store.event(db,'voice.config')
        return self.config()

    def key(self, config=None):
        config=self._config() if config is None else config
        if not config.get('secret'):raise ValueError('请先在 MiniMax 设置中填写并保存 API Key')
        try:return unprotect(config['secret'])
        except Exception:raise MiniMaxError('无法解密 MiniMax Key，请在当前 Windows 账号下重新保存') from None

    def voices(self):
        result=[voice_item(*v) for v in (*SYSTEM_VOICES, *LEGACY_SYSTEM_VOICES)]
        config=self._config();scope=config.get('scope','')
        if not scope:return result
        with self.store.connect() as db:
            rows=db.execute("SELECT voice_id,name FROM voice_clones WHERE scope=? AND status='ready' ORDER BY created_at",(scope,)).fetchall()
            cached=db.execute("SELECT value FROM settings WHERE key=?",('minimax_catalog:'+scope,)).fetchone()
        seen={v['voice'] for v in result}
        for row in rows:
            seen.add(row['voice_id'])
            result.append(voice_item(row['voice_id'], row['name'], '自定义克隆音色；首次正式合成可能收取音色激活费。',kind='clone'))
        for item in json.loads(cached[0]) if cached else []:
            if item['voice'] not in seen:result.append(item);seen.add(item['voice'])
        return result

    def get_voice(self, voice_id):
        found=next((v for v in self.voices() if v['id']==voice_id),None)
        if not found:raise ValueError('该 MiniMax 音色尚未导入，请刷新音色列表或重新选择')
        return found

    def refresh(self):
        config=self._config();key=self.key(config)
        data=remote_call(key,'/v1/get_voice',body={'voice_type':'all'})
        catalog=[]
        for field,kind in [('system_voice','system'),('voice_cloning','clone'),('voice_generation','designed')]:
            items=data.get(field,[])
            if not isinstance(items,list):raise MiniMaxError('MiniMax 音色列表格式无效')
            for item in items[:1500]:
                if not isinstance(item,dict):continue
                vid=item.get('voice_id','')
                if not isinstance(vid,str) or not 1<=len(vid)<=256:continue
                description=item.get('description',[])
                if isinstance(description,list):description='；'.join(str(s) for s in description)
                if key in vid:continue
                catalog.append(voice_item(vid,str(item.get('voice_name') or vid).replace(key,'[已隐藏密钥]')[:100],str(description).replace(key,'[已隐藏密钥]')[:300],kind))
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('minimax_catalog:'+config['scope'],json.dumps(catalog,ensure_ascii=False)))
        return {'count':len(catalog),'detail':'连接成功，音色列表已同步'}

    def clone(self, request_id, name, data, extension):
        request_id=str(uuid.UUID(request_id))
        name=name.strip()
        if not 1<=len(name)<=60:raise ValueError('请填写 1～60 字的音色名称')
        config=self._config();key=self.key(config)
        scope=config['scope'];digest=hashlib.sha256(data).hexdigest()
        vid='Relay'+uuid.UUID(request_id).hex
        # Reserve before the remote upload. A replay cannot create/pay for another voice.
        with self.store.connect() as db:
            old=db.execute('SELECT * FROM voice_clones WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old['scope']!=scope or old['digest']!=digest or old['name']!=name:
                    raise ValueError('克隆请求已存在且内容不同，请重新选择样音')
                if old['status']=='ready':return {'voice_id':'minimax:'+vid,'name':name,'status':'ready'}
                raise ValueError(old['error'] or '克隆正在处理或结果尚待核对，请勿重复提交')
        audio,extension,duration=validate_sample(data,extension)
        with self.store.connect() as db:
            inserted=db.execute('INSERT OR IGNORE INTO voice_clones VALUES(?,?,?,?,?,?,?,?)',
                (request_id,scope,vid,name,digest,'pending','',now())).rowcount
            if not inserted:raise ValueError('克隆正在处理，请勿重复提交')
        submitted=False
        try:
            uploaded=remote_call(key,'/v1/files/upload',files={'file':('sample.'+extension,audio,'audio/mpeg' if extension=='mp3' else 'audio/wav')})
            try:
                file_id=int(uploaded['file']['file_id'])
                if file_id<=0:raise ValueError()
            except (KeyError,TypeError,ValueError):raise MiniMaxError('MiniMax 未返回有效的样音文件 ID') from None
            submitted=True
            remote_call(key,'/v1/voice_clone',body={'file_id':file_id,'voice_id':vid,
                'need_noise_reduction':True,'need_volume_normalization':True})
        except Exception as exc:
            rejected=isinstance(exc,MiniMaxError) and exc.rejected
            status='uncertain' if submitted and not rejected else 'failed'
            # MiniMaxError contains only our safe messages, never remote bodies,
            # authorization headers or raw transport exception text.
            reason=str(exc) if isinstance(exc,MiniMaxError) else '网络连接中断、超时或服务响应异常'
            error=(('克隆结果尚无法确认：'+reason+'。请在 MiniMax 平台核对音色 '+vid+'；不会自动重复创建')
                   if status=='uncertain' else
                   ('克隆失败：' if submitted else '样音上传失败：')+reason+'。处理后请重新选择录音并创建。')
            with self.store.connect() as db:
                db.execute('UPDATE voice_clones SET status=?,error=? WHERE request_id=?',(status,error,request_id))
            raise MiniMaxError(error,rejected=rejected) from None
        with self.store.connect() as db:
            db.execute("UPDATE voice_clones SET status='ready' WHERE request_id=?",(request_id,))
            self.store.event(db,'voice.config')
        return {'voice_id':'minimax:'+vid,'name':name,'status':'ready','duration':duration}

    def synthesize(self, text, voice_id, model):
        if model not in MODELS:raise ValueError('请选择 Turbo 或 HD 模型')
        voice=self.get_voice(voice_id)
        data=remote_call(self.key(),'/v1/t2a_v2',body={'model':model,'text':text,'stream':False,
            'output_format':'hex','voice_setting':{'voice_id':voice['voice'],'speed':.95,'vol':1,'pitch':0},
            'audio_setting':{'format':'mp3','sample_rate':32000,'channel':1}})
        try:
            encoded=data['data']['audio']
            if not isinstance(encoded,str) or not 2<=len(encoded)<=4_000_000:raise ValueError()
            audio=bytes.fromhex(encoded)
        except (KeyError,TypeError,ValueError):raise MiniMaxError('MiniMax 未返回有效的音频') from None
        return audio
