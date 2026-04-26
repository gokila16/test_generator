"""
build_class_inventory.py

Walks one or more Java source trees, parses every .java file with javalang,
and produces class_inventory.json — a map of class information used by the
test-generation pipeline to figure out how to instantiate objects.

Pass 1: extract class structure from each file.
Pass 2: populate concrete_subclasses transitively.

FIX (v2):
  - SOURCE_DIRS is now a list of roots so sibling modules (e.g. pdfbox-io)
    are also scanned. Previously SOURCE_DIR only pointed at the main pdfbox
    module, which meant the entire org.apache.pdfbox.io package was missing
    from the inventory. That caused RandomAccessReadBuffer, RandomAccessRead,
    IOUtils, and related classes to be absent, leading to:
      * ALLOWLIST failures (checker had no record of their methods)
      * constructor_with_args fallback emitting /* RandomAccessReadBuffer */null
"""

import os
import json
import javalang

# ============================================================
# CONFIGURATION
# ============================================================
# FIX: Changed from a single SOURCE_DIR to a list SOURCE_DIRS.
# Add every source root that contains classes the test generator may reference.
# The pdfbox-io module (org.apache.pdfbox.io.*) was previously missing entirely.
SOURCE_DIRS = [
    r"C:\Users\Harini\Documents\thesis_research\PDFBOX-v5\pdfbox\src\main\java",
    # Add the io module if it is a sibling module — adjust path if needed
    r"C:\Users\Harini\Documents\thesis_research\PDFBOX-v5\pdfbox-io\src\main\java",
    # Add rendering module if present
    r"C:\Users\Harini\Documents\thesis_research\PDFBOX-v5\pdfbox-rendering\src\main\java",
]

OUTPUT_FILE = r"C:\Users\Harini\Documents\thesis_research\test_generator\class_inventory.json"


# ============================================================
# HELPERS
# ============================================================

def _visibility(modifiers):
    """Return the visibility keyword from a modifier set, defaulting to 'package'."""
    for v in ('public', 'protected', 'private'):
        if v in modifiers:
            return v
    return 'package'


def _param_types(parameters):
    """Extract simple type name strings from a list of javalang FormalParameter nodes."""
    types = []
    for p in parameters:
        t = p.type
        name = t.name if hasattr(t, 'name') else str(t)
        if hasattr(t, 'dimensions') and t.dimensions:
            name += '[]' * len(t.dimensions)
        types.append(name)
    return types


def _process_class(node, package_name, outer_name=None):
    """
    Extract inventory entry from a ClassDeclaration, InterfaceDeclaration,
    or EnumDeclaration node.
    """
    simple_name = node.name
    class_name  = f"{outer_name}.{simple_name}" if outer_name else simple_name
    full_name   = f"{package_name}.{class_name}" if package_name else class_name

    is_interface = isinstance(node, javalang.tree.InterfaceDeclaration)
    is_enum      = isinstance(node, javalang.tree.EnumDeclaration)
    is_abstract  = 'abstract' in (node.modifiers or set()) and not is_interface
    is_final     = 'final'    in (node.modifiers or set()) and not is_interface and not is_enum

    # --- Constructors ---
    constructors = []
    has_explicit_ctors = False

    if not is_interface and not is_enum:
        raw_ctors = getattr(node, 'constructors', []) or []
        for ctor in raw_ctors:
            has_explicit_ctors = True
            constructors.append({
                'params':     _param_types(ctor.parameters or []),
                'visibility': _visibility(ctor.modifiers or set()),
            })
        constructors.sort(key=lambda c: len(c['params']))

        if not has_explicit_ctors and not is_abstract:
            constructors.append({'params': [], 'visibility': 'public'})

    # --- Factory methods + public instance/static methods ---
    factory_methods = []
    public_methods  = []
    raw_methods = getattr(node, 'methods', []) or []

    is_iface = isinstance(node, javalang.tree.InterfaceDeclaration)

    for m in raw_methods:
        mods    = m.modifiers or set()
        vis     = _visibility(mods)
        is_pub  = vis in ('public', 'protected') or is_iface
        is_stat = 'static' in mods

        ret = m.return_type
        if ret is None:
            ret_name = 'void'
        elif hasattr(ret, 'name'):
            ret_name = ret.name
            if hasattr(ret, 'dimensions') and ret.dimensions:
                ret_name += '[]' * len(ret.dimensions)
        else:
            ret_name = str(ret)

        params_str = ', '.join(_param_types(m.parameters or []))

        if 'public' in mods and is_stat and ret_name == simple_name:
            factory_methods.append({
                'name':    m.name,
                'params':  _param_types(m.parameters or []),
                'returns': ret_name,
            })

        if is_pub:
            sig = f"{'static ' if is_stat else ''}{ret_name} {m.name}({params_str})"
            public_methods.append(sig)

    # --- Interfaces implemented ---
    implements_list = []
    if not is_interface and not is_enum:
        raw_impl = getattr(node, 'implements', None) or []
        for iface in raw_impl:
            implements_list.append(iface.name if hasattr(iface, 'name') else str(iface))

    # --- Parent class ---
    extends_class = None
    if not is_interface and not is_enum:
        ext = getattr(node, 'extends', None)
        if ext:
            if hasattr(ext, 'name'):
                extends_class = ext.name
            elif isinstance(ext, list) and ext:
                extends_class = ext[0].name

    return {
        'package_name':           package_name,
        'class_name':             class_name,
        'full_name':              full_name,
        'is_abstract':            is_abstract,
        'is_interface':           is_interface,
        'is_enum':                is_enum,
        'is_final':               is_final,
        'constructors':           constructors,
        'factory_methods':        factory_methods,
        'public_methods':         public_methods,
        'interfaces_implemented': implements_list,
        'extends_class':          extends_class,
        'concrete_subclasses':    [],
    }


