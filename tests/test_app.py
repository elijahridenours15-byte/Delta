import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import run as run_module
from run import app
from security_hardening import secure_config


def test_healthz():
    client = app.test_client()
    response = client.get('/healthz')

    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'service': 'delta-coding'}


def test_ads_txt_serves_google_seller_record():
    client = app.test_client()
    response = client.get('/ads.txt')
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == 'text/plain'
    assert 'google.com, pub-2518102292380228, DIRECT, f08c47fec0942fa0' in body


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
    assert '/survival/meal-bible' in html
    assert '/culture' in html
    assert '/schooling' in html


def test_key_pages_share_current_bar_stylesheet_version():
    client = app.test_client()

    for route in ['/', '/ai', '/map', '/drone', '/weapons', '/weapons/armory', '/survival', '/survival/army-library', '/survival/army-library/offline', '/bible', '/cyber', '/radio', '/schooling', '/truth', '/advertise']:
        response = client.get(route)
        html = response.get_data(as_text=True)

        assert response.status_code == 200, route
        assert "style.css?v=20260527r2" in html, route
        assert 'topline-strip' in html, route
        assert 'LIVE // BREAKING' in html, route


def test_shared_stylesheet_contains_mobile_shell_rules():
    client = app.test_client()
    response = client.get('/static/style.css')
    css = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '.top-nav .nav-dropdown-menu{position:static' in css
    assert '.top-nav > a:not(.online-indicator){display:none}' in css
    assert '@media(max-width:480px){' in css
    assert '.top-nav{grid-template-columns:1fr}' in css
    assert '.topline-strip{grid-template-columns:1fr;min-height:auto}' in css


def _configure_temp_auth_db(tmp_path, monkeypatch):
    auth_path = tmp_path / 'auth.db'
    monkeypatch.setattr(run_module, 'auth_db_path', str(auth_path))
    run_module._init_auth_db()
    run_module.rate_limiter.ip_requests.clear()
    run_module.rate_limiter.endpoint_requests.clear()
    run_module.rate_limiter.blocked_ips.clear()


def _configure_temp_journal_db(tmp_path, monkeypatch):
    journal_path = tmp_path / 'journal.db'
    monkeypatch.setattr(run_module, 'journal_db_path', str(journal_path))
    run_module._init_journal_db()


