"""Exercise the saved Aria model in an isolated group; never sends WeChat.

Run after capturing an explicitly supplied test picture with the native bridge.
Credentials are copied locally as encrypted resources; real chat history is not.
"""
import argparse
import base64
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_panel.ai import AIService
from wechat_panel.group_bridge import group_key
from wechat_panel.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-id', required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    turns = []
    with tempfile.TemporaryDirectory(prefix='group-dialogue-') as folder:
        store = Store(Path(folder) / 'panel.sqlite3')
        ai = AIService(store)
        try:
            with sqlite3.connect('.panel-data/panel.sqlite3') as source, store.connect() as target:
                target.execute('DELETE FROM ai_resources')
                for row in source.execute('SELECT kind,id,data,created_at,updated_at FROM ai_resources'):
                    target.execute('INSERT OR REPLACE INTO ai_resources VALUES(?,?,?,?,?)', row)
            agent = ai.data.get('agents', args.agent_id)
            ai.data.settings({'enabled': True})
            contact = store.add_contact({'lookup': '隔离群聊验收', 'nickname': '隔离群聊验收',
                'account_id': 'group-qa-self', 'wechat_id': group_key('group-qa-self', '隔离群聊验收'),
                'chat_type': 'group', 'group_members': [], 'group_self_name': '机器人',
                'group_identity_verified': True, 'group_members_verified': True, 'group_self_name_verified': True})
            cid = contact['id']
            store.update_contact(cid, ai_agent_id=agent['id'], mode='ai_auto', listening=True)
            store.ingest(cid, [])
            rows = []

            def incoming(text, sender='Aria', picture=False):
                item = {'key': 'qa-' + str(len(rows)), 'kind': 'image' if picture else 'text',
                    'text': text, 'direction': 'incoming', 'sender': sender, 'sender_verified': True,
                    'direction_verified': True, 'mention_me': not picture, 'mention_verified': True}
                if picture:item['image_data'] = base64.b64encode(args.image.read_bytes()).decode()
                rows.append(item)
                store.ingest(cid, rows)
                return store.messages(cid)[-1]

            def turn(text, sender='Aria'):
                message = incoming(text, sender)
                run = ai.create_run(agent['id'], text, cid, message['id'])
                ai.execute(run['id'])
                run = ai.data.run(run['id'])
                detail = ai.data.detail(run['id'])
                result = {'question': text, 'sender': sender, 'status': run['status'], 'reply': run['reply'],
                    'error': run['error'], 'trace': detail['trace'], 'scope': run['state']['scope'],
                    'images': run['state'].get('dynamic_context', {}).get('images', [])}
                turns.append(result)
                print(json.dumps({k: result[k] for k in ('question', 'sender', 'status', 'reply', 'error')}, ensure_ascii=False), flush=True)
                # Confirm only inside this isolated fake transport. The report
                # never calls these real WeChat receipts.
                for outgoing in store.pending():
                    if outgoing.get('ai_run_id') != run['id']:continue
                    store.set_message_state(outgoing['id'], 'confirmed', source_key='qa-receipt-' + str(outgoing['id']))
                    rows.append({'key': 'qa-receipt-' + str(outgoing['id']), 'text': outgoing['text'],
                        'kind': outgoing['kind'], 'direction': 'outgoing', 'sender': '机器人',
                        'sender_verified': True, 'direction_verified': True})
                store.ingest(cid, rows)
                return result

            turn('@机器人 晚上好，我今天有点累，想随便聊两句。不要联网。')
            turn('@机器人 我刚才说今天怎么样？一句话回答，不要联网。')
            incoming('图片', picture=True)
            turn('@机器人 这张图里有什么？简单描述，不要联网。')
            turn('@机器人 刚才图片中头发的主要颜色是什么？不要联网。')
            turn('@机器人 请调用 use_skill 加载 web-video-reader Skill，再告诉我它能处理哪几类内容。这次只读技能，不要联网或读取网址。')
            turn('@机器人 这是隔离功能测试：我的测试猫叫橘团，请记住。不要联网。')
            turn('@机器人 我刚才说的测试猫叫什么？不要联网。')
            turn('@机器人 我在群里的发言名字是什么？你被 @ 的群昵称是什么？不要联网。')
            turn('@机器人 我之前说的测试猫叫什么？不知道就说不知道，不要联网。', '验收成员乙')
        finally:
            ai.stop()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'transport': 'isolated; real model and bound skills; no WeChat sends',
        'agent': agent['name'], 'turns': turns}, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(t['status'] == 'queued' for t in turns), 'Inspect the saved report for model or tool failures'
    assert turns[2]['images'] and all(o['status'] == 'ready' for o in turns[2]['images'])
    assert turns[3]['images'] and any(t['step'] == '图片追问核对' for t in turns[3]['trace'])
    assert any(t['step'] == '加载技能' and t['detail'].get('skill') == 'web-video-reader' for t in turns[4]['trace'])
    assert '橘团' in turns[6]['reply'] and '橘团' not in turns[-1]['reply']
    assert 'Aria' in turns[7]['reply'] and '机器人' in turns[7]['reply']
    assert not any(t['step'] == '联网结果核对' for turn in turns for t in turn['trace'])
    assert turns[-1]['scope'] != turns[0]['scope']
    print('Saved:', args.output)


if __name__ == '__main__':
    main()