def _collect_entries(node, package_name, outer_name=None):
    """
    Recursively collect entries for a class node and all its inner classes.
    """
    entries = []
    entry = _process_class(node, package_name, outer_name)
    entries.append(entry)

    body = getattr(node, 'body', None) or []
    for member in body:
        if isinstance(member, (
            javalang.tree.ClassDeclaration,
            javalang.tree.InterfaceDeclaration,
            javalang.tree.EnumDeclaration,
        )):
            entries.extend(_collect_entries(member, package_name, entry['class_name']))

    return entries


# ============================================================
# PASS 1 — Walk source trees and parse
# ============================================================

def pass1(source_dirs):
    """
    FIX: Accepts a list of source directories instead of a single path.
    Skips directories that don't exist with a warning rather than crashing.
    """
    inventory   = {}
    files_total = 0
    parse_fails = 0

    java_files = []
    for source_dir in source_dirs:
        if not os.path.isdir(source_dir):
            print(f"  WARNING: source dir not found, skipping: {source_dir}")
            continue
        print(f"  Scanning: {source_dir}")
        for root, _, files in os.walk(source_dir):
            for f in files:
                if f.endswith('.java'):
                    java_files.append(os.path.join(root, f))

    print(f"Found {len(java_files)} .java files across all source roots")

    for idx, filepath in enumerate(java_files, 1):
        if idx % 100 == 0:
            print(f"  Progress: {idx}/{len(java_files)} files parsed ...")

        files_total += 1
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            tree = javalang.parse.parse(source)
        except Exception as exc:
            print(f"  WARNING: failed to parse {os.path.basename(filepath)}: {exc}")
            parse_fails += 1
            continue

        pkg = tree.package.name if tree.package else ''

        for node in tree.types:
            if not isinstance(node, (
                javalang.tree.ClassDeclaration,
                javalang.tree.InterfaceDeclaration,
                javalang.tree.EnumDeclaration,
            )):
                continue
            for entry in _collect_entries(node, pkg):
                # FIX: If two modules define the same full_name (shouldn't happen
                # but guard against it), keep the one with more constructors as it
                # is likely the real implementation rather than a stub.
                existing = inventory.get(entry['full_name'])
                if existing:
                    if len(entry.get('constructors', [])) > len(existing.get('constructors', [])):
                        inventory[entry['full_name']] = entry
                else:
                    inventory[entry['full_name']] = entry

    return inventory, files_total, parse_fails


# ============================================================
# PASS 2 — Populate concrete_subclasses transitively
# ============================================================

