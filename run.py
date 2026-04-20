#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import tempfile
import shutil
import os
import subprocess
import uuid
import requests as http_requests
from agent.agent import run_instruction
from agent import generate_with_llm
from flask import send_file
import io
import zipfile

app = Flask(__name__, static_folder='static', template_folder='templates')

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
    tempdir = tempfile.mkdtemp(prefix=f'batcode_{run_id}_')
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
        {'role': 'system', 'content': 'You are BatAgent, a helpful AI coding assistant. Provide clear, concise answers with code examples when relevant.'},
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


if __name__ == '__main__':
    import webbrowser, threading
    threading.Timer(1.2, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=True, host='127.0.0.1', port=5000)
