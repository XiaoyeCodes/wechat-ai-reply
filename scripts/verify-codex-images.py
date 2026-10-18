"""Live integration check; uses the logged-in ChatGPT account's image quota."""
import argparse
import base64
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wechat_panel.codex_images import generate

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference',type=Path)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--prompt',required=True)
args=parser.parse_args()
reference=base64.b64encode(args.reference.read_bytes()).decode() if args.reference else None
data=generate(args.prompt,reference,timeout=300)
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_bytes(data)
print(str(args.output.resolve()))
