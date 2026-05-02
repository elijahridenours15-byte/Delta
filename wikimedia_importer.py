"""Helpers to search Wikimedia Commons and optionally download public-domain images.

This module is used by the admin UI importer and the bulk-import CLI.
"""
import os
import uuid
import time
import requests
from werkzeug.utils import secure_filename

WIKIMEDIA_API = 'https://commons.wikimedia.org/w/api.php'


def _default_session(user_agent=None):
    s = requests.Session()
    ua = user_agent or 'FutureCodeDelta/1.0 (+https://futurecodedelta.org)'
    s.headers.update({'User-Agent': ua, 'Accept': 'application/json'})
    return s


def search_commons(query, limit=6, retries=3, backoff=1.5, session=None):
    """Search Wikimedia Commons for images matching `query`.

    Returns a list of candidate dicts with keys: title, url, license, usage, page.
    Retries on transient HTTP errors and sets a sensible User-Agent header.
    """
    if session is None:
        session = _default_session()

    params = {
        'action': 'query',
        'format': 'json',
        'generator': 'search',
        'gsrsearch': query,
        'gsrlimit': limit,
        'prop': 'imageinfo',
        'iiprop': 'url|extmetadata',
    }

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(WIKIMEDIA_API, params=params, timeout=15)
            if resp.status_code == 403:
                # Forbidden: often caused by missing/blocked UA or remote policy — return empty and surface error
                resp.raise_for_status()
            resp.raise_for_status()
            body = resp.json()
            pages = body.get('query', {}).get('pages', {})
            candidates = []
            for pid, p in pages.items():
                title = p.get('title')
                ii = p.get('imageinfo', [])
                if not ii:
                    continue
                info = ii[0]
                url = info.get('url')
                ext = info.get('extmetadata', {}) or {}
                license_short = (ext.get('LicenseShortName') or '').strip()
                usage = (ext.get('UsageTerms') or '').strip()
                candidates.append({'title': title, 'url': url, 'license': license_short, 'usage': usage, 'page': f'https://commons.wikimedia.org/wiki/{title.replace(" ", "_")}'})
            return candidates
        except requests.exceptions.RequestException as exc:
            if attempt == retries:
                raise
            time.sleep(backoff * attempt)


def download_public_domain_images(candidates, app_root, mid=None, limit=None, session=None):
    """Download candidates that appear to be public-domain/CC0.

    Returns a dict with keys 'mid' and 'downloaded' listing relative URLs.
    """
    if session is None:
        session = _default_session()

    if mid is None:
        mid = uuid.uuid4().hex
    image_dir = os.path.join(app_root, 'static', 'manuals', 'images', mid)
    os.makedirs(image_dir, exist_ok=True)
    downloaded = []
    selected = candidates[:limit] if limit else candidates
    for c in selected:
        lic = (c.get('license') or '').lower()
        if 'public domain' in lic or 'cc0' in lic or lic == 'pd':
            try:
                r = session.get(c['url'], timeout=30, stream=True)
                r.raise_for_status()
                fname = os.path.basename(c['url'].split('?')[0])
                safe = secure_filename(fname)
                save_path = os.path.join(image_dir, safe)
                with open(save_path, 'wb') as fh:
                    for chunk in r.iter_content(8192):
                        fh.write(chunk)
                downloaded.append(f'/static/manuals/images/{mid}/{safe}')
            except Exception:
                continue
    return {'mid': mid, 'downloaded': downloaded}
