"""Keep operational failure details in the panel, outside recipient messages."""
import re

REPLY='我遇到点问题，未能完成该任务。'


def instruction():
    return ('\n任务执行失败或无法完成本轮请求时，只回复“'+REPLY+'”。'
            '不向聊天用户解释脚本、命令、权限、接口、错误码、本机路径或调试过程，'
            '不让用户运行命令或提供日志；详细原因由工作台记录。'
            '缺少必要输入时可正常简短提问；正常的技术知识问答照常回答。')


def reason(reply,state):
    if state.get('task_failure'):return state['task_failure']['reason']
    if state.get('reply_failure_reason'):return state['reply_failure_reason']
    if reply.strip()==REPLY:return '任务未完成'
    # Quoted errors, code samples and third-party descriptions are not the
    # assistant reporting its own task outcome.
    text=re.sub(r'```.*?```','',reply,flags=re.S)
    text=re.sub(r'^[ \t]*>.*$','',text,flags=re.M)
    text=re.sub(r'[“「『"].*?[”」』"]','',text,flags=re.S)
    attempts=state.get('web_evidence',[])
    if (any(e.get('status') in ('error','unavailable','disabled','partial') for e in attempts)
            and not any(e.get('status')=='ok' for e in attempts)
            and re.search(r'读取不到|无法|不能|不可用|失败|未能|没能|不足|权限|未取得|没拿到',text)):
        return '本轮工具未取得完成任务所需的资料'
    patterns=(
        r'我[^。！？\n]{0,20}(?:没能|未能|无法|没法|未成功|没有成功|不能)[^。！？\n]{0,12}(?:完成|生成|执行|读取|获取|抓取|查询|访问|识别|处理|导出|合成|加载|发送|下载|连接)',
        r'我[^。！？\n]{0,12}(?:没有|缺少|不具备|未获得)[^。！？\n]{0,18}(?:权限|执行能力|执行工具)',
        r'(?:这次|本次|本轮|目前|当前)[^。！？\n]{0,16}(?:任务失败|查询失败|生成失败|执行失败|查询超时|生成超时|调用超时|未能完成|没能完成|服务暂时不可用|服务不可用|接口不可用)',
        r'(?:我|这份|这张|今天这份)[^。！？\n]{0,14}(?:PDF|图片|报告|日报|文件)[^。！？\n]{0,8}(?:没有|没能|未能|尚未|未)(?:成功)?(?:生成|完成|制作|导出)',
        r'(?:这次|本次|本轮)[^。！？\n]{0,14}(?:没有|未能|没能|没)[^。！？\n]{0,8}(?:生成|完成|取到|读到|加载成功)',
        r'我[^。！？\n]{0,18}(?:没有|没|尚未)[^。！？\n]{0,6}(?:读出|读到|识别出|加载出|看清|看到)(?:这张|原图|图|图片)',
        r'我[^。！？\n]{0,18}(?:没有|没|尚未)[^。！？\n]{0,6}(?:获取到|抓取到|拿到|查到)[^。！？\n]{0,8}(?:行情|数据|原文|新闻|内容|图片|原图)',
        r'^(?:抱歉[，,。]?\s*)?(?:图片|图像|PDF|报告|日报|文件|任务|查询|搜索)(?:生成|制作|读取|获取|执行|处理|导出)?(?:失败|未完成|未成功)(?:[，。！!\s]|了[，。！!\s]|$)',
        r'\bI\s+(?:cannot|can.t|could\s+not|couldn.t|am\s+unable\s+to)\s+(?:complete|generate|execute|fetch|read|access)\b',
    )
    return '模型反馈本轮任务未完成' if any(re.search(p,text,re.I) for p in patterns) else ''
