"""Live model + public-data smoke test, using synthetic messages and an isolated store."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wechat_panel.ai import AIService
from wechat_panel.ai_tools import complete
from wechat_panel.ai_store import unprotect
from wechat_panel.store import Store
from wechat_panel.vibe_catalog import REVISION
from wechat_panel.vibe import installation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=ROOT / '.panel-data')
    parser.add_argument('--provider-id')
    parser.add_argument('--agent-id', help='Test the saved agent and provider settings without token-limit overrides')
    parser.add_argument('--case', choices=['basic', 'crypto', 'recap'], default='basic')
    parser.add_argument('--sparkflow', type=Path, help='Test a SparkFlow model snapshot without changing the active panel')
    parser.add_argument('--live', action='store_true', help='Call the configured model and public data services')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this test uses the configured model and may incur API charges')
    database = args.data_dir / 'panel.sqlite3'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT id,data FROM ai_resources WHERE kind='providers'").fetchall()
        agent_row = db.execute("SELECT data FROM ai_resources WHERE kind='agents' AND id=?", (args.agent_id,)).fetchone() if args.agent_id else None
    if args.agent_id and not agent_row:
        raise ValueError('Agent not found')
    saved_agent = json.loads(agent_row[0]) if agent_row else None
    if saved_agent:
        args.provider_id = saved_agent['provider_id']
    rows = [r for r in rows if not args.provider_id or r[0] == args.provider_id]
    if len(rows) != 1:
        raise ValueError('Use --provider-id to select one configured model')
    provider = json.loads(rows[0][1])
    provider['api_key'] = unprotect(provider.pop('secret', ''))
    if args.sparkflow:
        settings = json.loads((args.sparkflow / 'UserData/settings/integration-settings.json').read_text(encoding='utf-8-sig'))['ai']
        provider.update(model=settings['model'], base_url=settings['baseUrl'], api_key=settings['apiKey'], temperature=0, thinking='default')
        saved_agent.update(vibe_engine='sparkflow', prompt='', style_id='', vibe_task_timeout=1800)
    if not saved_agent:
        provider['max_tokens'] = max(3000, provider.get('max_tokens', 1000))
        provider['timeout'] = 90
    report = {'revision': REVISION, 'engine': saved_agent.get('vibe_engine', 'github') if saved_agent else 'github', 'model': provider['model'], 'tests': []}
    if report['engine'] == 'sparkflow':
        report['revision'] = installation('sparkflow')['fingerprint']
    answers = []
    def call_model(provider, messages, tools):
        answer, usage = complete(provider, messages, tools)
        answers.append(answer)
        return answer, usage
    with tempfile.TemporaryDirectory(prefix='relay-vibe-live-') as tmp:
        ai = AIService(Store(Path(tmp) / 'panel.sqlite3'), model_call=call_model)
        try:
            p = ai.data.save('providers', None, provider)
            agent_config = saved_agent or dict(name='Vibe live verification', kind='vibe', provider_id=p['id'],
                  vibe_tools=['options_pricing', 'search_symbol', 'get_stock_profile', 'get_market_data'], vibe_skills=['options-payoff', 'research-discipline'],
                  vibe_max_iterations=8, vibe_task_timeout=300)
            agent_config.pop('secret', None)
            agent_config['provider_id'] = p['id']
            a = ai.data.save('agents', None, agent_config)
            cases = [
                ('skill-and-calculation', '这是软件测试，不是投资建议。请先加载 options-payoff 技能，然后调用 options_pricing。参数：spot=100，strike=100，expiry_days=30，volatility=0.2，risk_free_rate=0.05，option_type=call。用中文简短报告理论价格、Delta，并说明是基于输入假设的理论结果，不必重复输入数字。'),
                ('public-data', '这是公开数据查询测试。先调用 search_symbol，query 为 AAPL，确认美股苹果公司的代码；再调用 get_stock_profile，sections 为 ["key_stats"]。只需简短报告实际查到的公司名称、证券代码、交易所和报价币种，注明各自来源；不要报告其他财务指标或价格。缺失字段说未取得。'),
            ] if args.case == 'basic' else [('recap', '昨天美股市场的情况怎么样？帮我汇总一下。')] if args.case == 'recap' else [('crypto', '比特币现在的情况怎么样？可以买吗？')]
            for name, prompt in cases:
                answers.clear()
                run = ai.create_run(a['id'], prompt, dry_run=True)
                ai.execute(run['id'])
                result = ai.data.detail(run['id'])
                item = dict(name=name, status=result['status'], reply=result['reply'], error=result['error'], trace=result.get('trace', []), model_answers=list(answers))
                receipts = [t['detail'] for t in item['trace'] if t['step'] == 'Vibe 工具结果']
                if name == 'skill-and-calculation':
                    item['verified'] = ('2.493' in item['reply'] and ('0.539' in item['reply'] or '0.54' in item['reply'])
                                        and any(r['name'] == 'options_pricing' for r in receipts)
                                        and not any(s in item['reply'] for s in ('拒绝', '略※', 'omitted')))
                elif name == 'public-data':
                    item['verified'] = (any(r['name'] == 'get_stock_profile' and '"ok": true' in r['result'] and 'Nasdaq' in r['result'] for r in receipts)
                                        and any(r['name'] == 'search_symbol' and 'AAPL.US' in r['result'] for r in receipts)
                                        and ('Apple' in item['reply'] or '苹果' in item['reply']) and '拒绝' not in item['reply'])
                elif name == 'recap':
                    item['verified'] = (bool(receipts) and any(r['name'] in ('web_search', 'read_url', 'get_market_data') for r in receipts)
                        and len(item['reply']) > 300 and not any(s in item['reply'] for s in ('没拿到', '没有拿到', '全都失败', '全部失败', '无法获取')))
                else:
                    # Require real spot-crypto bars, not an ETF/company result
                    # or the model merely saying it fetched data.
                    bars = []
                    for receipt in receipts:
                        if receipt['name'] not in ('get_market_data', 'technical_indicators'):
                            continue
                        try:
                            payload = json.loads(receipt['result'])
                        except ValueError:
                            continue
                        if (receipt['name'] == 'technical_indicators' and payload.get('ok') is True
                                and payload.get('symbol') in ('BTC-USDT', 'BTC-USD') and payload.get('latest_close')
                                and payload.get('read_identity', {}).get('provenance', {}).get('source')):
                            bars.append({'close': payload['latest_close'], 'trade_date': payload.get('latest_date')})
                        for symbol in ('BTC-USDT', 'BTC-USD'):
                            series = payload.get(symbol, [])
                            if isinstance(series, dict):
                                series = series.get('data', [])
                            if isinstance(series, list):
                                bars.extend(row for row in series if isinstance(row, dict) and row.get('close'))
                    item['verified'] = bool(bars) and ('BTC' in item['reply'] or '比特币' in item['reply']) and not any(s in item['reply'] for s in ('没能拿到', '无法获取', '身份冲突'))
                report['tests'].append(item)
                print(json.dumps({k: v for k, v in item.items() if k not in ('trace', 'model_answers')}, ensure_ascii=False), flush=True)
        finally:
            ai.stop()
    output = ROOT / ('test-results/vibe-' + args.case + '-verification.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.case == 'recap':
        output.with_suffix('.md').write_text(report['tests'][0]['reply'], encoding='utf-8')
    if any(t['status'] != 'completed' or not t['verified'] for t in report['tests']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
