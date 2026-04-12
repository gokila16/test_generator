"""
Resource generator agent — complex path, step 2.

generate_resources() calls the LLM once to produce Java construction
statements for each fixture in the checklist's resource spec.
validate_resources() type-checks those constructions and retries up to
MAX_RETRIES times if they are invalid.
"""
import re
import config
from src.llm_client import call_llm
from src.prompt_builder import build_resource_generation_prompt, build_resource_fix_prompt


# ------------------------------------------------------------------ #
# Parsing                                                              #
# ------------------------------------------------------------------ #

def _parse_resources(text: str) -> dict | None:
    """
    Parses the LLM output into a dict of varName -> {type, construction}.
    Accepts either a raw list of Java declarations or a fenced code block.
    Returns None if no declarations were found.
    """
    if not text:
        return None

    # Strip markdown code fences if present
    fence_m = re.search(r'```(?:java)?\n(.*?)```', text, re.DOTALL)
    source = fence_m.group(1) if fence_m else text

    resources: dict = {}
    # Match: TypeName[<...>] varName = ...;
    decl_pattern = re.compile(
        r'([A-Z][A-Za-z0-9_]*(?:<[^>]*>)?)\s+([a-zA-Z_]\w*)\s*=\s*([^;]+);'
    )
    for m in decl_pattern.finditer(source):
        raw_type   = m.group(1)
        type_name  = raw_type.split('<')[0]   # strip generics for type map
        var_name   = m.group(2)
        rhs        = m.group(3).strip()
        construction = f"{raw_type} {var_name} = {rhs};"
        resources[var_name] = {'type': type_name, 'construction': construction}

    return resources if resources else None


# ------------------------------------------------------------------ #
# Validation                                                           #
# ------------------------------------------------------------------ #

def _build_short_name_map(method: dict) -> dict:
    """
    Builds {ShortName: qualified_name} from the method's source_file_imports.
    Wildcard imports are ignored (cannot be resolved without the classpath).
    """
    short_map: dict = {}
    for imp in method.get('source_file_imports') or []:
        # "import java.io.File" -> short "File"
        parts = imp.replace('import ', '').strip().rstrip(';').split('.')
        if parts and parts[-1] != '*':
            short_map[parts[-1]] = '.'.join(parts)
    return short_map


# Standard Java SE types that are always valid without an explicit import
_JAVA_LANG_TYPES = {
    'String', 'Integer', 'Long', 'Double', 'Float', 'Boolean',
    'Byte', 'Short', 'Character', 'Object', 'Number',
    'StringBuilder', 'StringBuffer', 'Throwable', 'Exception',
    'RuntimeException', 'IllegalArgumentException', 'IllegalStateException',
    'NullPointerException', 'UnsupportedOperationException',
}

_JAVA_IO_TYPES = {
    'File', 'InputStream', 'OutputStream', 'FileInputStream',
    'FileOutputStream', 'BufferedReader', 'BufferedWriter',
    'Reader', 'Writer', 'PrintStream',
}

_ALWAYS_VALID = _JAVA_LANG_TYPES | _JAVA_IO_TYPES


def _check_resources(
    resources: dict,
    method: dict,
) -> tuple[bool, list]:
    """
    Returns (passed, issues) without any LLM calls.

    For each resource construction:
    - Resolves the type name to a short name.
    - Checks it is either in source_file_imports, dependency_signatures,
      or the always-valid set.
    Wildcard-imported types are passed through (no false positives).
    """
    if not resources:
        return False, ["No resource constructions were produced."]

    short_map   = _build_short_name_map(method)
    dep_classes = {d['class_name'] for d in method.get('dependency_signatures') or []}
    issues      = []

    for var_name, res in resources.items():
        type_name = res.get('type', '')
        if not type_name:
            issues.append(f"Resource '{var_name}' has no type.")
            continue

        # Short-name map covers explicitly imported types
        if type_name in short_map:
            continue
        # Dependency classes are always valid
        if type_name in dep_classes:
            continue
        # Well-known Java SE types need no import
        if type_name in _ALWAYS_VALID:
            continue
        # If the source file has wildcard imports we cannot resolve the type —
        # pass it through rather than generating a false positive.
        wildcard_pkgs = [
            imp.replace('import ', '').strip().rstrip(';').rsplit('.', 1)[0]
            for imp in (method.get('source_file_imports') or [])
            if imp.rstrip(';').endswith('*')
        ]
        if wildcard_pkgs:
            # Cannot disprove the type comes from a wildcard — skip
            continue

        issues.append(
            f"Resource '{var_name}' has type '{type_name}' which is not in "
            f"source_file_imports or dependency_signatures."
        )

    return len(issues) == 0, issues


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

def generate_resources(
    method: dict,
    checklist: dict,
    dep_chain: dict,
    resource_files: list,
) -> dict | None:
    """
    Calls the LLM once to produce Java construction statements for the
    fixtures listed in checklist['resource_spec'].
    Returns the parsed resources dict, or None on API / parse failure.
    """
    prompt   = build_resource_generation_prompt(method, checklist, dep_chain, resource_files)
    response = call_llm(prompt)
    if not response:
        print("  [resource_generator] No LLM response on resource generation.")
        return None

    resources = _parse_resources(response)
    if resources is None:
        print("  [resource_generator] Could not parse resource declarations.")
    return resources


def validate_resources(
    resources: dict,
    method: dict,
) -> tuple[bool, list, dict]:
    """
    Validates resource constructions and, on failure, calls the LLM to fix
    them up to config.MAX_RETRIES times.  Retry loop is internal.

    Returns:
        (success: bool, issues: list[str], resources: dict)
        - success=True  → resources are valid; use the returned dict.
        - success=False → all retries exhausted.
    """
    for attempt in range(config.MAX_RETRIES + 1):
        passed, issues = _check_resources(resources, method)
        if passed:
            return True, [], resources

        print(f"  [resource_generator] Validation failed (attempt {attempt + 1}): "
              f"{issues}")

        if attempt >= config.MAX_RETRIES:
            break

        fix_prompt   = build_resource_fix_prompt(resources, issues, method)
        fix_response = call_llm(fix_prompt)
        if not fix_response:
            print("  [resource_generator] No LLM response on resource fix.")
            break

        fixed = _parse_resources(fix_response)
        if fixed is not None:
            resources = fixed
        else:
            print("  [resource_generator] Could not parse fixed resources.")
            break

    return False, issues, resources
