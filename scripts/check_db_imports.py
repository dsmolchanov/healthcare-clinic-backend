#!/usr/bin/env python3
"""
CI script to check for deprecated database import patterns.
Exit code 1 if violations found.
"""
import re
import sys
from pathlib import Path

VIOLATIONS = [
    # Direct create_client imports (except in database.py)
    (r'from supabase import.*create_client', 'Direct create_client import'),
    # Old config.py client
    (r'from app\.config import get_supabase_client', 'Deprecated config.get_supabase_client'),
    # Old db/supabase_client
    (r'from app\.db\.supabase_client import', 'Deprecated db.supabase_client'),
    # Inline ClientOptions (except in database.py)
    (r'ClientOptions\(schema=', 'Inline ClientOptions - use database.py helpers'),
]

ALLOWED_FILES = [
    'app/database.py',
    'app/services/database_manager.py',  # Advanced use case
    'app/services/db_pool.py',  # Advanced use case
    'scripts/check_db_imports.py',  # This script itself
]


def check_file(path: Path, root: Path) -> list:
    """Check a single file for violations."""
    relative = str(path.relative_to(root))

    if any(allowed in relative for allowed in ALLOWED_FILES):
        return []

    try:
        content = path.read_text()
    except Exception as e:
        return [f"{relative}: Could not read file: {e}"]

    violations = []

    for pattern, description in VIOLATIONS:
        if re.search(pattern, content):
            violations.append(f"{relative}: {description}")

    return violations


def main():
    # Find the root of the healthcare-backend app
    script_path = Path(__file__).resolve()
    root = script_path.parent.parent  # scripts/ -> apps/healthcare-backend/

    if not (root / 'app').exists():
        # Try alternative path
        root = Path('apps/healthcare-backend')
        if not (root / 'app').exists():
            root = Path('.')

    print(f"Checking {root} for database import violations...")

    all_violations = []

    for py_file in root.rglob('*.py'):
        # Skip test files, migrations, and __pycache__
        relative = str(py_file.relative_to(root))
        if any(skip in relative for skip in ['__pycache__', 'tests/', 'migrations/', '.pyc']):
            continue
        all_violations.extend(check_file(py_file, root))

    if all_violations:
        print("\n❌ Database import violations found:")
        for v in all_violations:
            print(f"  - {v}")
        print(f"\nTotal: {len(all_violations)} violation(s)")
        sys.exit(1)

    print("✅ No database import violations found.")
    sys.exit(0)


if __name__ == '__main__':
    main()
