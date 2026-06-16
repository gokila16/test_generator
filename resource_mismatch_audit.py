#!/usr/bin/env python
"""
resource_mismatch_audit.py - Find every params entry in dependency_chains.json
with strategy=resource whose method-name format signal disagrees with the
extension of the assigned resource file.

Example (the prof's seed case):
    org.apache.pdfbox.Loader.loadFDF(File file)
      -> resource_file = 'input/cweb.pdf'    <-- WRONG: should be a .fdf file

Strategy
--------
1. Read every entry in dependency_chains.json.
2. For each param with strategy=resource, extract:
     - the method's name (and class name, as a secondary signal)
     - the resource file's extension
3. Match the method/class name against FORMAT_SIGNALS, a dict of substrings
   that signal an expected file format. Longer signals are tried first so
   'TIFF' wins over 'TIF'.
4. If a signal fires AND the resource extension doesn't match the expected
   extension, flag the param. Propose a replacement file by picking from
   test_resource_metadata.json with the right extension (preferring smallest
   file as a deterministic tiebreak).
5. Print a per-method report. Write a machine-readable JSON sidecar for
   downstream tools (the fix script).

This is analysis-only. No files are modified.

Usage
-----
  python resource_mismatch_audit.py
      Uses defaults (V7's chains + resource metadata).

  python resource_mismatch_audit.py \
      --chains <path> --resources <path> --output <path>
"""

import argparse
import collections
import json
import os
import re


DEFAULT_CHAINS = r'c:\Users\Harini\Documents\thesis_research\test_generator_v7\test_generator\dependency_chains.json'
DEFAULT_RESOURCES = r'c:\Users\Harini\Documents\thesis_research\test_generator_v7\test_generator\test_resource_metadata.json'
DEFAULT_OUTPUT = r'c:\Users\Harini\Documents\thesis_research\testgenerator_v1\test_generator\resource_mismatch_audit.json'


# Order matters: longer signals first so 'TIFF' is detected before 'TIF',
# 'JPEG' before 'JPG'. Each entry maps a substring (case-sensitive, matched
# only when bordered by camelCase/underscore boundaries) to the expected file
# extension. The substring should be uppercase as it usually appears in
# Java method/class names (e.g. 'loadFDF', 'createFromTIFF').
FORMAT_SIGNALS = [
    ('CCITT', '.tif'),       # CCITT files in PDFBox are .tif
    ('TIFF',  '.tif'),
    ('TIF',   '.tif'),
    ('JPEG',  '.jpg'),
    ('JPG',   '.jpg'),
    ('PNG',   '.png'),
    ('FDF',   '.fdf'),
    ('XFDF',  '.xml'),       # XFDF is XML
    ('GIF',   '.gif'),
    ('TTF',   '.ttf'),
    ('OTF',   '.otf'),
    ('BMP',   '.bmp'),
    ('ICC',   '.icc'),
    ('PFX',   '.pfx'),
    ('PROPERTIES', '.properties'),
    # PDF is intentionally omitted: it's the default assumption and would
    # produce noise on PDF-handling methods.
]


def detect_signal(name):
    """
    Return the first matching (signal, expected_ext) tuple for `name`,
    or None. Match requires that the signal substring appear bordered by
    a non-alphabetic char or the string end (so 'getPdfAuthor' wouldn't
    match 'PDF', and 'fromFDF' would match 'FDF').
    """
    for sig, ext in FORMAT_SIGNALS:
        # Word boundary: the char before must not be uppercase letter,
        # and the char after must not be lowercase letter (we want
        # camelCase boundaries: loadFDF, FDFParser, FDF_load, etc.).
        pattern = rf'(?:^|[^A-Z]){re.escape(sig)}(?:$|[^a-z])'
        if re.search(pattern, name):
            return sig, ext
    return None


def build_resource_index(resources):
    """ext -> sorted-by-size list of resource keys."""
    by_ext = collections.defaultdict(list)
    for key, meta in resources.items():
        ext = meta.get('extension', '').lower()
        size = meta.get('file_size_bytes', 0) or 0
        by_ext[ext].append((size, key))
    for ext in by_ext:
        by_ext[ext].sort()
    return by_ext


def propose_replacement(expected_ext, by_ext):
    """Pick the smallest available resource with the expected extension."""
    candidates = by_ext.get(expected_ext.lower(), [])
    if not candidates:
        return None
    return candidates[0][1]


