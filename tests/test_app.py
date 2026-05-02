import io
import zipfile

import run as run_module
from run import app


def test_healthz():
    client = app.test_client()
    response = client.get('/healthz')

    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'service': 'delta-coding'}


def test_index_includes_pwa_registration_injection():
    client = app.test_client()
    response = client.get('/')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '/site.webmanifest' in html
    assert '/static/pwa-register.js' in html


def test_service_worker_and_manifest_routes_exist():
    client = app.test_client()

    sw_response = client.get('/sw.js')
    manifest_response = client.get('/site.webmanifest')

    assert sw_response.status_code == 200
    assert sw_response.mimetype == 'application/javascript'
    assert sw_response.headers['Service-Worker-Allowed'] == '/'
    assert manifest_response.status_code == 200
    assert manifest_response.mimetype == 'application/manifest+json'


def test_api_ai_models_only_returns_key_free_models_by_default(monkeypatch):
    monkeypatch.delenv('HF_API_TOKEN', raising=False)

    client = app.test_client()
    response = client.get('/api/ai/models')
    data = response.get_json()

    assert response.status_code == 200
    assert data['ok'] is True
    assert [model['id'] for model in data['models']] == ['openai', 'openai-fast']
    assert {model['provider'] for model in data['models']} == {'pollinations'}


def test_share_api_round_trip_for_web_payload():
    client = app.test_client()
    payload = {
        'language': 'web',
        'code': '<h1>Delta</h1>',
        'css': 'body { color: olive; }',
        'js': 'console.log("delta");',
    }

    create_response = client.post('/api/share', json=payload)
    create_data = create_response.get_json()
    share_id = create_data['id']

    assert create_response.status_code == 200
    assert create_data['ok'] is True
    assert create_data['share_url'] == f'/share/{share_id}'

    fetch_response = client.get(f'/api/share/{share_id}')
    assert fetch_response.status_code == 200
    assert fetch_response.get_json() == {'ok': True, **payload}

    share_page_response = client.get(f'/share/{share_id}')
    assert share_page_response.status_code == 200


def test_truth_summary_uses_backend_pollinations(monkeypatch):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                'choices': [{'message': {'content': 'Summary text'}}],
                'model': 'openai-fast',
            }

    def fake_post(url, json=None, timeout=None):
        assert url == run_module.POLLINATIONS_URL
        assert json['model'] == 'openai-fast'
        assert timeout == 45
        return DummyResponse()

    monkeypatch.setattr(run_module.http_requests, 'post', fake_post)

    client = app.test_client()
    response = client.post('/api/truth/summary', json={'query': 'Why does the resurrection matter?'})
    data = response.get_json()

    assert response.status_code == 200
    assert data == {
        'ok': True,
        'text': 'Summary text',
        'model': 'openai-fast',
        'provider': 'pollinations',
    }


def test_offline_site_bundle_contains_runnable_site_files():
    client = app.test_client()

    response = client.get('/download/offline-site')

    assert response.status_code == 200
    assert response.mimetype == 'application/zip'

    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = set(archive.namelist())

        assert 'delta-coding-offline/run.py' in names
        assert 'delta-coding-offline/requirements.txt' in names
        assert 'delta-coding-offline/templates/index.html' in names
        assert 'delta-coding-offline/templates/map.html' in names
        assert 'delta-coding-offline/static/style.css' in names
        assert 'delta-coding-offline/agent/agent.py' in names
        assert 'delta-coding-offline/README_OFFLINE.txt' in names
        assert 'delta-coding-offline/start_delta.command' in names
        assert 'delta-coding-offline/start_delta.bat' in names

        readme = archive.read('delta-coding-offline/README_OFFLINE.txt').decode('utf-8')
        assert 'python run.py' in readme
        assert 'public map tiles' in readme


def test_map_page_includes_planning_controls():
    client = app.test_client()

    response = client.get('/map')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'ROUTE PLANNING' in html
    assert 'planning-travel-mode' in html
    assert 'planning-air-overlay' in html
    assert 'planning-export-route' in html
    assert 'planning-waypoint-list' in html
    assert 'planning-save-route' in html
    assert 'planning-saved-routes' in html
    assert 'planning-print-manifest' in html
    assert 'data-waypoint-up' in html


def test_survival_page_and_shared_stealth_nav_render():
    client = app.test_client()

    survival_response = client.get('/survival')
    survival_html = survival_response.get_data(as_text=True)
    mechanics_response = client.get('/mechanics')
    mechanics_html = mechanics_response.get_data(as_text=True)
    index_response = client.get('/')
    index_html = index_response.get_data(as_text=True)

    assert survival_response.status_code == 200
    assert 'CIVILIAN BUGOUT BUILDER' in survival_html
    assert 'deltaSurvivalLoadoutV1' in survival_html
    assert 'Realistic Bugout Bag Options' in survival_html
    assert 'Executive Brief Sling' in survival_html
    assert 'Family Evac Duffel' in survival_html
    assert 'Office Exit' in survival_html
    assert 'Folding Solar Panel' in survival_html
    assert 'Stealth' in survival_html
    assert '/download/offline-site' in survival_html

    assert mechanics_response.status_code == 200
    assert 'FIELD MECHANICS INDEX' in mechanics_html
    assert 'Mechanics' in mechanics_html
    assert 'Service Bay' in mechanics_html
    assert 'Recovery Kit' in mechanics_html
    assert 'Mechanics Operations Board' in mechanics_html
    assert '/mechanics/browser' in mechanics_html
    assert '/mechanics/blueprints' in mechanics_html
    assert '/manuals' in mechanics_html
    assert '/admin/manuals' in mechanics_html
    assert 'Recent Workbench Entries' in mechanics_html

    assert index_response.status_code == 200
    assert 'Stealth' in index_html
    assert '/survival' in index_html
    assert '/mechanics' in index_html
    assert 'Ops Center' in index_html
    assert 'AI Recon' in index_html