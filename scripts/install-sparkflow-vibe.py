"""Copy SparkFlow's research source without changing it or copying user history."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_panel.vibe_snapshot import snapshot_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sparkflow', type=Path, required=True)
    args = parser.parse_args()
    original = args.sparkflow.resolve() / 'services/vibe-trading'
    runtime = ROOT / '.runtime/vibe-sparkflow'
    source = runtime / 'source'
    if source.exists():
        raise SystemExit('Snapshot already exists; preserve it and choose an explicit upgrade before reinstalling.')
    runtime.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache', '.env', '.env.*',
                                   'runs', 'sessions', 'uploads', '*.sqlite', '*.sqlite-*', '*.log')
    shutil.copytree(original, source, ignore=ignore)
    digest = hashlib.sha256()
    files = {}
    for path in sorted(source.rglob('*')):
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if sha != hashlib.sha256((original / relative).read_bytes()).hexdigest():
                raise RuntimeError('Source changed during snapshot; retry against a stable source')
            files[relative] = sha
            digest.update((relative + '\0' + sha + '\n').encode())
    python = runtime / 'venv/Scripts/python.exe'
    subprocess.run(['uv', 'venv', '--python', '3.11.9', str(runtime / 'venv')], check=True)
    subprocess.run(['uv', 'pip', 'install', '--python', str(python), '--require-hashes',
                    '-r', str(source / 'requirements-lock.txt')], check=True)
    manifest = dict(engine='sparkflow', source=str(source), python=str(python),
                    original=str(original), fingerprint=digest.hexdigest(), files=files)
    manifest.update(snapshot_context(args.sparkflow, runtime))
    (ROOT / '.runtime/vibe-sparkflow.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'fingerprint': manifest['fingerprint'], 'files': len(files)}))


if __name__ == '__main__':
    main()