def audit(chains, resources):
    by_ext = build_resource_index(resources)
    findings = []
    signal_counts = collections.Counter()
    full_chain_keys_with_mismatch = set()

    for chain_key, entry in chains.items():
        full_name = entry.get('full_name', chain_key)
        class_name = entry.get('class_name', '')
        method_name = entry.get('method_name', full_name.split('.')[-1])
        signature = entry.get('signature', '')
        resolution = entry.get('resolution_quality', 'unknown')
        params = entry.get('params') or []

        # Signals from method name take priority; class name is a fallback.
        method_signal = detect_signal(method_name)
        class_signal = detect_signal(class_name) if class_name else None
        signal = method_signal or class_signal
        if signal is None:
            continue

        sig_token, expected_ext = signal

        param_findings = []
        for pi, p in enumerate(params):
            if p.get('strategy') != 'resource':
                continue
            rfile = p.get('resource_file') or ''
            _, actual_ext = os.path.splitext(rfile)
            actual_ext = actual_ext.lower()
            if actual_ext == expected_ext.lower():
                continue
            # Mismatch
            replacement = propose_replacement(expected_ext, by_ext)
            param_findings.append({
                'param_index': pi,
                'param_name': p.get('name'),
                'param_type': p.get('type'),
                'actual_resource': rfile,
                'actual_extension': actual_ext,
                'expected_extension': expected_ext,
                'proposed_replacement': replacement,
                'replacement_available': replacement is not None,
            })

        if param_findings:
            findings.append({
                'chain_key': chain_key,
                'full_name': full_name,
                'class_name': class_name,
                'method_name': method_name,
                'signature': signature,
                'resolution_quality': resolution,
                'signal_token': sig_token,
                'signal_source': 'method_name' if method_signal else 'class_name',
                'param_findings': param_findings,
            })
            signal_counts[sig_token] += len(param_findings)
            full_chain_keys_with_mismatch.add(chain_key)

    return findings, signal_counts, full_chain_keys_with_mismatch


def print_report(findings, signal_counts, total_chain_entries):
    n_chains = len({f['chain_key'] for f in findings})
    n_params = sum(len(f['param_findings']) for f in findings)
    print()
    print('=' * 78)
    print('  Resource-extension mismatch audit')
    print('=' * 78)
    print(f'  Chain entries scanned        : {total_chain_entries}')
    print(f'  Chain entries with mismatch  : {n_chains}')
    print(f'  Individual param mismatches  : {n_params}')
    print('-' * 78)
    print('  Mismatches by format signal:')
    for sig, n in signal_counts.most_common():
        print(f'    {sig:<14} {n}')
    print('-' * 78)
    full_name_groups = collections.defaultdict(list)
    for f in findings:
        full_name_groups[f['full_name']].append(f)
    print(f'  Unique full_names affected   : {len(full_name_groups)}')
    print('=' * 78)
    print()
    print('Per-method detail (grouped by full_name):')
    for full_name in sorted(full_name_groups):
        entries = full_name_groups[full_name]
        print()
        print(f'  {full_name}   [{len(entries)} overload(s) affected]')
        for e in entries:
            sig = e['signature'].replace('\n', ' ')
            if len(sig) > 90:
                sig = sig[:87] + '...'
            print(f'    signature  : {sig}')
            print(f'    resolution : {e["resolution_quality"]}')
            print(f'    signal     : {e["signal_token"]} (from {e["signal_source"]})')
            for pf in e['param_findings']:
                avail = 'available' if pf['replacement_available'] else 'NO REPLACEMENT IN POOL'
                print(f'    param[{pf["param_index"]}] {pf["param_name"]}: '
                      f'{pf["param_type"]}')
                print(f'      actual    : {pf["actual_resource"]}  ({pf["actual_extension"]})')
                print(f'      expected  : {pf["expected_extension"]} files  [{avail}]')
                if pf['proposed_replacement']:
                    print(f'      proposed  : {pf["proposed_replacement"]}')


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    p.add_argument('--chains', default=DEFAULT_CHAINS)
    p.add_argument('--resources', default=DEFAULT_RESOURCES)
    p.add_argument('--output', default=DEFAULT_OUTPUT,
                   help='Write machine-readable findings here.')
    args = p.parse_args()

    with open(args.chains, 'r', encoding='utf-8') as f:
        chains = json.load(f)
    with open(args.resources, 'r', encoding='utf-8') as f:
        resources = json.load(f)

    findings, signal_counts, mismatch_keys = audit(chains, resources)
    print_report(findings, signal_counts, total_chain_entries=len(chains))

    payload = {
        'chains_path': args.chains,
        'resources_path': args.resources,
        'total_chain_entries': len(chains),
        'unique_chain_keys_with_mismatch': len(mismatch_keys),
        'total_param_mismatches': sum(len(f['param_findings']) for f in findings),
        'signal_counts': dict(signal_counts),
        'findings': findings,
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    print()
    print(f'Wrote machine-readable findings -> {args.output}')


if __name__ == '__main__':
    main()
