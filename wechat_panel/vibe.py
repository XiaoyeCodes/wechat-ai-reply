"""Manage bounded Vibe-Trading workers without sharing SparkFlow state or credentials."""
import json
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .vibe_catalog import REVISION, REPOSITORY, TOOLS, SKILLS, DEFAULT_TOOLS, DEFAULT_SKILLS

ROOT = Path(__file__).resolve().parents[1]


def installation(engine='github'):
    path = ROOT / ('.runtime/vibe-sparkflow.json' if engine == 'sparkflow' else '.runtime/vibe-trading.json')
    if not path.is_file():
        raise ValueError('请先运行 scripts/install-vibe-trading.ps1 安装 Vibe-Trading')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if engine == 'sparkflow':
        if not Path(value['python']).is_file() or not (Path(value['source']) / 'agent/src/agent/loop.py').is_file():
            raise ValueError('SparkFlow 同源引擎未安装完整')
        return value
    if value.get('revision') != REVISION:
        raise ValueError('Vibe-Trading 版本与适配器不匹配，请运行安装脚本')
    if not Path(value['python']).is_file() or not (Path(value['source']) / 'agent/src/agent/loop.py').is_file():
        raise ValueError('Vibe-Trading 安装不完整，请重新运行安装脚本')
    marker = Path(value['source']) / '.relay-revision'
    if not marker.is_file() or marker.read_text(encoding='utf-8-sig').strip() != REVISION:
        raise ValueError('Vibe-Trading 源码版本未验证，请重新运行安装脚本')
    return value


def catalog():
    try:
        sparkflow = installation('sparkflow')
        sparkflow = {'installed': True, 'fingerprint': sparkflow['fingerprint'], 'tools': sparkflow.get('tools', []), 'skills': sparkflow.get('skills', [])}
    except (ValueError, KeyError, OSError):
        sparkflow = {'installed': False}
    try:
        installation()
        installed, detail = True, '独立运行环境已安装'
    except (ValueError, KeyError, OSError):
        installed, detail = False, '请先运行 scripts/install-vibe-trading.ps1'
    return dict(installed=installed, detail=detail, revision=REVISION, repository=REPOSITORY, sparkflow=sparkflow,
                tools=[dict(name=n, label=v[2], default=n in DEFAULT_TOOLS) for n, v in TOOLS.items()],
                skills=[dict(name=n, label=v, default=n in DEFAULT_SKILLS) for n, v in SKILLS.items()])


