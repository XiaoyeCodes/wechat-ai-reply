"""Run the complete newspaper pipeline; return JSON and never send or mark delivery."""
import argparse
import json
import os
import subprocess
import traceback
from datetime import date
from pathlib import Path

from workflow import (SCRIPTS, artifact_marker, issue_is_delivered, now,
                      poppler_runtime, python_runtime, read_json, sha256,
                      validate_snapshot, write_json)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workspace', type=Path, required=True)
    ap.add_argument('--date', help='Expected article date (required offline)')
    ap.add_argument('--snapshot-date', help='Archived collection date, offline only')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--force', action='store_true', help='Bypass delivery dedupe; keep same-day snapshot')
    ap.add_argument('--fetch-python')
    ap.add_argument('--render-python')
    ap.add_argument('--poppler')
    ap.add_argument('--standalone', action='store_true', help='Outside Codex only: skip app operation marker')
    args = ap.parse_args()
    root = args.workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)
    started = now()
    log_path = root / 'logs' / (started.strftime('%Y%m%d-%H%M%S-%f') + '.log')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JIN10_DAILY_WORKSPACE=str(root), PYTHONIOENCODING='utf-8')

    def run(command, timeout=180):
        result = subprocess.run([str(c) for c in command], cwd=root, env=env,
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=timeout)
        with log_path.open('a', encoding='utf-8') as log:
            log.write(json.dumps([str(c) for c in command], ensure_ascii=False) + '\n')
            log.write(result.stdout + '\n' + result.stderr + '\n')
        if result.returncode:
            raise RuntimeError(f'{Path(str(command[0])).name} failed ({result.returncode}); see log')
        return result.stdout

    try:
        if args.date:
            date.fromisoformat(args.date)
        if args.snapshot_date:
            date.fromisoformat(args.snapshot_date)
        if args.offline and not args.date:
            raise ValueError('--offline requires --date')
        if args.snapshot_date and not args.offline:
            raise ValueError('--snapshot-date is only for offline reprints')
        collection_date = (args.snapshot_date or args.date) if args.offline else started.date().isoformat()
        markets_path = root / 'source' / collection_date / 'markets.json'
        if not args.offline:
            fetch_python = python_runtime(['requests', 'bs4'], args.fetch_python)
            if not markets_path.exists():
                run(fetch_python + ['-X', 'utf8', SCRIPTS / 'fetch_markets.py', '--date', collection_date])
        # News may legitimately be an earlier edition; snapshots have their own date.
        snapshot = validate_snapshot(read_json(markets_path), collection_date)
        if args.offline:
            issue_date = args.date
        else:
            command = fetch_python + ['-X', 'utf8', SCRIPTS / 'fetch_latest.py']
            if args.date:
                command += ['--date', args.date]
            fetched = json.loads(run(command))
            issue_date = fetched['date']
        issue_path = root / 'source' / issue_date / 'issue.json'
        issue = read_json(issue_path)
        if issue['date'] != issue_date or issue.get('text_integrity') is not True:
            raise ValueError('Article date or extraction integrity invalid')
        if issue.get('content_policy') != 'news-text-only-v2':
            raise ValueError('Archive uses an outdated source-media policy')
        if issue_is_delivered(root, issue) and not args.force:
            return {'status': 'unchanged', 'date': issue_date, 'url': issue['url'], 'log': str(log_path)}
        render_python = python_runtime(['reportlab', 'pypdf', 'pdfplumber', 'PIL'], args.render_python)
        poppler = poppler_runtime(args.poppler)
        outdir = root / 'output' / issue_date
        pdf = outdir / 'financial-morning.pdf'
        if not args.standalone:
            run(artifact_marker() + ['--operation-kind', 'edit' if pdf.exists() else 'create',
                                     '--expected-output-count', '1', '--output-format', 'pdf'])
        run(render_python + ['-X', 'utf8', SCRIPTS / 'render_daily.py', '--issue', issue_path,
                             '--date', issue_date, '--markets', markets_path])
        validation_path = outdir / 'validation.json'
        validation = json.loads(run(render_python + ['-X', 'utf8', SCRIPTS / 'verify_report.py',
                                    '--issue', issue_path, '--markets', markets_path, '--pdf', pdf,
                                    '--output', validation_path]))
        # Only remove old numbered previews within this report's output directory.
        for old in outdir.glob('daily-*.png'):
            if old.stem.removeprefix('daily-').isdigit():
                old.unlink()
        run([poppler, '-scale-to', '1200', '-png', pdf, outdir / 'daily'])
        previews = sorted(outdir.glob('daily-*.png'), key=lambda p: int(p.stem.split('-')[-1]))
        if len(previews) != validation['pages']:
            raise ValueError('Not every PDF page has a PNG preview')
        warnings = [f"{r['name']}: {r['status']}" for r in snapshot['rows'] if r['status'] != 'ok']
        if snapshot['generated_at'][11:13] != '08':
            warnings.append('本次为实际采集时间快照，并非北京时间08:00快照。')
        manifest_path = outdir / 'run-result.json'
        result = {'status': 'ready', 'date': issue_date, 'url': issue['url'], 'sha256': issue['sha256'],
                  'issue': str(issue_path), 'markets': str(markets_path),
                  'snapshot_at': snapshot['generated_at'], 'started_at': started.isoformat(),
                  'completed_at': now().isoformat(), 'offline': args.offline,
                  'pdf': str(pdf), 'artifact_sha256': sha256(pdf), 'cover': str(previews[0]),
                  'previews': [str(p) for p in previews], 'manifest': str(manifest_path),
                  'validation': validation, 'warnings': warnings, 'log': str(log_path)}
        write_json(manifest_path, result)
        return result
    except Exception as exc:
        with log_path.open('a', encoding='utf-8') as log:
            log.write(traceback.format_exc())
        return {'status': 'failed', 'error': str(exc), 'log': str(log_path)}


if __name__ == '__main__':
    result = main()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result['status'] == 'failed' else 0)
