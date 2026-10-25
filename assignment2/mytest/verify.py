"""Apply a candidate in a fresh offline container and score restored held-out tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def score(report: dict, spec: dict) -> tuple[bool, dict[str, str], list[str]]:
    statuses = {}
    for suite in report.get('testResults', []):
        path = suite['name'].removeprefix('/app/')
        for test in suite.get('assertionResults', []):
            key = path + '::' + test['fullName']
            if key in statuses:
                raise ValueError('Duplicate test ID: ' + key)
            statuses[key] = test['status']
    required = spec['fail_to_pass'] + spec['pass_to_pass']
    missing = [name for name in required if statuses.get(name) != 'passed']
    resolved = bool(spec['fail_to_pass'] and spec['pass_to_pass']) and not missing
    resolved = resolved and bool(report.get('success')) and bool(statuses)
    return resolved, statuses, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance_id')
    parser.add_argument('patch', nargs='?', type=Path)
    parser.add_argument('--report', type=Path, help='Save structured results, including test IDs')
    args = parser.parse_args()
    task = json.loads((HERE / 'tasks.json').read_text())[args.instance_id]
    spec = json.loads((HERE / 'task_test.json').read_text())[args.instance_id]
    task_dir = HERE / 'tasks' / args.instance_id
    quote = shlex.quote
    print(f"TASK {args.instance_id}\nBASE {task['base_commit']}\nPATCH {args.patch or '(baseline)'}", flush=True)
    with tempfile.TemporaryDirectory(prefix='mytest-verify-') as tmp:
        cmd = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '2', '--memory', '3g',
               '--pids-limit', '512', '--entrypoint', 'bash',
               '-v', f'{task_dir}/tests:/heldout:ro',
               '-v', f'{task_dir}/vitest.config.ts:/eval-config.ts:ro',
               '-v', f'{tmp}:/eval-results']
        apply = ''
        if args.patch:
            patch = args.patch.resolve(strict=True)
            cmd += ['-v', f'{patch}:/candidate.diff:ro']
            # Empty patches are valid predictions; tests still decide the outcome.
            apply = 'if [ -s /candidate.diff ]; then git apply /candidate.diff; fi\n'
        selected = ' '.join(quote(f'/app/{f}') for f in spec['selected_test_files_to_run'])
        shell = f'''set -e
cd /app
{spec['before_repo_set_cmd']}
{apply}
# Agent edits to these tests/configuration cannot change the grading criteria.
cp -R /heldout/. /app/
cp /eval-config.ts /app/.cs2680-vitest.config.ts
if git cat-file -e HEAD:src/test/setup.ts 2>/dev/null; then
    git checkout HEAD -- src/test/setup.ts
fi
timeout 180 node /app/node_modules/vitest/vitest.mjs run {selected} --config /app/.cs2680-vitest.config.ts --root /app --maxWorkers=1 --no-file-parallelism --reporter=default --reporter=json --outputFile=/eval-results/vitest.json
'''
        cmd += [task['docker_image'], '-c', shell]
        try:
            result = subprocess.run(cmd, timeout=210)
        except subprocess.TimeoutExpired:
            print('unresolved: evaluator timeout', flush=True)
            return 2
        report_file = Path(tmp) / 'vitest.json'
        if not report_file.exists():
            print(f'unresolved: no test report (container exit {result.returncode})', flush=True)
            return 2
        report = json.loads(report_file.read_text())
        resolved, statuses, missing = score(report, spec)
        resolved = resolved and result.returncode == 0
        data = {'instance_id': args.instance_id, 'patch': str(args.patch) if args.patch else None,
                'resolved': resolved, 'exit_code': result.returncode,
                'tests': statuses, 'missing_or_failed': missing}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(data, indent=2) + '\n')
        for name, status in statuses.items():
            print(f'{status.upper()} {name}')
        for name in missing:
            print(f'REQUIRED_NOT_PASSED {name}')
        print(('RESOLVED' if resolved else 'unresolved') + f' {args.instance_id}', flush=True)
        return 0 if resolved else 1


if __name__ == '__main__':
    raise SystemExit(main())
