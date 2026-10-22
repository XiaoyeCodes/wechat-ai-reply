"""Shared state, runtime discovery and validation for the daily newspaper."""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
SCRIPTS = Path(__file__).resolve().parent
KEYS = ('nasdaq', 'sp500', 'mags', 'shanghai', 'hsi', 'btc', 'gold', 'brent', 'us10y')


def article_text(reader):
    """Extract body frames in content order, excluding page header/footer text.

    A source paragraph may continue on the next page. Including the repeated
    page furniture between its fragments falsely reports missing source text.
    Keep text by its drawing position, never by removing matching news words.
    """
    fragments = []
    for page in reader.pages[1:]:
        def visitor(text, cm, tm, font, size):
            y = tm[4]*cm[1] + tm[5]*cm[3] + cm[5]
            if 72 <= y <= 785:fragments.append(text)
        page.extract_text(visitor_text=visitor)
    return ''.join(fragments)


def now():
    return datetime.now(CST)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dependency_root():
    return Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'


def python_runtime(modules, explicit=None):
    candidates = [[explicit]] if explicit else [[sys.executable]]
    if not explicit:
        if shutil.which('py'):
            candidates.append([shutil.which('py'), '-3.12'])
        candidates += [[str(dependency_root() / 'python/python.exe')],
                       [shutil.which('python') or 'python']]
    for command in candidates:
        try:
            result = subprocess.run(command + ['-c', 'import ' + ','.join(modules)],
                                    capture_output=True, timeout=15)
            if result.returncode == 0:
                return command
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError('No Python with modules: ' + ', '.join(modules) + '; see requirements.txt')


def poppler_runtime(explicit=None):
    choices = [explicit] if explicit else [dependency_root() / 'native/poppler/Library/bin/pdftoppm.exe', shutil.which('pdftoppm')]
    for path in choices:
        if path and Path(path).is_file():
            return str(path)
    raise RuntimeError('pdftoppm not found; pass --poppler with its executable path')


def artifact_marker():
    codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    choices = list(codex_home.glob('plugins/cache/openai-primary-runtime/pdf/*/skills/pdf/container_tools/mark_artifact_operation_started.mjs'))
    if not choices:
        raise RuntimeError('Codex PDF operation marker unavailable; use --standalone only outside Codex')
    marker = max(choices, key=lambda p: p.stat().st_mtime)
    node = dependency_root() / 'node/bin/node.exe'
    executable = str(node) if node.exists() else shutil.which('node')
    if not executable:
        raise RuntimeError('Node runtime missing for Codex PDF operation marker')
    return [executable, str(marker)]


def validate_snapshot(snapshot, collection_date):
    rows = snapshot.get('rows', [])
    if len(rows) != 9 or {r.get('key') for r in rows} != set(KEYS):
        raise ValueError('Snapshot must contain exactly the nine requested instruments')
    generated = datetime.fromisoformat(snapshot['generated_at'])
    if generated.tzinfo is None or generated.astimezone(CST).date().isoformat() != collection_date:
        raise ValueError('Snapshot collection date/time does not match its directory')
    if generated > now() + timedelta(minutes=5):
        raise ValueError('Snapshot collection time is in the future')
    available = 0
    for row in rows:
        if row.get('price') is None:
            if row.get('status') != 'missing':
                raise ValueError('Missing price must have missing status')
            continue
        if row.get('status') not in ('ok', 'cached', 'older-session'):
            raise ValueError('Unknown quote status')
        if not all(math.isfinite(float(row[k])) for k in ('price', 'reference', 'change_pct')):
            raise ValueError('Nonfinite quote')
        if row['price'] <= 0 or row['reference'] <= 0:
            raise ValueError('Invalid price/reference')
        if abs((row['price'] / row['reference'] - 1) * 100 - row['change_pct']) > .04:
            raise ValueError('Snapshot price and change disagree')
        if not row.get('source_url', '').startswith('https://') or not row.get('asof'):
            raise ValueError('Quote has no provenance or timestamp')
        available += 1
    if available == 0:
        raise ValueError('All nine market quotes are unavailable')
    if snapshot.get('available_count') != available:
        raise ValueError('Snapshot available_count mismatch')
    return snapshot


def issue_is_delivered(workspace, issue):
    state = Path(workspace) / 'delivery-state.json'
    return state.exists() and read_json(state).get('sha256') == issue['sha256']


def record_delivery(workspace, manifest_path):
    workspace = Path(workspace).resolve()
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_relative_to(workspace):
        raise ValueError('Manifest must be inside the selected workspace')
    manifest = read_json(manifest_path)
    if manifest.get('status') != 'ready' or not manifest.get('validation', {}).get('passed'):
        raise ValueError('Only a validated ready report can be marked delivered')
    artifact = Path(manifest['pdf']).resolve()
    issue_path = Path(manifest['issue']).resolve()
    if not artifact.is_relative_to(workspace) or not issue_path.is_relative_to(workspace):
        raise ValueError('Report inputs and output must stay inside the workspace')
    if sha256(artifact) != manifest['artifact_sha256']:
        raise ValueError('PDF hash changed since validation')
    issue = read_json(issue_path)
    if issue['sha256'] != manifest['sha256'] or issue['date'] != manifest['date']:
        raise ValueError('Issue changed since validation')
    state = {'date': issue['date'], 'sha256': issue['sha256'],
             'delivered_at': now().isoformat(), 'destination': 'current chat',
             'artifact_sha256': manifest['artifact_sha256']}
    write_json(workspace / 'delivery-state.json', state)
    return state
