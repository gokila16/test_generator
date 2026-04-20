import json

def load_methods(json_path):
    """
    Loads methods from extracted_metadata_final.json
    Returns only methods that have a body and status OK
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"Total methods in JSON: {len(data)}")

    valid = [
        m for m in data
        if m.get('status') == 'OK'
        and m.get('body')
        and m.get('body').strip() != ''
        and not m.get('has_developer_tests', False)
    ]

    skipped = len(data) - len(valid)
    has_dev_tests = sum(1 for m in data if m.get('has_developer_tests', False))
    print(f"Valid methods to process: {len(valid)}")
    print(f"Skipped (no body):        {skipped - has_dev_tests}")
    print(f"Skipped (has dev tests):  {has_dev_tests}")

    return valid