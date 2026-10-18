"""Isolated upstream AgentLoop. JSON-lines IPC keeps model credentials in Relay."""
import contextlib
import importlib
import json
import os
from pathlib import Path
import sys
import threading

from vibe_catalog import TOOLS, SKILLS, OFFLINE_TOOLS, PROMPT
from vibe_network import configure_ccxt_network

WIRE = sys.stdout
WIRE_LOCK = threading.Lock()


def emit(kind, **payload):
    with WIRE_LOCK:
        WIRE.write(json.dumps({'type': kind, **payload}, ensure_ascii=False) + '\n')
        WIRE.flush()


def main(request):
    sys.path.insert(0, str(Path(request['source']) / 'agent'))
    configure_ccxt_network()
    from jsonschema import Draft202012Validator
    from src.agent.tools import ToolRegistry
    from src.agent.skills import SkillsLoader
    from src.tools.load_skill_tool import LoadSkillTool

    selected_tools = request.get('tools', [])
    selected_skills = request.get('skills', [])
    if set(selected_tools) - TOOLS.keys() or set(selected_skills) - SKILLS.keys():
        raise ValueError('未授权的 Vibe-Trading 技能或工具')

    class BoundSkills(SkillsLoader):
        def get_content(self, name):
            if name not in selected_skills:
                return 'Error: Skill is not enabled for this agent.'
            return super().get_content(name)

    loader = BoundSkills(Path(request['source']) / 'agent/src/skills',
                         Path(os.environ['VIBE_TRADING_HOME']) / 'empty-skills')
    loader._user_skills_dir = None  # Disable the upstream on-disk fallback.
    loader.skills = [s for s in loader.skills if s.name in selected_skills]

    class CheckedRegistry(ToolRegistry):
        def execute(self, name, params):
            tool = self.get(name)
            if not tool:
                return json.dumps({'status': 'error', 'error': 'Tool not authorized'})
            if list(Draft202012Validator(tool.parameters).iter_errors(params)):
                return json.dumps({'status': 'error', 'error': 'Invalid tool arguments'})
            if name == 'load_skill' and params.get('name') not in selected_skills:
                return json.dumps({'status': 'error', 'error': 'Skill not authorized'})
            emit('tool_start', name=name, arguments=params)
            result = super().execute(name, params)
            if name == 'options_pricing':
                # Upstream's generic ledger treats every `price` leaf as an observed
                # market quote. This tool computes a hypothetical model value; keep
                # it distinct without disabling identity or price grounding checks.
                payload = json.loads(result)
                if payload.get('status') == 'ok' and 'price' in payload:
                    payload['theoretical_value'] = payload.pop('price')
                    payload['value_type'] = 'Black-Scholes calculation from supplied assumptions; not a market quote'
                    result = json.dumps(payload, ensure_ascii=False)
            emit('tool_result', name=name, result=result[:12000])
            return result

    registry = CheckedRegistry()
    if selected_skills:
        registry.register(LoadSkillTool(skills_loader=loader))
    unavailable = []
    for name in selected_tools:
        if not request.get('online', True) and name not in OFFLINE_TOOLS:
            continue
        module, cls, _ = TOOLS[name]
        try:
            tool_type = getattr(importlib.import_module('src.tools.' + module), cls)
            if not tool_type.check_available():
                unavailable.append(name)
                continue
            tool = tool_type()
            if tool.name != name or not tool.is_readonly:
                raise ValueError('Tool contract changed')
            registry.register(tool)
        except Exception:
            unavailable.append(name)

    if request['action'] == 'check':
        # Import the actual loop as well, so a tool-only success cannot hide a broken engine.
        from src.agent.loop import AgentLoop
        emit('result', ready=True, tools=registry.tool_names,
             skills=[s.name for s in loader.skills], unavailable=unavailable)
        return
    if request['action'] == 'tool':
        emit('result', result=registry.execute(request['name'], request.get('arguments', {})))
        return

    from src.agent import loop as engine
    from src.agent.context import ContextBuilder
    from src.providers.chat import LLMResponse, ToolCallRequest

    class WechatContext(ContextBuilder):
        def __init__(self, registry, memory, **kwargs):
            super().__init__(registry, memory, skills_loader=loader, **kwargs)

        def build_system_prompt(self, user_message=''):
            return (super().build_system_prompt(user_message) + '\n\n# WeChat channel rules\n' + PROMPT
                    + '\n' + request.get('prompt', '')
                    + '\n本轮实际可用工具：' + ', '.join(registry.tool_names)
                    + ('\n本轮禁止联网，只能分析用户给出的信息或进行本地计算。' if not request.get('online', True) else ''))

    engine.ContextBuilder = WechatContext

    class RelayLLM:
        model_name = request['model']

        def chat(self, messages, tools=None, **kwargs):
            # Some compatible models confuse tool names with call IDs when
            # declaring computed figures. Supply verbatim references from the
            # actual receipt; upstream still validates every claimed number.
            hints = []
            for message in messages:
                if message.get('role') != 'tool' or not message.get('tool_call_id'):
                    continue
                try:
                    receipt = json.loads(message.get('content', ''))
                except (ValueError, TypeError):
                    continue
                if not isinstance(receipt, dict) or 'theoretical_value' not in receipt:
                    continue
                for field in ('theoretical_value', 'delta', 'gamma', 'theta', 'vega', 'rho'):
                    value = receipt.get(field)
                    if type(value) in (int, float):
                        hints.append(f"{value} | observed | {field} | {message['tool_call_id']}::{field}")
            if hints and messages and messages[0].get('role') == 'system':
                messages = [dict(messages[0]), *messages[1:]]
                messages[0]['content'] += ('\n\n本轮实际计算结果的有效 figures 声明如下。仅引用用户所问指标，'
                    '正文数值与声明必须完全一致，保留工具返回的小数精度，不自行四舍五入，不增加其他数字示例。'
                    '在回复末尾的 figures 围栏复制对应声明，禁止把工具名称当作调用 ID：\n' + '\n'.join(hints))
            emit('model', messages=messages, tools=tools or [])
            raw = sys.stdin.readline()
            if not raw:
                raise RuntimeError('Relay connection closed')
            response = json.loads(raw)
            if response.get('error'):
                raise RuntimeError(response['error'])
            answer = response['answer']
            usage = response.get('usage') or {}
            return LLMResponse(
                content=answer.get('content'),
                reasoning_content=answer.get('reasoning_content'),
                tool_calls=[ToolCallRequest(id=t['id'], name=t['function']['name'],
                                           arguments=json.loads(t['function']['arguments']))
                            for t in answer.get('tool_calls', [])],
                usage_metadata={'input_tokens': usage.get('prompt_tokens', 0),
                                'output_tokens': usage.get('completion_tokens', 0),
                                'total_tokens': usage.get('total_tokens', 0)})

        def stream_chat(self, messages, tools=None, **kwargs):
            # Relay already supports compatible non-streaming providers and secret redaction.
            return self.chat(messages, tools)

    emit('ready', tools=registry.tool_names, skills=[s.name for s in loader.skills], unavailable=unavailable)
    agent = engine.AgentLoop(registry, RelayLLM(), max_iterations=request['max_iterations'])
    result = agent.run(request['message'], history=request.get('history') or [], session_id=request['session'])
    emit('result', status=result.get('status'), content=result.get('content', ''),
         reason=result.get('reason', ''), iterations=result.get('iterations'))


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.readline())
        # Dependencies can print diagnostics; keep the protocol channel unambiguous.
        with contextlib.redirect_stdout(sys.stderr):
            main(request)
    except Exception as exc:
        emit('error', error='Vibe-Trading 引擎执行失败：' + type(exc).__name__)
        sys.exit(1)
