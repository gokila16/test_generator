import json
import os
import re

# Parameter types that indicate a method requires file input
_FILE_PARAM_TYPES = {
    'File', 'Path', 'InputStream', 'OutputStream',
    'RandomAccessRead', 'RandomAccessReadBuffer',
    'RandomAccessReadBufferedFile', 'RandomAccessStreamCache',
    'SeekableByteChannel',
}

_FILE_PARAM_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in _FILE_PARAM_TYPES) + r')\b'
)

# test_resource_metadata.json lives at the project root (same directory as config.py)
_METADATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               'test_resource_metadata.json')


def _load_metadata() -> dict:
    """
    Returns the contents of test_resource_metadata.json keyed by relative
    path, or an empty dict if the file does not exist yet.
    """
    if not os.path.isfile(_METADATA_PATH):
        return {}
    with open(_METADATA_PATH, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def scan_test_resources(resources_dir: str) -> dict:
    """
    Walks resources_dir recursively and groups files by extension.
    Returns a dict mapping extension (e.g. '.pdf') -> list of entry dicts.

    Each entry dict contains at minimum:
        rel_path  - relative path from resources_dir (for getResource())
    And, when test_resource_metadata.json is present, also:
        is_encrypted      - bool
        password          - str | None
        file_size_bytes   - int

    These relative paths are correct for use in getResource() / getResourceAsStream().
    """
    result = {}
    if not os.path.isdir(resources_dir):
        return result
    resources_dir = os.path.normpath(resources_dir)
    metadata = _load_metadata()

    for root, _, files in os.walk(resources_dir):
        for filename in files:
            _, ext = os.path.splitext(filename)
            if not ext:
                continue
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, resources_dir).replace(os.sep, '/')

            entry = {'rel_path': rel_path}
            if rel_path in metadata:
                meta = metadata[rel_path]
                entry['is_encrypted']    = meta.get('is_encrypted', False)
                entry['password']        = meta.get('password')
                entry['file_size_bytes'] = meta.get('file_size_bytes')

            result.setdefault(ext.lower(), []).append(entry)
    return result


def is_file_dependent(method: dict) -> bool:
    """
    Returns True if the method signature contains any file-related parameter type.
    """
    signature = method.get('signature', '')
    return bool(_FILE_PARAM_PATTERN.search(signature))
