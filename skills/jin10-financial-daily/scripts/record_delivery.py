"""Record a report only after the caller has actually delivered it."""
import argparse
import json
from pathlib import Path
from workflow import record_delivery


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workspace', required=True, type=Path)
    ap.add_argument('--manifest', required=True, type=Path)
    args = ap.parse_args()
    print(json.dumps(record_delivery(args.workspace, args.manifest), ensure_ascii=False))
