import io
import json
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
    assert 'topline-strip' in html
    assert 'LIVE // BREAKING' in html
    assert 'Mission Brief' in html
    assert 'Operational lanes for build, recon, and deployment' in html
    assert '/api/topline-intel?refresh=1' in html
    assert '/api/topline-intel' in html
    assert '3600000' in html
    assert 'computeDurationSeconds' in html
    assert 'normalizeClassToken' in html


def test_key_pages_share_current_bar_stylesheet_version():
    client = app.test_client()

    for route in ['/', '/ai', '/map', '/drone', '/weapons', '/weapons/armory', '/survival', '/bible', '/cyber', '/radio', '/truth']:
        response = client.get(route)
        html = response.get_data(as_text=True)

        assert response.status_code == 200, route
        assert "style.css?v=20260508r18" in html, route
        assert 'topline-strip' in html, route
        assert 'LIVE // BREAKING' in html, route


def test_weapons_page_includes_loadout_attachment_planner():
    client = app.test_client()

    response = client.get('/weapons')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Attachment Loadout Planner' in html
    assert 'href="/weapons/armory"' in html
    assert 'Weapon Setup Control' in html
    assert 'weaponLoadoutSelect' in html
    assert 'Attachment Rack' in html
    assert 'Loadout Manifest' in html
    assert 'Copy Loadout' in html
    assert 'deltaWeaponsLoadoutV1' in html


def test_weapons_armory_page_renders_reference_viewer_controls():
    client = app.test_client()

    response = client.get('/weapons/armory')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Armory Reference Viewer' in html
    assert 'armoryStageViewport' in html
    assert 'armoryStageImage' in html
    assert 'armoryAttachmentReferenceGrid' in html
    assert 'armoryWeaponList' in html
    assert 'Attachment Bench' in html
    assert 'armoryBenchScroll' in html
    assert 'Stock Source Unavailable' in html
    assert 'three.module.min.js' not in html
    assert 'id="armoryResetView"' not in html
    assert 'id="armoryToggleSpin"' not in html
    assert 'deltaWeaponsArmoryV1' in html


def test_topline_intel_api_returns_scripture_and_headlines(monkeypatch):
    sample_items = [
        {
            'kind': 'scripture',
            'label': 'HOURLY SCRIPTURE',
            'source': 'Scripture Intel',
            'reference': 'Psalm 27:1',
            'text': 'Psalm 27:1 — The LORD is my light and my salvation; whom shall I fear?',
            'url': '/bible',
        },
        {
            'kind': 'headline',
            'label': 'END TIME HEADLINES',
            'source': 'End Time Headlines',
            'text': 'Sample headline',
            'url': 'https://example.com/story',
        },
    ]
    monkeypatch.setattr(run_module, '_get_topline_items', lambda force_refresh=False: sample_items)

    client = app.test_client()
    response = client.get('/api/topline-intel')
    data = response.get_json()

    assert response.status_code == 200
    assert data['ok'] is True
    assert data['items'] == sample_items
    assert data['generated_at']


def test_topline_items_place_scripture_once_per_full_headline_loop(monkeypatch):
    scripture_item = {
        'kind': 'scripture',
        'source_key': 'scripture-intel',
        'label': 'HOURLY SCRIPTURE',
        'source': 'Scripture Intel',
        'reference': 'Psalm 91:1',
        'text': 'Psalm 91:1 — He that dwelleth in the secret place of the most High shall abide under the shadow of the Almighty.',
        'url': '/bible',
    }
    source_items = {
        'alex-jones-show': [
            {'kind': 'headline', 'source_key': 'alex-jones-show', 'label': 'ALEX JONES SHOW', 'source': 'Alex Jones Show', 'text': 'Alex one', 'url': 'https://example.com/alex-1'},
            {'kind': 'headline', 'source_key': 'alex-jones-show', 'label': 'ALEX JONES SHOW', 'source': 'Alex Jones Show', 'text': 'Alex two', 'url': 'https://example.com/alex-2'},
        ],
        'end-time-headlines': [
            {'kind': 'headline', 'source_key': 'end-time-headlines', 'label': 'END TIME HEADLINES', 'source': 'End Time Headlines', 'text': 'ETH one', 'url': 'https://example.com/eth-1'},
            {'kind': 'headline', 'source_key': 'end-time-headlines', 'label': 'END TIME HEADLINES', 'source': 'End Time Headlines', 'text': 'ETH two', 'url': 'https://example.com/eth-2'},
        ],
    }

    monkeypatch.setattr(run_module, 'TOPLINE_CACHE', {'timestamp': 0.0, 'items': []})
    monkeypatch.setattr(run_module, 'TOPLINE_SOURCE_CACHE', {})
    monkeypatch.setattr(run_module, '_current_hourly_scripture', lambda now=None: scripture_item)
    monkeypatch.setattr(run_module, '_fetch_topline_source', lambda source, limit=4: source_items[source['key']])

    items = run_module._get_topline_items(force_refresh=True)

    assert [item['text'] for item in items[:5]] == [
        'Alex one',
        'ETH one',
        'Alex two',
        'ETH two',
        scripture_item['text'],
    ]


