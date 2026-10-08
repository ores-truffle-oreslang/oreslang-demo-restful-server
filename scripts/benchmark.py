#!/usr/bin/env python3
"""Sequential instrumented demo timings; mutates/deletes the demo resource."""
import argparse
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runs', type=int, default=6, help='first run is warm-up; minimum 2')
args = parser.parse_args()
if args.runs < 2:
    parser.error('--runs must be at least 2')
root = Path(__file__).resolve().parents[1]
base = os.environ.get('BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
samples = {'shared': [], 'isolated': []}
rounds = []
retries = 0
for run in range(args.runs):
    started = time.perf_counter()
    result = subprocess.run(['bash', str(root / 'scripts/curl-10.sh')],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    elapsed = (time.perf_counter() - started) * 1000
    print(result.stdout, end='', flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    rows = re.findall(r'actor=(shared|isolated).*?time=([0-9.]+)s retries=(\d+)', result.stdout)
    if len(rows) != 10:
        raise SystemExit('Expected 10 timed requests')
    retries += sum(int(row[2]) for row in rows)
    print(f'Round {run + 1}: {elapsed:.1f} ms' + (' (warm-up)' if run == 0 else ''), flush=True)
    if run:
        rounds.append(elapsed)
        for mode, seconds, _ in rows:
            samples[mode].append(float(seconds) * 1000)
# curl phases for a registered shared actor, rather than the root 404 route.
phases = []
for _ in range(20):
    result = subprocess.run(['curl', '--max-time', '10', '-sS', '-o', os.devnull,
                             '-w', '%{http_code} %{time_connect} %{time_starttransfer} %{time_total}',
                             base + '/health'], check=True, text=True, stdout=subprocess.PIPE)
    code, *values = result.stdout.split()
    if code != '200':
        raise SystemExit(f'Health timing request failed: HTTP {code}')
    phases.append([float(value) * 1000 for value in values])
print(json.dumps({
    'warm_median_request_ms': statistics.median(samples['shared'] + samples['isolated']),
    'warm_median_by_actor_ms': {mode: statistics.median(values) for mode, values in samples.items()},
    'warm_median_script_ms': statistics.median(rounds),
    'admission_retries': retries,
    'health_20_request_median_ms': dict(zip(['connect', 'first_byte', 'total'],
        [statistics.median(row[i] for row in phases) for i in range(3)])),
    'scope': 'instrumented sequential demo; first REST round excluded; curl final-attempt timings'
}, indent=2))
