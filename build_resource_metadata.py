"""
build_resource_metadata.py

Walks TEST_RESOURCES_DIR, probes every PDF for encryption status and known
test passwords, and writes test_resource_metadata.json to the project root.

Uses pypdf (pip install pypdf).

Run from the project root:
    python build_resource_metadata.py
"""

import os
import json

import pypdf

import config

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), 'test_resource_metadata.json')

# Passwords tried against every encrypted PDF, in order.
# Empty string is tried last (covers unencrypted PDFs opened without a password,
# but for truly unencrypted files pypdf doesn't require a password at all).
_CANDIDATE_PASSWORDS = ["userpassword", "owner", "password", "test", ""]


def _probe_pdf(abs_path):
    """
    Returns (is_encrypted: bool, password: str | None).

    - Not encrypted          → (False, None)
    - Encrypted, known pass  → (True,  matching_password_string)
    - Encrypted, unknown     → (True,  None)
    """
    try:
        reader = pypdf.PdfReader(abs_path)
    except Exception:
        # Unreadable / corrupt — treat as encrypted-unknown so the LLM skips it
        return True, None

    if not reader.is_encrypted:
        return False, None

    for pwd in _CANDIDATE_PASSWORDS:
        try:
            result = reader.decrypt(pwd)
            # decrypt() returns PasswordType enum (non-zero) on success
            if result:
                return True, pwd
        except Exception:
            continue

    return True, None


def build_metadata(resources_dir):
    metadata = {}
    resources_dir = os.path.normpath(resources_dir)

    all_files = []
    for root, _, files in os.walk(resources_dir):
        for filename in files:
            all_files.append(os.path.join(root, filename))

    print(f"Scanning {len(all_files)} files in {resources_dir} ...")

    pdf_total = encrypted_count = known_pass_count = 0

    for abs_path in sorted(all_files):
        rel_path = os.path.relpath(abs_path, resources_dir).replace(os.sep, '/')
        _, ext = os.path.splitext(abs_path)
        ext = ext.lower()

        entry = {
            'filename':        os.path.basename(abs_path),
            'extension':       ext,
            'file_size_bytes': os.path.getsize(abs_path),
            'is_encrypted':    False,
            'password':        None,
        }

        if ext == '.pdf':
            pdf_total += 1
            is_enc, pwd = _probe_pdf(abs_path)
            entry['is_encrypted'] = is_enc
            entry['password']     = pwd
            if is_enc:
                encrypted_count += 1
                if pwd is not None:
                    known_pass_count += 1

        metadata[rel_path] = entry

    print(f"  PDFs scanned:   {pdf_total}")
    print(f"  Encrypted:      {encrypted_count}")
    print(f"  Password known: {known_pass_count}")
    print(f"  Password unknown (skip for happy-path): {encrypted_count - known_pass_count}")
    return metadata


def main():
    print("=" * 50)
    print("BUILD RESOURCE METADATA")
    print("=" * 50)
    metadata = build_metadata(config.TEST_RESOURCES_DIR)

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as fh:
        json.dump(metadata, fh, indent=2, sort_keys=True)

    print(f"\nOutput written to: {OUTPUT_FILE}")
    print(f"Total entries:     {len(metadata)}")


if __name__ == '__main__':
    main()