class VibeService:
    def __init__(self, ai):
        self.ai = ai

    def worker(self, request, *, timeout, active=lambda: True, on_model=None, on_event=None):
        native = request.get('engine') == 'sparkflow'
        install = installation('sparkflow') if native else installation()
        home = self.ai.store.path.parent / ('vibe-sparkflow' if native else 'vibe-trading') / request.get('workspace', request.get('session', 'health'))
        home.mkdir(parents=True, exist_ok=True)
        # Intentionally exclude API keys, user dotfiles and SparkFlow configuration.
        allowed = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'TEMP', 'TMP',
                   'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1',
                   VIBE_TRADING_HOME=str(home.resolve()), USERPROFILE=str(home.resolve()),
                   HOME=str(home.resolve()), APPDATA=str(home.resolve()), LOCALAPPDATA=str(home.resolve()),
                   LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false',
                   LANGCHAIN_MODEL_NAME=request.get('model', 'relay'), LANGCHAIN_PROVIDER='openai')
        if native:
            env.update(install.get('network', {}))
        request = {**request, 'source': install['source'], 'preferences': install.get('preferences') if native else None}
        messages = queue.Queue(maxsize=64)
        worker_file = 'vibe_sparkflow_worker.py' if native else 'vibe_worker.py'
        process = subprocess.Popen([install['python'], str(Path(__file__).with_name(worker_file))],
            cwd=home, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        reader_stop = threading.Event()

        def read():
            try:
                while not reader_stop.is_set():
                    line = process.stdout.readline(2_000_001)
                    if not line:
                        break
                    if len(line) > 2_000_000:
                        item = {'type': 'error', 'error': 'Vibe-Trading 响应超过大小限制'}
                    else:
                        try:
                            item = json.loads(line)
                        except ValueError:
                            item = {'type': 'error', 'error': 'Vibe-Trading 通信格式无效'}
                    while not reader_stop.is_set():
                        try:
                            messages.put(item, timeout=.2)
                            break
                        except queue.Full:
                            continue
            finally:
                try:
                    messages.put_nowait(None)
                except queue.Full:
                    pass

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout if timeout is not None else float('inf')
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
            process.stdin.flush()
            while True:
                if self.ai.stopping.is_set() or not active():
                    return None
                if time.monotonic() > deadline:
                    raise RuntimeError('Vibe-Trading 任务超过时限，已停止引擎')
                try:
                    item = messages.get(timeout=.2)
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive():
                        raise RuntimeError('Vibe-Trading 引擎提前退出，请检查安装环境')
                    continue
                if item is None:
                    raise RuntimeError('Vibe-Trading 引擎未返回结果')
                kind = item.get('type')
                if kind == 'result':
                    return item
                if kind == 'error':
                    raise RuntimeError(item['error'])
                if kind == 'model':
                    if not on_model:
                        raise RuntimeError('本次检查不允许调用模型')
                    # A slow HTTP model call must not hold the child alive after
                    # cancellation or its overall deadline. The bounded provider
                    # request can finish separately; its late answer is discarded.
                    model_results = queue.Queue(maxsize=1)

                    def call_model(payload=item, results=model_results):
                        try:
                            results.put((True, on_model(payload['messages'], payload['tools'])))
                        except Exception as exc:
                            results.put((False, exc))

                    threading.Thread(target=call_model, daemon=True).start()
                    while True:
                        if self.ai.stopping.is_set() or not active():
                            return None
                        if time.monotonic() > deadline:
                            raise RuntimeError('Vibe-Trading 任务超过时限，已停止引擎')
                        try:
                            ok, value = model_results.get(timeout=.2)
                            break
                        except queue.Empty:
                            continue
                    if not ok:
                        raise value
                    answer, usage = value
                    if self.ai.stopping.is_set() or not active():
                        return None
                    process.stdin.write(json.dumps(dict(answer=answer, usage=usage), ensure_ascii=False) + '\n')
                    process.stdin.flush()
                elif on_event:
                    on_event(item)
        finally:
            reader_stop.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            reader.join(timeout=2)
            for stream in (process.stdin, process.stdout):
                stream.close()

    def connection_test(self, agent_id):
        agent = self.ai.data.get('agents', agent_id)
        if agent['kind'] != 'vibe':
            raise ValueError('请选择 Vibe-Trading Agent')
        result = self.worker(dict(action='check', engine=agent.get('vibe_engine', 'github'), tools=agent['vibe_tools'], skills=agent['vibe_skills'],
                                  online=agent['web_policy'] != 'off'), timeout=90)
        if result is None:
            raise RuntimeError('工作台正在停止')
        revision = installation('sparkflow')['fingerprint'] if agent.get('vibe_engine') == 'sparkflow' else REVISION
        return {**result, 'revision': revision}

    def execute(self, run, state):
        from .web_policy import allowed
        agent = state['snapshot']['agent']
        provider = state['snapshot']['provider']
        history = list(state.get('context', []))
        if run['dry_run'] and not history:
            # Reuse only the same test conversation and agent; never inspect contact chats.
            with self.ai.store.connect() as db:
                rows = db.execute("SELECT id FROM ai_runs WHERE agent_id=? AND dry_run=1 AND status='completed' "
                                  "AND created_at<? ORDER BY created_at DESC LIMIT 60",
                                  (agent['id'], run['created_at'])).fetchall()
            for row in reversed(rows):
                previous = self.ai.data.run(row['id'])
                if previous['state'].get('session_id') == state['session_id']:
                    history.extend([{'role': 'user', 'content': previous['input']},
                                    {'role': 'assistant', 'content': previous['reply']}])
            if agent.get('vibe_engine') != 'sparkflow':
                history = history[-self.ai.data.settings()['context_messages']:]
        # Native AgentLoop appends the current message itself.
        if history and history[-1] == {'role': 'user', 'content': run['input']}:
            history.pop()
        # Vibe uses its own native prompt. Relay's style/custom instructions
        # must not quietly constrain the research or rewrite its answer.
        prompt = ''
        if state.get('input_media_id'):
            raise ValueError('Vibe-Trading 当前接入支持文字消息；图片请先转换为文字描述')
        native = agent.get('vibe_engine') == 'sparkflow'
        revision = installation('sparkflow')['fingerprint'] if native else REVISION
        self.ai.data.trace(run['id'], 'Vibe-Trading 原生引擎', {'revision': revision, 'engine': agent.get('vibe_engine', 'github'), 'online': allowed(state, run)})

        def model(messages, tools):
            if not self.ai._guard(run['id']):
                raise RuntimeError('任务已取消')
            answer, usage = self.ai.model_call(provider, messages, tools)
            if not self.ai._guard(run['id']):
                raise RuntimeError('任务已取消')
            self.ai.data.trace(run['id'], 'Vibe 模型处理', {'model': provider['model'], 'usage': usage})
            return answer, usage

        research_queries = set()
        def event(item):
            labels = {'ready': 'Vibe 技能与工具', 'tool_start': 'Vibe 开始工具调用', 'tool_result': 'Vibe 工具结果'}
            if item['type'] in labels:
                self.ai.data.trace(run['id'], labels[item['type']], {k: v for k, v in item.items() if k != 'type'})
            # Follow-up questions may omit "分析". Actual analytical tools are
            # another start signal; a simple search/price lookup is not.
            if item['type'] == 'tool_start' and item.get('name') in ('financial_rigor','technical_indicators','options_pricing','options_payoff'):
                self.ai.research_started(run['id'])
            if item['type'] == 'tool_start' and item.get('name') in ('get_market_data','get_stock_news','get_fundamentals','get_financial_statements','get_macro_series'):
                research_queries.add(item['name'])
                if len(research_queries) >= 2:self.ai.research_started(run['id'])

        native_model = None
        if native:
            from .ai_store import unprotect
            from urllib.parse import urlsplit
            native_model = dict(model=provider['model'], base_url=provider['base_url'],
                api_key=unprotect(provider.get('secret', '')),
                provider='deepseek' if urlsplit(provider['base_url']).hostname == 'api.deepseek.com' else 'openai')
        message = run['input']
        from .failure_replies import instruction as failure_instruction
        message += failure_instruction()
        if state.get('output_format') == 'pdf':
            message += '\n\n本次交付为 PDF，系统将负责排版与发送。请直接返回完整研究正文，使用清晰标题、段落及适当的 Markdown 数据表格，保留数据来源；不要自行生成文件或返回文件路径。'
        result = self.worker(dict(action='run', engine=agent.get('vibe_engine', 'github'), native_model=native_model,
            workspace=hashlib.sha256((agent['id'] + ':' + (run.get('contact_id') or state['session_id'])).encode()).hexdigest() if native else run['id'],
            session=run['id'], model=provider['model'], message=message,
            history=history, prompt=prompt, tools=agent['vibe_tools'], skills=agent['vibe_skills'],
            online=allowed(state, run), max_iterations=agent['vibe_max_iterations']),
            timeout=None if native else agent['vibe_task_timeout'], active=lambda: bool(self.ai._guard(run['id'])),
            on_model=model, on_event=event)
        if result is None or not self.ai._guard(run['id']):
            return
        if result.get('status') not in ('success', 'completed') or not result.get('content', '').strip():
            # No upstream exception text is exposed: it can include request bodies.
            raise RuntimeError('Vibe-Trading 未完成回复，请查看本轮模型和工具记录')
        self.ai.finish(run['id'], result['content'])
