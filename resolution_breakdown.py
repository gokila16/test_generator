#!/usr/bin/env python
"""
resolution_breakdown.py - Split a V7 pass-rate run across the four
resolution-quality buckets the construction-chain extractor assigns
(full / partial / unresolvable / unknown), to test whether pass rate
monotonically decays with chain quality.

If we see pass_rate(full) > pass_rate(partial) > pass_rate(unresolvable)
> pass_rate(unknown), that is strong evidence the chain component is
contributing real signal to V7's gains.

Join model
----------
- dependency_chains.json keys entries by 'full_name|signature' and tags
  each with resolution_quality. For an overloaded method, each signature
  has its own resolution; ordering of entries with the same full_name in
  the JSON file matches V7's assign_unique_keys() iteration order, so the
  N-th entry for full_name X corresponds to results.json key X_overload_N.
- results.json keys entries by '<full_name>' (no overload suffix) for
  singletons or '<full_name>_overload_N' for overloaded methods.

Usage
-----
  python resolution_breakdown.py
      Uses defaults pinned for V7 gpt5mini.

  python resolution_breakdown.py \
      --chains <path-to-dependency_chains.json> \
      --results <path-to-results.json> \
      --label "Gemini V7"

Output
------
- Prints a per-bucket table: count, PASSED count, pass rate, plus the
  status mix within the bucket.
- Prints whether monotonic decay holds (strict, weak, or violated).
- No files written.
"""

import argparse
import collections
import json
import os
import sys


DEFAULT_CHAINS = r'c:\Users\Harini\Documents\thesis_research\test_generator_v7\test_generator\dependency_chains.json'
DEFAULT_RESULTS = r'c:\Users\Harini\Documents\thesis_research\PDFBOX-v5\generated_files\gpt5mini-v7\results\results.json'

BUCKET_ORDER = ['full', 'partial', 'unresolvable', 'unknown']


def build_overload_index(chains):
    """
    Given the dependency_chains.json dict, return a map from
    {results.json key} -> resolution_quality. Singletons map by full_name;
    overloads map by '<full_name>_overload_N' where N is the position of
    that signature within its full_name group, in insertion order.
    """
    by_full = collections.defaultdict(list)
    for key, entry in chains.items():
        fn = entry.get('full_name')
        if not fn:
            continue
        by_full[fn].append(entry.get('resolution_quality', 'unknown'))

    out = {}
    for fn, qualities in by_full.items():
        if len(qualities) == 1:
            out[fn] = qualities[0]
        else:
            for i, q in enumerate(qualities):
                out[f'{fn}_overload_{i}'] = q
    return out


def analyze(chains_path, results_path, label):
    with open(chains_path, 'r', encoding='utf-8') as f:
        chains = json.load(f)
    with open(results_path, 'r', encoding='utf-8') as f:
        results = json.load(f)

    res_map = build_overload_index(chains)

    # bucket -> Counter of statuses
    bucket_status = {b: collections.Counter() for b in BUCKET_ORDER}
    bucket_status['MISSING_RESOLUTION'] = collections.Counter()

    unmapped_results = []
    for rkey, entry in results.items():
        status = entry.get('status', 'UNKNOWN')
        q = res_map.get(rkey)
        if q is None:
            unmapped_results.append(rkey)
            bucket_status['MISSING_RESOLUTION'][status] += 1
        else:
            bucket_status.setdefault(q, collections.Counter())[status] += 1

    return bucket_status, unmapped_results, len(chains), len(results)


def pass_rate(counter):
    total = sum(counter.values())
    if total == 0:
        return 0.0
    return 100.0 * counter.get('PASSED', 0) / total


def monotonic_decay_verdict(rates_in_order):
    """
    rates_in_order: list of (label, rate) in declining-quality order.
    Returns one of 'strict', 'weak', 'violated' plus a per-step diff list.
    'weak' means non-increasing but at least one step is flat. 'violated'
    means at least one step goes up.
    """
    steps = []
    strict = True
    any_violation = False
    for (a, ra), (b, rb) in zip(rates_in_order, rates_in_order[1:]):
        d = rb - ra
        steps.append((a, b, d))
        if d > 0:
            any_violation = True
        if d >= 0:
            strict = False
    if any_violation:
        verdict = 'violated'
    elif strict:
        verdict = 'strict'
    else:
        verdict = 'weak'
    return verdict, steps


def print_report(label, bucket_status, unmapped, chain_count, result_count):
    print()
    print('=' * 76)
    print(f'  {label}')
    print('=' * 76)
    print(f'  dependency_chains.json entries : {chain_count}')
    print(f'  results.json entries           : {result_count}')
    print(f'  unmapped results               : {len(unmapped)}')
    print('-' * 76)
    print(f'  {"Bucket":<22} {"N":>5} {"PASSED":>7} {"Pass%":>7}   status mix')
    print('-' * 76)

    rates_in_order = []
    for b in BUCKET_ORDER:
        c = bucket_status.get(b, collections.Counter())
        n = sum(c.values())
        p = c.get('PASSED', 0)
        rate = pass_rate(c)
        rates_in_order.append((b, rate))
        mix = ', '.join(f'{k}={v}' for k, v in c.most_common())
        print(f'  {b:<22} {n:>5} {p:>7} {rate:>6.1f}%   {mix}')

    miss = bucket_status.get('MISSING_RESOLUTION', collections.Counter())
    if sum(miss.values()):
        n = sum(miss.values())
        p = miss.get('PASSED', 0)
        rate = pass_rate(miss)
        mix = ', '.join(f'{k}={v}' for k, v in miss.most_common())
        print(f'  {"(missing resolution)":<22} {n:>5} {p:>7} {rate:>6.1f}%   {mix}')

    print('-' * 76)
    verdict, steps = monotonic_decay_verdict(rates_in_order)
    print(f'  Monotonic decay (full -> partial -> unresolvable -> unknown): {verdict}')
    for a, b, d in steps:
        arrow = '<=' if d <= 0 else '>'
        sign = '' if d >= 0 else ''
        print(f'    {a:<14} -> {b:<14} {d:+6.1f} pp   ({arrow} 0 means decay holds)')
    print('=' * 76)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    parser.add_argument('--chains', default=DEFAULT_CHAINS,
                        help='Path to dependency_chains.json')
    parser.add_argument('--results', default=DEFAULT_RESULTS,
                        help='Path to results.json (any pipeline)')
    parser.add_argument('--label', default=None,
                        help='Label to print in the header (defaults to '
                             'parent dir of results.json)')
    args = parser.parse_args()

    label = args.label or os.path.basename(
        os.path.dirname(os.path.dirname(args.results))) or args.results

    bucket_status, unmapped, chain_count, result_count = analyze(
        args.chains, args.results, label,
    )

    print_report(label, bucket_status, unmapped, chain_count, result_count)

    if unmapped:
        print()
        print('First 10 unmapped result keys (no matching entry in chains):')
        for k in unmapped[:10]:
            print(f'  {k}')


if __name__ == '__main__':
    main()
