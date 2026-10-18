import json
from types import SimpleNamespace

from wechat_panel.hermes_descriptions import describe


def test_terminal_description_translates_actual_command_without_exposing_it():
    command='ls -d "$HOME/Desktop" "$HOME/OneDrive/Desktop" 2>/dev/null; echo "---"; ls "$HOME/Desktop" | head -30'
    args={'description':'电脑操作需要确认。'+json.dumps({'工具':'terminal','参数':{'command':command}})}
    calls=[]
    def model(provider,messages,tools):
        calls.append(messages)
        assert tools==[] and command in json.loads(messages[-1]['content'])['实际参数']['command']
        return {'content':json.dumps({'action':'查找桌面位置，查看前 30 个文件或文件夹',
            'target':'电脑桌面和 OneDrive 桌面','effect':'读取文件列表，不修改文件内容','unclear':False})},{}
    ai=SimpleNamespace(model_call=model)
    run={'input':'帮我在桌面创建文件夹','state':{'snapshot':{'provider':{'id':'test'}}}}
    summary,panel=describe(ai,run,args,'operation')
    assert not panel and '前 30 个' in summary and 'terminal' not in summary and '$HOME' not in summary
    assert len(calls)==1


def test_file_write_uses_real_path_and_explains_overwrite_without_model():
    ai=SimpleNamespace(model_call=lambda *a:(_ for _ in ()).throw(AssertionError('no model needed')))
    run={'input':'保存笔记','state':{'snapshot':{}}}
    args={'description':'电脑操作需要确认。'+json.dumps({'工具':'write_file','参数':{'path':'notes.txt','content':'今日笔记'}})}
    summary,panel=describe(ai,run,args,'operation')
    assert not panel and 'notes.txt' in summary and '今日笔记' in summary and '覆盖' in summary
    assert 'write_file' not in summary


def test_failed_translation_never_returns_raw_code_or_executes_it():
    ai=SimpleNamespace(model_call=lambda *a:(_ for _ in ()).throw(RuntimeError('unavailable')))
    run={'input':'整理桌面文件','state':{'snapshot':{'provider':{'id':'test'}}}}
    args={'command':'unknown-code(); delete-everything'}
    summary,panel=describe(ai,run,args,'operation')
    assert panel and 'unknown-code' not in summary and '查看完整' in summary
    summary,panel=describe(ai,run,args,'task')
    assert not panel and '整理桌面文件' in summary and 'unknown-code' not in summary
