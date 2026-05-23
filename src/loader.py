import json
import config

def load_methods(json_path):
    """
    Loads methods from extracted_metadata_final.json.
    Returns methods that have a body and status OK.

    When config.INCLUDE_DEVELOPER_TESTED is False, methods that already have
    developer tests are excluded (original 932-method behavior). When True,
    all qualifying methods are included (1309).
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"Total methods in JSON: {len(data)}")

    valid = [
        m for m in data
        if m.get('status') == 'OK'
        and m.get('body')
        and m.get('body').strip() != ''
        and (config.INCLUDE_DEVELOPER_TESTED or not m.get('has_developer_tests', False))
    ]

    has_dev_tests = sum(1 for m in data if m.get('has_developer_tests', False))
    print(f"Valid methods to process: {len(valid)}")
    print(f"INCLUDE_DEVELOPER_TESTED: {config.INCLUDE_DEVELOPER_TESTED} "
          f"(developer-tested methods in dataset: {has_dev_tests})")

    return valid