def test_secure_config_persists_fallback_secret_key(tmp_path, monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    first_app = Flask('first-app', root_path=str(tmp_path))
    second_app = Flask('second-app', root_path=str(tmp_path))

    secure_config(first_app)
    secure_config(second_app)

    secret_path = tmp_path / 'generated' / 'runtime_secret.key'

    assert first_app.secret_key
    assert first_app.secret_key == second_app.secret_key
    assert first_app.config['SESSION_COOKIE_PATH'] == '/'
    assert first_app.config['SESSION_REFRESH_EACH_REQUEST'] is True
    assert secret_path.exists()
    assert secret_path.read_text(encoding='utf-8').strip() == first_app.secret_key


def test_shared_nav_renders_account_sync_controls():
    client = app.test_client()
    response = client.get('/')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="nav-auth"' in html
    assert '/static/auth.js?v=20260527r4' in html
    assert 'Save site progression, map planning, and loadout work to your account.' in html
    assert 'Reset Passcode' in html
    assert 'New Recovery Key' in html


def test_auth_register_login_logout_and_state_round_trip(tmp_path, monkeypatch):
    _configure_temp_auth_db(tmp_path, monkeypatch)
    client = app.test_client()

    register_response = client.post('/api/auth/register', json={
        'username': 'DeltaUser',
        'password': 'Passcode99',
        'remember': True,
    })
    register_json = register_response.get_json()

    assert register_response.status_code == 200
    assert register_response.headers['Cache-Control'] == 'no-store'
    assert register_json['ok'] is True
    assert register_json['authenticated'] is True
    assert register_json['user']['username'] == 'DeltaUser'
    assert register_json['user']['has_recovery_key'] is True
    assert register_json['recovery_key'].startswith('DCRK-')
    assert register_json['recovery_generated_at'].endswith('Z')

    session_response = client.get('/api/auth/session')
    session_json = session_response.get_json()

    assert session_response.status_code == 200
    assert session_json['authenticated'] is True
    assert session_json['user']['username'] == 'DeltaUser'

    save_response = client.post('/api/auth/state', json={
        'storage': {
            'deltaMapPrefsV1': '{"zoom":4}',
            'deltaLoadoutV1': '{"kit":"alpha"}',
            'deltaAuthSessionStorage:survivalArmyLibraryState': '{"selected":["fm3-90"]}',
        },
    })
    save_json = save_response.get_json()

    assert save_response.status_code == 200
    assert save_json['ok'] is True
    assert save_json['keys'] == 3

    state_response = client.get('/api/auth/state')
    state_json = state_response.get_json()

    assert state_response.status_code == 200
    assert state_json['storage']['deltaMapPrefsV1'] == '{"zoom":4}'
    assert state_json['storage']['deltaLoadoutV1'] == '{"kit":"alpha"}'
    assert state_json['storage']['deltaAuthSessionStorage:survivalArmyLibraryState'] == '{"selected":["fm3-90"]}'

    page_response = client.get('/')
    page_html = page_response.get_data(as_text=True)

    assert page_response.status_code == 200
    assert page_response.headers['Cache-Control'] == 'no-store'
    assert 'Cookie' in page_response.headers['Vary']
    assert 'DeltaUser' in page_html
    assert 'delta-auth-bootstrap' in page_html

    logout_response = client.post('/api/auth/logout')
    logout_json = logout_response.get_json()

    assert logout_response.status_code == 200
    assert logout_json['authenticated'] is False

    relogin_response = client.post('/api/auth/login', json={
        'username': 'deltauser',
        'password': 'Passcode99',
        'remember': False,
    })
    relogin_json = relogin_response.get_json()

    assert relogin_response.status_code == 200
    assert relogin_json['authenticated'] is True
    assert relogin_json['user']['username'] == 'DeltaUser'

    stale_save_response = client.post('/api/auth/state', json={
        'storage': {
            'deltaLoadoutV1': '{"kit":"stale"}',
        },
    })
    stale_save_json = stale_save_response.get_json()

    assert stale_save_response.status_code == 200
    assert stale_save_json['ok'] is True
    assert stale_save_json['restored'] is True

    relogin_state_response = client.get('/api/auth/state')
    relogin_state_json = relogin_state_response.get_json()

    assert relogin_state_response.status_code == 200
    assert relogin_state_json['storage']['deltaLoadoutV1'] == '{"kit":"alpha"}'
    assert relogin_state_json['storage']['deltaAuthSessionStorage:survivalArmyLibraryState'] == '{"selected":["fm3-90"]}'


def test_auth_recovery_key_rotation_and_passcode_reset(tmp_path, monkeypatch):
    _configure_temp_auth_db(tmp_path, monkeypatch)
    client = app.test_client()

    register_response = client.post('/api/auth/register', json={
        'username': 'RecoveryUser',
        'password': 'Passcode99',
        'remember': False,
    })
    register_json = register_response.get_json()
    original_recovery_key = register_json['recovery_key']

    rotate_response = client.post('/api/auth/recovery-key')
    rotate_json = rotate_response.get_json()
    rotated_recovery_key = rotate_json['recovery_key']

    assert rotate_response.status_code == 200
    assert rotate_json['ok'] is True
    assert rotated_recovery_key.startswith('DCRK-')
    assert rotated_recovery_key != original_recovery_key

    client.post('/api/auth/logout')

    old_reset_response = client.post('/api/auth/reset-password', json={
        'username': 'RecoveryUser',
        'recovery_key': original_recovery_key,
        'password': 'NewPasscode42',
        'remember': False,
    })
    old_reset_json = old_reset_response.get_json()

    assert old_reset_response.status_code == 401
    assert old_reset_json['error'] == 'invalid login id or recovery key'

    reset_response = client.post('/api/auth/reset-password', json={
        'username': 'RecoveryUser',
        'recovery_key': rotated_recovery_key,
        'password': 'NewPasscode42',
        'remember': True,
    })
    reset_json = reset_response.get_json()

    assert reset_response.status_code == 200
    assert reset_json['ok'] is True
    assert reset_json['authenticated'] is True
    assert reset_json['user']['username'] == 'RecoveryUser'
    assert reset_json['recovery_key'].startswith('DCRK-')
    assert reset_json['recovery_key'] != rotated_recovery_key

    client.post('/api/auth/logout')

    old_login_response = client.post('/api/auth/login', json={
        'username': 'RecoveryUser',
        'password': 'Passcode99',
        'remember': False,
    })
    new_login_response = client.post('/api/auth/login', json={
        'username': 'RecoveryUser',
        'password': 'NewPasscode42',
        'remember': False,
    })

    assert old_login_response.status_code == 401
    assert new_login_response.status_code == 200


def test_auth_state_requires_login(tmp_path, monkeypatch):
    _configure_temp_auth_db(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.get('/api/auth/state')
    data = response.get_json()

    assert response.status_code == 401
    assert data['ok'] is False
    assert data['error'] == 'authentication required'


def test_advertise_page_renders_sponsor_inventory_and_ads_alias():
    client = app.test_client()

    advertise_response = client.get('/advertise')
    advertise_html = advertise_response.get_data(as_text=True)
    ads_response = client.get('/ads')

    assert advertise_response.status_code == 200
    assert ads_response.status_code == 200
    assert 'Advertise With Delta' in advertise_html
    assert 'Dedicated Monetization Surface' in advertise_html
    assert 'Direct Sponsorship Inventory' in advertise_html
    assert 'Hero Sponsor Canvas' in advertise_html
    assert 'AdSense Or Network Embed' in advertise_html
    assert 'Book Ad Space' in advertise_html
    assert 'submitAdInquiry' in advertise_html
    assert '$129' in advertise_html
    assert 'copyAdPitch' in advertise_html
    assert 'copyPlacementSpecs' in advertise_html
    assert 'href="/advertise" class="active"' in advertise_html


def test_advertise_inquiry_api_stores_and_lists_booking_leads():
    client = app.test_client()

    submit_response = client.post('/api/advertise/inquiry', json={
        'company': 'Acme Optics',
        'contact_name': 'Dana Roe',
        'email': 'dana@acme.test',
        'website': 'https://acme.test',
        'placement': 'Hero Sponsor',
        'budget': '$129',
        'start_window': 'June 2026',
        'notes': 'Interested in a 30 day headline placement.',
    })
    submit_json = submit_response.get_json()

    list_response = client.get('/api/advertise/inquiries')
    list_json = list_response.get_json()

    assert submit_response.status_code == 200
    assert submit_json['ok'] is True
    assert submit_json['id'] >= 1
    assert list_response.status_code == 200
    assert list_json['ok'] is True
    assert any(inquiry['company'] == 'Acme Optics' and inquiry['placement'] == 'Hero Sponsor' for inquiry in list_json['inquiries'])


def test_advertise_inquiry_api_requires_required_fields():
    client = app.test_client()

    response = client.post('/api/advertise/inquiry', json={
        'company': '',
        'contact_name': '',
        'email': 'broken',
        'placement': '',
    })
    data = response.get_json()

    assert response.status_code == 400
    assert data['ok'] is False


def test_advertise_inquiry_api_rate_limits_repeat_posts(monkeypatch):
    client = app.test_client()
    payload = {
        'company': 'Acme Optics',
        'contact_name': 'Dana Roe',
        'email': 'dana@acme.test',
        'placement': 'Hero Sponsor',
    }

    run_module.rate_limiter.ip_requests.clear()
    run_module.rate_limiter.endpoint_requests.clear()
    run_module.rate_limiter.blocked_ips.clear()
    monkeypatch.setattr(
        run_module,
        'REQUEST_RATE_LIMITS',
        {**run_module.REQUEST_RATE_LIMITS, ('POST', '/api/advertise/inquiry'): (1, 60)},
    )

    first_response = client.post('/api/advertise/inquiry', json=payload)
    second_response = client.post('/api/advertise/inquiry', json=payload)
    data = second_response.get_json()

    assert first_response.status_code == 200
    assert second_response.status_code == 429
    assert data['ok'] is False
    assert data['error'] == 'rate limit exceeded'
    assert second_response.headers['Cache-Control'] == 'no-store'
    assert second_response.headers['Retry-After'] == '60'


def test_bible_page_never_renders_admin_token_value(monkeypatch):
    client = app.test_client()
    monkeypatch.setenv('ADMIN_TOKEN', 'super-secret-token')

    response = client.get('/bible')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'super-secret-token' not in html
    assert 'Public Scripture Journal' in html
    assert 'Personal Journal' in html
    assert 'deltaBiblePersonalJournalV1' in html
    assert 'deltaBibleTextToneV1' in html
    assert 'deltaBibleHighlightColorV1' in html
    assert 'deltaBibleVerseHighlightsV1' in html
    assert 'type="color"' in html
    assert 'id="bible-text-color"' in html
    assert 'id="bible-text-color-reset"' in html
    assert 'id="bible-highlight-color"' in html
    assert 'id="bible-highlight-clear"' in html
    assert 'journal-moderation-form' in html
    assert 'id="verse-search"' not in html
    assert 'id="ws-input"' not in html
    assert 'id="ws-results"' not in html
    assert 'id="bible-audio-bar"' not in html
    assert 'id="ba-listen"' not in html
    assert 'REAL VOICE — KJV AUDIO' not in html
    scripture_box_index = html.index('id="bible-scripture-box"')
    translation_index = html.index('id="translation"')
    book_index = html.index('id="book"')
    chapter_index = html.index('id="chapter"')
    load_index = html.index('id="btn-load"')
    prev_index = html.index('id="prev-ch"')
    next_index = html.index('id="next-ch"')
    text_index = html.index('id="bible-text"')
    assert scripture_box_index < translation_index < book_index < chapter_index < load_index < prev_index < next_index < text_index
    assert 'name="admin_token"' not in html


def test_public_scripture_journal_requires_login_and_returns_account_authored_entries(tmp_path, monkeypatch):
    _configure_temp_auth_db(tmp_path, monkeypatch)
    _configure_temp_journal_db(tmp_path, monkeypatch)
    client = app.test_client()

    unauthorized_response = client.post('/api/journal/add', json={
        'entry': 'Grace carried me through this chapter.',
        'passage': 'John 1 • WEB',
    })

    assert unauthorized_response.status_code == 401
    assert unauthorized_response.get_json()['error'] == 'sign in to post to the public journal'

    register_response = client.post('/api/auth/register', json={
        'username': 'fieldscribe',
        'password': 'securepass123',
        'remember': True,
    })
    assert register_response.status_code == 200
    assert register_response.get_json()['ok'] is True

    add_response = client.post('/api/journal/add', json={
        'entry': 'Grace carried me through this chapter.',
        'passage': 'John 1 • WEB',
    })
    add_json = add_response.get_json()

    assert add_response.status_code == 201
    assert add_json['ok'] is True
    assert add_json['entry']['author'] == 'fieldscribe'
    assert add_json['entry']['passage'] == 'John 1 • WEB'
    assert add_json['entry']['entry'] == 'Grace carried me through this chapter.'

    list_response = client.get('/api/journal')
    list_json = list_response.get_json()

    assert list_response.status_code == 200
    assert list_json['ok'] is True
    assert list_json['entries'][0]['author'] == 'fieldscribe'
    assert list_json['entries'][0]['passage'] == 'John 1 • WEB'
    assert list_json['entries'][0]['entry'] == 'Grace carried me through this chapter.'


def test_public_scripture_journal_moderation_requires_password_and_can_delete_entries(tmp_path, monkeypatch):
    _configure_temp_auth_db(tmp_path, monkeypatch)
    _configure_temp_journal_db(tmp_path, monkeypatch)
    monkeypatch.setenv(
        'BIBLE_JOURNAL_MODERATOR_PASSWORD_SHA256',
        hashlib.sha256('moderator-pass'.encode('utf-8')).hexdigest(),
    )
    client = app.test_client()

    register_response = client.post('/api/auth/register', json={
        'username': 'fieldscribe',
        'password': 'securepass123',
        'remember': True,
    })
    assert register_response.status_code == 200

    add_response = client.post('/api/journal/add', json={
        'entry': 'Keep the watch and test the spirits.',
        'passage': '1 John 4 • WEB',
    })
    assert add_response.status_code == 201
    entry_id = add_response.get_json()['entry']['id']

    unauthorized_delete = client.post('/api/journal/moderation/delete', json={'id': entry_id})
    assert unauthorized_delete.status_code == 403
    assert unauthorized_delete.get_json()['error'] == 'moderation access required'

    failed_login = client.post('/api/journal/moderation/login', json={'password': 'wrong-pass'})
    assert failed_login.status_code == 403
    assert failed_login.get_json()['error'] == 'invalid moderation password'

    login_response = client.post('/api/journal/moderation/login', json={'password': 'moderator-pass'})
    assert login_response.status_code == 200
    assert login_response.get_json()['moderation_enabled'] is True

    list_response = client.get('/api/journal')
    list_json = list_response.get_json()
    assert list_json['moderation_enabled'] is True
    assert list_json['entries'][0]['id'] == entry_id

    delete_response = client.post('/api/journal/moderation/delete', json={'id': entry_id})
    assert delete_response.status_code == 200
    assert delete_response.get_json()['deleted_id'] == entry_id

    final_list_response = client.get('/api/journal')
    final_list_json = final_list_response.get_json()
    assert final_list_json['entries'] == []

    logout_response = client.post('/api/journal/moderation/logout')
    assert logout_response.status_code == 200
    assert logout_response.get_json()['moderation_enabled'] is False


def test_pages_do_not_render_footer_sponsor_slot_with_adsense_config(tmp_path, monkeypatch):
    config_path = tmp_path / 'monetization_config.json'
    config_path.write_text(json.dumps({
        'adsense_client_id': 'ca-pub-1234567890123456',
        'default_slot': '1111111111',
        'slots': {
            'survival': '2222222222',
            'drone': '3333333333',
        },
    }), encoding='utf-8')
    monkeypatch.setattr(run_module, 'monetization_config_path', str(config_path))

    client = app.test_client()
    survival_response = client.get('/survival')
    survival_library_response = client.get('/survival/army-library')
    survival_offline_response = client.get('/survival/army-library/offline')
    survival_binder_response = client.get('/survival/army-library/binder')
    drone_response = client.get('/drone')
    advertise_response = client.get('/advertise')

    survival_html = survival_response.get_data(as_text=True)
    survival_library_html = survival_library_response.get_data(as_text=True)
    survival_offline_html = survival_offline_response.get_data(as_text=True)
    survival_binder_html = survival_binder_response.get_data(as_text=True)
    drone_html = drone_response.get_data(as_text=True)
    advertise_html = advertise_response.get_data(as_text=True)

    assert survival_response.status_code == 200
    assert survival_library_response.status_code == 200
    assert survival_offline_response.status_code == 200
    assert survival_binder_response.status_code == 200
    assert drone_response.status_code == 200
    assert advertise_response.status_code == 200

    assert 'Footer sponsor slot' not in survival_html
    assert 'data-ad-client="ca-pub-1234567890123456"' not in survival_html
    assert 'data-ad-slot="2222222222"' not in survival_html
    assert 'Footer sponsor slot' not in survival_library_html
    assert 'data-ad-slot="2222222222"' not in survival_library_html
    assert 'Footer sponsor slot' not in survival_offline_html
    assert 'data-ad-slot="2222222222"' not in survival_offline_html
    assert 'Footer sponsor slot' not in survival_binder_html
    assert 'data-ad-slot="2222222222"' not in survival_binder_html
    assert 'data-ad-slot="3333333333"' not in drone_html
    assert 'Footer sponsor slot' not in advertise_html


def test_pages_do_not_render_auto_ads_placeholder_lane(tmp_path, monkeypatch):
    config_path = tmp_path / 'monetization_config.json'
    config_path.write_text(json.dumps({
        'adsense_client_id': 'pub-2518102292380228',
        'default_slot': '',
        'slots': {},
    }), encoding='utf-8')
    monkeypatch.setattr(run_module, 'monetization_config_path', str(config_path))

    client = app.test_client()
    survival_response = client.get('/survival')
    drone_response = client.get('/drone')
    manuals_response = client.get('/manuals')
    advertise_response = client.get('/advertise')

    survival_html = survival_response.get_data(as_text=True)
    drone_html = drone_response.get_data(as_text=True)
    manuals_html = manuals_response.get_data(as_text=True)
    advertise_html = advertise_response.get_data(as_text=True)

    assert survival_response.status_code == 200
    assert drone_response.status_code == 200
    assert manuals_response.status_code == 200
    assert advertise_response.status_code == 200

    assert 'Google Auto Ads active' not in survival_html
    assert 'pagead/js/adsbygoogle.js?client=ca-pub-2518102292380228' in survival_html
    assert 'Footer sponsor slot' not in survival_html
    assert 'Second themed lane' not in survival_html
    assert 'enable_page_level_ads' not in survival_html
    assert 'Google Auto Ads active' not in drone_html
    assert 'enable_page_level_ads' not in drone_html
    assert 'Footer sponsor slot' not in manuals_html
    assert 'Second themed lane' not in manuals_html
    assert 'Footer sponsor slot' not in advertise_html


def test_adsense_loader_is_injected_into_head_for_html_pages(tmp_path, monkeypatch):
    config_path = tmp_path / 'monetization_config.json'
    config_path.write_text(json.dumps({
        'adsense_client_id': 'pub-2518102292380228',
        'default_slot': '',
        'slots': {},
    }), encoding='utf-8')
    monkeypatch.setattr(run_module, 'monetization_config_path', str(config_path))

    client = app.test_client()
    response = client.get('/survival')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-2518102292380228' in html
    assert html.index('pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-2518102292380228') < html.index('</head>')


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
    assert 'Muzzle / Flight Speed' in html
    assert '880 m/s' in html
    assert '115 m/s launch, 295 m/s flight' in html
    assert '470 m/s (.50 AE)' in html
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
    assert 'armoryWeaponSpecGrid' in html
    assert 'armory-roster-spec' in html
    assert 'Attachment Bench' in html
    assert 'armoryBenchScroll' in html
    assert 'Pew Pew Tactical ACOG guide' in html
    assert 'Muzzle / Flight Speed' in html
    assert 'Capacity / Payload' in html
    assert 'Feed System' in html
    assert 'Service / Operators' in html
    assert 'Technical Reference' in html
    assert 'Platform Notes' in html
    assert 'TM 9-1005-319-10' in html
    assert '880 m/s' in html
    assert '30-round STANAG magazine' in html
    assert 'Belt-fed, disintegrating link' in html
    assert 'Standard US infantry carbine with 14.5-inch barrel and SOPMOD rail support.' in html
    assert 'Local Reference Plate' not in html
    assert 'technical reference plate' not in html
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
    assert "const SHELL_CACHE = 'delta-shell-v29';" in sw_text
    assert "event.respondWith(cacheFirst(request, SHELL_CACHE, false));" in sw_text
    assert manifest_response.status_code == 200
    assert manifest_response.mimetype == 'application/manifest+json'
    assert offline_response.status_code == 200
    assert 'Delta Offline Mode' in offline_response.get_data(as_text=True)
    assert '/static/auth.js' in sw_text
    assert '/weapons' in sw_text
    assert '/weapons/armory' in sw_text
    assert '/mechanics/blueprints' in sw_text
    assert '/manuals' in sw_text
    assert '/live' in sw_text
    assert '/static/offline.html' in sw_text
    assert '/static/survival_loadout.js' in sw_text
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


def test_api_ai_rejects_invalid_provider_settings():
    client = app.test_client()

    response = client.post('/api/ai', json={
        'prompt': 'hello',
        'provider': 'bad-provider',
        'max_tokens': 999999,
        'temperature': 3,
    })
    data = response.get_json()

    assert response.status_code == 400
    assert data['ok'] is False
    assert data['error'] == 'invalid model settings'


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


def test_download_project_rejects_path_traversal():
    client = app.test_client()

    response = client.get('/download/%2E%2E')
    data = response.get_json()

    assert response.status_code == 400
    assert data['error'] == 'invalid project name'


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


def test_cyber_headers_rejects_private_hosts():
    client = app.test_client()

    response = client.get('/api/cyber/headers?url=localhost')
    data = response.get_json()

    assert response.status_code == 400
    assert data['ok'] is False
    assert data['error'] == 'public http(s) url parameter required'


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
        assert 'delta-coding-offline/static/manuals/army-library/fm21-76.pdf' in names
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
    assert '/static/map-layer-catalog.js' in html
    assert '/static/scripture-hotspots.js' in html
    assert 'https://cesium.com/downloads/cesiumjs/releases/1.141/Build/Cesium/Cesium.js' in html
    assert 'https://unpkg.com/milsymbol@3.0.4/dist/milsymbol.js' in html
    assert '<div id="map"></div>' in html
    assert '<div id="map-3d"' in html
    assert 'preferCanvas: true' in html
    assert 'requestRenderMode: true' in html
    assert 'disableDepthTestDistance: 0' in html
    assert 'disableDepthTestDistance: Number.POSITIVE_INFINITY' not in html
    assert 'globe-zoom-controls' in html
    assert 'btn-globe-zoom-in' in html
    assert 'btn-globe-zoom-out' in html
    assert 'LIVE SAT FEED' in html
    assert 'High-Grade Calculator' in html
    assert 'id="tac-calculator-expression-input"' in html
    assert 'deltaTacCalculatorV1' in html
    assert 'live-sat-toggle' in html
    assert 'live-sat-open' in html
    assert 'live-sat-close' in html
    assert 'GOES-East_ABI_GeoColor' in html
    assert 'GOES-West_ABI_GeoColor' in html
    assert 'Himawari_AHI_Band13_Clean_Infrared' in html
    assert 'VIIRS_SNPP_DayNightBand' in html
    assert 'Offline Atlas' in html
    assert 'map-base-layer' in html
    assert 'settings-3d-toggle' in html
    assert 'settings-recon-toggle' in html
    assert 'settings-xray-toggle' in html
    assert 'settings-conflicts-toggle' in html
    assert 'settings-facilities-toggle' in html
    assert 'settings-scripture-toggle' in html
    assert 'settings-scripture-scope' in html
    assert 'settings-globe-bases-toggle' in html
    assert 'settings-globe-nuclear-toggle' in html
    assert 'settings-globe-quakes-toggle' in html
    assert 'settings-globe-autopan-toggle' in html
    assert 'settings-profile-xray' in html
    assert 'settings-profile-darkwatch' in html
    assert 'Topo Portfolio' in html
    assert 'Live Satellite Recon' in html
    assert 'Land Nav Training HD' in html
    assert 'World Hillshade Reference' in html
    assert 'USGS Imagery + Topo' in html
    assert 'CARTO Positron' not in html
    assert 'ROUTE PLANNING' in html
    assert 'planning-travel-mode' in html
    assert 'planning-air-overlay' in html
    assert 'planning-export-route' in html
    assert 'planning-waypoint-list' in html
    assert 'planning-save-route' in html
    assert 'planning-saved-routes' in html
    assert 'planning-print-manifest' in html
    assert 'data-waypoint-up' in html
    assert 'btn-symbols' in html
    assert 'symbol-panel' in html
    assert 'MGRS MAPPER SYMBOLS' in html
    assert 'ARM PLACEMENT' in html
    assert 'Lock Symbol Scale To Zoom' in html
    assert 'Built with the open-source milsymbol library' in html
    assert 'JESUS GROUND TRACK' in html
    assert 'PLANT INTEL' in html
    assert 'btn-facilities' in html
    assert 'Nuclear power facilities' in html
    assert 'delta-conflicts' in html
    assert 'delta-facilities' in html
    assert 'delta-xray' in html
    assert 'intel-popup-title' in html
    assert 'man_made"="chemical_plant"' in html
    assert 'previous plant sites remain visible while the live lookup timed out.' in html
    assert 'Plant lookup timed out upstream. Pan, zoom, or retry.' in html
    assert 'Book of Mormon and 1 Enoch textual intel notes' in html
    assert 'Tac Map and 3D globe' in html
    assert 'Cesium now idles between renders for faster movement' in html
    assert 'LAND NAVIGATION GUIDE' in html
    assert 'btn-landnav' in html
    assert 'landnav-panel' in html
    assert 'Street imagery, recon, X-ray, and conflict overlays can now be armed from the same settings drawer across every base layer, and the globe now mirrors the same recon, plant, conflict, and structure intel where a 3D equivalent exists.' in html
    assert 'Tac profiles sync the toolbar and the drawer together, and the layer button now opens a Topo Portfolio with Land Nav Training HD, World Hillshade Reference, and USGS Topo. Live Satellite Recon now runs an animated day/night feed by region' in html
    assert 'The plant overlay scans public OSM tags for nuclear, chemical, and gas facilities in the current view.' in html
    assert 'Professional field workflow for precise location capture using live latitude / longitude, grid, kilometers, and meters.' in html
    assert 'goto-grid' in html
    assert 'goto-grid-go' in html
    assert 'Current UTM' in html
    assert 'MGRS / UTM Entry' in html
    assert '11S MT 12345 67890 or 11S 412345 3767890' in html
    assert 'Azimuth / Route Leg Worksheet' in html
    assert 'landnav-route-origin' in html
    assert 'landnav-route-plot' in html
    assert 'AZIMUTH LEG EXAMPLE' in html
    assert '0.001 deg ~ 111 m' in html
    assert '0.0001 deg ~ 11 m' in html
    assert '0.00001 deg ~ 1.1 m' in html
    assert '0.000001 deg ~ 0.11 m' in html
    assert 'LEG 2.40 km | FINAL 180 m' in html


def test_map_live_satellite_availability_filters_missing_frames():
    client = app.test_client()

    statuses = {
        'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/2026-05-26T15:50:00Z/GoogleMapsCompatible_Level7/7/36/48.png': 200,
        'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/2026-05-26T16:00:00Z/GoogleMapsCompatible_Level7/7/36/48.png': 404,
        'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/2026-05-26T16:40:00Z/GoogleMapsCompatible_Level7/7/36/48.png': 200,
    }

    class DummyResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    def fake_head(url, timeout, headers):
        return DummyResponse(statuses.get(url, 404))

    with patch.object(run_module.http_requests, 'head', side_effect=fake_head):
        response = client.post('/api/map/live-satellite-availability', json={
            'sourceKey': 'goesEast',
            'frames': [
                '2026-05-26T15:50:00Z',
                '2026-05-26T16:00:00Z',
                '2026-05-26T16:40:00Z',
            ],
            'tiles': [
                {'z': 7, 'x': 48, 'y': 36},
            ],
        })
        data = response.get_json()

    assert response.status_code == 200
    assert data['ok'] is True
    assert data['frames'] == [
        '2026-05-26T15:50:00Z',
        '2026-05-26T16:40:00Z',
    ]


def test_webspace_map_catalog_export_is_synced():
    root = Path(__file__).resolve().parent.parent
    source = (root / 'templates' / '_map_layer_catalog.js').read_text(encoding='utf-8')
    live_export = (root / 'static' / 'map-layer-catalog.js').read_text(encoding='utf-8')
    webspace_export = (root / 'webspace-site' / 'assets' / 'map-layer-catalog.js').read_text(encoding='utf-8')

    assert 'CARTO Positron' not in source
    assert 'Land Nav Training HD' in source
    assert 'World Hillshade Reference' in source
    assert 'USGS Imagery + Topo' in source
    assert 'Live Satellite Recon' in source
    assert 'Animated day/night satellite loop' in source
    assert 'VIIRS_SNPP_CorrectedReflectance_TrueColor' in source
    assert 'OSM Humanitarian' in source
    assert 'ESRI Street' in source
    assert 'NatGeo Terrain' in source
    assert live_export == source
    assert webspace_export == source


def test_survival_page_and_shared_stealth_nav_render():
    client = app.test_client()

    survival_response = client.get('/survival')
    survival_html = survival_response.get_data(as_text=True)
    library_response = client.get('/survival/army-library?manual=fm21-76')
    library_html = library_response.get_data(as_text=True)
    offline_response = client.get('/survival/army-library/offline')
    offline_html = offline_response.get_data(as_text=True)
    mechanics_response = client.get('/mechanics')
    mechanics_html = mechanics_response.get_data(as_text=True)
    index_response = client.get('/')
    index_html = index_response.get_data(as_text=True)

    assert survival_response.status_code == 200
    assert 'CIVILIAN BUGOUT BUILDER' in survival_html
    assert 'Realistic Bugout Bag Options' in survival_html
    assert 'survival_loadout.js' in survival_html
    assert 'Team Role Key' in survival_html
    assert 'id="roleKeyRow"' in survival_html
    assert 'id="vestSelect"' in survival_html
    assert 'id="helmetSelect"' in survival_html
    assert 'Vest / Chest Rig Setup' in survival_html
    assert 'Ammo &amp; Magazine Table' in survival_html
    assert 'ammo-field-highlight' in survival_html
    assert 'ammo-input-highlight' in survival_html
    assert 'Field Knife Library' in survival_html
    assert 'Infantry, recon, and sniper role keys now unlock current military bags' in survival_html
    assert 'Clear Full Loadout' in survival_html
    assert 'Tactical Meal Bible' in survival_html
    assert '/survival/meal-bible' in survival_html
    assert 'Department of the Army Survival Library' in survival_html
    assert '/survival/army-library' in survival_html
    assert '/survival/army-library/offline' in survival_html
    assert 'Stealth' in survival_html
    assert '/ai' in survival_html
    assert '/map' in survival_html
    assert '/bible' in survival_html
    assert '/advertise' in survival_html
    assert '/download/offline-site' in survival_html

    assert library_response.status_code == 200
    assert 'Department of the Army Survival Library' in library_html
    assert 'FM 21-76 Survival' in library_html
    assert 'FM 21-26 Map Reading and Land Navigation' in library_html
    assert 'FM 21-11 First Aid for Soldiers' in library_html
    assert 'FM 7-8 The Infantry Rifle Platoon and Squad' in library_html
    assert 'FM 7-85 Ranger Unit Operations' in library_html
    assert 'FM 7-0 Training the Force' in library_html
    assert 'FM 4-02.17 Preventive Medicine Services' in library_html
    assert 'FM 4-02.6 The Medical Company' in library_html
    assert 'ATP 4-44 Water Support Operations' in library_html
    assert 'ATP 4-41 Army Field Feeding and Class I Operations' in library_html
    assert 'FM 90-10-1 An Infantryman&#39;s Guide to Urban Combat' in library_html
    assert 'FM 90-8 Counterguerilla Operations' in library_html
    assert 'FM 21-26 Map Reading and Land Navigation (1956 Edition)' in library_html
    assert 'FM 3-100.4 Environmental Considerations in Military Operations' in library_html
    assert 'FM 20-3 Camouflage, Concealment, and Decoys' in library_html
    assert 'FM 3-4 NBC Protection' in library_html
    assert 'armyManualSearch' in library_html
    assert 'armyCategoryFilters' in library_html
    assert 'armyFavoritesOnlyToggle' in library_html
    assert 'Download Selected Manuals' in library_html
    assert 'Open Selected Binder' in library_html
    assert 'armyFavoritesShelf' in library_html
    assert 'armyDownloadFavorites' in library_html
    assert 'armyOpenFavoritesBinder' in library_html
    assert 'armyPrintFavorites' in library_html
    assert 'armyExportFavorites' in library_html
    assert 'armyToggleActiveFavorite' in library_html
    assert 'armyManualDetailDialog' in library_html
    assert 'TOC Quick Links' in library_html
    assert 'armyReaderFrame' in library_html
    assert 'army-manual-cover' in library_html
    assert 'Sustainment' in library_html
    assert '/survival/army-library/offline' in library_html
    assert '/survival/army-library/binder' in library_html
    assert '/survival/army-library/download' in library_html
    assert '/survival/army-library/fm21-76/pdf' in library_html
    assert 'Open Source Mirror' in library_html
    assert 'href="/survival" class="active"' in library_html

    assert offline_response.status_code == 200
    assert 'Offline Army Survival Shelf' in offline_html
    assert 'Download Entire Shelf' in offline_html
    assert 'Medical + Water Sustainment Pack' in offline_html
    assert 'Navigation + Climate Environment Pack' in offline_html
    assert 'Fieldcraft + Protection Readiness Pack' in offline_html
    assert 'ATP 4-41 Army Field Feeding and Class I Operations' in offline_html
    assert 'FM 4-02.6 The Medical Company' in offline_html
    assert 'FM 7-85 Ranger Unit Operations' in offline_html
    assert 'FM 7-0 Training the Force' in offline_html
    assert 'FM 90-8 Counterguerilla Operations' in offline_html
    assert 'FM 3-100.4 Environmental Considerations in Military Operations' in offline_html
    assert 'Open Binder' in offline_html
    assert '/survival/army-library/binder' in offline_html
    assert 'scope=all' in offline_html
    assert 'category=medical' in offline_html
    assert 'category=water' in offline_html
    assert 'category=sustainment' in offline_html
    assert 'href="/survival" class="active"' in offline_html

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
    assert '/survival/meal-bible' in index_html
    assert '/culture' in index_html
    assert '/mechanics' in index_html
    assert 'Ops Center' in index_html
    assert 'AI Recon' in index_html
    assert '<nav class="top-nav">\n  <details class="nav-dropdown' in index_html
    assert '<nav class="top-nav">\n  <a href="/"' not in index_html
    assert 'Open all 1,000 field meal builds' in index_html
    assert 'Open the five-country culture reference' in index_html


def test_survival_meal_bible_page_renders():
    client = app.test_client()

    meal_bible_response = client.get('/survival/meal-bible')
    meal_bible_html = meal_bible_response.get_data(as_text=True)

    assert meal_bible_response.status_code == 200
    assert 'Tactical Meal Bible' in meal_bible_html
    assert '500 perishable meals' in meal_bible_html
    assert '500 nonperishable meals' in meal_bible_html
    assert '1000 total meals' in meal_bible_html
    assert 'Print Selected Meal' in meal_bible_html
    assert 'Export Selected Meal' in meal_bible_html
    assert 'Export Filtered Meals' in meal_bible_html
    assert 'Print Filtered Meals' in meal_bible_html
    assert 'mealSearch' in meal_bible_html
    assert 'mealStorageFilters' in meal_bible_html
    assert 'mealCategoryFilters' in meal_bible_html
    assert 'mealServingFilters' in meal_bible_html
    assert 'mealGrid' in meal_bible_html
    assert 'mealDetailCard' in meal_bible_html
    assert 'meal-bible-route-hero' in meal_bible_html
    assert 'survival_meal_bible.js' in meal_bible_html
    assert '/survival' in meal_bible_html
    assert 'Back to Survival Loadout' in meal_bible_html
    assert 'Jump to Meal Browser' in meal_bible_html
    assert 'Skillet Stack: Chicken Thigh with Broccoli Florets and Jasmine Rice' in meal_bible_html
    assert 'Field Braise: Chicken Thigh with Bell Pepper Medley and Jasmine Rice' in meal_bible_html
    assert 'Cold-chain meal. Stage chilled ingredients last and keep raw proteins separated until the pan is hot.' in meal_bible_html
    assert 'JavaScript is currently off' in meal_bible_html


def test_schooling_page_renders_core_subjects():
    client = app.test_client()

    response = client.get('/schooling')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Schooling Core Reference' in html
    assert 'Basic Maths' in html
    assert 'Land Navigation Basics' in html
    assert 'Basic Science' in html
    assert 'Basic English' in html
    assert 'Number Sense &amp; Order of Operations' in html or 'Number Sense & Order of Operations' in html
    assert 'Fractions, Decimals &amp; Percentages' in html or 'Fractions, Decimals & Percentages' in html
    assert 'Scientific Method' in html
    assert 'Matter, Forces &amp; Life Science' in html or 'Matter, Forces & Life Science' in html
    assert 'Sentence Building &amp; Grammar' in html or 'Sentence Building & Grammar' in html
    assert 'Paragraph Writing &amp; Reading Response' in html or 'Paragraph Writing & Reading Response' in html
    assert 'subject-jump' in html
    assert 'lesson-grid' in html
    assert 'Practice Cycle' in html
    assert 'Land Nav' in html
    assert 'Field Skills' in html
    assert 'Maths Workstation' in html
    assert '100 addition and subtraction problems, one at a time' in html
    assert '100 multiplication and division problems, one at a time' in html
    assert '100 distance conversion problems, one at a time' in html
    assert html.index('100 addition and subtraction problems, one at a time') < html.index('100 multiplication and division problems, one at a time') < html.index('100 distance conversion problems, one at a time')
    assert 'Science Workstation' in html
    assert 'English Workstation' in html
    assert 'workstation-shell' in html
    assert 'Problem 1 of 100' in html
    assert 'Submit and Refresh' in html
    assert 'Progress saves in this browser' in html
    assert 'Answer explanation appears here after each response' in html
    assert 'Operations Breakdown' in html
    assert 'Addition Drills' in html
    assert 'Subtraction Drills' in html
    assert 'Multiplication Drills' in html
    assert 'Division Drills' in html
    assert 'Distance Conversion Drills' in html
    assert 'Map Orientation &amp; Terrain Association' in html or 'Map Orientation & Terrain Association' in html
    assert 'Grid Coordinates &amp; Plotting' in html or 'Grid Coordinates & Plotting' in html
    assert 'Compass Bearings &amp; Back Azimuths' in html or 'Compass Bearings & Back Azimuths' in html
    assert 'Pace Count &amp; Distance Control' in html or 'Pace Count & Distance Control' in html
    assert 'Route Planning, Attack Points &amp; Catching Features' in html or 'Route Planning, Attack Points & Catching Features' in html
    assert 'Relocation &amp; Resection' in html or 'Relocation & Resection' in html
    assert 'Land Navigation Quick Reference' in html
    assert 'Read right, then up' in html
    assert 'Back azimuth = azimuth ± 180' in html
    assert '62 paces x 5 legs is about 500 meters.' in html
    assert 'Meters to Feet' in html
    assert 'Miles to Kilometers' in html
    assert 'Inches to Centimeters' in html
    assert 'Yards to Meters' in html
    assert '1 meter is about 3.28 feet' in html
    assert '1 mile is about 1.61 kilometers' in html
    assert '1 inch = 2.54 centimeters' in html
    assert '1 yard is about 0.91 meters' in html
    assert 'WORKSTATION_TOTAL = 100' in html
    assert 'buildMathProblemBank' in html
    assert 'buildMultiplyDivideProblemBank' in html
    assert 'buildDistanceProblemBank' in html
    assert 'buildScienceProblemBank' in html
    assert 'buildEnglishProblemBank' in html
    assert 'About how many feet are in' in html
    assert 'About how many kilometers are in' in html
    assert 'About how many centimeters are in' in html
    assert 'About how many meters are in' in html
    assert 'saveWorkstationState' in html
    assert 'loadWorkstationState' in html
    assert 'Step-by-Step Maths Examples' in html
    assert 'How To Work It Out Step By Step' in html
    assert 'maths-examples-shell' in html
    assert 'Signature Tutor Layout' in html
    assert 'Clean Solve Order' in html
    assert 'Solve Standard' in html
    assert '1. Add ones: 8 + 7 = 15, write 5 and carry 1.' in html
    assert '1. Multiply miles by 1.61.' in html
    assert '1. Solve inside the parentheses first: 6 / 2 = 3.' in html
    assert '1. Subtract 5 from the left side.' in html
    assert '1. Add the scores: 4 + 5 + 5 + 6 = 20.' in html
    assert 'How To Work It Out' in html
    assert '1 ÷ 2 = 0.5, then 0.5 × 100 = 50%.' in html
    assert '1 ÷ 4 = 0.25, then 0.25 × 100 = 25%.' in html
    assert '3 ÷ 4 = 0.75, then 0.75 × 100 = 75%.' in html
    assert '1 ÷ 5 = 0.2, then 0.2 × 100 = 20%.' in html
    assert 'Problems reshuffled for cycle' in html
    assert html.count('id="rf-wrapper"') == 1


def test_culture_build_page_renders_comprehensive_reference():
    client = app.test_client()

    response = client.get('/culture')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Culture Build Reference' in html
    assert 'Iran' in html
    assert 'China' in html
    assert 'Russia' in html
    assert 'Mexico' in html
    assert 'Israel' in html
    assert 'USA' in html
    assert 'United States' in html
    assert 'Military Ranks' in html
    assert 'Major Holidays' in html
    assert 'Numbers & Military Commands' in html
    assert 'Basic Phrases & Questions' in html
    assert 'Cultural History' in html
    assert 'Ethnic Groups' in html
    assert 'Social Structure & Religions' in html
    assert 'Cultural Customs & Traditions' in html
    assert 'Nowruz' in html
    assert 'Day of the Dead' in html
    assert 'Passover' in html
    assert 'dragon-boat-festival' in html.lower() or 'Dragon Boat' in html
    assert 'Victory Day' in html or 'Victory' in html
    assert 'tab-button' in html
    assert 'country-section' in html
    assert 'phrase-grid' in html
    assert 'culture-section' in html
    assert 'rank-insignia-art' in html
    assert 'culture-ranks/iran-general.svg' in html
    assert 'culture-ranks/usa-general.svg' in html
    assert 'culture-ranks/iran-brigadier-general.svg' in html
    assert 'culture-ranks/israel-lieutenant.svg' in html
    assert 'Enlisted &amp; NCO Insignia' in html or 'Enlisted & NCO Insignia' in html
    assert 'Chief Warrant Officer' in html
    assert 'First Sergeant' in html
    assert 'Sergeant Major of the Army' in html
    assert 'Command Sergeant Major' in html
    assert 'Specialist' in html
    assert 'Sergeant Third Class' in html
    assert 'Staff Sergeant Second Class' in html
    assert 'Starshina (First Sergeant)' in html
    assert 'Staff Sergeant' in html
    assert 'culture-ranks/iran-chief-warrant-officer.svg' in html
    assert 'culture-ranks/iran-private.svg' in html
    assert 'culture-ranks/china-private.png' in html
    assert 'culture-ranks/usa-sergeant-major-of-the-army.svg' in html
    assert 'Army-USA-OR-09a.svg' in html
    assert 'Army-USA-OR-09b.svg' in html
    assert 'IDF_Ranks_Ranag.svg' in html
    assert 'IDF_Ranks_Samal.svg' in html
    assert 'Russia-Army-OR-1-2010.svg' in html
    assert 'Wikimedia Commons' in html
    assert 'Closest current army insignia' in html
    assert 'Location & Cultural History' in html
    assert 'culture-mini-map' in html
    assert 'history-scroll' in html
    assert 'culture-tabs-stealth' in html
    assert 'Service &amp; Federal Agency Boards' in html or 'Service & Federal Agency Boards' in html
    assert 'usa-branch-button' in html
    assert 'United States Marine Corps' in html
    assert 'United States Navy' in html
    assert 'United States Air Force' in html
    assert 'United States Space Force' in html
    assert 'Federal Bureau of Investigation' in html
    assert 'Central Intelligence Agency' in html
    assert 'United States Coast Guard' in html
    assert 'Special:FilePath/File:Seal_of_the_United_States_Space_Force.svg' in html
    assert 'Chris_Wray_official_photo_%283x4_cropped%29.jpg' in html
    assert 'Field / Ceremony Photo' in html
    assert 'William_Burns_Swear-In.jpg' in html
    assert '<header class="hero">' in html
    assert 'delta-logo' in html
    assert 'CULTURE BUILD REFERENCE' in html
    assert '<main class="culture-build-page">' in html
    assert 'Presidents &amp; Vice Presidents' in html or 'Presidents & Vice Presidents' in html
    assert 'Country Match' in html
    assert 'Country-to-president reference' in html
    assert 'Vice President or Closest Deputy' in html
    assert 'leadership-grid' in html
    assert 'Masoud Pezeshkian' in html
    assert 'Mohammad Reza Aref' in html
    assert 'Xi Jinping' in html
    assert 'Han Zheng' in html
    assert 'Vladimir Putin' in html
    assert 'Mikhail Mishustin' in html
    assert 'Claudia Sheinbaum' in html
    assert 'Rosa Icela Rodriguez' in html
    assert 'Isaac Herzog' in html
    assert 'Benjamin Netanyahu' in html
    assert 'Donald Trump' in html
    assert 'JD Vance' in html
    assert 'closest executive deputy' in html
    assert 'Mission Highlights' not in html
    assert '6 country tabs' in html
    assert '98 real rank tiles' in html
    assert 'officer + enlisted ladders' in html
    assert 'Strategic command' in html
    assert html.count('id="rf-wrapper"') == 1


def test_meal_bible_and_culture_pages_ship_cache_headers_and_etags():
    client = app.test_client()

    for route in ['/survival/meal-bible', '/culture']:
        response = client.get(route)

        assert response.status_code == 200, route
        assert response.headers['Cache-Control'].startswith('public, max-age=300'), route
        assert response.headers['ETag'], route
        assert 'Accept-Encoding' in response.headers.get('Vary', ''), route


def test_schooling_page_ships_cache_headers_and_etags():
    client = app.test_client()

    response = client.get('/schooling')

    assert response.status_code == 200
    assert response.headers['Cache-Control'].startswith('public, max-age=300')
    assert response.headers['ETag']
    assert 'Accept-Encoding' in response.headers.get('Vary', '')


def test_run_and_agent_require_admin_token_when_configured(monkeypatch):
    client = app.test_client()
    monkeypatch.setenv('ADMIN_TOKEN', 'ops-lock')
    monkeypatch.setattr(run_module, 'run_instruction', lambda *args, **kwargs: {'ok': True, 'project_name': 'ops-lock'})

    run_denied = client.post('/run', json={'language': 'python', 'code': 'print(1)'})
    agent_denied = client.post('/agent', json={'instruction': 'hello'})
    run_allowed = client.post('/run', json={'language': 'python', 'code': 'print(1)'}, headers={'X-Admin-Token': 'ops-lock'})
    agent_allowed = client.post('/agent', json={'instruction': 'hello'}, headers={'X-Admin-Token': 'ops-lock'})

    assert run_denied.status_code == 403
    assert run_denied.get_json()['error'] == 'admin token required'
    assert agent_denied.status_code == 403
    assert agent_denied.get_json()['error'] == 'admin token required'
    assert run_allowed.status_code == 200
    assert run_allowed.get_json()['stdout'].strip() == '1'
    assert agent_allowed.status_code == 200
    assert agent_allowed.get_json()['project_name'] == 'ops-lock'


def test_run_and_agent_are_locked_on_non_local_hosts_without_admin_token(monkeypatch):
    client = app.test_client()
    monkeypatch.delenv('ADMIN_TOKEN', raising=False)

    run_denied = client.post('/run', json={'language': 'python', 'code': 'print(1)'}, base_url='https://futurecodedelta.org')
    agent_denied = client.post('/agent', json={'instruction': 'hello'}, base_url='https://futurecodedelta.org')

    assert run_denied.status_code == 403
    assert run_denied.get_json()['error'] == 'execution locked on this deployment'
    assert agent_denied.status_code == 403
    assert agent_denied.get_json()['error'] == 'execution locked on this deployment'


def test_index_shows_ops_token_prompt_without_leaking_value(monkeypatch):
    client = app.test_client()
    monkeypatch.setenv('ADMIN_TOKEN', 'ops-lock')

    response = client.get('/')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Admin token for Run + Agent' in html
    assert 'id="opsToken"' in html
    assert 'ops-lock' not in html


def test_index_disables_run_and_agent_when_execution_is_locked(monkeypatch):
    client = app.test_client()
    monkeypatch.delenv('ADMIN_TOKEN', raising=False)

    response = client.get('/', base_url='https://futurecodedelta.org')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'Run and Agent are locked on this deployment until an admin token is configured.' in html
    assert 'id="run" disabled' in html
    assert 'id="askAgent" disabled' in html


def test_primary_public_pages_ship_cache_headers_and_etags():
    client = app.test_client()

    for route in ['/', '/survival', '/survival/army-library', '/schooling', '/truth']:
        response = client.get(route)

        assert response.status_code == 200, route
        assert response.headers['Cache-Control'].startswith('public, max-age=300'), route
        assert response.headers['ETag'], route
        assert 'Accept-Encoding' in response.headers.get('Vary', ''), route


def test_survival_loadout_script_contains_real_platforms_and_sections():
    script_path = run_module.os.path.join(run_module.APP_ROOT, 'static', 'survival_loadout.js')

    with open(script_path, 'r', encoding='utf-8') as handle:
        script = handle.read()

    assert 'deltaSurvivalLoadoutV3' in script
    assert 'Mystery Ranch 2 Day Assault 27L' in script
    assert 'Mystery Ranch SATL 55L' in script
    assert 'Crye Precision JPC 2.0' in script
    assert 'Crye Precision SPC' in script
    assert 'Ops-Core FAST SF' in script
    assert 'Galvion Caiman Ballistic' in script
    assert 'Morakniv' in script
    assert '5.56 NATO / 30-Round STANAG' in script
    assert 'ATAK EUD + Mount Kit' in script
    assert 'PRC-163 Radio Kit' in script
    assert 'Spotting Scope Kit' in script
    assert 'Infantry Line' in script
    assert 'Recon Patrol' in script
    assert 'Sniper Overwatch' in script
    assert 'compatibleKinds' in script
    assert 'normalizeBagItemIds' in script
    assert 'bagPackingLimit' in script
    assert "would exceed the bag's listed weight limit" in script
    assert 'lb pack max' in script
    assert 'selectedBagItemEntries' in script
    assert 'Pack Another' in script
    assert 'Clear Item' in script


def test_all_army_library_manuals_have_local_hosted_pdf_copies():
    missing = [
        manual['slug']
        for manual in run_module.ARMY_SURVIVAL_MANUALS
        if not run_module.os.path.isfile(run_module._army_survival_manual_local_path(manual))
    ]

    assert missing == []


def test_survival_army_library_pdf_proxy_streams_manual(monkeypatch, tmp_path):
    class DummyUpstreamResponse:
        headers = {'Content-Length': '13'}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield b'%PDF-1.4 test'

        def close(self):
            return None

    def fake_get(url, timeout=None, stream=False, headers=None):
        assert url == 'https://www.bits.de/NRANEU/others/amd-us-archive/FM21-76%2892%29.pdf'
        assert stream is True
        return DummyUpstreamResponse()

    def fake_local_path(manual):
        return str(tmp_path / f"{manual['slug']}.pdf")

    monkeypatch.setattr(run_module, '_army_survival_manual_local_path', fake_local_path)
    monkeypatch.setattr(run_module.http_requests, 'get', fake_get)

    client = app.test_client()
    response = client.get('/survival/army-library/fm21-76/pdf')
    download_response = client.get('/survival/army-library/fm21-76/pdf?download=1')

    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert response.headers['Content-Disposition'] == 'inline; filename="FM_21-76_Survival.pdf"'
    assert response.headers['X-Army-PDF-Source'] == 'remote'
    assert response.get_data().startswith(b'%PDF-1.4 test')
    assert download_response.status_code == 200
    assert download_response.headers['Content-Disposition'] == 'attachment; filename="FM_21-76_Survival.pdf"'
    assert download_response.headers['X-Army-PDF-Source'] == 'remote'


def test_survival_army_library_pdf_prefers_local_copy(monkeypatch, tmp_path):
    local_pdf = tmp_path / 'fm21-76.pdf'
    local_pdf.write_bytes(b'%PDF-1.4 local copy')

    def fake_local_path(manual):
        assert manual['slug'] == 'fm21-76'
        return str(local_pdf)

    def fail_get(*args, **kwargs):
        raise AssertionError('remote fetch should not be used when a local PDF exists')

    monkeypatch.setattr(run_module, '_army_survival_manual_local_path', fake_local_path)
    monkeypatch.setattr(run_module.http_requests, 'get', fail_get)

    client = app.test_client()
    response = client.get('/survival/army-library/fm21-76/pdf')
    download_response = client.get('/survival/army-library/fm21-76/pdf?download=1')

    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert response.headers['X-Army-PDF-Source'] == 'local'
    assert response.headers['Content-Disposition'] == 'inline; filename=FM_21-76_Survival.pdf'
    assert response.get_data().startswith(b'%PDF-1.4 local copy')
    assert download_response.headers['X-Army-PDF-Source'] == 'local'
    assert download_response.headers['Content-Disposition'] == 'attachment; filename=FM_21-76_Survival.pdf'


def test_survival_army_library_download_bundle_streams_selected_manuals(monkeypatch, tmp_path):
    requested_urls = []

    class DummyUpstreamResponse:
        def __init__(self, url):
            self.url = url
            self.headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield f'%PDF-1.4 {self.url}'.encode('utf-8')

        def close(self):
            return None

    def fake_get(url, timeout=None, stream=False, headers=None):
        assert stream is True
        requested_urls.append(url)
        return DummyUpstreamResponse(url)

    def fake_local_path(manual):
        return str(tmp_path / f"{manual['slug']}.pdf")

    monkeypatch.setattr(run_module, '_army_survival_manual_local_path', fake_local_path)
    monkeypatch.setattr(run_module.http_requests, 'get', fake_get)

    client = app.test_client()
    response = client.get('/survival/army-library/download?manual=fm21-76&manual=fm21-26')

    assert response.status_code == 200
    assert response.mimetype == 'application/zip'
    assert response.headers['Content-Disposition'] == 'attachment; filename=delta_army_survival_library_bundle.zip'

    with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
        names = archive.namelist()
        readme = archive.read('README.txt').decode('utf-8')

    assert 'FM_21-76_Survival.pdf' in names
    assert 'FM_21-26_Map_Reading_and_Land_Navigation.pdf' in names
    assert 'README.txt' in names
    assert 'FM 21-76 Survival' in readme
    assert 'FM 21-26 Map Reading and Land Navigation' in readme
    assert requested_urls == [
        'https://www.bits.de/NRANEU/others/amd-us-archive/FM21-76%2892%29.pdf',
        'https://www.bits.de/NRANEU/others/amd-us-archive/FM21-26%2893%29.pdf',
    ]


def test_survival_army_library_download_bundle_supports_category_filters(monkeypatch, tmp_path):
    requested_urls = []

    class DummyUpstreamResponse:
        def __init__(self, url):
            self.url = url
            self.headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield f'%PDF-1.4 {self.url}'.encode('utf-8')

        def close(self):
            return None

    def fake_get(url, timeout=None, stream=False, headers=None):
        assert stream is True
        requested_urls.append(url)
        return DummyUpstreamResponse(url)

    def fake_local_path(manual):
        return str(tmp_path / f"{manual['slug']}.pdf")

    monkeypatch.setattr(run_module, '_army_survival_manual_local_path', fake_local_path)
    monkeypatch.setattr(run_module.http_requests, 'get', fake_get)

    client = app.test_client()
    response = client.get('/survival/army-library/download?category=medical&category=water')

    assert response.status_code == 200
    assert response.mimetype == 'application/zip'

    with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
        names = archive.namelist()
        readme = archive.read('README.txt').decode('utf-8')

    assert 'FM_21-11_First_Aid_for_Soldiers.pdf' in names
    assert 'ATP_4-44_Water_Support_Operations.pdf' in names
    assert 'FM_4-02.17_Preventive_Medicine_Services.pdf' in names
    assert 'FM_4-02.6_The_Medical_Company.pdf' in names
    assert 'FM 21-11 First Aid for Soldiers' in readme
    assert 'ATP 4-44 Water Support Operations' in readme
    assert 'FM 4-02.17 Preventive Medicine Services' in readme
    assert 'FM 4-02.6 The Medical Company' in readme
    assert requested_urls == [
        'https://www.bits.de/NRANEU/others/amd-us-archive/FM21-11%2854%29.pdf',
        'https://www.bits.de/NRANEU/others/amd-us-archive/ATP4-44%2815%29.pdf',
        'https://www.bits.de/NRANEU/others/amd-us-archive/FM-4-02.17%2800%29.pdf',
        'https://www.bits.de/NRANEU/others/amd-us-archive/FM4-02.6%2802%29.pdf',
    ]


def test_survival_army_library_download_bundle_prefers_local_manuals(monkeypatch, tmp_path):
    local_pdfs = {
        'fm21-76': tmp_path / 'fm21-76.pdf',
        'fm21-26': tmp_path / 'fm21-26.pdf',
    }
    local_pdfs['fm21-76'].write_bytes(b'%PDF-1.4 local fm21-76')
    local_pdfs['fm21-26'].write_bytes(b'%PDF-1.4 local fm21-26')

    def fake_local_path(manual):
        return str(local_pdfs.get(manual['slug'], tmp_path / 'missing.pdf'))

    def fail_get(*args, **kwargs):
        raise AssertionError('remote fetch should not be used when local bundle copies exist')

    monkeypatch.setattr(run_module, '_army_survival_manual_local_path', fake_local_path)
    monkeypatch.setattr(run_module.http_requests, 'get', fail_get)

    client = app.test_client()
    response = client.get('/survival/army-library/download?manual=fm21-76&manual=fm21-26')

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
        names = archive.namelist()
        readme = archive.read('README.txt').decode('utf-8')

    assert 'FM_21-76_Survival.pdf' in names
    assert 'FM_21-26_Map_Reading_and_Land_Navigation.pdf' in names
    assert 'hosted on FutureCodeDelta' in readme


def test_survival_army_library_binder_renders_selected_manuals():
    client = app.test_client()

    response = client.get('/survival/army-library/binder?manual=fm7-85&manual=fm4-02-6')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'ARMY SURVIVAL BINDER' in html
    assert 'Selected Army Manual Binder' in html
    assert 'armyBinderPrint' in html
    assert 'armyBinderCategoryGroups' in html
    assert 'FM 7-85 Ranger Unit Operations' in html
    assert 'FM 4-02.6 The Medical Company' in html
    assert 'TOC Quick Links' in html
    assert '/survival/army-library/download?manual=fm7-85&amp;manual=fm4-02-6' in html


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
    assert 'NATO Phonetic Alphabet &amp; Radio Transmit Flow' in html
    assert 'Alpha' in html
    assert 'THIS IS DELTA SIX. REQUEST RADIO CHECK.' in html
    assert 'Bluetooth Ham Radio Link' in html
    assert 'Radio Programming Shelf' in html
    assert 'Baofeng AR-152 Pro' in html
    assert 'Baofeng UV-5RM' in html
    assert '/static/manuals/uploads/radio/setup.exe' in html
    assert 'Transport profile' in html
    assert 'Bluetooth Serial (Web Serial)' in html
    assert 'Preferred GATT service UUID' in html
    assert 'Reconnect Last' in html
    assert 'navigator.bluetooth' in html
    assert 'navigator.serial' in html
    assert 'deltaHamBluetooth' in html
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
    assert 'TOPO + TRAINING' in html
    assert 'PUBLIC LIVE CAMS' in html
    assert 'PUBLIC / AUTHORIZED STREAMS ONLY' in html
    assert 'TRAINING MAPS' in html


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


def test_army_library_csp_allows_adsense_loader():
    client = app.test_client()

    response = client.get('/survival/army-library')
    csp = response.headers.get('Content-Security-Policy', '')

    assert response.status_code == 200
    assert 'https://pagead2.googlesyndication.com' in csp
    assert 'https://partner.googleadservices.com' in csp
    assert 'https://googleads.g.doubleclick.net' in csp
    assert 'https://*.adtrafficquality.google' in csp


def test_ionos_htaccess_csp_allows_adsense_loader():
    htaccess_path = run_module.os.path.join(run_module.APP_ROOT, 'ionos-backend', '.htaccess')

    with open(htaccess_path, 'r', encoding='utf-8') as handle:
        htaccess = handle.read()

    assert 'https://pagead2.googlesyndication.com' in htaccess
    assert 'https://partner.googleadservices.com' in htaccess
    assert 'https://googleads.g.doubleclick.net' in htaccess
    assert 'https://*.adtrafficquality.google' in htaccess
    assert "connect-src 'self' https: http:" in htaccess


def test_ionos_htaccess_blocks_sensitive_sources_and_methods():
    htaccess_path = run_module.os.path.join(run_module.APP_ROOT, 'ionos-backend', '.htaccess')

    with open(htaccess_path, 'r', encoding='utf-8') as handle:
        htaccess = handle.read()

    assert 'RewriteCond %{REQUEST_METHOD} ^(TRACE|TRACK|DELETE|PUT|PATCH|CONNECT)$ [NC]' in htaccess
    assert 'RedirectMatch gone ^/(?:generated|templates|tests|scripts|agent|memories)(?:/.*)?$' in htaccess
    assert 'security_hardening\\.py' in htaccess
    assert 'X-DNS-Prefetch-Control' in htaccess
    assert 'X-Permitted-Cross-Domain-Policies' in htaccess


def test_ionos_htaccess_blocks_common_attack_query_patterns():
    htaccess_path = run_module.os.path.join(run_module.APP_ROOT, 'ionos-backend', '.htaccess')

    with open(htaccess_path, 'r', encoding='utf-8') as handle:
        htaccess = handle.read()

    assert 'Options +ExecCGI -MultiViews -Indexes' in htaccess
    assert 'RewriteCond %{THE_REQUEST} \\?\\ HTTP/ [NC,OR]' in htaccess
    assert 'RewriteCond %{QUERY_STRING} (\\.\\./|%2e%2e%2f|%2e%2e/|\\.\\.%2f|%2e\\.%2f|%2e\\./|\\.%2e%2f|\\.%2e/) [NC,OR]' in htaccess
    assert 'RewriteCond %{QUERY_STRING} (\\<|%3C).*(script|iframe|object|embed).*(\\>|%3E) [NC,OR]' in htaccess
    assert 'RewriteCond %{QUERY_STRING} base64_(en|de)code[^\\(]*\\([^\\)]*\\) [NC,OR]' in htaccess
    assert 'RewriteCond %{QUERY_STRING} (GLOBALS|_REQUEST)(=|\\[|\\%[0-9A-Z]{0,2}) [NC]' in htaccess
    assert '(?:py|pyc|pyo|db|sqlite|sqlite3|bak|ini|inc|log|sh|md|toml|yaml|yml)' in htaccess


def test_ionos_htaccess_blocks_common_probe_paths():
    htaccess_path = run_module.os.path.join(run_module.APP_ROOT, 'ionos-backend', '.htaccess')

    with open(htaccess_path, 'r', encoding='utf-8') as handle:
        htaccess = handle.read()

    assert 'RewriteCond %{REQUEST_URI} (?:^|/)(?:wp-config\\.php|bb-config\\.php|timthumb\\.php|phpthumb\\.php|thumb\\.php|thumbs\\.php|debug\\.log)$ [NC]' in htaccess