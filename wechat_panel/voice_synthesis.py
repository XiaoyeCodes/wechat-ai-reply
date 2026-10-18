"""Shared synthesis for browser previews and native WeChat voice delivery."""
import asyncio
from io import BytesIO
from pathlib import Path
import tempfile

SAMPLE_TEXT='晚上好呀，今天辛苦啦。我在这儿呢，想说什么都可以慢慢跟我讲。'
PRESETS=(
    dict(id='xiaoxiao',name='晓晓 · 温柔陪伴',description='普通话，温暖柔和，语速稍慢。推荐给阿慧。',engine='edge',voice='zh-CN-XiaoxiaoNeural',rate='-5%',pitch='+2Hz'),
    dict(id='xiaoyi',name='晓伊 · 元气甜美',description='普通话，轻快活泼，适合撒娇和日常闲聊。',engine='edge',voice='zh-CN-XiaoyiNeural',rate='-3%',pitch='+2Hz'),
    dict(id='hsiaochen',name='晓臻 · 轻柔亲切',description='台湾口音，语气轻柔，适合放松陪伴。',engine='edge',voice='zh-TW-HsiaoChenNeural',rate='-5%',pitch='+0Hz'),
    dict(id='hsiaoyu',name='晓雨 · 细腻轻声',description='台湾口音，声音细腻，可以试听后比较。',engine='edge',voice='zh-TW-HsiaoYuNeural',rate='-3%',pitch='+0Hz'),
    dict(id='windows-huihui',name='慧慧 · 本机离线',description='原来的系统女声，不需要联网。',engine='windows',voice='Windows Huihui',rate='+0%',pitch='+0Hz'),
)


def preset(voice_id, *, store=None):
    if voice_id.startswith('minimax:'):
        if store is None:raise ValueError('MiniMax 音色需要本机配置')
        from .minimax_voice import MiniMaxVoice
        return MiniMaxVoice(store).get_voice(voice_id)
    found=next((v for v in PRESETS if v['id']==voice_id),None)
    if found is None:raise ValueError('请选择列表中的音色')
    return dict(found)


async def edge_audio(text,voice):
    import edge_tts
    chunks=bytearray()
    async with asyncio.timeout(22):
        speech=edge_tts.Communicate(text,voice['voice'],rate=voice['rate'],pitch=voice['pitch'],
                                    connect_timeout=6,receive_timeout=12)
        async for event in speech.stream():
            if event['type']=='audio':
                chunks.extend(event['data'])
                if len(chunks)>2_000_000:raise ValueError('语音服务返回内容过大')
    if not chunks:raise RuntimeError('在线音色暂未返回声音，请稍后重试')
    return bytes(chunks)


def decode_wav(data):
    import soundfile as sf
    import numpy as np
    # Inspect duration before decoding, and bound the amount of audio in memory.
    with sf.SoundFile(BytesIO(data)) as source:
        if source.channels not in (1,2) or not 1<=len(source)/source.samplerate<=50:
            raise ValueError('单条语音需要在 1～50 秒以内')
        rate=source.samplerate
        samples=source.read(dtype='float32',always_2d=True).mean(axis=1)
    if not np.isfinite(samples).all() or float(np.max(np.abs(samples)))<.01:
        raise ValueError('合成语音为空或无效')
    target=BytesIO();sf.write(target,samples,rate,format='WAV',subtype='PCM_16')
    return target.getvalue()


def synthesize(text,path,voice_id='windows-huihui', *, store=None, model='speech-2.8-turbo'):
    voice=preset(voice_id,store=store)
    if not isinstance(text,str) or not 1<=len(text.strip())<=200:raise ValueError('单条语音限 1～200 字')
    path=Path(path)
    if voice['engine']=='windows':
        import pythoncom
        from .wechat_voice import synthesize_windows
        pythoncom.CoInitialize()
        try:synthesize_windows(text,path)
        finally:pythoncom.CoUninitialize()
    elif voice['engine']=='minimax':
        from .minimax_voice import MiniMaxVoice
        data=MiniMaxVoice(store).synthesize(text,voice_id,model)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(decode_wav(data))
        voice['model']=model
    else:
        try:data=asyncio.run(edge_audio(text,voice))
        except Exception as exc:
            # Don't expose remote headers or request text in a UI error.
            raise RuntimeError('在线音色合成失败，请检查网络后重试；不会换回系统音色') from exc
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(decode_wav(data))
    return voice


def preview(text,voice_id, *, store=None, model='speech-2.8-turbo'):
    # A preview never touches the WeChat worker, microphone, or send queue.
    with tempfile.TemporaryDirectory(prefix='relay-voice-preview-') as directory:
        path=Path(directory)/'preview.wav'
        synthesize(text,path,voice_id,store=store,model=model)
        from .wechat_voice import load_pcm
        load_pcm(path)
        return path.read_bytes()
