#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import tempfile
import shutil
import os
import subprocess
import uuid
import hashlib
import base64
import requests as http_requests
from agent.agent import run_instruction
from agent import generate_with_llm
from flask import send_file
import io
import zipfile

app = Flask(__name__, static_folder='static', template_folder='templates')

# In-memory store for shared code snippets (use DB in production)
_shared_snippets = {}

def _which(candidates):
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return candidates[0]

@app.route('/')
def index():
    return render_template('index.html')

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

        base_dir = os.path.join(os.path.dirname(__file__), 'generated')
        resp = run_instruction(final_instruction, project_name=project_name, execute=execute, base_dir=base_dir)
        # include llm meta if present
        if use_llm:
            resp['_llm_raw'] = llm_resp
        return jsonify(resp)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/projects', methods=['GET'])
def list_projects():
    base_dir = os.path.join(os.path.dirname(__file__), 'generated')
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
    base_dir = os.path.join(os.path.dirname(__file__), 'generated')
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
    base_dir = os.path.join(os.path.dirname(__file__), 'generated')
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
    base_dir = os.path.join(os.path.dirname(__file__), 'generated')
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


# ── Cyber Security page ──────────────────────────────────────────────

NVD_API = 'https://services.nvd.nist.gov/rest/json/cves/2.0'

# ── AI providers (all free) ──────────────────────────────────────────
POLLINATIONS_URL = 'https://text.pollinations.ai/openai'
HF_ROUTER_URL = 'https://router.huggingface.co/v1/chat/completions'


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


def _call_pollinations(model, messages, max_tokens, temperature):
    """Call Pollinations.ai — completely free, no API key needed."""
    try:
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
    models = [
        {'id': 'openai', 'name': 'GPT-OSS 20B', 'desc': 'Free reasoning model, no API key needed', 'provider': 'pollinations'},
        {'id': 'openai-fast', 'name': 'GPT-OSS 20B Fast', 'desc': 'Same model, optimized for speed', 'provider': 'pollinations'},
        {'id': 'mistralai/Mistral-7B-Instruct-v0.3', 'name': 'Mistral 7B (HF)', 'desc': 'Requires free HF token', 'provider': 'huggingface'},
        {'id': 'HuggingFaceH4/zephyr-7b-beta', 'name': 'Zephyr 7B (HF)', 'desc': 'Requires free HF token', 'provider': 'huggingface'},
        {'id': 'microsoft/Phi-3-mini-4k-instruct', 'name': 'Phi-3 Mini (HF)', 'desc': 'Requires free HF token', 'provider': 'huggingface'},
    ]
    return jsonify({'ok': True, 'models': models})


# ── Code Sharing ──────────────────────────────────────────────────────

@app.route('/api/share', methods=['POST'])
def share_code():
    """Save a code snippet and return a share ID."""
    data = request.get_json(force=True)
    code = data.get('code', '')
    language = data.get('language', 'python')
    if not code.strip():
        return jsonify({'ok': False, 'error': 'No code to share'}), 400
    share_id = hashlib.sha256(code.encode()).hexdigest()[:10]
    _shared_snippets[share_id] = {'code': code, 'language': language}
    return jsonify({'ok': True, 'id': share_id})


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


# ── Scripture Intel ──────────────────────────────────────────────────

@app.route('/bible')
def bible_page():
    return render_template('bible.html')


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


# ── The Truth (hidden page — cross in logo) ──────────────────────────

@app.route('/truth')
def truth_page():
    return render_template('truth.html')


if __name__ == '__main__':
    import webbrowser, threading
    threading.Timer(1.2, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=True, host='127.0.0.1', port=5000)
