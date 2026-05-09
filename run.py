
#!/usr/bin/env python3
import base64
import html
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

import requests as http_requests
from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from agent import generate_with_llm
from agent.agent import run_instruction
from drone_bridge import DroneBridgeManager
from drone_bridge_http import create_drone_bridge_blueprint
from manual_store import ManualStore

app = Flask(__name__, static_folder='static', template_folder='templates')
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
manual_store = ManualStore(APP_ROOT)
drone_bridge = DroneBridgeManager()
app.register_blueprint(
    create_drone_bridge_blueprint(drone_bridge, service_label='site-bridge'),
    url_prefix='/api/drone/bridge',
)
journal_db_path = os.path.join(APP_ROOT, 'generated', 'journal.db')
visitor_db_path = os.path.join(APP_ROOT, 'generated', 'visitors.db')
os.makedirs(os.path.dirname(journal_db_path), exist_ok=True)


def _init_journal_db():
    import sqlite3

    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry TEXT NOT NULL,
        created TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()


_init_journal_db()


def _init_visitor_db():
    import sqlite3

    conn = sqlite3.connect(visitor_db_path)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS visitor_sessions (
        token TEXT PRIMARY KEY,
        last_seen REAL NOT NULL,
        lat REAL,
        lon REAL,
        country TEXT,
        city TEXT,
        flag TEXT
    )''')
    # add columns if upgrading from old schema
    for col, typ in [('lat','REAL'),('lon','REAL'),('country','TEXT'),('city','TEXT'),('flag','TEXT')]:
        try:
            cur.execute(f'ALTER TABLE visitor_sessions ADD COLUMN {col} {typ}')
        except Exception:
            pass
    cur.execute('CREATE INDEX IF NOT EXISTS idx_vs_seen ON visitor_sessions(last_seen)')
    conn.commit()
    conn.close()


_init_visitor_db()

# ── in-memory geo cache: ip_hash -> {lat,lon,country,city,flag} ──
_geo_cache = {}


def _geo_lookup(ip):
    """Look up lat/lon for an IP using ip-api.com (free, no key, 45 req/min).
    Returns a dict with lat, lon, country, city, flag (emoji).
    Cached in-process; gracefully returns None on any error.
    """
    import hashlib, time
    ip_hash = hashlib.sha1(ip.encode()).hexdigest()[:16]
    cached = _geo_cache.get(ip_hash)
    if cached:
        return cached
    # skip loopback / private addresses
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback or addr.is_private or addr.is_link_local:
            return None
    except ValueError:
        return None
    try:
        import urllib.request, json as _json
        url = f'http://ip-api.com/json/{ip}?fields=status,lat,lon,country,countryCode,city'
        req = urllib.request.Request(url, headers={'User-Agent': 'futurecodedelta/1.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read())
        if data.get('status') != 'success':
            return None
        # convert country code to flag emoji (each letter -> regional indicator)
        cc = data.get('countryCode', '')
        flag = ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in cc.upper()) if len(cc) == 2 else ''
        result = {
            'lat': round(data.get('lat', 0), 4),
            'lon': round(data.get('lon', 0), 4),
            'country': data.get('country', ''),
            'city': data.get('city', ''),
            'flag': flag,
        }
        _geo_cache[ip_hash] = result
        return result
    except Exception:
        return None

OFFLINE_BUNDLE_ROOT = 'delta-coding-offline'
OFFLINE_BUNDLE_ITEMS = (
    'run.py',
    'requirements.txt',
    'Dockerfile',
    'README.md',
    'LICENSE',
    'agent',
    'static',
    'templates',
    'java',
)
OPTIONAL_OFFLINE_BUNDLE_ITEMS = ('vendor',)
OFFLINE_BUNDLE_SKIP = {'.DS_Store', '__pycache__', '.pytest_cache'}
PWA_HEAD_INJECTION = """
    <meta name=\"theme-color\" content=\"#1a1f16\" />
    <link rel=\"manifest\" href=\"/site.webmanifest\" />
    <script defer src=\"/static/pwa-register.js\"></script>
""".strip()

CSP_POLICY = "; ".join((
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://unpkg.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com https://cdnjs.cloudflare.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob: https: http:",
    "media-src 'self' blob: data: https: http:",
    "connect-src 'self' https: http:",
    "frame-src 'self' https: http:",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "upgrade-insecure-requests",
    "block-all-mixed-content",
))

SECURITY_HEADERS = {
    'Content-Security-Policy': CSP_POLICY,
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'X-XSS-Protection': '1; mode=block',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Permissions-Policy': 'accelerometer=(), autoplay=(self), browsing-topics=(), camera=(self), fullscreen=(self), geolocation=(), gyroscope=(), magnetometer=(), microphone=(self), payment=(), usb=()',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Resource-Policy': 'same-site',
}

TOPLINE_CACHE_TTL_SECONDS = 3600
TOPLINE_REQUEST_HEADERS = {
    'User-Agent': 'FutureCodeDelta/1.0 (+https://futurecodedelta.org)',
    'Accept': 'application/rss+xml, application/xml, text/xml, application/json, text/html;q=0.9, */*;q=0.8',
}
TOPLINE_SOURCE_SPECS = (
    {
        'key': 'alex-jones-show',
        'name': 'Real Alex Jones',
        'label': 'REAL ALEX JONES',
        'link': 'https://realalexjones.com/',
        'urls': (
            'https://realalexjones.com/collections/new-releases/products.json?limit=6',
            'https://realalexjones.com/collections/best-sellers/products.json?limit=6',
            'https://realalexjones.com/products.json?limit=12',
        ),
    },
    {
        'key': 'end-time-headlines',
        'name': 'End Time Headlines',
        'label': 'END TIME HEADLINES',
        'link': 'https://endtimeheadlines.org/',
        'urls': (
            'https://endtimeheadlines.org/feed/',
        ),
    },
)
HOURLY_SCRIPTURES = (
    {'ref': 'Psalm 27:1', 'text': 'The LORD is my light and my salvation; whom shall I fear?'},
    {'ref': 'Isaiah 41:10', 'text': 'Fear thou not; for I am with thee: be not dismayed; for I am thy God.'},
    {'ref': 'Joshua 1:9', 'text': 'Be strong and of a good courage; be not afraid, neither be thou dismayed.'},
    {'ref': 'Psalm 91:1', 'text': 'He that dwelleth in the secret place of the most High shall abide under the shadow of the Almighty.'},
    {'ref': 'Proverbs 3:5', 'text': 'Trust in the LORD with all thine heart; and lean not unto thine own understanding.'},
    {'ref': 'Matthew 24:6', 'text': 'Ye shall hear of wars and rumours of wars: see that ye be not troubled.'},
    {'ref': 'Luke 21:28', 'text': 'When these things begin to come to pass, then look up, and lift up your heads.'},
    {'ref': 'John 14:27', 'text': 'Peace I leave with you, my peace I give unto you: let not your heart be troubled.'},
    {'ref': 'Romans 8:31', 'text': 'If God be for us, who can be against us?'},
    {'ref': '2 Timothy 1:7', 'text': 'God hath not given us the spirit of fear; but of power, and of love, and of a sound mind.'},
    {'ref': 'Hebrews 13:6', 'text': 'The Lord is my helper, and I will not fear what man shall do unto me.'},
    {'ref': '1 Peter 5:7', 'text': 'Casting all your care upon him; for he careth for you.'},
)
TOPLINE_TAG_RE = re.compile(r'<[^>]+>')
TOPLINE_SPACE_RE = re.compile(r'\s+')
TOPLINE_SOURCE_CACHE = {}
TOPLINE_CACHE = {
    'timestamp': 0.0,
    'items': [],
}


def _clean_topline_text(value):
    if not value:
        return ''
    cleaned = html.unescape(TOPLINE_TAG_RE.sub(' ', str(value)))
    return TOPLINE_SPACE_RE.sub(' ', cleaned).strip()


def _parse_topline_rss_items(payload, source, limit=4):
    if '<rss' not in payload and '<feed' not in payload:
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    items = []
    channel = root.find('channel')
    entries = channel.findall('item') if channel is not None else root.findall('.//item')
    if not entries:
        entries = root.findall('{*}entry')

    for entry in entries:
        title = _clean_topline_text(entry.findtext('title') or entry.findtext('{*}title'))
        link = _clean_topline_text(entry.findtext('link') or entry.findtext('{*}link'))
        if not link:
            link_node = entry.find('link') or entry.find('{*}link')
            if link_node is not None:
                link = _clean_topline_text(link_node.get('href', ''))
        if not title or not link:
            continue
        items.append({
            'kind': 'headline',
            'source_key': source['key'],
            'label': source['label'],
            'source': source['name'],
            'text': title,
            'url': link,
        })
        if len(items) >= limit:
            break
    return items


def _parse_topline_json_items(payload, source, limit=4):
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError:
        return []

    if isinstance(rows, dict) and isinstance(rows.get('products'), list):
        items = []
        seen_titles = set()
        for row in rows['products']:
            if not isinstance(row, dict):
                continue
            title = _clean_topline_text(row.get('title'))
            handle = _clean_topline_text(row.get('handle'))
            if not title or not handle:
                continue
            normalized_title = title.casefold()
            if normalized_title in seen_titles:
                continue
            if 'free gift' in normalized_title or 'bogos.io' in normalized_title:
                continue
            items.append({
                'kind': 'headline',
                'source_key': source['key'],
                'label': source['label'],
                'source': source['name'],
                'text': title,
                'url': f"{source['link'].rstrip('/')}/products/{handle}",
            })
            seen_titles.add(normalized_title)
            if len(items) >= limit:
                break
        return items

    if not isinstance(rows, list):
        return []

    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get('title')
        if isinstance(title, dict):
            title = title.get('rendered')
        title = _clean_topline_text(title)
        link = _clean_topline_text(row.get('link') or source['link'])
        if not title or not link:
            continue
        items.append({
            'kind': 'headline',
            'source_key': source['key'],
            'label': source['label'],
            'source': source['name'],
            'text': title,
            'url': link,
        })
        if len(items) >= limit:
            break
    return items


def _fetch_topline_source(source, limit=4):
    for url in source['urls']:
        try:
            response = http_requests.get(
                url,
                headers=TOPLINE_REQUEST_HEADERS,
                timeout=12,
                allow_redirects=True,
            )
        except Exception:
            continue

        if not response.ok:
            continue

        payload = response.text.strip()
        if not payload:
            continue
        if 'Just a moment...' in payload or 'Enable JavaScript and cookies to continue' in payload:
            continue
        if '<title>Off Air</title>' in payload:
            continue

        content_type = (response.headers.get('content-type') or '').lower()
        items = []
        if 'json' in content_type or payload.startswith('['):
            items = _parse_topline_json_items(payload, source, limit=limit)
        if not items:
            items = _parse_topline_rss_items(payload, source, limit=limit)
        if items:
            return items

    return []


def _current_hourly_scripture(now=None):
    current_time = now or datetime.now(timezone.utc)
    verse = HOURLY_SCRIPTURES[current_time.hour % len(HOURLY_SCRIPTURES)]
    return {
        'kind': 'scripture',
        'source_key': 'scripture-intel',
        'label': 'HOURLY SCRIPTURE',
        'source': 'Scripture Intel',
        'reference': verse['ref'],
        'text': f"{verse['ref']} — {verse['text']}",
        'url': '/bible',
    }


def _merge_topline_batches(source_batches, scripture_item):
    items = []
    max_batch_size = max((len(batch) for batch in source_batches), default=0)

    for index in range(max_batch_size):
        for batch in source_batches:
            if index < len(batch):
                items.append(batch[index])

    items.append(scripture_item)

    return items


def _get_topline_items(force_refresh=False):
    now_ts = time.time()
    if (
        not force_refresh
        and TOPLINE_CACHE['items']
        and now_ts - TOPLINE_CACHE['timestamp'] < TOPLINE_CACHE_TTL_SECONDS
    ):
        return TOPLINE_CACHE['items']

    scripture_item = _current_hourly_scripture()
    source_batches = []
    for source in TOPLINE_SOURCE_SPECS:
        headlines = _fetch_topline_source(source, limit=4)
        if headlines:
            TOPLINE_SOURCE_CACHE[source['key']] = {
                'timestamp': now_ts,
                'items': headlines,
            }
        else:
            headlines = TOPLINE_SOURCE_CACHE.get(source['key'], {}).get('items', [])
        source_batches.append(headlines[:3])

    items = _merge_topline_batches(source_batches, scripture_item)

    if len(items) == 1:
        items.append({
            'kind': 'status',
            'source_key': 'intel-watch',
            'label': 'INTEL WATCH',
            'source': 'Delta Coding',
            'text': 'Headline sources are temporarily unreachable. Hourly scripture rotation remains active.',
            'url': '/bible',
        })

    TOPLINE_CACHE['timestamp'] = now_ts
    TOPLINE_CACHE['items'] = items
    return items

# In-memory store for shared code snippets (use DB in production)
_shared_snippets = {}


@app.route('/api/journal', methods=['GET'])
def api_journal():
    import sqlite3

    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute('SELECT entry, created FROM journal ORDER BY created DESC')
    rows = [{'entry': row[0], 'created': row[1]} for row in cur.fetchall()]
    conn.close()
    return jsonify({'ok': True, 'entries': rows})


@app.route('/api/journal/add', methods=['POST'])
def api_journal_add():
    admin_token = os.environ.get('ADMIN_TOKEN')
    provided = request.form.get('admin_token') or request.headers.get('X-Admin-Token')
    if admin_token and provided != admin_token:
        return jsonify({'ok': False, 'error': 'admin token required'}), 403

    entry = (request.form.get('entry') or '').strip()
    if not entry:
        return jsonify({'ok': False, 'error': 'Entry required'}), 400

    import sqlite3
    from datetime import datetime

    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute('INSERT INTO journal (entry, created) VALUES (?, ?)', (entry, datetime.utcnow().isoformat() + 'Z'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


import re as _re
_TOKEN_RE = _re.compile(r'^[0-9a-f\-]{8,72}$')


@app.route('/api/ping', methods=['POST'])
def api_ping():
    import sqlite3, time
    data = request.get_json(silent=True) or {}
    token = str(data.get('token', ''))[:72]
    if not _TOKEN_RE.match(token):
        return jsonify({'ok': False}), 400
    now = time.time()
    # resolve visitor IP (respect X-Forwarded-For from IONOS proxy)
    ip = (request.headers.get('X-Forwarded-For', '') or '').split(',')[0].strip()
    if not ip:
        ip = request.remote_addr or ''
    geo = _geo_lookup(ip)
    conn = sqlite3.connect(visitor_db_path)
    cur = conn.cursor()
    if geo:
        cur.execute(
            'INSERT INTO visitor_sessions(token,last_seen,lat,lon,country,city,flag) VALUES(?,?,?,?,?,?,?) '
            'ON CONFLICT(token) DO UPDATE SET last_seen=excluded.last_seen,'
            'lat=excluded.lat,lon=excluded.lon,country=excluded.country,city=excluded.city,flag=excluded.flag',
            (token, now, geo['lat'], geo['lon'], geo['country'], geo['city'], geo['flag']),
        )
    else:
        cur.execute(
            'INSERT INTO visitor_sessions(token,last_seen) VALUES(?,?) '
            'ON CONFLICT(token) DO UPDATE SET last_seen=excluded.last_seen',
            (token, now),
        )
    # prune sessions older than 10 minutes
    cur.execute('DELETE FROM visitor_sessions WHERE last_seen < ?', (now - 600,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/online', methods=['GET'])
def api_online():
    import sqlite3, time
    window = 300  # 5-minute active window
    now = time.time()
    conn = sqlite3.connect(visitor_db_path)
    cur = conn.cursor()
    cur.execute(
        'SELECT COUNT(*), lat, lon, country, city, flag FROM visitor_sessions '
        'WHERE last_seen >= ? GROUP BY lat, lon, country, city, flag',
        (now - window,)
    )
    rows = cur.fetchall()
    conn.close()
    count = sum(r[0] for r in rows)
    locations = [
        {'n': r[0], 'lat': r[1], 'lon': r[2], 'country': r[3] or '', 'city': r[4] or '', 'flag': r[5] or ''}
        for r in rows if r[1] is not None
    ]
    return jsonify({'count': count, 'locations': locations})


@app.route('/api/topline-intel', methods=['GET'])
def api_topline_intel():
    items = _get_topline_items(force_refresh=request.args.get('refresh') == '1')
    return jsonify({
        'ok': True,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'items': items,
    })


@app.route('/live')
def live_map():
    return render_template('live_map.html')


def _which(candidates):
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return candidates[0]


def _generated_root():
    configured_root = os.environ.get('DELTA_GENERATED_DIR')
    base_dir = configured_root or os.path.join(APP_ROOT, 'generated')
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def _write_bundle_path(zf, source_path, arcname_root):
        if os.path.isfile(source_path):
                zf.write(source_path, arcname_root)
                return

        for root, dirs, files in os.walk(source_path):
                dirs[:] = [name for name in sorted(dirs) if name not in OFFLINE_BUNDLE_SKIP]
                for file_name in sorted(files):
                        if file_name in OFFLINE_BUNDLE_SKIP:
                                continue
                        abs_path = os.path.join(root, file_name)
                        rel_path = os.path.relpath(abs_path, source_path)
                        zf.write(abs_path, os.path.join(arcname_root, rel_path))


def _offline_bundle_readme(has_vendor):
        vendor_note = (
                'Vendored Python packages are included in this bundle, so the site can start without downloading dependencies.\n'
                'The launcher scripts automatically place the bundled vendor tree on PYTHONPATH.\n'
                if has_vendor else
                'Vendored Python packages were not present when this bundle was created.\n'
                'If you truly need first-run offline support, create the bundle from a deployment that contains a vendor directory.\n'
        )
        return (
                'DELTA CODING OFFLINE BUNDLE\n'
                '==========================\n\n'
            'This archive contains the runnable Delta Coding website packaged so it can be installed on a PC like an offline app.\n\n'
                f'{vendor_note}\n'
            'Install as an offline app:\n'
            '1. Extract the zip.\n'
            '2. Run install_delta.command on macOS / Linux or install_delta.bat on Windows.\n'
            '3. The installer copies the bundle to a stable app folder and creates a desktop launcher.\n'
            '4. Launch Delta Coding from that desktop shortcut whenever you want the offline app window.\n\n'
                'Quick start (macOS / Linux):\n'
                '1. Extract the zip.\n'
                '2. Open a terminal in the extracted folder.\n'
                '3. If needed, make the launcher executable: chmod +x start_delta.command\n'
                '4. Run ./start_delta.command\n\n'
                'Quick start (Windows):\n'
                '1. Extract the zip.\n'
                '2. Double-click start_delta.bat or run it from Command Prompt.\n\n'
                'Manual start:\n'
                '1. Open a terminal in the extracted folder.\n'
                '2. If vendor/ exists, run with PYTHONPATH=vendor python run.py\n'
                '3. Otherwise install requirements first, then run python run.py\n\n'
                'Offline behavior notes:\n'
                '- The local site shell, templates, editors, and bundled pages run locally.\n'
                '- The launcher opens the site in an app-style window when Chrome, Edge, or Chromium is available, and falls back to the default browser otherwise.\n'
                '- Internet-backed features such as remote AI calls, live CVE lookups, public map tiles, geocoding, street imagery, and external intelligence feeds still depend on network availability unless you replace them with local data sources.\n'
                '- The map and recon UI will still open offline, but external map and imagery providers may show limited or cached data only.\n'
        )


def _offline_bundle_launcher_sh():
        return """#!/bin/sh
set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
if [ -z "$PYTHON_BIN" ]; then
    echo "Python 3 is required to launch Delta Coding offline."
    exit 1
fi

if [ -d "vendor" ]; then
    export PYTHONPATH="$PWD/vendor${PYTHONPATH:+:$PYTHONPATH}"
else
    echo "No bundled vendor tree found. Installing requirements requires internet access."
    "$PYTHON_BIN" -m pip install -r requirements.txt
fi

export PORT="${PORT:-5080}"
export OPEN_APP=1
"$PYTHON_BIN" run.py
"""


def _offline_bundle_launcher_bat():
    return r"""@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_BIN=python"
where py >nul 2>nul && set "PYTHON_BIN=py -3"

if exist vendor (
    set "PYTHONPATH=%CD%\vendor;%PYTHONPATH%"
) else (
    echo No bundled vendor tree found. Installing requirements requires internet access.
    %PYTHON_BIN% -m pip install -r requirements.txt
)

if "%PORT%"=="" set "PORT=5080"
set OPEN_APP=1
%PYTHON_BIN% run.py
endlocal
"""


def _offline_bundle_installer_sh():
    return """#!/bin/sh
set -e
cd "$(dirname "$0")"

INSTALL_BASE="${HOME}/Applications"
INSTALL_ROOT="${INSTALL_BASE}/Delta Coding Offline"
DESKTOP_LAUNCHER="${HOME}/Desktop/Delta Coding Offline.command"

mkdir -p "$INSTALL_BASE"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete ./ "$INSTALL_ROOT/"
else
    rm -rf "$INSTALL_ROOT"
    mkdir -p "$INSTALL_ROOT"
    cp -R ./* "$INSTALL_ROOT/"
fi

chmod +x "$INSTALL_ROOT/start_delta.command"
cat > "$DESKTOP_LAUNCHER" <<'EOF'
#!/bin/sh
exec "$HOME/Applications/Delta Coding Offline/start_delta.command"
EOF
chmod +x "$DESKTOP_LAUNCHER"

echo "Installed Delta Coding Offline to: $INSTALL_ROOT"
echo "Desktop launcher created at: $DESKTOP_LAUNCHER"
exec "$INSTALL_ROOT/start_delta.command"
"""


def _offline_bundle_installer_bat():
    return r"""@echo off
setlocal
cd /d "%~dp0"

set "INSTALL_DIR=%LOCALAPPDATA%\DeltaCodingOffline"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

robocopy "%CD%" "%INSTALL_DIR%" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Install failed while copying files.
    exit /b 1
)

set "DESKTOP_LAUNCHER=%USERPROFILE%\Desktop\Delta Coding Offline.bat"
(
    echo @echo off
    echo call "%INSTALL_DIR%\start_delta.bat"
) > "%DESKTOP_LAUNCHER%"

set "START_MENU_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
if not exist "%START_MENU_DIR%" mkdir "%START_MENU_DIR%"
set "START_MENU_LAUNCHER=%START_MENU_DIR%\Delta Coding Offline.bat"
(
    echo @echo off
    echo call "%INSTALL_DIR%\start_delta.bat"
) > "%START_MENU_LAUNCHER%"

echo Installed Delta Coding Offline to %INSTALL_DIR%
echo Desktop launcher created at %DESKTOP_LAUNCHER%
start "" "%INSTALL_DIR%\start_delta.bat"
"""


def _launch_browser_app_mode(url):
    quiet = {
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
    }

    if sys.platform == 'darwin':
        for app_name in ('Google Chrome', 'Microsoft Edge', 'Chromium'):
            try:
                subprocess.Popen(['open', '-na', app_name, '--args', f'--app={url}'], **quiet)
                return True
            except Exception:
                continue
        return False

    if os.name == 'nt':
        candidates = []
        for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LocalAppData')):
            if not base:
                continue
            candidates.extend((
                os.path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                os.path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                os.path.join(base, 'Chromium', 'Application', 'chromium.exe'),
            ))
        candidates.extend(filter(None, (
            shutil.which('msedge'),
            shutil.which('chrome'),
            shutil.which('chrome.exe'),
            shutil.which('chromium'),
            shutil.which('chromium.exe'),
        )))

        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if not os.path.exists(candidate):
                continue
            try:
                subprocess.Popen([candidate, f'--app={url}'], **quiet)
                return True
            except Exception:
                continue
        return False

    for browser_name in ('microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser'):
        executable = shutil.which(browser_name)
        if not executable:
            continue
        try:
            subprocess.Popen([executable, f'--app={url}'], **quiet)
            return True
        except Exception:
            continue
    return False


def _open_local_client(port):
    url = f'http://127.0.0.1:{port}'
    if os.environ.get('OPEN_APP', '0') == '1' and _launch_browser_app_mode(url):
        return

    import webbrowser
    webbrowser.open(url)


@app.after_request
def apply_security_headers(response):
    for header_name, header_value in SECURITY_HEADERS.items():
        response.headers[header_name] = header_value
    if request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'

    if response.mimetype == 'text/html' and response.status_code == 200 and not response.direct_passthrough:
        try:
            html = response.get_data(as_text=True)
        except Exception:
            return response

        if '/site.webmanifest' not in html and '</head>' in html:
            html = html.replace('</head>', f'    {PWA_HEAD_INJECTION}\n  </head>', 1)
            response.set_data(html)
            charset = response.mimetype_params.get('charset', 'utf-8')
            response.content_length = len(html.encode(charset))
    return response

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/healthz')
def healthz():
    return jsonify({'ok': True, 'service': 'delta-coding'})


@app.route('/sw.js')
def service_worker():
    response = send_file(os.path.join(APP_ROOT, 'static', 'sw.js'), mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/site.webmanifest')
def site_manifest():
    response = send_file(os.path.join(APP_ROOT, 'static', 'site.webmanifest'), mimetype='application/manifest+json')
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/robots.txt')
def robots_txt():
    response = send_file(os.path.join(APP_ROOT, 'static', 'robots.txt'), mimetype='text/plain')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@app.route('/sitemap.xml')
def sitemap_xml():
    response = send_file(os.path.join(APP_ROOT, 'static', 'sitemap.xml'), mimetype='application/xml')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@app.route('/BingSiteAuth.xml')
def bing_site_auth():
    response = send_file(os.path.join(APP_ROOT, 'static', 'BingSiteAuth.xml'), mimetype='application/xml')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

@app.route('/run', methods=['POST'])
def run_code():
    data = request.get_json(force=True)
    language = data.get('language', 'python')
    code = data.get('code', '')
    run_id = uuid.uuid4().hex[:8]
    tempdir = tempfile.mkdtemp(prefix=f'delta_{run_id}_')
    try:
        if language == 'python':
            script_path = os.path.join(tempdir, 'script.py')
            with open(script_path, 'w') as f:
                f.write(code)
            py = _which(['python3', 'python'])
            try:
                proc = subprocess.run([py, script_path], capture_output=True, text=True, cwd=tempdir, timeout=5)
                return jsonify({'stdout': proc.stdout, 'stderr': proc.stderr, 'returncode': proc.returncode})
            except subprocess.TimeoutExpired:
                return jsonify({'error': 'Execution timed out'}), 504

        elif language == 'java':
            java_file = os.path.join(tempdir, 'Main.java')
            with open(java_file, 'w') as f:
                f.write(code)
            javac = _which(['javac'])
            java = _which(['java'])
            try:
                compile_proc = subprocess.run([javac, 'Main.java'], capture_output=True, text=True, cwd=tempdir, timeout=10)
            except subprocess.TimeoutExpired:
                return jsonify({'error': 'Compilation timed out'}), 504
            if compile_proc.returncode != 0:
                return jsonify({'compile_error': compile_proc.stderr})
            try:
                run_proc = subprocess.run([java, '-cp', '.', 'Main'], capture_output=True, text=True, cwd=tempdir, timeout=5)
                return jsonify({'stdout': run_proc.stdout, 'stderr': run_proc.stderr, 'returncode': run_proc.returncode})
            except subprocess.TimeoutExpired:
                return jsonify({'error': 'Execution timed out'}), 504

        elif language == 'html':
            css = data.get('css', '')
            js = data.get('js', '')
            # Inject CSS and JS into the HTML if provided separately
            combined = code
            if css:
                if '</head>' in combined:
                    combined = combined.replace('</head>', f'<style>{css}</style>\n</head>')
                else:
                    combined = f'<style>{css}</style>\n' + combined
            if js:
                if '</body>' in combined:
                    combined = combined.replace('</body>', f'<script>{js}</script>\n</body>')
                else:
                    combined = combined + f'\n<script>{js}</script>'
            return jsonify({'html': combined})

        elif language == 'css':
            # Preview CSS with a sample HTML structure
            html_code = data.get('html', '')
            if not html_code:
                html_code = '<!DOCTYPE html><html><head></head><body>\n<h1>Heading 1</h1>\n<h2>Heading 2</h2>\n<p>Paragraph text for preview.</p>\n<a href="#">Link</a>\n<button>Button</button>\n<ul><li>Item 1</li><li>Item 2</li><li>Item 3</li></ul>\n<div class="box">Box div</div>\n</body></html>'
            if '</head>' in html_code:
                html_code = html_code.replace('</head>', f'<style>{code}</style>\n</head>')
            else:
                html_code = f'<style>{code}</style>\n' + html_code
            return jsonify({'html': html_code})

        else:
            return jsonify({'error': 'Unsupported language'}), 400

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
    finally:
        try:
            shutil.rmtree(tempdir)
        except Exception:
            pass


@app.route('/agent', methods=['POST'])
def agent_route():
    data = request.get_json(force=True)
    instruction = data.get('instruction', '')
    project_name = data.get('project_name')
    execute = bool(data.get('execute', False))
    confirm = bool(data.get('confirm', False))
    use_llm = bool(data.get('use_llm', False))
    llm_model = data.get('llm_model')

    # Safety: require explicit confirmation to execute generated code
    if execute and not confirm:
        return jsonify({'error': 'Execution requested but not confirmed. Set confirm=true to execute.'}), 400

    try:
        # Optionally expand instruction using LLM
        final_instruction = instruction
        if use_llm:
            llm_resp = generate_with_llm(instruction, model=llm_model)
            if llm_resp.get('ok') and llm_resp.get('text'):
                final_instruction = llm_resp['text']

        base_dir = _generated_root()
        resp = run_instruction(final_instruction, project_name=project_name, execute=execute, base_dir=base_dir)
        # include llm meta if present
        if use_llm:
            resp['_llm_raw'] = llm_resp
        return jsonify(resp)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/projects', methods=['GET'])
def list_projects():
    base_dir = _generated_root()
    if not os.path.isdir(base_dir):
        return jsonify({'projects': []})
    projects = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
    return jsonify({'projects': projects})


@app.route('/api/projects/delete', methods=['POST'])
def delete_projects():
    """Delete one or more generated projects."""
    data = request.get_json(force=True)
    names = data.get('projects', [])
    if not names:
        return jsonify({'ok': False, 'error': 'No projects specified'}), 400
    base_dir = _generated_root()
    deleted = []
    errors = []
    for name in names:
        # Prevent path traversal
        safe_name = os.path.basename(name)
        project_dir = os.path.join(base_dir, safe_name)
        if os.path.isdir(project_dir):
            shutil.rmtree(project_dir)
            deleted.append(safe_name)
        else:
            errors.append(f'{safe_name} not found')
    return jsonify({'ok': True, 'deleted': deleted, 'errors': errors})


@app.route('/download/<project_name>', methods=['GET'])
def download_project(project_name):
    base_dir = _generated_root()
    project_dir = os.path.join(base_dir, project_name)
    if not os.path.isdir(project_dir):
        return jsonify({'error': 'project not found'}), 404

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                abs_path = os.path.join(root, f)
                arcname = os.path.relpath(abs_path, project_dir)
                zf.write(abs_path, arcname)
    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True, download_name=f'{project_name}.zip')


@app.route('/api/projects/download', methods=['POST'])
def download_multiple():
    """Download multiple projects as a single zip."""
    data = request.get_json(force=True)
    names = data.get('projects', [])
    if not names:
        return jsonify({'ok': False, 'error': 'No projects specified'}), 400
    base_dir = _generated_root()
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            safe_name = os.path.basename(name)
            project_dir = os.path.join(base_dir, safe_name)
            if os.path.isdir(project_dir):
                for root, dirs, files in os.walk(project_dir):
                    for f in files:
                        abs_path = os.path.join(root, f)
                        arcname = os.path.join(safe_name, os.path.relpath(abs_path, project_dir))
                        zf.write(abs_path, arcname)
    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True, download_name='delta_projects.zip')


@app.route('/download/offline-site', methods=['GET'])
def download_offline_site():
    """Download the runnable site as an offline bundle zip."""
    bundle_items = list(OFFLINE_BUNDLE_ITEMS)
    optional_items = [name for name in OPTIONAL_OFFLINE_BUNDLE_ITEMS if os.path.exists(os.path.join(APP_ROOT, name))]
    has_vendor = 'vendor' in optional_items

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for relative_path in (*bundle_items, *optional_items):
            abs_path = os.path.join(APP_ROOT, relative_path)
            if os.path.exists(abs_path):
                _write_bundle_path(zf, abs_path, os.path.join(OFFLINE_BUNDLE_ROOT, relative_path))

        zf.writestr(f'{OFFLINE_BUNDLE_ROOT}/README_OFFLINE.txt', _offline_bundle_readme(has_vendor))
        zf.writestr(f'{OFFLINE_BUNDLE_ROOT}/start_delta.command', _offline_bundle_launcher_sh())
        zf.writestr(f'{OFFLINE_BUNDLE_ROOT}/start_delta.bat', _offline_bundle_launcher_bat())
        zf.writestr(f'{OFFLINE_BUNDLE_ROOT}/install_delta.command', _offline_bundle_installer_sh())
        zf.writestr(f'{OFFLINE_BUNDLE_ROOT}/install_delta.bat', _offline_bundle_installer_bat())

    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True, download_name='delta_coding_offline_app.zip')


# ── Cyber Security page ──────────────────────────────────────────────

NVD_API = 'https://services.nvd.nist.gov/rest/json/cves/2.0'

# ── AI providers (all free) ──────────────────────────────────────────
POLLINATIONS_URL = 'https://text.pollinations.ai/openai'
HF_ROUTER_URL = 'https://router.huggingface.co/v1/chat/completions'
FREE_AI_MODELS = [
    {'id': 'openai', 'name': 'GPT-OSS 20B', 'desc': 'Key-free reasoning model served through Delta backend', 'provider': 'pollinations'},
    {'id': 'openai-fast', 'name': 'GPT-OSS 20B Fast', 'desc': 'Key-free faster mode for quicker responses', 'provider': 'pollinations'},
]
OPTIONAL_HF_MODELS = [
    {'id': 'mistralai/Mistral-7B-Instruct-v0.3', 'name': 'Mistral 7B (HF)', 'desc': 'Available when HF_API_TOKEN is configured', 'provider': 'huggingface'},
    {'id': 'HuggingFaceH4/zephyr-7b-beta', 'name': 'Zephyr 7B (HF)', 'desc': 'Available when HF_API_TOKEN is configured', 'provider': 'huggingface'},
    {'id': 'microsoft/Phi-3-mini-4k-instruct', 'name': 'Phi-3 Mini (HF)', 'desc': 'Available when HF_API_TOKEN is configured', 'provider': 'huggingface'},
]


def _available_ai_models():
    models = list(FREE_AI_MODELS)
    if os.environ.get('HF_API_TOKEN'):
        models.extend(OPTIONAL_HF_MODELS)
    return models


@app.route('/cyber')
def cyber():
    return render_template('cyber.html')


@app.route('/api/cves')
def api_cves():
    """Fetch recent CVEs from the NIST NVD (free, no key)."""
    limit = min(int(request.args.get('limit', 20)), 50)
    try:
        resp = http_requests.get(NVD_API, params={
            'resultsPerPage': limit,
            'startIndex': 0,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        cves = []
        for item in data.get('vulnerabilities', []):
            c = item.get('cve', {})
            desc_list = c.get('descriptions', [])
            desc = next((d['value'] for d in desc_list if d.get('lang') == 'en'), '')
            metrics = c.get('metrics', {})
            score = None
            severity = None
            for v in ('cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
                m = metrics.get(v, [])
                if m:
                    cvss = m[0].get('cvssData', {})
                    score = cvss.get('baseScore')
                    severity = cvss.get('baseSeverity') or m[0].get('baseSeverity')
                    break
            cves.append({
                'id': c.get('id'),
                'published': c.get('published', '')[:10],
                'description': desc[:300],
                'score': score,
                'severity': severity,
                'url': f"https://nvd.nist.gov/vuln/detail/{c.get('id')}",
            })
        return jsonify({'ok': True, 'total': data.get('totalResults'), 'cves': cves})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502


@app.route('/api/cves/search')
def api_cve_search():
    """Search CVEs by keyword via NVD."""
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        return jsonify({'ok': False, 'error': 'keyword required'}), 400
    limit = min(int(request.args.get('limit', 20)), 50)
    try:
        resp = http_requests.get(NVD_API, params={
            'keywordSearch': keyword,
            'resultsPerPage': limit,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        cves = []
        for item in data.get('vulnerabilities', []):
            c = item.get('cve', {})
            desc_list = c.get('descriptions', [])
            desc = next((d['value'] for d in desc_list if d.get('lang') == 'en'), '')
            metrics = c.get('metrics', {})
            score = None
            severity = None
            for v in ('cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
                m = metrics.get(v, [])
                if m:
                    cvss = m[0].get('cvssData', {})
                    score = cvss.get('baseScore')
                    severity = cvss.get('baseSeverity') or m[0].get('baseSeverity')
                    break
            cves.append({
                'id': c.get('id'),
                'published': c.get('published', '')[:10],
                'description': desc[:300],
                'score': score,
                'severity': severity,
                'url': f"https://nvd.nist.gov/vuln/detail/{c.get('id')}",
            })
        return jsonify({'ok': True, 'total': data.get('totalResults'), 'cves': cves})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502


# ── Free AI Agent ─────────────────────────────────────────────────────

@app.route('/ai')
def ai_page():
    return render_template('ai.html')


@app.route('/api/ai', methods=['POST'])
def api_ai():
    """Chat with a free AI model. Uses Pollinations.ai (no auth) or HF Router (free token)."""
    data = request.get_json(force=True)
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'ok': False, 'error': 'prompt is required'}), 400

    model = data.get('model', 'openai')
    provider = data.get('provider', 'pollinations')
    max_tokens = int(data.get('max_tokens', 512))
    temperature = float(data.get('temperature', 0.7))

    messages = [
        {'role': 'system', 'content': 'You are Delta AI, a tactical coding assistant. Provide clear, precise answers with code examples when relevant.'},
        {'role': 'user', 'content': prompt},
    ]

    if provider == 'huggingface':
        return _call_hf(model, messages, max_tokens, temperature)
    return _call_pollinations(model, messages, max_tokens, temperature)


def _pollinations_chat(model, messages, max_tokens, temperature):
    resp = http_requests.post(POLLINATIONS_URL, json={
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
    }, timeout=45)
    resp.raise_for_status()
    result = resp.json()
    text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
    used_model = result.get('model', model)
    return {'text': text, 'model': used_model}


def _call_pollinations(model, messages, max_tokens, temperature):
    """Call Pollinations.ai — completely free, no API key needed."""
    try:
        result = _pollinations_chat(model, messages, max_tokens, temperature)
        text = result['text']
        used_model = result['model']
        return jsonify({'ok': True, 'text': text, 'model': used_model, 'provider': 'pollinations'})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502


def _call_hf(model, messages, max_tokens, temperature):
    """Call HF Router — requires free HF_API_TOKEN."""
    hf_token = os.environ.get('HF_API_TOKEN', '')
    if not hf_token:
        return jsonify({'ok': False, 'error': 'HF_API_TOKEN not set. Get a free token at huggingface.co/settings/tokens'}), 400
    try:
        resp = http_requests.post(HF_ROUTER_URL, json={
            'model': model,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'stream': False,
        }, headers={
            'Authorization': f'Bearer {hf_token}',
            'Content-Type': 'application/json',
        }, timeout=45)
        if resp.status_code == 503:
            body = resp.json()
            wait = body.get('estimated_time', 20)
            return jsonify({'ok': False, 'error': f'Model loading, retry in ~{int(wait)}s', 'loading': True}), 503
        resp.raise_for_status()
        result = resp.json()
        text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
        return jsonify({'ok': True, 'text': text, 'model': model, 'provider': 'huggingface'})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502


@app.route('/api/ai/models')
def api_ai_models():
    """Return available free models grouped by provider."""
    return jsonify({'ok': True, 'models': _available_ai_models()})


@app.route('/api/truth/summary', methods=['POST'])
def api_truth_summary():
    """Generate a Truth-page summary through the site backend using a key-free model."""
    data = request.get_json(force=True)
    query = (data.get('query') or '').strip()
    if not query:
        return jsonify({'ok': False, 'error': 'query is required'}), 400

    messages = [
        {
            'role': 'system',
            'content': 'You are a biblical scholar and historian. Answer with 3-4 factual, reverent paragraphs grounded in scripture references and public historical evidence.',
        },
        {'role': 'user', 'content': query},
    ]

    try:
        result = _pollinations_chat('openai-fast', messages, 500, 0.4)
        return jsonify({'ok': True, 'text': result['text'], 'model': result['model'], 'provider': 'pollinations'})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502


# ── Code Sharing ──────────────────────────────────────────────────────

@app.route('/api/share', methods=['POST'])
def share_code():
    """Save a code snippet and return a share ID."""
    data = request.get_json(force=True)
    code = data.get('code', '')
    language = data.get('language', 'python')
    css = data.get('css', '')
    js = data.get('js', '')
    if not any(part.strip() for part in (code, css, js) if isinstance(part, str)):
        return jsonify({'ok': False, 'error': 'No code to share'}), 400

    snippet = {'code': code, 'language': language}
    if language == 'web' or css:
        snippet['css'] = css
    if language == 'web' or js:
        snippet['js'] = js

    share_seed = json.dumps(snippet, sort_keys=True)
    share_id = hashlib.sha256(share_seed.encode()).hexdigest()[:10]
    _shared_snippets[share_id] = snippet
    return jsonify({'ok': True, 'id': share_id, 'share_url': f'/share/{share_id}'})


@app.route('/api/share/<share_id>')
def get_shared(share_id):
    snippet = _shared_snippets.get(share_id)
    if not snippet:
        return jsonify({'ok': False, 'error': 'Snippet not found'}), 404
    return jsonify({'ok': True, **snippet})


@app.route('/share/<share_id>')
def share_page(share_id):
    return render_template('index.html')


# ── Cyber Security Tools ─────────────────────────────────────────────

@app.route('/api/cyber/encrypt', methods=['POST'])
def encrypt_text():
    """Encrypt/hash text using various algorithms (all local, no data sent externally)."""
    data = request.get_json(force=True)
    text = data.get('text', '')
    algo = data.get('algorithm', 'sha256')
    if not text:
        return jsonify({'ok': False, 'error': 'text required'}), 400

    results = {}
    text_bytes = text.encode('utf-8')

    if algo in ('all', 'sha256'):
        results['sha256'] = hashlib.sha256(text_bytes).hexdigest()
    if algo in ('all', 'sha512'):
        results['sha512'] = hashlib.sha512(text_bytes).hexdigest()
    if algo in ('all', 'md5'):
        results['md5'] = hashlib.md5(text_bytes).hexdigest()
    if algo in ('all', 'sha1'):
        results['sha1'] = hashlib.sha1(text_bytes).hexdigest()
    if algo in ('all', 'base64'):
        results['base64_encode'] = base64.b64encode(text_bytes).decode()
    if algo in ('all', 'base64_decode'):
        try:
            results['base64_decode'] = base64.b64decode(text_bytes).decode()
        except Exception:
            results['base64_decode'] = '(invalid base64 input)'
    if not results:
        return jsonify({'ok': False, 'error': f'Unknown algorithm: {algo}'}), 400
    return jsonify({'ok': True, 'results': results, 'algorithm': algo})


@app.route('/api/cyber/headers')
def check_headers():
    """Check security headers of any public URL."""
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'ok': False, 'error': 'url parameter required'}), 400
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    security_headers = [
        'Strict-Transport-Security',
        'Content-Security-Policy',
        'X-Content-Type-Options',
        'X-Frame-Options',
        'X-XSS-Protection',
        'Referrer-Policy',
        'Permissions-Policy',
        'Cross-Origin-Opener-Policy',
        'Cross-Origin-Resource-Policy',
    ]

    try:
        resp = http_requests.get(url, timeout=10, allow_redirects=True,
                                  headers={'User-Agent': 'DeltaCoding-SecurityScanner/1.0'})
        found = {}
        missing = []
        for h in security_headers:
            val = resp.headers.get(h)
            if val:
                found[h] = val
            else:
                missing.append(h)

        grade = 'A' if len(missing) == 0 else 'B' if len(missing) <= 2 else 'C' if len(missing) <= 4 else 'D' if len(missing) <= 6 else 'F'
        return jsonify({
            'ok': True,
            'url': url,
            'status_code': resp.status_code,
            'headers_found': found,
            'headers_missing': missing,
            'grade': grade,
            'total_checked': len(security_headers),
            'ssl': url.startswith('https://'),
        })
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502


@app.route('/api/cyber/resources')
def cyber_resources():
    """Return curated list of free cybersecurity tools and platforms."""
    resources = [
        {'name': 'OWASP Top 10', 'url': 'https://owasp.org/www-project-top-ten/', 'category': 'Learning', 'desc': 'Top 10 web application security risks'},
        {'name': 'Have I Been Pwned', 'url': 'https://haveibeenpwned.com/', 'category': 'Breach Check', 'desc': 'Check if your email has been in a data breach'},
        {'name': 'SSL Labs', 'url': 'https://www.ssllabs.com/ssltest/', 'category': 'SSL Scanner', 'desc': 'Deep analysis of SSL/TLS configuration'},
        {'name': 'SecurityHeaders.com', 'url': 'https://securityheaders.com/', 'category': 'Headers', 'desc': 'Analyze HTTP response headers'},
        {'name': 'VirusTotal', 'url': 'https://www.virustotal.com/', 'category': 'Malware', 'desc': 'Scan files and URLs for malware'},
        {'name': 'Shodan', 'url': 'https://www.shodan.io/', 'category': 'Recon', 'desc': 'Search engine for Internet-connected devices'},
        {'name': 'CyberChef', 'url': 'https://gchq.github.io/CyberChef/', 'category': 'Encryption', 'desc': 'Web app for encryption, encoding, compression'},
        {'name': 'Exploit Database', 'url': 'https://www.exploit-db.com/', 'category': 'Exploits', 'desc': 'Archive of public exploits and software'},
        {'name': 'NIST NVD', 'url': 'https://nvd.nist.gov/', 'category': 'CVE', 'desc': 'National Vulnerability Database'},
        {'name': 'CTFtime', 'url': 'https://ctftime.org/', 'category': 'Practice', 'desc': 'Capture The Flag competitions and writeups'},
        {'name': 'TryHackMe', 'url': 'https://tryhackme.com/', 'category': 'Practice', 'desc': 'Free hands-on cybersecurity training'},
        {'name': 'HackTheBox', 'url': 'https://www.hackthebox.com/', 'category': 'Practice', 'desc': 'Penetration testing labs and challenges'},
        {'name': 'CRT.sh', 'url': 'https://crt.sh/', 'category': 'Certificates', 'desc': 'Certificate transparency log search'},
        {'name': 'DNSDumpster', 'url': 'https://dnsdumpster.com/', 'category': 'Recon', 'desc': 'Free domain research and DNS recon'},
        {'name': "Let's Encrypt", 'url': 'https://letsencrypt.org/', 'category': 'SSL', 'desc': 'Free SSL/TLS certificates'},
        {'name': 'Mozilla Observatory', 'url': 'https://observatory.mozilla.org/', 'category': 'Scanner', 'desc': 'Free website security scanner'},
    ]
    return jsonify({'ok': True, 'resources': resources})


# ── Tactical Map ──────────────────────────────────────────────────────

@app.route('/map')
def map_page():
    return render_template('map.html')


@app.route('/survival')
def survival_page():
    return render_template('survival.html')


@app.route('/weapons')
def weapons_page():
    return render_template('weapons.html')


@app.route('/weapons/armory')
def weapons_armory_page():
    return render_template('weapons_armory.html')


def _fallback_mechanics_items():
    return [
        {
            'id': 'demo1',
            'brand': 'Toyota',
            'model': 'Corolla',
            'year': '1996',
            'title': 'Toyota Corolla 1996 Blueprint',
            'description': 'Blueprint and parts for Toyota Corolla 1996.',
            'license': 'Repo asset',
            'pdf_path': None,
            'image_paths': ['/static/manuals/images/demo1/blueprint.png'],
        },
        {
            'id': 'demo2',
            'brand': 'Ford',
            'model': 'F-150',
            'year': '2015',
            'title': 'Ford F-150 2015 Blueprint',
            'description': 'Blueprint and parts for Ford F-150 2015.',
            'license': 'Repo asset',
            'pdf_path': None,
            'image_paths': ['/static/manuals/images/demo2/blueprint.png'],
        },
        {
            'id': 'demo3',
            'brand': 'Honda',
            'model': 'Civic',
            'year': '2010',
            'title': 'Honda Civic 2010 Blueprint',
            'description': 'Blueprint and parts for Honda Civic 2010.',
            'license': 'Repo asset',
            'pdf_path': None,
            'image_paths': ['/static/manuals/images/demo3/blueprint.png'],
        },
    ]


def _load_mechanics_catalog():
    try:
        items = manual_store.search_manuals()
        if not items:
            raise ValueError('manual store returned no mechanics entries')
        brands = manual_store.list_brands()
        models_by_brand = {brand: manual_store.list_models(brand) for brand in brands}
        years_by_brand_model = {}
        for brand in brands:
            for model in models_by_brand.get(brand, []):
                years_by_brand_model[(brand, model)] = manual_store.list_years(brand, model)
        return items, brands, models_by_brand, years_by_brand_model
    except Exception:
        items = _fallback_mechanics_items()
        brands = sorted({item['brand'] for item in items})
        models_by_brand = {}
        years_by_brand_model = {}
        for brand in brands:
            brand_items = [item for item in items if item['brand'] == brand]
            models = sorted({item['model'] for item in brand_items})
            models_by_brand[brand] = models
            for model in models:
                years_by_brand_model[(brand, model)] = sorted({item['year'] for item in brand_items if item['model'] == model})
        return items, brands, models_by_brand, years_by_brand_model


@app.route('/mechanics')
def mechanics_page():
    items, brands, models_by_brand, years_by_brand_model = _load_mechanics_catalog()

    indexed_vehicles = 0
    featured_brands = []
    for brand in brands:
        models = models_by_brand.get(brand, [])
        years_count = 0
        for model in models:
            years_count += len(years_by_brand_model.get((brand, model), []))
        indexed_vehicles += years_count
        featured_brands.append({
            'brand': brand,
            'models': len(models),
            'years': years_count,
        })

    featured_brands.sort(key=lambda entry: (-entry['years'], -entry['models'], entry['brand']))

    blueprint_entries = []
    recent_entries = []
    for item in items:
        brand = item.get('brand') or 'Unknown'
        model = item.get('model') or 'Platform'
        year = item.get('year') or 'Field'
        href = f'/mechanics/{brand}/{model}/{year}'
        images = item.get('image_paths') or []

        if images:
            blueprint_entries.append({
                'title': item.get('title') or f'{brand} {model} {year}',
                'brand': brand,
                'model': model,
                'year': year,
                'thumb': images[0],
                'href': href,
            })

        recent_entries.append({
            'title': item.get('title') or f'{brand} {model} {year}',
            'brand': brand,
            'model': model,
            'year': year,
            'description': item.get('description') or 'Indexed service entry.',
            'image_count': len(images),
            'has_pdf': bool(item.get('pdf_path')),
            'href': href,
        })

    mechanics_stats = {
        'brands': len(brands),
        'models': sum(len(models) for models in models_by_brand.values()),
        'vehicles': indexed_vehicles,
        'entries': len(items),
        'blueprints': len(blueprint_entries),
    }

    return render_template(
        'mechanics.html',
        mechanics_stats=mechanics_stats,
        featured_brands=featured_brands[:6],
        recent_entries=recent_entries[:6],
        featured_blueprints=blueprint_entries[:4],
    )


def _mechanics_items_for_vehicle(brand, model, year):
    items, _, _, _ = _load_mechanics_catalog()
    return [
        item for item in items
        if str(item.get('brand') or '') == brand
        and str(item.get('model') or '') == model
        and str(item.get('year') or '') == year
    ]


def _build_mechanics_reference_rows(items):
    indexed_lookup = {}
    for item in items:
        brand = str(item.get('brand') or '').strip()
        model = str(item.get('model') or '').strip()
        year = str(item.get('year') or '').strip()
        if not (brand and model and year):
            continue

        key = (brand.lower(), model.lower(), year)
        indexed_entry = indexed_lookup.setdefault(
            key,
            {
                'href': f'/mechanics/{brand}/{model}/{year}',
                'document_count': 0,
                'has_pdf': False,
            },
        )
        indexed_entry['document_count'] += 1
        indexed_entry['has_pdf'] = indexed_entry['has_pdf'] or bool(item.get('pdf_path'))

    def decade_label(year_value):
        try:
            year_number = int(year_value)
        except (TypeError, ValueError):
            return 'Reference'
        return f'{(year_number // 10) * 10}s'

    reference_rows = [
        {
            'decade': '1980s', 'year': '1984', 'brand': 'Toyota', 'model': 'Pickup 4x4',
            'segment': 'Compact field utility truck',
            'layout': '4×4 Gasoline Pickup',
            'powertrain': '22R inline-four gasoline engine, five-speed manual transmission, gear-driven transfer case, and solid rear axle.',
            'service_focus': 'Carburetor vacuum leaks, cooling reserve capacity, manual hub engagement, and rear leaf-spring shackle wear.',
            'inspection_notes': 'Check steering box frame mounts for cracking, inspect front axle seals, verify clutch hydraulic travel, and confirm transfer-case engagement under load.',
        },
        {
            'decade': '1990s', 'year': '1996', 'brand': 'Toyota', 'model': 'Corolla',
            'segment': 'Compact commuter service track',
            'layout': 'Front-Wheel-Drive Gasoline Sedan',
            'powertrain': '1.6L or 1.8L gasoline inline-four, transverse layout, manual or automatic transaxle, front MacPherson strut suspension.',
            'service_focus': 'Cooling fan relay behavior, distributor and plug wire condition, axle boot leakage, and front brake wear balance.',
            'inspection_notes': 'Check accessory belt tension, inspect radiator end tanks for seepage, verify lower control arm bushings, and review idle quality under electrical load.',
        },
        {
            'decade': '2010s', 'year': '2015', 'brand': 'Ford', 'model': 'F-150',
            'segment': 'Half-ton utility pickup',
            'layout': 'Rear-Wheel or 4×4 Gasoline Truck',
            'powertrain': '3.5L EcoBoost, 5.0L V8, or 2.7L EcoBoost gasoline powertrain with six-speed automatic transmission and boxed aluminum-intensive body.',
            'service_focus': 'Turbo plumbing security, coil-on-plug misfire tracking, transfer-case engagement, and rear leaf-spring shackle wear.',
            'inspection_notes': 'Inspect intercooler condensation drain path, verify front hub vacuum operation on 4×4 trims, and check transmission cooler lines for seepage.',
        },
        {
            'decade': '2010s', 'year': '2010', 'brand': 'Honda', 'model': 'Civic',
            'segment': 'Compact front-wheel-drive sedan',
            'layout': 'Front-Wheel-Drive Gasoline Sedan',
            'powertrain': '1.8L gasoline inline-four, front transaxle, electric power steering, and front MacPherson strut suspension.',
            'service_focus': 'Accessory belt condition, radiator support corrosion, electric steering assist behavior, and lower ball-joint wear.',
            'inspection_notes': 'Verify engine mount condition, inspect serpentine belt routing, check front strut top hats, and review battery charging voltage at idle.',
        },
        # ── GROUND COMBAT ────────────────────────────────────────────────────
        {
            'decade': '2000s', 'year': '2004', 'brand': 'AM General', 'model': 'HMMWV M1114',
            'segment': 'Up-armored light tactical vehicle',
            'layout': '4×4 Diesel Wheeled',
            'powertrain': '6.5L turbocharged V8 diesel, 4-speed TH400 automatic, two-speed 242 transfer case, Dana 44 axles',
            'service_focus': 'Turbocharger seals, intercooler clamp condition, CTI leak-down, and A-kit armor panel bolt torque.',
            'inspection_notes': 'Check air cleaner restriction indicator, glow plug circuit resistance, differential lock actuation, and power steering pump flow under load.',
        },
        {
            'decade': '2010s', 'year': '2016', 'brand': 'AM General', 'model': 'HMMWV M1151A1',
            'segment': 'Expanded Capacity Vehicle (ECV)',
            'layout': '4×4 Diesel Wheeled',
            'powertrain': '6.5L turbocharged V8 diesel, TH400 automatic, upgraded suspension, underbody blast protection plating',
            'service_focus': 'Underbody armor mount integrity, suspension bushing fatigue, and runflat insert condition.',
            'inspection_notes': 'Verify roof weapon station rotation, inspect armor panel seals for moisture intrusion, check auxiliary battery bank.',
        },
        {
            'decade': '2010s', 'year': '2012', 'brand': 'Oshkosh', 'model': 'M-ATV',
            'segment': 'MRAP All-Terrain Vehicle',
            'layout': '4×4 Independent Suspension Diesel',
            'powertrain': 'Caterpillar C7 ACERT diesel 370 hp, Allison 3500SP automatic, TAK-4i IWS, onboard hydraulic power system',
            'service_focus': 'TAK-4i suspension stroke limits, hydraulic fluid contamination, and turbo boost pressure sensor accuracy.',
            'inspection_notes': 'Check central tire inflation manifold seals, inspect IWS articulation under full lock, and test OBIGGS nitrogen generator output.',
        },
        {
            'decade': '2010s', 'year': '2020', 'brand': 'Oshkosh', 'model': 'JLTV L-ATV',
            'segment': 'Joint Light Tactical Vehicle',
            'layout': '4×4 IWS Diesel',
            'powertrain': '6.6L GM Duramax LML turbodiesel 300 hp, Allison 2500SP automatic, IWS independent wheel suspension, 70 mph road speed',
            'service_focus': 'IWS isolator mounts, DPF regeneration cycles, and B-kit armor rail torque sequences.',
            'inspection_notes': 'Verify CTIS system leak-down rate, inspect differential lock engagement solenoids, and check DEF fluid level/quality.',
        },
        {
            'decade': '2000s', 'year': '2006', 'brand': 'Oshkosh', 'model': 'FMTV A2',
            'segment': 'Medium tactical cargo/fuel/van truck',
            'layout': '6×6 Diesel Wheeled',
            'powertrain': 'Caterpillar C7 ACERT 330 hp, Allison 3000SP, CTI, 2.5–10 ton variants, forward control cab',
            'service_focus': 'CTI valve corrosion, Allison transmission cooler lines, and PTO engagement clutch wear.',
            'inspection_notes': 'Check air brake compressor output pressure, inspect frame crossmember welds, verify CTI manifold balance across all six positions.',
        },
        {
            'decade': '2000s', 'year': '2005', 'brand': 'Oshkosh', 'model': 'HEMTT A4',
            'segment': 'Heavy tactical truck / 8×8 platform',
            'layout': '8×8 Diesel Wheeled',
            'powertrain': 'Detroit Diesel Series 60 12.7L 500 hp, Allison HD4060P automatic, CTI, 65,000 lb GVW',
            'service_focus': 'Series 60 injector O-ring seepage, Allison retarder heat rejection, and front steer axle kingpin wear.',
            'inspection_notes': 'Inspect air dryer cartridge condition, check all CTI valve actuators, and verify fifth-wheel plate lubrication on semitrailer variant.',
        },
        {
            'decade': '2000s', 'year': '2008', 'brand': 'BAE Systems', 'model': 'M2A3 Bradley IFV',
            'segment': 'Infantry Fighting Vehicle',
            'layout': 'Tracked Diesel',
            'powertrain': 'Cummins VTA-903T turbocharged diesel 600 hp, HMPT-500 transmission, torsion-bar suspension, 33-ton combat weight',
            'service_focus': 'Track tension and end connector wear, HMPT oil temperature spikes, and 25mm feed system timing.',
            'inspection_notes': 'Check final drive oil level, inspect road wheel bearing play, verify TOW launcher elevation worm gear backlash, and test Gunner Primary Sight boresight.',
        },
        {
            'decade': '2020s', 'year': '2022', 'brand': 'BAE Systems', 'model': 'AMPV',
            'segment': 'Armored Multi-Purpose Vehicle',
            'layout': 'Tracked Diesel',
            'powertrain': 'BAE turbodiesel 675 hp, automatic transmission, torsion-bar suspension, 40-ton range, improved blast floor',
            'service_focus': 'Blast floor mounting integrity, torsion bar suspension alignment, and electric ramp actuator cycles.',
            'inspection_notes': 'Inspect NBC overpressure seals, verify driver vision block cleanliness, and check vehicle management system fault log on CPC.',
        },
        {
            'decade': '2010s', 'year': '2010', 'brand': 'General Dynamics', 'model': 'M1A2 SEP Abrams',
            'segment': 'Main Battle Tank',
            'layout': 'Tracked Gas Turbine',
            'powertrain': 'Honeywell AGT1500 gas turbine 1,500 hp, Allison X1100-3B transmission, torsion-bar suspension, 68-ton combat weight',
            'service_focus': 'Gas turbine inlet barrier filter loading, hydraulic turret traverse pressure, and track shoe pin wear.',
            'inspection_notes': 'Check AGT1500 oil chip detector, inspect T-158 track tension, verify CITV boresight to main gun, and test NBC system positive pressure.',
        },
        {
            'decade': '2020s', 'year': '2020', 'brand': 'General Dynamics', 'model': 'M1A2 SEPv3 Abrams',
            'segment': 'Main Battle Tank — SEPv3',
            'layout': 'Tracked Gas Turbine',
            'powertrain': 'Honeywell AGT1500C gas turbine 1,500 hp, improved APU, new generator, torsion-bar, Trophy APS mount points',
            'service_focus': 'APU diesel fuel filter intervals, Trophy APS radar alignment, and commander thermal viewer focus calibration.',
            'inspection_notes': 'Verify USB-3 vehicle architecture BIT results, inspect Trophy launcher arm travel, and check crew compartment wiring harness chafing points.',
        },
        {
            'decade': '2000s', 'year': '2006', 'brand': 'General Dynamics', 'model': 'Stryker ICV',
            'segment': 'Infantry Carrier Vehicle 8×8',
            'layout': '8×8 Diesel Wheeled',
            'powertrain': 'Caterpillar C7 ACERT 350 hp, Allison 3500SP automatic, independent double-wishbone suspension, double V-hull',
            'service_focus': 'Double V-hull fastener torque, slat armor cage weld integrity, and Allison shift quality under load.',
            'inspection_notes': 'Check CTI system balance, inspect run-flat tire insert condition, verify ramp cylinder hydraulic pressure, and test driver viewer defogging.',
        },
        {
            'decade': '2000s', 'year': '2008', 'brand': 'Force Protection', 'model': 'Cougar 4x4 MRAP',
            'segment': 'Category I MRAP',
            'layout': '4×4 Diesel V-Hull',
            'powertrain': 'Caterpillar C7 diesel 330 hp, Allison 3000SP automatic, monocoque V-hull crew capsule, run-flat tires',
            'service_focus': 'V-hull weld inspection points, door seal condition, and blast attenuating seat retention hardware.',
            'inspection_notes': 'Inspect belly armor attachment bolts, check gun ring rotation stops, verify intercom system at all crew positions, and test fire suppression actuators.',
        },
        {
            'decade': '2000s', 'year': '2009', 'brand': 'Navistar', 'model': 'MaxxPro MRAP',
            'segment': 'Category I MRAP',
            'layout': '4×4 Diesel V-Hull',
            'powertrain': 'Navistar MaxxForce 9 diesel 330 hp, Allison 3000 SP, V-hull under-body deflection, add-on armor kit',
            'service_focus': 'MaxxForce injector sealing, V-hull seam inspection, and EFP add-on armor panel alignment.',
            'inspection_notes': 'Check under-vehicle armor fastener torque, verify air conditioning belt tension, inspect locking door hinge bolts for shear.',
        },
        {
            'decade': '2010s', 'year': '2018', 'brand': 'Polaris', 'model': 'MRZR-D4',
            'segment': 'Ultra-Light Combat Vehicle (ULCV)',
            'layout': '4×4 Turbodiesel',
            'powertrain': '4-cyl turbodiesel, CVT, independent suspension, 14.5 in ground clearance, 1,500 lb payload',
            'service_focus': 'CVT belt condition, air filter service interval in dusty environments, and frame tube weld inspection.',
            'inspection_notes': 'Verify differential lock engagement, inspect roll bar structural integrity, check tow hook attachment welds, and test brake cylinder reservoirs.',
        },
        # ── AVIATION ─────────────────────────────────────────────────────────
        {
            'decade': '2000s', 'year': '2005', 'brand': 'Boeing', 'model': 'AH-64D Apache Longbow',
            'segment': 'Attack helicopter',
            'layout': 'Twin Turboshaft Rotary',
            'powertrain': 'Two GE T700-GE-701C 1,890 shp each, four-blade main rotor, hydraulic flight controls, Longbow radar mast',
            'service_focus': 'Engine hot-section TBO tracking, Hellfire rail alignment, and hydraulic actuator fluid contamination.',
            'inspection_notes': 'Check FLIR/TADS boresight, inspect tail rotor pitch change rod bearings, verify Longbow radar dome seal, and review engine chip detector.',
        },
        {
            'decade': '2010s', 'year': '2016', 'brand': 'Boeing', 'model': 'AH-64E Apache Guardian',
            'segment': 'Block III attack helicopter',
            'layout': 'Twin Turboshaft Rotary',
            'powertrain': 'Two GE T700-GE-701D 2,000 shp each, improved drivetrain, UAS Level IV teaming datalink, 30mm M230',
            'service_focus': 'Enhanced drivetrain vibration tracking, digital TADS/PNVS alignment, and UAS link antenna inspection.',
            'inspection_notes': 'Review Level IV autonomy BIT results, check main rotor blade erosion strips, inspect mast-mounted sight rotation stops.',
        },
        {
            'decade': '2010s', 'year': '2014', 'brand': 'Boeing', 'model': 'CH-47F Chinook',
            'segment': 'Heavy-lift tandem rotor helicopter',
            'layout': 'Twin Turboshaft Tandem Rotor',
            'powertrain': 'Two Honeywell T55-GA-714A 4,733 shp each, CAAS glass cockpit, DAFCS digital flight control system',
            'service_focus': 'Forward/aft rotor head bearing condition, cargo hook load cell calibration, and DAFCS servo actuator seal integrity.',
            'inspection_notes': 'Inspect syncronization shaft universal joints, check triple-hook load beam visual for cracks, verify APU exhaust shroud heat shield.',
        },
        {
            'decade': '2010s', 'year': '2015', 'brand': 'Sikorsky', 'model': 'UH-60M Black Hawk',
            'segment': 'Utility transport helicopter',
            'layout': 'Twin Turboshaft Rotary',
            'powertrain': 'Two GE T700-GE-701D 2,000 shp each, wide-chord composite blades, EFIS cockpit, FADEC engine controls',
            'service_focus': 'Composite main rotor blade delamination checks, FADEC ground test, and stability augmentation actuator cycling.',
            'inspection_notes': 'Check tail rotor gearbox chip detector, inspect blade fold retention bolts (SH-60 variant), verify emergency float squib continuity.',
        },
        {
            'decade': '2010s', 'year': '2014', 'brand': 'Bell', 'model': 'AH-1Z Viper',
            'segment': 'USMC attack helicopter',
            'layout': 'Twin Turboshaft Rotary',
            'powertrain': 'Two GE T700-GE-401C, four-blade semi-rigid rotor, 20mm M197 tri-barrel cannon, NTS targeting system',
            'service_focus': 'Four-blade rotor head elastomeric bearing replacement intervals, M197 ammunition feed path inspection, and NTS sensor alignment.',
            'inspection_notes': 'Check stub wing pylon attachment hardware, verify Hellfire rail continuity, inspect rotor head yoke for composite damage.',
        },
        {
            'decade': '2010s', 'year': '2010', 'brand': 'Lockheed Martin', 'model': 'F-22A Raptor',
            'segment': '5th-gen air superiority fighter',
            'layout': 'Twin Turbofan Fixed Wing',
            'powertrain': 'Two P&W F119-PW-100 35,000 lbf with afterburner, 2D thrust-vector nozzles, supercruise at Mach 1.8',
            'service_focus': 'LO coating delamination around panel fasteners, thrust-vector actuator rod end play, and GBC oxygen generator servicing.',
            'inspection_notes': 'Inspect RAM material at control surface edges, verify canopy LO sealant integrity, check radar absorbent material adhesion on inlet lips.',
        },
        {
            'decade': '2010s', 'year': '2018', 'brand': 'Lockheed Martin', 'model': 'F-35A Lightning II',
            'segment': '5th-gen CTOL multi-role fighter',
            'layout': 'Single Turbofan Fixed Wing',
            'powertrain': 'P&W F135-PW-100 43,000 lbf afterburning, EODAS/EOTS sensor fusion, APG-81 AESA, internal weapons bay',
            'service_focus': 'LO panel gap tolerances, F135 fan blade erosion inspection, and DAS window exterior clean-up protocols.',
            'inspection_notes': 'Verify ALIS/ODIN maintenance system sync, check aft fuselage thermal barrier coating, inspect canopy LO seal for de-bond.',
        },
        {
            'decade': '2010s', 'year': '2012', 'brand': 'General Atomics', 'model': 'MQ-9A Reaper',
            'segment': 'MALE UCAV',
            'layout': 'Single Turboprop Fixed Wing',
            'powertrain': 'Honeywell TPE331-10T 900 shp, Lynx SAR radar, MTS-B EO/IR ball, 50,000 ft ceiling, 14+ hr endurance',
            'service_focus': 'Turboprop hot section inspection, MTS-B window anti-ice element, and Hellfire pylon electrical continuity.',
            'inspection_notes': 'Check propeller blade de-bond, inspect fuel vent system for insect intrusion, verify Ground Control Station datalink signal margins.',
        },
        # ── NAVAL ────────────────────────────────────────────────────────────
        {
            'decade': '2000s', 'year': '2005', 'brand': 'Huntington Ingalls', 'model': 'DDG-51 Arleigh Burke',
            'segment': 'Guided-missile destroyer',
            'layout': 'COGAG Gas Turbine Combatant',
            'powertrain': 'Two GE LM2500-30 gas turbines 100,000 shp, two shafts, 30+ kt speed, Mk 41 VLS 96 cells',
            'service_focus': 'LM2500 variable stator actuator wear, Aegis SPY-1D radar waveguide seals, and Mk 41 launcher cell alignment.',
            'inspection_notes': 'Verify gas turbine inlet screens for debris, inspect SPY-1D phase array cooling water flow, check Phalanx CIWS gimbal limits.',
        },
        {
            'decade': '2010s', 'year': '2015', 'brand': 'Huntington Ingalls', 'model': 'LCS-1 Freedom',
            'segment': 'Freedom-class Littoral Combat Ship',
            'layout': 'CODAG Propulsion Surface Combatant',
            'powertrain': 'Two GE LM2500 gas turbines + two MTU diesel electric, 47-kt sprint, Rolls-Royce waterjets, reconfigurable mission modules',
            'service_focus': 'Waterjet inlet grate fouling, module reconfiguration crane limit switches, and Mk 110 57mm ammunition hoist.',
            'inspection_notes': 'Check gas turbine air inlet vane actuators, inspect mooring system bollard condition, verify MH-60 haul-down hardware.',
        },
        {
            'decade': '2010s', 'year': '2014', 'brand': 'General Dynamics', 'model': 'Virginia-class SSN',
            'segment': 'Fast-attack nuclear submarine',
            'layout': 'PWR Nuclear Submarine',
            'powertrain': 'S9G pressurized water reactor, GE steam turbines, 25+ kt submerged, 12× VLS Tomahawk, four 533mm Mk 48 ADCAP tubes',
            'service_focus': 'Photonic mast window inspection, Virginia Payload Module VLS alignment, and BRD-7 acoustic decoy launcher arming circuits.',
            'inspection_notes': 'Verify reactor compartment shielding survey logs, inspect periscope hydraulic ram seals, check underwater vehicle lock-out/lock-in hatch integrity.',
        },
        {
            'decade': '2020s', 'year': '2022', 'brand': 'Huntington Ingalls', 'model': 'CVN-78 Ford-class',
            'segment': 'Nuclear aircraft carrier',
            'layout': 'A1B PWR Nuclear Surface Combatant',
            'powertrain': 'Two A1B PWR reactors, EMALS electromagnetic catapult, AAG advanced arresting gear, 90 aircraft, AN/SPY-3 MFR',
            'service_focus': 'EMALS energy storage module capacitor bank inspection, AAG cross-deck pendant tension, and A1B secondary coolant chemistry.',
            'inspection_notes': 'Verify EMALS launch energy calibration for aircraft weight class, inspect AAG cable dead-end fittings, check flight deck non-skid coating delamination.',
        },
    ]

    existing_keys = {
        (row['brand'].lower(), row['model'].lower(), row['year'])
        for row in reference_rows
    }

    for item in items:
        brand = str(item.get('brand') or '').strip()
        model = str(item.get('model') or '').strip()
        year = str(item.get('year') or '').strip()
        if not (brand and model and year):
            continue

        key = (brand.lower(), model.lower(), year)
        if key in existing_keys:
            continue

        description = str(item.get('description') or '').strip()
        reference_rows.append({
            'decade': decade_label(year),
            'year': year,
            'brand': brand,
            'model': model,
            'segment': item.get('title') or f'{brand} {model} indexed workbench entry',
            'layout': 'Indexed manual track',
            'powertrain': description or 'Powertrain details are stored in the linked indexed documents for this vehicle.',
            'service_focus': 'Open the linked detail page to review indexed manuals, image sets, and any attached PDF procedures.',
            'inspection_notes': description or 'This row is generated from the current manual store and links directly into the mechanics workbench.',
        })
        existing_keys.add(key)

    for row in reference_rows:
        key = (row['brand'].lower(), row['model'].lower(), row['year'])
        indexed_entry = indexed_lookup.get(key)
        row['href'] = indexed_entry['href'] if indexed_entry else None
        row['linked'] = bool(indexed_entry)
        row['status'] = (
            f"{indexed_entry['document_count']} indexed doc"
            f"{'s' if indexed_entry['document_count'] != 1 else ''}"
            + (' / PDF attached' if indexed_entry['has_pdf'] else ' / detail page ready')
            if indexed_entry else
            'Reference profile'
        )

    return reference_rows


# ── Blueprint Gallery: free public-domain / CC images per platform ─────────
_BLUEPRINT_GALLERY = [
    # AVIATION
    {'id':'ah64','platform':'AH-64D/E Apache','maker':'Boeing','category':'air','type':'Attack Helicopter',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/5/5f/McDonnell_Douglas_AH-64_Apache_3-view_line_drawing.png/574px-McDonnell_Douglas_AH-64_Apache_3-view_line_drawing.png',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Boeing_AH-64_Apache',
     'official':'https://www.boeing.com/defense/apache-helicopter/','license':'PD-USGov','tm':'TM 1-1520-238-10','has_blueprint':True},
    {'id':'uh60','platform':'UH-60M Black Hawk','maker':'Sikorsky','category':'air','type':'Utility Helicopter',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/9/95/Sikorsky_UH-60_Black_Hawk_orthographical_image.svg/960px-Sikorsky_UH-60_Black_Hawk_orthographical_image.svg.png',
     'img_type':'Orthographic 3-View','wiki':'https://en.wikipedia.org/wiki/Sikorsky_UH-60_Black_Hawk',
     'official':'https://www.lockheedmartin.com/en-us/products/uh-60-black-hawk.html','license':'CC-BY-SA 4.0','tm':'TM 1-1520-237-10','has_blueprint':True},
    {'id':'ch47','platform':'CH-47F Chinook','maker':'Boeing','category':'air','type':'Heavy Lift Helicopter',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/Boeing_CH-47_Chinook_3-view_line_drawing.svg/960px-Boeing_CH-47_Chinook_3-view_line_drawing.svg.png',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Boeing_CH-47_Chinook',
     'official':'https://www.boeing.com/defense/chinook/','license':'CC-BY 3.0','tm':'TM 1-1520-240-10','has_blueprint':True},
    {'id':'ah1z','platform':'AH-1Z Viper','maker':'Bell','category':'air','type':'Attack Helicopter',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/2/28/Bell_AH-1Z_Viper_Line_Drawing.svg/960px-Bell_AH-1Z_Viper_Line_Drawing.svg.png',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Bell_AH-1Z_Viper',
     'official':'https://www.bellflight.com/products/bell-ah-1z','license':'CC-BY 3.0','tm':'NAVAIR 01-H57BJ-1','has_blueprint':True},
    {'id':'f35a','platform':'F-35A Lightning II','maker':'Lockheed Martin','category':'air','type':'Multirole Fighter (CTOL)',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/c/cc/Lockheed_Martin_F-35A_Lightning_II_3-view_drawing.png/1115px-Lockheed_Martin_F-35A_Lightning_II_3-view_drawing.png',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Lockheed_Martin_F-35_Lightning_II',
     'official':'https://www.lockheedmartin.com/en-us/products/f-35.html','license':'Public Domain','tm':'T.O. 1F-35A-1','has_blueprint':True},
    {'id':'f35b','platform':'F-35B Lightning II','maker':'Lockheed Martin','category':'air','type':'Multirole Fighter (STOVL)',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/c/cc/Lockheed_Martin_F-35A_Lightning_II_3-view_drawing.png/1115px-Lockheed_Martin_F-35A_Lightning_II_3-view_drawing.png',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Lockheed_Martin_F-35_Lightning_II',
     'official':'https://www.lockheedmartin.com/en-us/products/f-35.html','license':'Public Domain','tm':'T.O. 1F-35B-1','has_blueprint':True},
    {'id':'f35c','platform':'F-35C Lightning II','maker':'Lockheed Martin','category':'air','type':'Multirole Fighter (CV)',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/c/cc/Lockheed_Martin_F-35A_Lightning_II_3-view_drawing.png/1115px-Lockheed_Martin_F-35A_Lightning_II_3-view_drawing.png',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Lockheed_Martin_F-35_Lightning_II',
     'official':'https://www.lockheedmartin.com/en-us/products/f-35.html','license':'Public Domain','tm':'T.O. 1F-35C-1','has_blueprint':True},
    {'id':'f22','platform':'F-22A Raptor','maker':'Lockheed Martin','category':'air','type':'Air Superiority Fighter',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/4/40/Lockheed_Martin_F-22A_Raptor_3-view_line_drawing.jpg/670px-Lockheed_Martin_F-22A_Raptor_3-view_line_drawing.jpg',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Lockheed_Martin_F-22_Raptor',
     'official':'https://www.lockheedmartin.com/en-us/products/f-22.html','license':'PD-USGov','tm':'T.O. 1F-22A-1','has_blueprint':True},
    {'id':'f15ex','platform':'F-15EX Eagle II','maker':'Boeing','category':'air','type':'Multirole Fighter',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/McDonnell_Douglas_F-15_Eagle_3-view.svg/960px-McDonnell_Douglas_F-15_Eagle_3-view.svg.png',
     'img_type':'Blueprint 3-View','wiki':'https://en.wikipedia.org/wiki/Boeing_F-15EX_Eagle_II',
     'official':'https://www.boeing.com/defense/f-15ex-eagle-ii/','license':'PD-USGov','tm':'T.O. 1F-15E-1','has_blueprint':True},
    {'id':'mq9','platform':'MQ-9A Reaper','maker':'General Atomics','category':'air','type':'Unmanned Aerial Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/d/d8/MQ-9_Reaper_dimensioned_sketch.png/960px-MQ-9_Reaper_dimensioned_sketch.png',
     'img_type':'Dimensioned Sketch','wiki':'https://en.wikipedia.org/wiki/General_Atomics_MQ-9_Reaper',
     'official':'https://www.ga-asi.com/remotely-piloted-aircraft/mq-9a','license':'Public Domain','tm':'T.O. 1MQ-9A-1','has_blueprint':True},
    # NAVAL
    {'id':'ddg51','platform':'DDG-51 Arleigh Burke','maker':'Huntington Ingalls','category':'naval','type':'Guided Missile Destroyer',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/Burke_class_destroyer_profile%3Bwpe47485.png/960px-Burke_class_destroyer_profile%3Bwpe47485.png',
     'img_type':'Profile Diagram','wiki':'https://en.wikipedia.org/wiki/Arleigh_Burke-class_destroyer',
     'official':'https://www.navy.mil/Resources/Fact-Files/Display-FactFiles/Article/2169512/ddg-51-arleigh-burke-class/','license':'PD-USGov','tm':'NAVSEA S9AA0-AA-SPN-010','has_blueprint':True},
    {'id':'cvn78','platform':'CVN-78 Gerald R. Ford','maker':'Huntington Ingalls','category':'naval','type':'Nuclear Aircraft Carrier',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/5/5b/Ford_class.png/960px-Ford_class.png',
     'img_type':'Side Profile','wiki':'https://en.wikipedia.org/wiki/Gerald_R._Ford-class_aircraft_carrier',
     'official':'https://www.navy.mil/Resources/Fact-Files/Display-FactFiles/Article/2169581/cvn-aircraft-carriers/','license':'CC-BY-SA 4.0','tm':'NAVSEA CVN-78','has_blueprint':True},
    {'id':'lcs1','platform':'LCS-1 Freedom-class','maker':'Huntington Ingalls','category':'naval','type':'Littoral Combat Ship',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/0/0a/USS-Freedom-130222-N-DR144-174-crop.jpg/960px-USS-Freedom-130222-N-DR144-174-crop.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Freedom-class_littoral_combat_ship',
     'official':'https://www.navy.mil/Resources/Fact-Files/Display-FactFiles/Article/2169590/lcs-littoral-combat-ship/','license':'PD-USGov','tm':'NAVSEA LCS-1','has_blueprint':False},
    {'id':'lcs2','platform':'LCS-2 Independence-class','maker':'General Dynamics','category':'naval','type':'Littoral Combat Ship',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/f/ff/USS_Gabrielle_Giffords_%28LCS-10%29_underway_in_the_Philippine_Sea_on_1_October_2019_%28191001-N-YI115-2128%29.JPG/960px-USS_Gabrielle_Giffords_%28LCS-10%29_underway_in_the_Philippine_Sea_on_1_October_2019_%28191001-N-YI115-2128%29.JPG',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Independence-class_littoral_combat_ship',
     'official':'https://www.navy.mil/Resources/Fact-Files/Display-FactFiles/Article/2169590/lcs-littoral-combat-ship/','license':'PD-USGov','tm':'NAVSEA LCS-2','has_blueprint':False},
    {'id':'virginia','platform':'Virginia-class SSN','maker':'General Dynamics','category':'naval','type':'Nuclear Attack Submarine',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/b/b7/Virginia_class_submarine.jpg/960px-Virginia_class_submarine.jpg',
     'img_type':'Official Illustration','wiki':'https://en.wikipedia.org/wiki/Virginia-class_submarine',
     'official':'https://www.navy.mil/Resources/Fact-Files/Display-FactFiles/Article/2169558/ssn-virginia-class/','license':'PD-USGov','tm':'NAVSEA SSN-774','has_blueprint':False},
    # GROUND COMBAT
    {'id':'abrams','platform':'M1A2 SEP Abrams','maker':'General Dynamics','category':'ground','type':'Main Battle Tank',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/0/0b/M1A2_SEP_v3.jpg/960px-M1A2_SEP_v3.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/M1_Abrams',
     'official':'https://www.gd.com/products/land-systems/m1-abrams','license':'PD-USGov','tm':'TM 9-2350-255-10','has_blueprint':False},
    {'id':'stryker','platform':'Stryker ICV (M1126)','maker':'General Dynamics','category':'ground','type':'Infantry Carrier Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/5/51/Stryker_ICV_front_q.jpg/800px-Stryker_ICV_front_q.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Stryker',
     'official':'https://www.gd.com/products/land-systems/stryker','license':'PD-USGov','tm':'TM 9-2350-405-10','has_blueprint':False},
    {'id':'bradley','platform':'M2/M3 Bradley','maker':'BAE Systems','category':'ground','type':'Infantry Fighting Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/US_M2A1_Bradley_deployed_to_Saudi_Arabia_during_Operation_Desert_Shield.jpg/960px-US_M2A1_Bradley_deployed_to_Saudi_Arabia_during_Operation_Desert_Shield.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/M2_Bradley',
     'official':'https://www.bae-systems.com/en-us/products/fighting-vehicles/bradley','license':'PD-USGov','tm':'TM 9-2350-294-10','has_blueprint':False},
    {'id':'ampv','platform':'AMPV (M1283/M1284)','maker':'BAE Systems','category':'ground','type':'Armored Multi-Purpose Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/f/f2/AMPV_-_180920-A-EN512-002.jpg/960px-AMPV_-_180920-A-EN512-002.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Armored_Multi-Purpose_Vehicle',
     'official':'https://www.bae-systems.com/en-us/products/armored-multi-purpose-vehicle-ampv','license':'PD-USGov','tm':'TM 9-2350-408-10','has_blueprint':False},
    {'id':'hmmwv','platform':'AM General HMMWV','maker':'AM General','category':'ground','type':'Light Utility Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/7/74/2015_MCAS_Beaufort_Air_Show_041215-M-CG676-161.jpg/960px-2015_MCAS_Beaufort_Air_Show_041215-M-CG676-161.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Humvee',
     'official':'https://www.amgeneral.com/vehicles/humvee/','license':'PD-USGov','tm':'TM 9-2320-280-10','has_blueprint':False},
    {'id':'matv','platform':'Oshkosh M-ATV','maker':'Oshkosh','category':'ground','type':'Mine-Resistant All-Terrain Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/M153_CROWS_mounted_on_a_U.S._Army_M-ATV.jpg/960px-M153_CROWS_mounted_on_a_U.S._Army_M-ATV.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Oshkosh_M-ATV',
     'official':'https://oshkoshdefense.com/vehicles/joint-light-tactical-vehicles/m-atv/','license':'PD-USGov','tm':'TM 9-2355-xxx-10','has_blueprint':False},
    {'id':'jltv','platform':'Oshkosh JLTV','maker':'Oshkosh','category':'ground','type':'Joint Light Tactical Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/6/63/L-ATV_4.jpg/960px-L-ATV_4.jpg',
     'img_type':'Manufacturer Photo','wiki':'https://en.wikipedia.org/wiki/Joint_Light_Tactical_Vehicle',
     'official':'https://oshkoshdefense.com/vehicles/joint-light-tactical-vehicles/jltv/','license':'CC-BY-SA 4.0','tm':'TM 9-2355-yyy-10','has_blueprint':False},
    {'id':'fmtv','platform':'Oshkosh FMTV (MTV)','maker':'Oshkosh','category':'ground','type':'Medium Tactical Truck',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/MTV-of-the-New-Jersey-National-Guard.jpg/960px-MTV-of-the-New-Jersey-National-Guard.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Family_of_Medium_Tactical_Vehicles',
     'official':'https://oshkoshdefense.com/vehicles/family-of-medium-tactical-vehicles/fmtv/','license':'PD-USGov','tm':'TM 9-2320-335-10','has_blueprint':False},
    {'id':'hemtt','platform':'Oshkosh HEMTT','maker':'Oshkosh','category':'ground','type':'Heavy Expanded Mobility Tactical Truck',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/f/f7/HEMTT_M1120A4_in_B-kit_configuration.jpg/960px-HEMTT_M1120A4_in_B-kit_configuration.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Heavy_Expanded_Mobility_Tactical_Truck',
     'official':'https://oshkoshdefense.com/vehicles/tactical-trucks/hemtt/','license':'CC-BY-SA 4.0','tm':'TM 9-2320-279-10','has_blueprint':False},
    {'id':'cougar','platform':'Cougar MRAP','maker':'Force Protection','category':'ground','type':'Mine-Resistant Ambush Protected',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/e/ed/U.S.Marine_Cougar_H_EOD.jpg/960px-U.S.Marine_Cougar_H_EOD.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Cougar_(MRAP)',
     'official':'https://www.textron.com/defense/systems/tactical-and-combat-vehicles','license':'PD-USGov','tm':'TM 9-2355-aaa-10','has_blueprint':False},
    {'id':'maxxpro','platform':'Navistar MaxxPro MRAP','maker':'Navistar','category':'ground','type':'Mine-Resistant Ambush Protected',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/6/68/US_Army_M-ATV_and_MRAP_MaxxPro_Dash_in_Afghanistan.jpg/960px-US_Army_M-ATV_and_MRAP_MaxxPro_Dash_in_Afghanistan.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/Navistar_MaxxPro',
     'official':'https://www.internationaltruck.com/defense/','license':'PD-USGov','tm':'TM 9-2355-bbb-10','has_blueprint':False},
    {'id':'mrzr','platform':'Polaris MRZR-D4','maker':'Polaris','category':'ground','type':'Ultralight Tactical Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/6/67/Special_Warfare_MRZR_with_M240_-_Aviation_Nation_2019.jpg/960px-Special_Warfare_MRZR_with_M240_-_Aviation_Nation_2019.jpg',
     'img_type':'Air Show Photo','wiki':'https://en.wikipedia.org/wiki/MRZR',
     'official':'https://www.polaris.com/en-us/off-road/defense/','license':'CC-BY-SA 4.0','tm':'Polaris MRZR-D4 Service Manual','has_blueprint':False},
    {'id':'m1117','platform':'M1117 Guardian ASV','maker':'Textron','category':'ground','type':'Armored Security Vehicle',
     'img':'https://upload.wikimedia.org/wikipedia/commons/thumb/9/90/M1117_Guardian_Armored_Security_Vehicle.jpg/960px-M1117_Guardian_Armored_Security_Vehicle.jpg',
     'img_type':'Official Photo','wiki':'https://en.wikipedia.org/wiki/M1117_armored_security_vehicle',
     'official':'https://www.textron.com/defense/systems/tactical-and-combat-vehicles','license':'PD-USGov','tm':'TM 9-2355-ccc-10','has_blueprint':False},
]


@app.route('/mechanics/gallery')
def mechanics_gallery():
    gallery_stats = {
        'total': len(_BLUEPRINT_GALLERY),
        'ground': sum(1 for x in _BLUEPRINT_GALLERY if x['category'] == 'ground'),
        'air': sum(1 for x in _BLUEPRINT_GALLERY if x['category'] == 'air'),
        'naval': sum(1 for x in _BLUEPRINT_GALLERY if x['category'] == 'naval'),
        'blueprints': sum(1 for x in _BLUEPRINT_GALLERY if x.get('has_blueprint')),
    }
    return render_template('mechanics_gallery.html', blueprints=_BLUEPRINT_GALLERY, gallery_stats=gallery_stats)


@app.route('/mechanics/blueprints')
def mechanics_blueprints():
    items, _, _, _ = _load_mechanics_catalog()
    blueprint_rows = _build_mechanics_reference_rows(items)

    blueprint_stats = {
        'entries': len(blueprint_rows),
        'brands': len({entry['brand'] for entry in blueprint_rows}),
        'linked': sum(1 for entry in blueprint_rows if entry['linked']),
    }

    return render_template(
        'mechanics_blueprints.html',
        blueprint_rows=blueprint_rows,
        blueprint_stats=blueprint_stats,
    )


@app.route('/mechanics/<brand>/<model>/<year>')
def mechanics_blueprint(brand, model, year):
    items = _mechanics_items_for_vehicle(brand, model, year)
    blueprint_img = None
    gallery_imgs = []
    for item in items:
        images = item.get('image_paths') or []
        if images and blueprint_img is None:
            blueprint_img = images[0]
            gallery_imgs = images[1:]
            break

    vehicle_stats = {
        'documents': len(items),
        'images': sum(len(item.get('image_paths') or []) for item in items),
        'pdfs': sum(1 for item in items if item.get('pdf_path')),
    }

    if not items:
        return render_template(
            'mechanics_blueprint.html',
            brand=brand,
            model=model,
            year=year,
            blueprint_img=None,
            gallery_imgs=[],
            items=[],
            vehicle_stats=vehicle_stats,
        ), 404

    return render_template(
        'mechanics_blueprint.html',
        brand=brand,
        model=model,
        year=year,
        blueprint_img=blueprint_img,
        gallery_imgs=gallery_imgs,
        items=items,
        vehicle_stats=vehicle_stats,
    )


# Mechanics browser: all makes/models/years with links
@app.route('/mechanics/browser')
def mechanics_browser():
    _, brands, models_by_brand, years_by_brand_model = _load_mechanics_catalog()
    return render_template(
        'mechanics_browser.html',
        brands=brands,
        models_by_brand=models_by_brand,
        years_by_brand_model=years_by_brand_model
    )


# ── Scripture Intel ──────────────────────────────────────────────────

@app.route('/bible')
def bible_page():
    admin_token = os.environ.get('ADMIN_TOKEN')
    return render_template('bible.html', admin_token_set=bool(admin_token), admin_token=admin_token or '')


@app.route('/api/bible')
def api_bible():
    ref = request.args.get('ref', 'John 3:16')
    translation = request.args.get('translation', 'web')
    try:
        url = f'https://bible-api.com/{ref}?translation={translation}'
        resp = http_requests.get(url, timeout=10)
        data = resp.json()
        if 'error' in data:
            return jsonify({'error': data['error']}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Drone Command ────────────────────────────────────────────────────

@app.route('/drone')
def drone_page():
    return render_template('drone.html')


# ── SIGINT Radio Lab (hidden page) ───────────────────────────────────

@app.route('/radio')
def radio_page():
    return render_template('radio.html')


@app.route('/manuals')
def manuals_page():
    """Manuals index - placeholders only. Actual manuals must be uploaded by authorized users."""
    brands = manual_store.list_brands()
    return render_template('manuals.html', brands=brands)


@app.route('/admin/manuals')
def admin_manuals():
    brands = manual_store.list_brands()
    return render_template('admin_manuals.html', brands=brands, admin_token_set=bool(os.environ.get('ADMIN_TOKEN')))


@app.route('/api/manuals/upload', methods=['POST'])
def api_manuals_upload():
    admin_token = os.environ.get('ADMIN_TOKEN')
    provided = request.headers.get('X-Admin-Token') or request.form.get('admin_token')
    if admin_token and provided != admin_token:
        return jsonify({'ok': False, 'error': 'admin token required'}), 403

    brand = (request.form.get('brand') or '').strip()
    model = (request.form.get('model') or '').strip()
    year = (request.form.get('year') or '').strip()
    title = (request.form.get('title') or '').strip() or f"{brand} {model} {year}"
    description = (request.form.get('description') or '').strip()
    license = (request.form.get('license') or '').strip()
    source_url = (request.form.get('source_url') or '').strip()

    if not brand:
        return jsonify({'ok': False, 'error': 'brand required'}), 400

    pdf = request.files.get('pdf')
    images = request.files.getlist('images')

    mid = uuid.uuid4().hex
    upload_dir = os.path.join(APP_ROOT, 'static', 'manuals', 'uploads', mid)
    image_dir = os.path.join(APP_ROOT, 'static', 'manuals', 'images', mid)
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(image_dir, exist_ok=True)

    pdf_rel = None
    if pdf and pdf.filename:
        fname = secure_filename(pdf.filename)
        save_path = os.path.join(upload_dir, fname)
        pdf.save(save_path)
        pdf_rel = f'/static/manuals/uploads/{mid}/{fname}'

    image_rels = []
    for img in images:
        if img and img.filename:
            iname = secure_filename(img.filename)
            save_path = os.path.join(image_dir, iname)
            img.save(save_path)
            image_rels.append(f'/static/manuals/images/{mid}/{iname}')

    new_id = manual_store.add_manual(brand=brand, model=model, year=year, title=title,
                                     description=description, license=license,
                                     source_url=source_url, pdf_path=pdf_rel,
                                     image_paths=image_rels, mid=mid)
    return jsonify({'ok': True, 'id': new_id, 'pdf': pdf_rel, 'images': image_rels})


@app.route('/api/manuals/search')
def api_manuals_search():
    brand = request.args.get('brand')
    model = request.args.get('model')
    year = request.args.get('year')
    q = request.args.get('q')
    results = manual_store.search_manuals(brand=brand, model=model, year=year, q=q)
    return jsonify({'ok': True, 'manuals': results})


@app.route('/manuals/<brand>')
def manuals_brand(brand):
    models = manual_store.list_models(brand)
    models_years = {m: manual_store.list_years(brand, m) for m in models}
    return render_template('manuals_brand.html', brand=brand, models=models, models_years=models_years)


@app.route('/manuals/<brand>/<model>/<year>')
def manuals_detail(brand, model, year):
    items = manual_store.search_manuals(brand=brand, model=model, year=year)
    return render_template('manuals_detail.html', brand=brand, model=model, year=year, items=items)


@app.route('/api/manuals/import_wikimedia', methods=['POST'])
def api_manuals_import_wikimedia():
    admin_token = os.environ.get('ADMIN_TOKEN')
    provided = request.headers.get('X-Admin-Token') or (request.json or {}).get('admin_token')
    if admin_token and provided != admin_token:
        return jsonify({'ok': False, 'error': 'admin token required'}), 403

    data = request.get_json(force=True)
    brand = (data.get('brand') or '').strip()
    model = (data.get('model') or '').strip()
    year = (data.get('year') or '').strip()
    query = (data.get('query') or f"{brand} {model}").strip()
    limit = min(int(data.get('limit', 6)), 20)
    download = bool(data.get('download', False))

    if not query:
        return jsonify({'ok': False, 'error': 'query required'}), 400

    params = {
        'action': 'query',
        'format': 'json',
        'generator': 'search',
        'gsrsearch': query,
        'gsrlimit': limit,
        'prop': 'imageinfo',
        'iiprop': 'url|extmetadata',
    }
    try:
        resp = http_requests.get('https://commons.wikimedia.org/w/api.php', params=params, timeout=15)
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
            # Collect candidate info
            candidates.append({'title': title, 'url': url, 'license': license_short, 'usage': usage, 'page': f'https://commons.wikimedia.org/wiki/{title.replace(" ", "_")}'})

        # If requested, download only public-domain / CC0 images and create a manual entry
        downloaded = []
        if download and brand:
            mid = uuid.uuid4().hex
            image_dir = os.path.join(APP_ROOT, 'static', 'manuals', 'images', mid)
            os.makedirs(image_dir, exist_ok=True)
            for c in candidates:
                lic = (c.get('license') or '').lower()
                if 'public domain' in lic or 'cc0' in lic or lic == 'pd':
                    try:
                        r = http_requests.get(c['url'], timeout=20, stream=True)
                        r.raise_for_status()
                        fname = os.path.basename(c['url'].split('?')[0])
                        save_path = os.path.join(image_dir, secure_filename(fname))
                        with open(save_path, 'wb') as fh:
                            for chunk in r.iter_content(8192):
                                fh.write(chunk)
                        downloaded.append(f'/static/manuals/images/{mid}/{os.path.basename(save_path)}')
                    except Exception:
                        continue
            # Create a stub manual entry to hold these images
            title = f"{brand} {model} {year} (Wikimedia images)"
            mid_saved = manual_store.add_manual(brand=brand, model=model, year=year, title=title, description='Imported from Wikimedia Commons', license='various', source_url=None, pdf_path=None, image_paths=downloaded, mid=mid)
            return jsonify({'ok': True, 'imported': len(downloaded), 'images': downloaded, 'id': mid_saved})

        return jsonify({'ok': True, 'candidates': candidates})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502


# ── The Truth (hidden page — cross in logo) ──────────────────────────

@app.route('/truth')
def truth_page():
    return render_template('truth.html')


if __name__ == '__main__':
    import threading

    port = int(os.environ.get('PORT', '5000'))
    debug = os.environ.get('FLASK_DEBUG') == '1'

    should_open_browser = port == 5000 and os.environ.get('OPEN_BROWSER', '1') == '1'
    should_open_app = os.environ.get('OPEN_APP', '0') == '1'
    if should_open_browser or should_open_app:
        threading.Timer(1.2, lambda: _open_local_client(port)).start()

    app.run(debug=debug, host='127.0.0.1', port=port)
