"""Execute the reviewed newspaper package, never an imported shell command."""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

NAME = 'jin10-financial-daily'
ROOT = Path(__file__).resolve().parents[1] / 'skills' / NAME
DESCRIPTION = '运行已绑定的金十财经日报技能：抓取原文和九项真实行情，校验并生成原版双栏 PDF 及逐页预览。目录由工作台管理，无需终端；只生成文件，不自行发送。'
PROPERTIES = {'date': {'type':'string','pattern':r'^\d{4}-\d{2}-\d{2}$','description':'可选预期原文日期；省略则使用来源最新一期，不更改其日期'}}


def bound(state):
    return any(s.get('enabled') and s.get('name') == NAME for s in state['snapshot'].get('skills', {}).values())


def verify_package():
    manifest = json.loads((ROOT/'package.json').read_text(encoding='utf-8'))
    for name, digest in manifest['sha256'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('财经日报执行文件变化，请重新核验安装包')


def run_pipeline(workspace, expected_date=None):
    verify_package()
    if expected_date:date.fromisoformat(expected_date)
    poppler = shutil.which('pdftoppm')
    if not poppler:
        candidate = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe'
        if candidate.is_file():poppler = str(candidate)
    if not poppler:raise RuntimeError('财经日报缺少 Poppler，请安装后重试')
    command = [sys.executable,'-X','utf8',str(ROOT/'scripts/run_daily.py'),
               '--workspace',str(workspace),'--standalone','--fetch-python',sys.executable,
               '--render-python',sys.executable,'--poppler',poppler]
    if expected_date:command += ['--date',expected_date]
    env = {k:v for k,v in os.environ.items() if k.upper() in {
        'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA',
        'HOMEDRIVE','HOMEPATH','HTTP_PROXY','HTTPS_PROXY','NO_PROXY','SSL_CERT_FILE','REQUESTS_CA_BUNDLE'}}
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    process = subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:output,errors = process.communicate(timeout=420)
    except subprocess.TimeoutExpired:
        import psutil
        try:
            for child in psutil.Process(process.pid).children(recursive=True):
                try:child.kill()
                except psutil.Error:pass
        except psutil.Error:pass
        process.kill();process.communicate()
        raise RuntimeError('财经日报生成超时，未交付文件；请查看本次运行日志') from None
    if len(output)>100_000:raise ValueError('财经日报返回内容过大')
    try:result = json.loads(output)
    except ValueError:raise RuntimeError('财经日报未返回有效结果：'+errors[-500:]) from None
    if result.get('status') != 'ready':
        raise RuntimeError('财经日报未生成完整文件：'+str(result.get('error') or result.get('status'))[:600])
    return result


def checked_file(workspace, value, suffix):
    path = Path(value).resolve()
    if not path.is_relative_to(workspace.resolve()) or path.suffix.lower()!=suffix or not path.is_file():
        raise ValueError('财经日报返回的文件不在本次运行目录内')
    if not 0 < path.stat().st_size <= 25_000_000:raise ValueError('财经日报文件大小无效')
    return path


def collect(args, run, state, data_dir):
    if not bound(state):raise ValueError('当前 Agent 尚未绑定财经日报 Skill')
    if set(args)-{'date'}:raise ValueError('财经日报不接受目录、命令或其他执行参数')
    if args.get('date'):date.fromisoformat(args['date'])
    from .financial_pdf import report_path
    destination = report_path(data_dir,run['id'])
    workspace = destination.parent/'financial-daily'
    workspace.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    result=run_pipeline(workspace,args.get('date'))
    pdf=checked_file(workspace,result['pdf'],'.pdf')
    if not result.get('validation',{}).get('passed'):raise ValueError('财经日报内容校验未通过')
    if hashlib.sha256(pdf.read_bytes()).hexdigest()!=result.get('artifact_sha256'):
        raise ValueError('财经日报 PDF 校验值不一致')
    from pypdf import PdfReader
    pages=len(PdfReader(pdf).pages)
    previews=[checked_file(workspace,p,'.png') for p in result.get('previews',[])]
    if pages<2 or pages!=len(previews) or pages!=result['validation'].get('pages'):
        raise ValueError('财经日报未提供全部页面预览')
    markets=checked_file(workspace,result['markets'],'.json')
    snapshot=json.loads(markets.read_text(encoding='utf-8'))
    if len(snapshot.get('rows',[]))!=9:raise ValueError('财经日报行情不满九项')
    destination.write_bytes(pdf.read_bytes())
    state['financial_daily']={k:result[k] for k in ('date','url','snapshot_at','warnings','artifact_sha256')}
    state['financial_daily'].update(status='ready',previews=[str(p) for p in previews],
                                   workspace=str(workspace),validation=result['validation'])
    state['output_format']='pdf'
    state['document_generation_seconds']=time.monotonic()-started
    state['pdf']={'name':f"每日财经-{result['date']}.pdf",'url':f"/api/ai/runs/{run['id']}/pdf",
                  'preview_urls':[f"/api/ai/runs/{run['id']}/pdf/pages/{n}" for n in range(1,pages+1)]}
    return json.dumps({'status':'ready',**state['pdf'],'date':result['date'],'url':result['url'],
                       'snapshot_at':result['snapshot_at'],'warnings':result['warnings'],
                       'instruction':'文件已真实生成；保持原版 PDF，不重排为聊天文字。测试只提供预览。'},ensure_ascii=False)


def verified_pdf(data_dir, run):
    from .financial_pdf import report_path
    path=report_path(data_dir,run['id'])
    receipt=run['state'].get('financial_daily',{})
    if (receipt.get('status')!='ready' or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest()!=receipt.get('artifact_sha256')):
        raise ValueError('财经日报文件尚未通过校验')
    return path


def preview_path(data_dir,run,page):
    verified_pdf(data_dir,run)
    values=run['state'].get('financial_daily',{}).get('previews',[])
    if not 1<=page<=len(values):raise ValueError('该预览页不存在')
    workspace=Path(data_dir).resolve()/'reports'/str(uuid.UUID(run['id']))/'financial-daily'
    return checked_file(workspace,values[page-1],'.png')


def clone_artifact(data_dir,source,target_id):
    """Copy a checked report into a new authorized run without re-typesetting."""
    from .financial_pdf import report_path
    original=verified_pdf(data_dir,source)
    destination=report_path(data_dir,target_id)
    workspace=destination.parent/'financial-daily';workspace.mkdir(parents=True,exist_ok=True)
    receipt=dict(source['state']['financial_daily'])
    previews=[]
    for n in range(1,len(receipt['previews'])+1):
        path=workspace/f'daily-{n}.png'
        path.write_bytes(preview_path(data_dir,source,n).read_bytes());previews.append(str(path))
    destination.write_bytes(original.read_bytes())
    receipt.update(workspace=str(workspace),previews=previews)
    pdf=dict(source['state']['pdf'],url=f'/api/ai/runs/{target_id}/pdf',
             preview_urls=[f'/api/ai/runs/{target_id}/pdf/pages/{n}' for n in range(1,len(previews)+1)])
    return {'financial_daily':receipt,'pdf':pdf,'output_format':'pdf'}


def execute(ai,run,state):
    if not bound(state) or not re.search(r'财经日报|每日财经|全球财经早餐|jin10-financial-daily',run['input'],re.I):return False
    if re.search(r'(?:不要|不用|别)(?:生成|制作|运行|执行)|(?:介绍|解释).{0,8}(?:技能|财经日报)',run['input']):return False
    from .web_policy import allowed
    if not allowed(state,run):raise ValueError('本轮已关闭联网，未执行财经日报抓取')
    tool=next((t for t in state['snapshot']['tools'].values() if t.get('enabled') and t.get('builtin')=='financial_daily'),None)
    if not tool:raise ValueError('财经日报执行工具尚未启用')
    call=state.get('financial_daily_call')
    if not call:
        requested_date=re.search(r'\b\d{4}-\d{2}-\d{2}\b',run['input'])
        call={'id':'financial-daily-report','tool_id':tool['id'],
              'arguments':{'date':requested_date[0]} if requested_date else {}}
        state['financial_daily_call']=call
        ai.data.update_run(run['id'],state=state)
    if not state.get('financial_daily_result'):
        result=ai._tool(run,state,call)
        if result is None:return True
        state['financial_daily_result']=result
        ai.data.update_run(run['id'],state=state)
    receipt=state['financial_daily']
    ai.finish(run['id'],f"财经日报已生成，原文日期 {receipt['date']}。\n行情采集时间：{receipt['snapshot_at']}。\n原文：{receipt['url']}"+
              ('\n'+'\n'.join(receipt['warnings']) if receipt['warnings'] else ''))
    return True
