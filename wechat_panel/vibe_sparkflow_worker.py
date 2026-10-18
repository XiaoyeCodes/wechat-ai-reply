"""Run an unmodified SparkFlow research snapshot; Relay only transports text."""
import contextlib
import json
import os
from pathlib import Path
import sys
import threading
import shutil

from vibe_network import configure_ccxt_network

WIRE = sys.stdout
LOCK = threading.Lock()


def emit(kind, **payload):
    with LOCK:
        WIRE.write(json.dumps({'type': kind, **payload}, ensure_ascii=False) + '\n')
        WIRE.flush()


def main(request):
    sys.path.insert(0, str(Path(request['source']) / 'agent'))
    home = Path(os.environ['HOME'])
    memory = home / '.vibe-trading/memory'
    if request.get('preferences') and not memory.exists():
        shutil.copytree(request['preferences'], memory)
    os.environ.update(SPARKFLOW_USER_DATA_DIR=str(home / 'UserData'),
                      SPARKFLOW_VIBE_ENV_FILE=str(home / 'settings/vibe.env'),
                      SPARKFLOW_INTEGRATION_MODEL_LOCK='1', LANGCHAIN_TEMPERATURE='0',
                      TIMEOUT_SECONDS='180', MAX_RETRIES='2', LANGCHAIN_REASONING_EFFORT='')
    model = request.get('native_model') or {}
    if model:
        provider = model['provider']
        os.environ['LANGCHAIN_PROVIDER'] = provider
        os.environ['LANGCHAIN_MODEL_NAME'] = model['model']
        prefix = 'DEEPSEEK' if provider == 'deepseek' else 'OPENAI'
        os.environ[prefix + '_API_KEY'] = model['api_key']
        os.environ[prefix + '_BASE_URL'] = model['base_url']
        os.environ['OPENAI_API_KEY'] = model['api_key']
        os.environ['OPENAI_API_BASE'] = model['base_url']
    configure_ccxt_network()

    from src.tools import build_registry
    from src.memory.persistent import PersistentMemory
    from src.agent.skills import SkillsLoader
    from src.agent.loop import AgentLoop
    from src.config.loader import load_runtime_agent_config

    pm = PersistentMemory()
    registry = build_registry(persistent_memory=pm, include_shell_tools=False,
                              agent_config=load_runtime_agent_config(), session_id=request.get('session'),
                              interactive=False)
    # An explicit offline setting is a user constraint, not a reply-style prompt.
    if not request.get('online', True):
        from src.agent.tools import ToolRegistry
        offline = ToolRegistry()
        for name in ('load_skill', 'options_pricing', 'options_payoff', 'financial_rigor'):
            if registry.get(name):
                offline.register(registry.get(name))
        registry = offline
    skills = [s.name for s in SkillsLoader().skills]
    metadata = dict(tools=registry.tool_names, skills=skills, unavailable=[])
    if request['action'] == 'check':
        emit('result', ready=True, **metadata)
        return

    # Trace the real tools without altering their schemas, arguments or output.
    execute = registry.execute
    def traced(name, params):
        emit('tool_start', name=name, arguments=params)
        result = execute(name, params)
        emit('tool_result', name=name, result=result[:12000])
        return result
    registry.execute = traced
    from src.providers.chat import ChatLLM
    from src.session.service import SessionService
    llm = ChatLLM()
    agent = AgentLoop(registry=registry, llm=llm, max_iterations=50, persistent_memory=pm)
    emit('ready', **metadata)
    # Use the same history conversion/budget as SparkFlow's session endpoint.
    messages = [*(request.get('history') or []), {'role': 'user', 'content': request['message']}]
    history = SessionService._convert_messages_to_history(messages)
    result = agent.run(user_message=request['message'], history=history or None,
                       session_id=request['session'])
    emit('result', status=result.get('status'), content=result.get('content', ''),
         reason=result.get('reason', ''), iterations=result.get('iterations'))


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.readline())
        with contextlib.redirect_stdout(sys.stderr):
            main(request)
    except Exception as exc:
        emit('error', error='SparkFlow 同源引擎执行失败：' + type(exc).__name__)
        sys.exit(1)