def test_real_alex_jones_shopify_products_are_parsed_for_topline():
    payload = json.dumps({
        'products': [
            {'title': 'The Men’s Drive Stack', 'handle': 'the-men-s-drive-stack'},
            {'title': 'BOGOS.io Free Gift', 'handle': 'free-gift-ignore-me'},
            {'title': 'No Peace Limited Edition Fundraiser Poster', 'handle': 'no-peace-limited-edition-poster-1'},
            {'title': 'The Men’s Drive Stack', 'handle': 'duplicate-the-mens-drive-stack'},
        ]
    })

    items = run_module._parse_topline_json_items(payload, {
        'key': 'alex-jones-show',
        'name': 'Real Alex Jones',
        'label': 'REAL ALEX JONES',
        'link': 'https://realalexjones.com/',
    }, limit=4)

    assert items == [
        {
            'kind': 'headline',
            'source_key': 'alex-jones-show',
            'label': 'REAL ALEX JONES',
            'source': 'Real Alex Jones',
            'text': 'The Men’s Drive Stack',
            'url': 'https://realalexjones.com/products/the-men-s-drive-stack',
        },
        {
            'kind': 'headline',
            'source_key': 'alex-jones-show',
            'label': 'REAL ALEX JONES',
            'source': 'Real Alex Jones',
            'text': 'No Peace Limited Edition Fundraiser Poster',
            'url': 'https://realalexjones.com/products/no-peace-limited-edition-poster-1',
        },
    ]


def test_service_worker_and_manifest_routes_exist():
    client = app.test_client()

    sw_response = client.get('/sw.js')
    manifest_response = client.get('/site.webmanifest')
    offline_response = client.get('/static/offline.html')
    sw_text = sw_response.get_data(as_text=True)

    assert sw_response.status_code == 200
    assert sw_response.mimetype == 'application/javascript'
    assert sw_response.headers['Service-Worker-Allowed'] == '/'
    assert "const SHELL_CACHE = 'delta-shell-v14';" in sw_text
    assert "event.respondWith(cacheFirst(request, SHELL_CACHE, false));" in sw_text
    assert manifest_response.status_code == 200
    assert manifest_response.mimetype == 'application/manifest+json'
    assert offline_response.status_code == 200
    assert 'Delta Offline Mode' in offline_response.get_data(as_text=True)
    assert '/weapons' in sw_text
    assert '/weapons/armory' in sw_text
    assert '/mechanics/blueprints' in sw_text
    assert '/manuals' in sw_text
    assert '/live' in sw_text
    assert '/static/offline.html' in sw_text
    assert '/static/vendor/leaflet/leaflet.css' in sw_text
    assert '/static/vendor/leaflet/leaflet.js' in sw_text
    assert '/static/vendor/leaflet/images/layers.png' in sw_text
    assert '/static/vendor/html2canvas.min.js' in sw_text
    assert '/static/vendor/three/three.module.min.js' not in sw_text
    assert "count: 0" in sw_text


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
        assert 'delta-coding-offline/install_delta.command' in names
        assert 'delta-coding-offline/install_delta.bat' in names

        readme = archive.read('delta-coding-offline/README_OFFLINE.txt').decode('utf-8')
        launcher_sh = archive.read('delta-coding-offline/start_delta.command').decode('utf-8')
        launcher_bat = archive.read('delta-coding-offline/start_delta.bat').decode('utf-8')
        assert 'python run.py' in readme
        assert 'install_delta.bat' in readme
        assert 'offline app window' in readme
        assert 'public map tiles' in readme
        assert 'OPEN_APP=1' in launcher_sh
        assert 'set OPEN_APP=1' in launcher_bat


