"""
Checklist agent — complex path, step 1.

generate_checklist() calls the LLM once to produce a structured checklist,
then hands it to validate_checklist() which fixes and re-validates up to
MAX_RETRIES times (retry loop is internal — the pipeline calls each once).
"""
import re
import config
from src.llm_client import call_llm
from src.prompt_builder import build_checklist_prompt, build_checklist_fix_prompt
from src.complexity_classifier import compute_clc


# ------------------------------------------------------------------ #
# Parsing                                                              #
# ------------------------------------------------------------------ #

def _parse_checklist(text: str) -> dict | None:
    """
    Parses the structured checklist text produced by the LLM.
    Returns a dict with keys 'branch_plan', 'resource_spec', 'input_types',
    or None if the text cannot be parsed into at least one branch_plan entry.
    """
    if not text:
        return None

    checklist: dict = {'branch_plan': [], 'resource_spec': [], 'input_types': []}

    # ---- BRANCH PLAN ----
    bp_match = re.search(
        r'BRANCH PLAN:\s*\n(.*?)(?=\nRESOURCE SPEC:|\nINPUT TYPES:|$)',
        text, re.DOTALL | re.IGNORECASE
    )
    if bp_match:
        bp_text = bp_match.group(1)
        # Split on numbered entries: "1." "2." etc at line start
        raw_entries = re.split(r'\n(?=\d+\.)', bp_text.strip())
        for raw in raw_entries:
            raw = raw.strip()
            if not raw:
                continue
            entry: dict = {}
            # First line is "<number>. <camelCaseName>"
            first_line = raw.split('\n')[0]
            name_m = re.match(r'^\d+\.\s*(\w+)', first_line)
            if name_m:
                entry['name'] = name_m.group(1)
            scenario_m = re.search(r'Scenario:\s*(.+)', raw)
            if scenario_m:
                entry['scenario'] = scenario_m.group(1).strip()
            branch_m = re.search(r'Branch:\s*(.+)', raw)
            if branch_m:
                entry['branch'] = branch_m.group(1).strip()
            expected_m = re.search(r'Expected[^:]*:\s*(.+)', raw)
            if expected_m:
                entry['expected'] = expected_m.group(1).strip()
            if entry:
                checklist['branch_plan'].append(entry)

    # ---- RESOURCE SPEC ----
    rs_match = re.search(
        r'RESOURCE SPEC:\s*\n(.*?)(?=\nINPUT TYPES:|$)',
        text, re.DOTALL | re.IGNORECASE
    )
    if rs_match:
        for line in rs_match.group(1).split('\n'):
            line = line.strip().lstrip('-').strip()
            if not line:
                continue
            # "JavaType varName: description"
            m = re.match(r'([A-Z][A-Za-z0-9_<>]*)\s+([a-zA-Z_]\w*)\s*:\s*(.*)', line)
            if m:
                checklist['resource_spec'].append({
                    'type': m.group(1).split('<')[0],
                    'name': m.group(2),
                    'description': m.group(3).strip(),
                })

    # ---- INPUT TYPES ----
    it_match = re.search(r'INPUT TYPES:\s*\n(.*?)$', text, re.DOTALL | re.IGNORECASE)
    if it_match:
        for line in it_match.group(1).split('\n'):
            line = line.strip().lstrip('-').strip()
            if not line:
                continue
            # "paramName: JavaType — description"
            m = re.match(r'(\w+)\s*:\s*([A-Za-z_]\w*)\s*(?:—|-)\s*(.*)', line)
            if m:
                checklist['input_types'].append({
                    'name': m.group(1),
                    'type': m.group(2),
                    'description': m.group(3).strip(),
                })

    return checklist if checklist['branch_plan'] else None


# ------------------------------------------------------------------ #
# Validation                                                           #
# ------------------------------------------------------------------ #

def _check_checklist(checklist: dict, method: dict, clc: int) -> tuple[bool, list]:
    """
    Returns (passed, issues) without any LLM calls.

    Checks:
    - Every branch_plan entry has name, scenario, branch, expected.
    - Number of scenarios is at least 1.
    - Number of scenarios is at least min(clc, 4) — soft ceiling at 4
      to avoid runaway checklists on very complex methods.
    - RESOURCE SPEC types are plausible (start uppercase, not empty).
    """
    issues = []
    bp = checklist.get('branch_plan') or []

    if not bp:
        issues.append("BRANCH PLAN is empty — at least one scenario is required.")
        return False, issues

    required_fields = {'name', 'scenario', 'branch', 'expected'}
    for i, entry in enumerate(bp, 1):
        missing = [f for f in required_fields if not entry.get(f)]
        if missing:
            issues.append(
                f"Branch plan entry {i} is missing fields: {', '.join(missing)}."
            )

    min_scenarios = max(1, min(clc, 4))
    if len(bp) < min_scenarios:
        issues.append(
            f"Only {len(bp)} scenario(s) but method CLC is {clc} — "
            f"need at least {min_scenarios}."
        )

    for res in checklist.get('resource_spec') or []:
        t = res.get('type', '')
        if not t or not t[0].isupper():
            issues.append(
                f"RESOURCE SPEC entry '{res.get('name','')}' has invalid type '{t}'."
            )

    return len(issues) == 0, issues


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

def generate_checklist(
    method: dict,
    dep_chain: dict,
    resource_files: list,
    caller_snippets: list,
) -> dict | None:
    """
    Calls the LLM once to produce a structured checklist for the method.
    Returns the parsed checklist dict, or None on API / parse failure.
    """
    prompt   = build_checklist_prompt(method, dep_chain, resource_files, caller_snippets)
    response = call_llm(prompt)
    if not response:
        print("  [checklist_agent] No LLM response on checklist generation.")
        return None

    checklist = _parse_checklist(response)
    if checklist is None:
        print("  [checklist_agent] Could not parse checklist from LLM response.")
    return checklist


def validate_checklist(
    checklist: dict,
    method: dict,
    clc: int,
) -> tuple[bool, list, dict]:
    """
    Validates the checklist and, on failure, calls the LLM to fix it up to
    config.MAX_RETRIES times.  The retry loop lives here — the pipeline
    calls this once and reads the bool.

    Returns:
        (success: bool, issues: list[str], checklist: dict)
        - success=True  → checklist is valid; use the returned dict.
        - success=False → all retries exhausted; issues explains why.
    """
    for attempt in range(config.MAX_RETRIES + 1):
        passed, issues = _check_checklist(checklist, method, clc)
        if passed:
            return True, [], checklist

        print(f"  [checklist_agent] Validation failed (attempt {attempt + 1}): "
              f"{issues}")

        if attempt >= config.MAX_RETRIES:
            break

        # Ask the LLM to fix the checklist
        fix_prompt = build_checklist_fix_prompt(checklist, issues, method)
        fix_response = call_llm(fix_prompt)
        if not fix_response:
            print("  [checklist_agent] No LLM response on checklist fix.")
            break

        fixed = _parse_checklist(fix_response)
        if fixed is not None:
            checklist = fixed
        else:
            print("  [checklist_agent] Could not parse fixed checklist.")
            break

    return False, issues, checklist
