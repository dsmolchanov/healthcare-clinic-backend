#!/usr/bin/env python3
"""
Automated migration script for Supabase client imports.
Converts direct create_client imports to canonical database.py helpers.

This script performs two types of migrations:
1. Simple: Replace import + module-level client with helper import
2. Complex: Replace inline imports (inside functions) with helper calls

Usage:
    python scripts/migrate_supabase_imports.py --dry-run  # Preview changes
    python scripts/migrate_supabase_imports.py            # Apply changes
    python scripts/migrate_supabase_imports.py --file path/to/file.py  # Single file

IMPORTANT: Run canary migration on complex files first before bulk migration.
"""
import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple, Optional

# Configuration
APP_DIR = Path(__file__).parent.parent / "app"
EXCLUDED_FILES = ["database.py", "__pycache__"]
EXCLUDED_DIRS = ["__pycache__", ".git", "_archive"]

# Import patterns to detect
IMPORT_PATTERNS = [
    r'^from supabase import create_client.*$',
    r'^from supabase import create_client, Client.*$',
    r'^from supabase import create_client, Client, ClientOptions.*$',
]

# Client initialization patterns (module-level)
MODULE_CLIENT_PATTERNS = [
    # Pattern: supabase_url = os.getenv(...) / supabase_key = os.getenv(...) / supabase = create_client(...)
    r'^supabase_url\s*=\s*os\.getenv\(["\']SUPABASE_URL["\']\).*$',
    r'^supabase_key\s*=\s*os\.getenv\(["\']SUPABASE.*KEY["\']\).*$',
    r'^supabase\s*[:=].*create_client\(.*\).*$',
    r'^options\s*=\s*ClientOptions\(.*$',
]


def find_files_to_migrate(root_dir: Path) -> List[Path]:
    """Find all Python files with direct Supabase imports."""
    files = []
    for py_file in root_dir.rglob("*.py"):
        # Skip excluded
        if any(exc in str(py_file) for exc in EXCLUDED_FILES):
            continue
        if any(exc in str(py_file.parent) for exc in EXCLUDED_DIRS):
            continue

        try:
            content = py_file.read_text()
            if "from supabase import create_client" in content:
                files.append(py_file)
        except Exception as e:
            print(f"Warning: Could not read {py_file}: {e}")

    return sorted(files)


def analyze_file(file_path: Path) -> dict:
    """Analyze a file to understand its Supabase usage pattern."""
    content = file_path.read_text()
    lines = content.split('\n')

    analysis = {
        'file': str(file_path),
        'import_lines': [],
        'client_init_lines': [],
        'inline_imports': [],
        'schema_used': None,
        'complexity': 'simple',
    }

    for i, line in enumerate(lines, 1):
        # Check for import statements
        if 'from supabase import create_client' in line:
            if line.strip().startswith('from'):
                analysis['import_lines'].append(i)
            else:
                # Inline import inside a function
                analysis['inline_imports'].append(i)

        # Check for client initialization
        if 'create_client(' in line and 'from supabase' not in line:
            analysis['client_init_lines'].append(i)

        # Detect schema
        if "schema='healthcare'" in line or 'schema="healthcare"' in line:
            analysis['schema_used'] = 'healthcare'
        elif "schema='public'" in line or 'schema="public"' in line:
            analysis['schema_used'] = 'public'
        elif "schema='core'" in line or 'schema="core"' in line:
            analysis['schema_used'] = 'core'

    # Determine complexity
    if analysis['inline_imports']:
        analysis['complexity'] = 'complex'
    elif len(analysis['client_init_lines']) > 1:
        analysis['complexity'] = 'complex'

    return analysis


