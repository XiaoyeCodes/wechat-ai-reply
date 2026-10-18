"""Send the approved design preview once to the explicitly named contact."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--contact', required=True)
    args = parser.parse_args()
    from wechat_panel.store import Store
    from wechat_panel.bridge import WeixinBridge
    store = Store(ROOT / '.panel-data/panel.sqlite3')
    contacts = [c for c in store.contacts() if args.contact in (c['lookup'], c['nickname'])]
    if len(contacts) != 1: raise SystemExit('Contact is missing or ambiguous')
    bridge = WeixinBridge()
    try:
        contact = contacts[0]
        bridge.attach(contact, for_send=True)
        result = bridge.send_file(contact, ROOT / 'output/pdf/financial-report-preview.pdf')
        print(json.dumps({'confirmed': True, 'source_key': result, 'contact': args.contact}))
    finally:
        bridge.close()
