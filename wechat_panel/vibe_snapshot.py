"""Read SparkFlow launch settings and copy its native preference memory."""
import os
from pathlib import Path
import re
import shutil


def snapshot_context(sparkflow, runtime):
    config = (Path(sparkflow) / 'vite.config.ts').read_text(encoding='utf-8')
    match = re.search(r"const foreignProxyUrl\s*=\s*['\"]([^'\"]+)['\"]", config)
    if not match:
        raise ValueError('Cannot determine SparkFlow research proxy configuration')
    proxy = match.group(1)
    network = {key: os.environ.get(key) or proxy for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
    network['NO_PROXY'] = os.environ.get('NO_PROXY') or 'localhost,127.0.0.1,::1'
    original = Path.home() / '.vibe-trading/memory'
    target = Path(runtime) / 'preferences'
    target.mkdir(parents=True, exist_ok=True)
    for path in original.glob('*.md'):
        shutil.copy2(path, target / path.name)
    return dict(network=network, preferences=str(target.resolve()))
