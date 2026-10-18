"""Copy the selected SparkFlow model into Relay; never write to SparkFlow."""
import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_panel.ai_store import AIStore
from wechat_panel.ai_models import Agent
from wechat_panel.store import Store
from wechat_panel.vibe import installation, VibeService
from wechat_panel.vibe_snapshot import snapshot_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sparkflow', type=Path, required=True)
    parser.add_argument('--agent-id', required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / '.panel-data')
    args = parser.parse_args()
    install = installation('sparkflow')
    install.update(snapshot_context(args.sparkflow, Path(install['source']).parent))
    (ROOT / '.runtime/vibe-sparkflow.json').write_text(json.dumps(install, indent=2), encoding='utf-8')
    from types import SimpleNamespace
    from threading import Event
    probe = VibeService(SimpleNamespace(store=SimpleNamespace(path=args.data_dir / 'panel.sqlite3'), stopping=Event()))
    loaded = probe.worker(dict(action='check', engine='sparkflow'), timeout=90)
    install.update(tools=loaded['tools'], skills=loaded['skills'])
    (ROOT / '.runtime/vibe-sparkflow.json').write_text(json.dumps(install, indent=2), encoding='utf-8')
    source = json.loads((args.sparkflow / 'UserData/settings/integration-settings.json').read_text(encoding='utf-8-sig'))['ai']
    if source['provider'] != 'deepseek':
        raise ValueError('This synchronization currently requires the verified SparkFlow DeepSeek configuration')
    data = AIStore(Store(args.data_dir / 'panel.sqlite3'))
    previous = data.get('agents', args.agent_id, private=True)
    if previous['kind'] != 'vibe':
        raise ValueError('Choose an existing Vibe agent')
    backup = ROOT / '.runtime' / ('vibe-before-sparkflow-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    backup.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding='utf-8')
    name = 'SparkFlow 同源 · DeepSeek V4 Flash'
    prior = next((p for p in data.list('providers') if p['name'] == name), None)
    provider = data.save('providers', prior['id'] if prior else None, dict(
        name=name, base_url=source['baseUrl'], model=source['model'], api_key=source['apiKey'],
        temperature=0, thinking='default', max_tokens=8000, timeout=120))
    value = {k: v for k, v in previous.items() if k in Agent.model_fields}
    value.update(vibe_engine='sparkflow', provider_id=provider['id'], prompt='', style_id='',
                 vibe_tools=[], vibe_skills=[], vibe_max_iterations=50, vibe_task_timeout=None,
                 description=f"SparkFlow 同源 AI 助手，{len(loaded['tools'])} 项原生工具、{len(loaded['skills'])} 项技能，最多 50 轮研究。")
    result = data.save('agents', args.agent_id, value)
    print(json.dumps(dict(agent_id=result['id'], engine=result['vibe_engine'], model=provider['model'],
                         backup=str(backup)), ensure_ascii=False))


if __name__ == '__main__':
    main()