def test_map_page_includes_planning_controls():
    client = app.test_client()

    response = client.get('/map')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<head>' in html
    assert '/static/vendor/leaflet/leaflet.css' in html
    assert '/static/vendor/leaflet/leaflet.js' in html
    assert '/static/vendor/html2canvas.min.js' in html
    assert '<div id="map"></div>' in html
    assert 'Offline Atlas' in html
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


def test_radio_footer_uses_original_bar_with_toggle_controls():
    client = app.test_client()

    response = client.get('/radio')
    html = response.get_data(as_text=True)
    footer_html = html.split('<div class="radio-footer" id="radio-footer">', 1)[1].split('<button class="radio-footer-open"', 1)[0]

    assert response.status_code == 200
    assert 'NOAA Weather</option>' in footer_html
    assert 'Scanner Radio</option>' in footer_html
    assert 'End Time Headlines Live</option>' in footer_html
    assert 'Prophecy Today Radio</option>' in footer_html
    assert 'Deep Space Radio</option>' not in footer_html
    assert 'rf-label' not in footer_html
    assert 'class="rf-settings"' in footer_html
    assert 'href="/radio"' in footer_html
    assert 'rf-toggle' not in footer_html
    assert 'radio-footer-open' not in html
    assert 'deltaRadioBarCollapsed' not in html


def test_radio_lab_includes_frequency_settings_and_new_live_sources():
    client = app.test_client()

    response = client.get('/radio')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'End Time Headlines Live' in html
    assert 'Prophecy Today Radio' in html
    assert 'FREQUENCY SETTINGS &amp; BAND PLAN' in html
    assert 'Ham / Amateur' in html
    assert 'VHF / Marine' in html
    assert 'CB / 11 Meter' in html
    assert 'HF / Long Haul' in html
    assert '156.050 MHz' in html
    assert '156.100 MHz' in html
    assert '26.965' in html
    assert '11175 kHz' in html


def test_mechanics_blueprints_render_compact_reference_list():
    client = app.test_client()

    response = client.get('/mechanics/blueprints')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Mechanics Blueprint Reference' in html
    assert '1984 Toyota Pickup 4x4' in html
    assert '1996 Toyota Corolla' in html
    assert '2015 Ford F-150' in html
    assert 'Filter mechanic roster' in html
    assert 'Linked Workbench Files' in html
    assert 'Mechanic Notes' in html
    assert 'Open Detail' in html


def test_drone_page_supports_multi_drone_roster_and_tracking_map():
    client = app.test_client()

    response = client.get('/drone')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'DRONE COMMAND CENTER' in html
    assert 'CONNECTED DRONE ROSTER' in html
    assert 'id="drone-source-type"' in html
    assert 'id="drone-camera-device"' in html
    assert 'id="fleet-feed-strip"' in html
    assert 'id="fleet-list"' in html
    assert 'id="drone-mini-map"' in html
    assert 'FOLLOW ACTIVE' in html
    assert 'Bridge: Tello / RTSP' in html
    assert 'QUAD VIEW FLIGHT DECK' in html
    assert 'id="quad-grid"' in html
    assert 'id="drone-bridge-endpoint"' in html
    assert 'id="drone-bridge-token"' in html
    assert 'id="drone-bridge-preset"' in html
    assert 'SYNC SESSIONS' in html
    assert 'SAVE PRESET' in html


def test_drone_bridge_api_reports_capabilities_and_sessions():
    client = app.test_client()

    capabilities_response = client.get('/api/drone/bridge/capabilities')
    capabilities = capabilities_response.get_json()
    sessions_response = client.get('/api/drone/bridge/sessions')
    sessions = sessions_response.get_json()

    assert capabilities_response.status_code == 200
    assert capabilities['ok'] is True
    assert 'tello' in capabilities['capabilities']
    assert 'rtsp' in capabilities['capabilities']

    assert sessions_response.status_code == 200
    assert sessions['ok'] is True
    assert isinstance(sessions['sessions'], list)


def test_drone_page_csp_allows_remote_bridge_hosts():
    client = app.test_client()

    response = client.get('/drone')
    csp = response.headers.get('Content-Security-Policy', '')

    assert response.status_code == 200
    assert "connect-src 'self' https: http:" in csp
    assert "img-src 'self' data: blob: https: http:" in csp