def migrate_simple_file(file_path: Path, dry_run: bool = True) -> Tuple[bool, str]:
    """
    Migrate a simple file with module-level client initialization.

    Simple pattern:
        from supabase import create_client, Client
        supabase = create_client(...)

    Becomes:
        from app.database import get_healthcare_client

        supabase = get_healthcare_client()
    """
    content = file_path.read_text()
    original = content
    analysis = analyze_file(file_path)

    # Determine which helper to use based on schema
    if analysis['schema_used'] == 'public':
        helper = 'get_main_client'
    elif analysis['schema_used'] == 'core':
        helper = 'get_core_client'
    else:
        helper = 'get_healthcare_client'

    # Remove old imports
    content = re.sub(
        r'^from supabase import create_client.*\n',
        '',
        content,
        flags=re.MULTILINE
    )
    content = re.sub(
        r'^from supabase\.client import ClientOptions.*\n',
        '',
        content,
        flags=re.MULTILINE
    )

    # Remove URL/key initialization lines
    content = re.sub(
        r'^supabase_url\s*=\s*os\.getenv\(["\']SUPABASE_URL["\']\).*\n',
        '',
        content,
        flags=re.MULTILINE
    )
    content = re.sub(
        r'^supabase_key\s*=\s*os\.getenv\(["\']SUPABASE.*KEY["\']\).*\n',
        '',
        content,
        flags=re.MULTILINE
    )

    # Remove ClientOptions initialization
    content = re.sub(
        r'^options\s*=\s*ClientOptions\(.*?\)\s*\n',
        '',
        content,
        flags=re.MULTILINE | re.DOTALL
    )

    # Replace client initialization
    content = re.sub(
        r'^supabase\s*[:=].*create_client\(.*?\)\s*\n',
        f'supabase = {helper}()\n',
        content,
        flags=re.MULTILINE | re.DOTALL
    )

    # Add new import after other imports
    if f'from app.database import {helper}' not in content:
        # Find a good place to insert the import
        import_match = re.search(r'^(import .*|from .* import .*)$', content, re.MULTILINE)
        if import_match:
            insert_pos = import_match.end()
            content = content[:insert_pos] + f'\nfrom app.database import {helper}' + content[insert_pos:]
        else:
            content = f'from app.database import {helper}\n\n' + content

    changed = content != original

    if changed and not dry_run:
        file_path.write_text(content)

    return changed, f"{'Would migrate' if dry_run else 'Migrated'}: {file_path.name} -> {helper}()"


def migrate_inline_import(file_path: Path, dry_run: bool = True) -> Tuple[bool, str]:
    """
    Migrate files with inline imports (inside functions).

    This requires more careful handling as the import is inside function scope.
    """
    content = file_path.read_text()
    original = content

    # Pattern for inline imports with create_client
    # Example:
    #     from supabase import create_client
    #     client = create_client(url, key)
    pattern = r'''
        (\s+)from\s+supabase\s+import\s+create_client[^\n]*\n
        (?:\1[^\n]*\n)*?  # Skip intermediate lines
        \1\w+\s*=\s*create_client\([^)]+\)
    '''

    # Replace with helper import
    def replace_inline(match):
        indent = match.group(1)
        return f'{indent}from app.database import get_healthcare_client\n{indent}client = get_healthcare_client()'

    content = re.sub(pattern, replace_inline, content, flags=re.VERBOSE)

    # Simpler pattern for direct replacement
    content = re.sub(
        r'(\s+)from supabase import create_client\n(\s+)(\w+)\s*=\s*create_client\([^)]+\)',
        r'\1from app.database import get_healthcare_client\n\2\3 = get_healthcare_client()',
        content
    )

    changed = content != original

    if changed and not dry_run:
        file_path.write_text(content)

    return changed, f"{'Would migrate' if dry_run else 'Migrated'} inline: {file_path.name}"


def main():
    parser = argparse.ArgumentParser(
        description='Migrate Supabase imports to canonical database.py helpers'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without applying them'
    )
    parser.add_argument(
        '--file',
        type=Path,
        help='Migrate a single file instead of all files'
    )
    parser.add_argument(
        '--analyze',
        action='store_true',
        help='Only analyze files, do not migrate'
    )

    args = parser.parse_args()

    if args.file:
        files = [args.file] if args.file.exists() else []
    else:
        files = find_files_to_migrate(APP_DIR)

    print(f"Found {len(files)} files with direct Supabase imports\n")

    if args.analyze:
        print("Analysis of files:")
        print("-" * 80)
        for f in files:
            analysis = analyze_file(f)
            print(f"\n{f.relative_to(APP_DIR)}:")
            print(f"  Complexity: {analysis['complexity']}")
            print(f"  Schema: {analysis['schema_used'] or 'default (healthcare)'}")
            print(f"  Import lines: {analysis['import_lines']}")
            print(f"  Inline imports: {analysis['inline_imports']}")
            print(f"  Client init lines: {analysis['client_init_lines']}")
        return

    migrated = 0
    failed = 0
    skipped = 0

    for f in files:
        try:
            analysis = analyze_file(f)

            if analysis['complexity'] == 'complex':
                print(f"⚠️  COMPLEX (manual review needed): {f.relative_to(APP_DIR)}")
                skipped += 1
                continue

            changed, message = migrate_simple_file(f, args.dry_run)
            if changed:
                print(f"✅ {message}")
                migrated += 1
            else:
                print(f"⏭️  No changes needed: {f.name}")

        except Exception as e:
            print(f"❌ Failed to migrate {f.name}: {e}")
            failed += 1

    print(f"\n{'Would migrate' if args.dry_run else 'Migrated'}: {migrated}/{len(files)} files")
    print(f"Skipped (complex): {skipped}")
    print(f"Failed: {failed}")

    if args.dry_run:
        print("\nRun without --dry-run to apply changes")

    if skipped > 0:
        print("\n⚠️  Complex files need manual migration. See plan section 2.4 for canary migration.")


if __name__ == "__main__":
    main()
