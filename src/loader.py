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
    ]

    skipped = len(data) - len(valid)
    print(f"Valid methods to process: {len(valid)}")
    print(f"Skipped (no body):        {skipped}")

    return valid