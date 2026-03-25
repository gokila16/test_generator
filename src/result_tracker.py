import json
import os
from datetime import datetime

def load_results(results_path):
    if os.path.exists(results_path):
        # Check if file is empty
        if os.path.getsize(results_path) == 0:
            return {}
        with open(results_path, 'r') as f:
            return json.load(f)
    return {}

def save_result(results_path, full_name, result):
    """
    Saves a single result to results.json
    Loads existing results first to avoid overwriting
    """
    # Create the folder if it doesn't exist
    os.makedirs(os.path.dirname(results_path), exist_ok=True)
    
    results = load_results(results_path)
    result['timestamp'] = str(datetime.now())
    results[full_name] = result

    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2)

def is_already_processed(results_path, full_name):
    """
    Checks if a method was already processed
    """
    results = load_results(results_path)
    return full_name in results