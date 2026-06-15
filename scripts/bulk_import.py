#!/usr/bin/env python3
"""Bulk import public-domain images from Wikimedia Commons using a CSV.

CSV format (header optional): brand,model,year,query,limit,download
Example row: Toyota,Corolla,1996,"Toyota Corolla 1996",6,1

The script will search Wikimedia Commons for each query and (optionally) download
public-domain / CC0 images and create a stub manual entry in the local ManualStore.
"""
import csv
import os
import time
import argparse
from wikimedia_importer import search_commons, download_public_domain_images, _default_session
from manual_store import ManualStore


def run(csv_path, delay=1.0, download=False, default_limit=6, app_root=None):
    app_root = app_root or os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    store = ManualStore(app_root)
    session = _default_session()

    with open(csv_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            brand = (row.get('brand') or '').strip()
            model = (row.get('model') or '').strip()
            year = (row.get('year') or '').strip()
            query = (row.get('query') or f"{brand} {model} {year}").strip()
            limit = int(row.get('limit') or default_limit)
            do_download = bool(int(row.get('download') or (1 if download else 0)))

            if not brand or not query:
                print('Skipping row with missing brand/query:', row)
                continue

            print(f'Searching Commons for: {query} (limit={limit})')
            try:
                candidates = search_commons(query, limit=limit, session=session)
            except Exception as exc:
                print('Search failed for', query, '->', exc)
                time.sleep(delay)
                continue

            if do_download:
                print(f'Attempting to download PD/CC0 images for {brand} {model} {year}')
                res = download_public_domain_images(candidates, app_root, limit=limit, session=session)
                mid = res.get('mid')
                downloaded = res.get('downloaded', [])
                # Create a stub manual entry referencing downloaded images
                title = f"{brand} {model} {year} (Wikimedia import)"
                mid_saved = store.add_manual(brand=brand, model=model, year=year, title=title, description='Imported Wikimedia Commons public-domain images', license='various', source_url=None, pdf_path=None, image_paths=downloaded, mid=mid)
                print('Imported', len(downloaded), 'images -> manual id', mid_saved)
            else:
                print('Found', len(candidates), 'candidates. Use download=1 to fetch PD/CC0 images.')

            time.sleep(delay)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('csv', help='CSV file with brand,model,year,query,limit,download')
    p.add_argument('--delay', type=float, default=1.0, help='Seconds to wait between rows')
    p.add_argument('--download', action='store_true', help='Download PD/CC0 images when found')
    p.add_argument('--limit', type=int, default=6, help='Per-query candidate limit')
    args = p.parse_args()
    run(args.csv, delay=args.delay, download=args.download, default_limit=args.limit)


if __name__ == '__main__':
    main()
