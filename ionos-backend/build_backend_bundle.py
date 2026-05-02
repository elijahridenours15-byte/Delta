#!/usr/bin/env python3
from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
IONOS_DIR = ROOT / 'ionos-backend'
BUILD_DIR = IONOS_DIR / 'build'
RUNTIME_DEPS = ['Flask>=2.0', 'requests>=2.28']
BUNDLE_PATHS = [
    ROOT / 'run.py',
    ROOT / 'agent',
    ROOT / 'static',
    ROOT / 'templates',
]
SKIP_NAMES = {'__pycache__', '.DS_Store'}


def ensure_build_dir() -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)


def clean_build_dir() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    ensure_build_dir()


def install_vendor_tree(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='delta_ionos_vendor_') as tmpdir:
        vendor_dir = Path(tmpdir) / 'vendor'
        vendor_dir.mkdir()
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--no-compile', '--target', str(vendor_dir), *RUNTIME_DEPS],
            check=True,
        )
        shutil.copytree(vendor_dir, target_dir, dirs_exist_ok=True)


def add_path_to_bundle(bundle: zipfile.ZipFile, source: Path) -> None:
    if source.is_file():
        bundle.write(source, arcname=source.name)
        return

    for path in sorted(source.rglob('*')):
        if not path.is_file():
            continue
        if any(part in SKIP_NAMES for part in path.parts):
            continue
        bundle.write(path, arcname=str(path.relative_to(ROOT)))


def write_bootstrap_script(token: str) -> None:
    template = (IONOS_DIR / 'bootstrap.cgi.template').read_text(encoding='utf-8')
    script = template.replace('__BOOTSTRAP_TOKEN__', token)
    (BUILD_DIR / 'bootstrap.cgi').write_text(script, encoding='utf-8')


def write_bundle() -> None:
    vendor_dir = BUILD_DIR / 'vendor'
    install_vendor_tree(vendor_dir)

    with zipfile.ZipFile(BUILD_DIR / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(IONOS_DIR / 'index.cgi', arcname='index.cgi')
        bundle.write(IONOS_DIR / 'index.cgi.py', arcname='index.cgi.py')
        for path in sorted(vendor_dir.rglob('*')):
            if not path.is_file():
                continue
            if any(part in SKIP_NAMES for part in path.parts):
                continue
            bundle.write(path, arcname=str(path.relative_to(BUILD_DIR)))
        for source in BUNDLE_PATHS:
            add_path_to_bundle(bundle, source)

    shutil.rmtree(vendor_dir)


def main() -> None:
    clean_build_dir()

    token = secrets.token_urlsafe(24)
    write_bootstrap_script(token)
    shutil.copy2(IONOS_DIR / '.htaccess', BUILD_DIR / '.htaccess')
    write_bundle()
    (BUILD_DIR / 'token.txt').write_text(token + '\n', encoding='utf-8')

    print('IONOS backend build complete:')
    print(f'- {BUILD_DIR / ".htaccess"}')
    print(f'- {BUILD_DIR / "bootstrap.cgi"}')
    print(f'- {BUILD_DIR / "bundle.zip"}')
    print(f'- token: {token}')


if __name__ == '__main__':
    main()