def pass2(inventory):
    """
    For every non-abstract, non-interface class that declares extends_class,
    walk up the inheritance chain and add it to every ancestor's
    concrete_subclasses list.
    """
    by_simple = {}
    for full_name, entry in inventory.items():
        by_simple.setdefault(entry['class_name'], []).append(full_name)

    def resolve(simple_name, child_package):
        candidates = by_simple.get(simple_name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        for c in candidates:
            if inventory[c]['package_name'] == child_package:
                return c
        return candidates[0]

    for full_name, entry in inventory.items():
        if entry['is_abstract'] or entry['is_interface']:
            continue
        current = entry
        visited = set()
        while current.get('extends_class'):
            parent_simple = current['extends_class']
            if parent_simple in visited:
                break
            visited.add(parent_simple)
            parent_full = resolve(parent_simple, current['package_name'])
            if parent_full is None or parent_full not in inventory:
                break
            parent_entry = inventory[parent_full]
            if entry['class_name'] not in parent_entry['concrete_subclasses']:
                parent_entry['concrete_subclasses'].append(entry['class_name'])
            current = parent_entry

        for iface_name in entry.get('interfaces_implemented', []):
            if iface_name in inventory:
                iface_full = iface_name
            else:
                iface_full = resolve(iface_name, entry['package_name'])
            if iface_full is None or iface_full not in inventory:
                continue
            iface_entry = inventory[iface_full]
            if entry['class_name'] not in iface_entry['concrete_subclasses']:
                iface_entry['concrete_subclasses'].append(entry['class_name'])


# ============================================================
# PASS 3 — Verify key classes are present and warn if missing
# ============================================================

# FIX: Added a verification pass that warns about classes the pipeline depends on.
# This makes it obvious when a source root is missing rather than silently
# producing an inventory with gaps that cause downstream failures.
EXPECTED_CLASSES = [
    "org.apache.pdfbox.io.RandomAccessReadBuffer",
    "org.apache.pdfbox.io.RandomAccessReadBufferedFile",
    "org.apache.pdfbox.io.RandomAccessRead",
    "org.apache.pdfbox.io.IOUtils",
    "org.apache.pdfbox.text.PDFTextStripper",
    "org.apache.pdfbox.pdmodel.PDDocument",
    "org.apache.pdfbox.pdmodel.PDPage",
    "org.apache.pdfbox.rendering.PageDrawer",
]

def pass3_verify(inventory):
    print("\n" + "=" * 50)
    print("PASS 3: Verifying expected classes are present")
    print("=" * 50)
    missing = []
    for cls in EXPECTED_CLASSES:
        if cls in inventory:
            entry = inventory[cls]
            print(f"  OK  {cls.split('.')[-1]:40} ctors={len(entry.get('constructors',[]))}")
        else:
            print(f"  MISSING  {cls}")
            missing.append(cls)
    if missing:
        print(f"\n  WARNING: {len(missing)} expected classes are missing from the inventory.")
        print("  Check that all source roots in SOURCE_DIRS are correct.")
    else:
        print("\n  All expected classes found.")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 50)
    print("PASS 1: Parsing Java source files")
    print("=" * 50)
    inventory, files_total, parse_fails = pass1(SOURCE_DIRS)

    print("\n" + "=" * 50)
    print("PASS 2: Populating concrete_subclasses")
    print("=" * 50)
    pass2(inventory)

    pass3_verify(inventory)

    # Summary stats
    classes_total      = len(inventory)
    abstract_count     = sum(1 for e in inventory.values() if e['is_abstract'])
    interface_count    = sum(1 for e in inventory.values() if e['is_interface'])
    enum_count         = sum(1 for e in inventory.values() if e.get('is_enum'))
    final_count        = sum(1 for e in inventory.values() if e.get('is_final'))
    private_only_count = sum(
        1 for e in inventory.values()
        if e['constructors']
        and all(c['visibility'] == 'private' for c in e['constructors'])
    )
    total_methods = sum(len(e['public_methods']) for e in inventory.values())

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"  Files scanned:                   {files_total}")
    print(f"  Parse failures:                  {parse_fails}")
    print(f"  Total classes/interfaces:        {classes_total}")
    print(f"  Abstract classes:                {abstract_count}")
    print(f"  Interfaces:                      {interface_count}")
    print(f"  Enums:                           {enum_count}")
    print(f"  Final classes:                   {final_count}")
    print(f"  Classes with only private ctors: {private_only_count}")
    print(f"  Total public methods extracted:  {total_methods}")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as fh:
        json.dump(inventory, fh, indent=2)
    print(f"\nOutput written to: {OUTPUT_FILE}")


if __name__ == '__main__':
    main()