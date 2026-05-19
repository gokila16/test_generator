import json
import os


def load_context_data(dep_chains_path, call_graph_path):
    """
    Loads dependency_chains.json and call_graph.json once at startup.
    Returns (dep_chains_dict, call_graph_dict).
    If a file is missing or unreadable, prints a warning and returns {} for that file.
    """
    def _load(path, label):
        if not os.path.exists(path):
            print(f"  WARNING: {label} not found at {path} — skipping.")
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:
            print(f"  WARNING: failed to load {label}: {exc}")
            return {}

    dep_chains = _load(dep_chains_path, 'dependency_chains.json')
    call_graph  = _load(call_graph_path,  'call_graph.json')
    print(f"  Context loaded: {len(dep_chains)} dep chains, {len(call_graph)} call graph entries.")
    return dep_chains, call_graph


def get_dependency_chain(dep_chains, method):
    """
    Looks up the method in dep_chains using key "full_name|signature".
    Returns the chain dict or None if not found.
    """
    key = f"{method.get('full_name', '')}|{method.get('signature', '')}"
    return dep_chains.get(key)


def get_caller_snippets(call_graph, method, max_snippets=2):
    """
    Looks up the method in call_graph using key "full_name|signature".
    Returns a list of up to max_snippets caller dicts.
    Returns empty list if not found or no callers.
    """
    key = f"{method.get('full_name', '')}|{method.get('signature', '')}"
    entry = call_graph.get(key)
    if not entry:
        return []
    callers = entry.get('callers') or []
    return callers[:max_snippets]
