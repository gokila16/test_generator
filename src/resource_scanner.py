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


def scan_test_resources(resources_dir: str) -> dict:
    """
    Walks resources_dir recursively and groups files by extension.
    Returns a dict mapping extension (e.g. '.pdf') -> list of RELATIVE PATHS
    from resources_dir (e.g. 'cweb.pdf' or 'org/apache/pdfbox/test.pdf').
    These relative paths are correct for use in getResource() / getResourceAsStream().
    """
    result = {}
    if not os.path.isdir(resources_dir):
        return result
    resources_dir = os.path.normpath(resources_dir)
    for root, _, files in os.walk(resources_dir):
        for filename in files:
            _, ext = os.path.splitext(filename)
            if not ext:
                continue
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, resources_dir).replace(os.sep, '/')
            result.setdefault(ext.lower(), []).append(rel_path)
    return result


def is_file_dependent(method: dict) -> bool:
    """
    Returns True if the method signature contains any file-related parameter type.
    """
    signature = method.get('signature', '')
    return bool(_FILE_PARAM_PATTERN.search(signature))
