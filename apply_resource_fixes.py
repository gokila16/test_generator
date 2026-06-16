#!/usr/bin/env python
"""
apply_resource_fixes.py - Apply the resource-extension fixes from
resource_mismatch_audit.json to V7's dependency_chains.json.

For each finding, the script:
  - Replaces the param's `resource_file` with the proposed replacement.
  - Updates the param's `construction` string (which embeds the old path).
  - Adds `resource_file_original` next to the patched value for audit trail.

Backs up each chain file as <path>.bak before writing.

By default it patches all three V7 model-variant chain copies (which are
byte-identical) so they stay in sync.

Usage
-----
  python apply_resource_fixes.py
  python apply_resource_fixes.py --dry-run    # show plan, don't write
"""

import argparse
import json
import os
import shutil


DEFAULT_AUDIT = r'c:\Users\Harini\Documents\thesis_research\testgenerator_v1\test_generator\resource_mismatch_audit.json'

DEFAULT_CHAIN_PATHS = [
    r'c:\Users\Harini\Documents\thesis_research\test_generator_v7\test_generator\dependency_chains.json',
    r'c:\Users\Harini\Documents\thesis_research\test_generator_v7_deepseek\test_generator\dependency_chains.json',
    r'c:\Users\Harini\Documents\thesis_research\test_generator_v7_opus\test_generator\dependency_chains.json',
]


def patch_chain(chains, findings):
    """
    Mutates the chains dict in place. Returns a list of patch records:
      { chain_key, param_index, old_file, new_file, construction_changed }
    """
    patches = []
    for f in findings:
        chain_key = f['chain_key']
        if chain_key not in chains:
            raise KeyError(f'Chain key not found: {chain_key}')
        entry = chains[chain_key]
        for pf in f['param_findings']:
            pi = pf['param_index']
            old = pf['actual_resource']
            new = pf['proposed_replacement']
            if new is None:
                # No replacement available - skip and record.
                patches.append({
                    'chain_key': chain_key,
                    'param_index': pi,
                    'status': 'skipped_no_replacement',
                    'old_file': old,
                })
                continue
            params = entry.get('params') or []
            if pi >= len(params):
                raise IndexError(f'{chain_key} param[{pi}] out of range')
            param = params[pi]
            if param.get('resource_file') != old:
                raise ValueError(
                    f'{chain_key} param[{pi}] resource_file is '
                    f'{param.get("resource_file")!r}, expected {old!r}. '
                    'Aborting to avoid clobbering unexpected data.'
                )

            param['resource_file_original'] = old
            param['resource_file'] = new

            construction = param.get('construction', '')
            new_construction = construction.replace(old, new) if construction else construction
            construction_changed = new_construction != construction
            if construction_changed:
                param['construction_original'] = construction
                param['construction'] = new_construction

            patches.append({
                'chain_key': chain_key,
                'param_index': pi,
                'status': 'patched',
                'old_file': old,
                'new_file': new,
                'construction_changed': construction_changed,
            })
    return patches


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    p.add_argument('--audit', default=DEFAULT_AUDIT)
    p.add_argument('--chains', nargs='+', default=DEFAULT_CHAIN_PATHS,
                   help='One or more dependency_chains.json paths to patch.')
    p.add_argument('--dry-run', action='store_true',
                   help='Print the patch plan but do not write files.')
    args = p.parse_args()

    with open(args.audit, 'r', encoding='utf-8') as f:
        audit = json.load(f)
    findings = audit['findings']

    print(f'Loaded {len(findings)} findings from {args.audit}')
    print(f'{sum(len(x["param_findings"]) for x in findings)} param-level patches to apply.')
    print()

    for chain_path in args.chains:
        if not os.path.isfile(chain_path):
            print(f'  SKIP (missing): {chain_path}')
            continue

        with open(chain_path, 'r', encoding='utf-8') as f:
            chains = json.load(f)

        patches = patch_chain(chains, findings)
        n_patched = sum(1 for p in patches if p['status'] == 'patched')
        n_skipped = sum(1 for p in patches if p['status'] == 'skipped_no_replacement')

        if args.dry_run:
            print(f'  DRY-RUN: would patch {n_patched} params in {chain_path}')
            for p_ in patches:
                if p_['status'] == 'patched':
                    print(f'    {p_["chain_key"]} param[{p_["param_index"]}]: '
                          f'{p_["old_file"]} -> {p_["new_file"]}')
            continue

        bak_path = chain_path + '.bak'
        if not os.path.isfile(bak_path):
            shutil.copy2(chain_path, bak_path)
            backed = 'created'
        else:
            backed = 'already existed (not overwritten)'

        with open(chain_path, 'w', encoding='utf-8') as f:
            json.dump(chains, f, indent=2)

        print(f'  PATCHED: {chain_path}')
        print(f'    backup .bak {backed}')
        print(f'    params patched : {n_patched}')
        if n_skipped:
            print(f'    params skipped : {n_skipped} (no replacement available)')
        for p_ in patches:
            if p_['status'] == 'patched':
                print(f'      {p_["chain_key"]} param[{p_["param_index"]}]: '
                      f'{p_["old_file"]} -> {p_["new_file"]}'
                      + ('  (+construction)' if p_['construction_changed'] else ''))
        print()


if __name__ == '__main__':
    main()
