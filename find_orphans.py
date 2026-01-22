#!/usr/bin/env python3
"""
Orphan File Finder for AURA Backend
Scans the aurabackend folder and identifies Python files that are not imported anywhere.
Excludes main.py, enhanced_main.py, orchestrator.py, and __init__.py from being marked as orphans.
"""

import os
import re
from pathlib import Path
from typing import Set, Dict, List

# Files to exclude from orphan detection (these are entry points)
EXCLUDE_FROM_ORPHAN_CHECK = {
    "main.py",
    "enhanced_main.py",
    "orchestrator.py",
    "__init__.py",
    "__main__.py",
    "setup.py",
    "conftest.py",
}

# Directories to skip
SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    "dist",
    "build",
    "*.egg-info",
}


def get_all_python_files(root_dir: Path) -> List[Path]:
    """Get all Python files in the directory tree."""
    python_files = []
    for root, dirs, files in os.walk(root_dir):
        # Skip directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        
        for file in files:
            if file.endswith('.py'):
                file_path = Path(root) / file
                python_files.append(file_path)
    
    return python_files


def extract_imports_from_file(file_path: Path) -> Set[str]:
    """
    Extract all imported module names from a Python file.
    Returns module names (without extensions) that are imported.
    """
    imports = set()
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Pattern 1: from module import ...
        # Examples: from shared.file_service import FileService
        #           from .models import User
        from_imports = re.findall(r'from\s+([.\w]+)\s+import', content)
        imports.update(from_imports)
        
        # Pattern 2: import module
        # Examples: import httpx
        #           import sys
        direct_imports = re.findall(r'^\s*import\s+([\w.]+)', content, re.MULTILINE)
        imports.update(direct_imports)
        
        # Pattern 3: import module as alias
        # Examples: import pandas as pd
        alias_imports = re.findall(r'^\s*import\s+([\w.]+)\s+as\s+\w+', content, re.MULTILINE)
        imports.update(alias_imports)
        
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}")
    
    return imports


def get_module_name_from_path(file_path: Path, root_dir: Path) -> str:
    """
    Convert file path to module name.
    Example: aurabackend/shared/file_service.py -> shared.file_service
    """
    relative = file_path.relative_to(root_dir)
    parts = list(relative.parts)
    
    # Remove .py extension
    if parts[-1].endswith('.py'):
        parts[-1] = parts[-1][:-3]
    
    # Remove __init__ (it represents the package itself)
    if parts[-1] == '__init__':
        parts = parts[:-1]
    
    return '.'.join(parts)


def find_orphan_files(root_dir: Path) -> Dict[str, List[Path]]:
    """
    Find all Python files that are not imported by any other file.
    Returns a dictionary with categories of files.
    """
    print(f"🔍 Scanning {root_dir} for orphan Python files...\n")
    
    # Get all Python files
    all_files = get_all_python_files(root_dir)
    print(f"Found {len(all_files)} Python files\n")
    
    # Build a map of module names to file paths
    module_to_file: Dict[str, Path] = {}
    for file_path in all_files:
        module_name = get_module_name_from_path(file_path, root_dir.parent)
        module_to_file[module_name] = file_path
    
    # Extract all imports from all files
    all_imports: Set[str] = set()
    for file_path in all_files:
        imports = extract_imports_from_file(file_path)
        all_imports.update(imports)
    
    # Expand imports to match module names
    # Example: "shared" matches "shared.file_service"
    expanded_imports = set()
    for imp in all_imports:
        expanded_imports.add(imp)
        # Also add parent modules
        parts = imp.split('.')
        for i in range(1, len(parts)):
            expanded_imports.add('.'.join(parts[:i]))
    
    # Find orphans
    orphans = []
    excluded = []
    
    for module_name, file_path in module_to_file.items():
        file_name = file_path.name
        
        # Skip excluded files
        if file_name in EXCLUDE_FROM_ORPHAN_CHECK:
            excluded.append(file_path)
            continue
        
        # Check if this module is imported anywhere
        is_imported = False
        
        # Check full module name
        if module_name in expanded_imports:
            is_imported = True
        
        # Check if any import pattern matches this file
        for imp in expanded_imports:
            # Match patterns like "shared.file_service" or just "file_service"
            if module_name.endswith(imp) or imp.endswith(module_name.split('.')[-1]):
                is_imported = True
                break
        
        if not is_imported:
            orphans.append(file_path)
    
    return {
        "orphans": orphans,
        "excluded": excluded,
        "total_files": len(all_files),
        "all_imports": all_imports
    }


def main():
    """Main entry point"""
    print("=" * 80)
    print("AURA BACKEND ORPHAN FILE FINDER")
    print("=" * 80)
    print()
    
    # Scan aurabackend directory
    project_root = Path(__file__).parent
    backend_dir = project_root / "aurabackend"
    
    if not backend_dir.exists():
        print(f"❌ Error: {backend_dir} does not exist!")
        return
    
    results = find_orphan_files(backend_dir)
    
    # Display results
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    print()
    
    print(f"📊 Total Python files: {results['total_files']}")
    print(f"✓  Excluded (entry points): {len(results['excluded'])}")
    print(f"🗑️  Potential orphans: {len(results['orphans'])}")
    print()
    
    if results['orphans']:
        print("=" * 80)
        print("ORPHAN FILES (Not imported by any other file)")
        print("=" * 80)
        print()
        
        # Group by directory
        orphans_by_dir: Dict[str, List[Path]] = {}
        for orphan in results['orphans']:
            dir_name = orphan.parent.name
            if dir_name not in orphans_by_dir:
                orphans_by_dir[dir_name] = []
            orphans_by_dir[dir_name].append(orphan)
        
        for dir_name, files in sorted(orphans_by_dir.items()):
            print(f"\n📁 {dir_name}/")
            for file in sorted(files):
                print(f"   - {file.name}")
                print(f"     Path: {file.relative_to(project_root)}")
    else:
        print("✅ No orphan files found! All Python files are imported somewhere.")
    
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()
    print("1. Review each orphan file manually")
    print("2. Check if it's genuinely unused or a potential entry point")
    print("3. If confirmed unused, consider deleting or moving to /backups")
    print("4. Some files may be test files or utilities - verify before deletion")
    print()
    print("⚠️  IMPORTANT: Always backup before deleting files!")
    print()


if __name__ == "__main__":
    main()
