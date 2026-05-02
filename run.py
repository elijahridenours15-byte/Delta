

import os
# --- Flask app and manual_store initialization (must be first) ---
from flask import Flask, render_template, request, jsonify
import tempfile
import shutil
import subprocess
import json
import uuid
import hashlib
import base64
import requests as http_requests
from agent.agent import run_instruction
from agent import generate_with_llm
from flask import send_file
import io
import zipfile
from werkzeug.utils import secure_filename
from manual_store import ManualStore

app = Flask(__name__, static_folder='static', template_folder='templates')
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
# Initialize manual store (SQLite + storage dirs)
manual_store = ManualStore(APP_ROOT)

# ── Journal (Scripture Tab) ─────────────────────────────────────────
journal_db_path = os.path.join(APP_ROOT, 'generated', 'journal.db')
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

@app.route('/api/journal', methods=['GET'])
def api_journal():
    import sqlite3
    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute('SELECT entry, created FROM journal ORDER BY created DESC')
    rows = [{'entry': r[0], 'created': r[1]} for r in cur.fetchall()]
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
    cur.execute('INSERT INTO journal (entry, created) VALUES (?, ?)', (entry, datetime.utcnow().isoformat()+'Z'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
# --- Flask app and manual_store initialization (must be first) ---
from flask import Flask, render_template, request, jsonify
import tempfile
import shutil
import os
import subprocess
import json
import uuid
import hashlib
import base64
import requests as http_requests
from agent.agent import run_instruction
from agent import generate_with_llm
from flask import send_file
import io
import zipfile
from werkzeug.utils import secure_filename
from manual_store import ManualStore

app = Flask(__name__, static_folder='static', template_folder='templates')
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
# Initialize manual store (SQLite + storage dirs)
manual_store = ManualStore(APP_ROOT)

# Debug route: dump all brands/models/years
@app.route('/mechanics/debug')
def mechanics_debug():
    brands = manual_store.list_brands()
    models_by_brand = {b: manual_store.list_models(b) for b in brands}
    years_by_brand_model = {}
    for b in brands:
        for m in models_by_brand[b]:
            years_by_brand_model[(b, m)] = manual_store.list_years(b, m)
    return {
        'brands': brands,
        'models_by_brand': models_by_brand,
        'years_by_brand_model': years_by_brand_model
    }
# Mechanics browser: all makes/models/years with links
@app.route('/mechanics/browser')
def mechanics_browser():
    brands = manual_store.list_brands()
    models_by_brand = {b: manual_store.list_models(b) for b in brands}
    years_by_brand_model = {}
    for b in brands:
        for m in models_by_brand[b]:
            years_by_brand_model[(b, m)] = manual_store.list_years(b, m)
    return render_template(
        'mechanics_browser.html',
        brands=brands,
        models_by_brand=models_by_brand,
        years_by_brand_model=years_by_brand_model
    )


@app.route('/mechanics/blueprints')
def mechanics_blueprints():
    # List available blueprint images from manuals and show first image as thumbnail
    items = manual_store.search_manuals()
    blueprints = []
    for it in items:
        imgs = it.get('image_paths', [])
        if not imgs:
            continue
        blueprints.append({
            'brand': it.get('brand'),
            'model': it.get('model'),
            'year': it.get('year'),
            'title': it.get('title') or f"{it.get('brand')} {it.get('model')} {it.get('year')}",
            'thumb': imgs[0],
            'id': it.get('id')
        })
    return render_template('mechanics_blueprints.html', blueprints=blueprints)

# Mechanics detail: professional blueprint/parts view
@app.route('/mechanics/<brand>/<model>/<year>')
def mechanics_blueprint(brand, model, year):
    items = manual_store.search_manuals(brand=brand, model=model, year=year)
    # For demo: show first image as blueprint, rest as gallery
    blueprint_img = None
    gallery_imgs = []
    if items:
        for it in items:
            imgs = it.get('image_paths', [])
            if imgs:
                blueprint_img = imgs[0]
                gallery_imgs = imgs[1:]
                break
    return render_template(
        'mechanics_blueprint.html',
        brand=brand, model=model, year=year,
        blueprint_img=blueprint_img, gallery_imgs=gallery_imgs, items=items
    )
#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import tempfile
import shutil
import os
import subprocess
import json
import uuid
import hashlib
import base64
import requests as http_requests
from agent.agent import run_instruction
from agent import generate_with_llm
from flask import send_file
import io
import zipfile
from werkzeug.utils import secure_filename
from manual_store import ManualStore

app = Flask(__name__, static_folder='static', template_folder='templates')
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
# Initialize manual store (SQLite + storage dirs)
manual_store = ManualStore(APP_ROOT)
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
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob: https:",
    "media-src 'self' blob: data: https:",
    "connect-src 'self' https:",
    "frame-src 'self' https:",
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

# In-memory store for shared code snippets (use DB in production)
_shared_snippets = {}

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
                'This archive contains the runnable Delta Coding website so it can be launched locally.\n\n'
                f'{vendor_note}\n'
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

OPEN_BROWSER=1 "$PYTHON_BIN" run.py
"""


def _offline_bundle_launcher_bat():
        return """@echo off
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

set OPEN_BROWSER=1
%PYTHON_BIN% run.py
endlocal
"""


@app.after_request
def apply_security_headers(response):
    for header_name, header_value in SECURITY_HEADERS.items():
        response.headers[header_name] = header_value
    if request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'

    if response.mimetype == 'text/html' and response.status_code == 200 and not response.direct_passthrough:
        html = response.get_data(as_text=True)
        if '/site.webmanifest' not in html and '</head>' in html:
            html = html.replace('</head>', f'    {PWA_HEAD_INJECTION}\n  </head>', 1)
            response.set_data(html)
            response.content_length = len(response.get_data())
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

    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True, download_name='delta_coding_offline_bundle.zip')


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


@app.route('/mechanics')
def mechanics_page():
    items = manual_store.search_manuals()
    brands = manual_store.list_brands()
    models_by_brand = {brand: manual_store.list_models(brand) for brand in brands}

    indexed_vehicles = 0
    featured_brands = []
    for brand in brands:
        models = models_by_brand.get(brand, [])
        years_count = 0
        for model in models:
            years_count += len(manual_store.list_years(brand, model))
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


# Mechanics browser: all makes/models/years with links
@app.route('/mechanics/browser')
def mechanics_browser():
    brands = manual_store.list_brands()
    models_by_brand = {b: manual_store.list_models(b) for b in brands}
    years_by_brand_model = {}
    for b in brands:
        for m in models_by_brand[b]:
            years_by_brand_model[(b, m)] = manual_store.list_years(b, m)
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
    import webbrowser

    port = int(os.environ.get('PORT', '5000'))
    debug = os.environ.get('FLASK_DEBUG') == '1'

    if port == 5000 and os.environ.get('OPEN_BROWSER', '1') == '1':
        threading.Timer(1.2, lambda: webbrowser.open('http://127.0.0.1:5000')).start()

    app.run(debug=debug, host='127.0.0.1', port=port)
