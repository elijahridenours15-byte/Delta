#!/usr/bin/env python3
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE_NAME = 'delta_coding_ionos_deploy.zip'
BUNDLE_ITEMS = [
    'run.py',
    'requirements.txt',
    'Dockerfile',
    'README.md',
    'LICENSE',
    'agent',
    'static',
    'templates',
    'java',
]
SKIP = {'.DS_Store', '__pycache__', '.pytest_cache'}

out_path = os.path.join(ROOT, BUNDLE_NAME)
print('Creating bundle:', out_path)
with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for item in BUNDLE_ITEMS:
        abs_item = os.path.join(ROOT, item)
        if not os.path.exists(abs_item):
            print('Skipping missing:', item)
            continue
        if os.path.isfile(abs_item):
            zf.write(abs_item, arcname=os.path.relpath(abs_item, ROOT))
            continue
        for root, dirs, files in os.walk(abs_item):
            dirs[:] = [d for d in dirs if d not in SKIP]
            for f in files:
                if f in SKIP:
                    continue
                abs_path = os.path.join(root, f)
                rel = os.path.relpath(abs_path, ROOT)
                zf.write(abs_path, arcname=rel)
print('Bundle created.')
print(out_path)
