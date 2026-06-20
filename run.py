
#!/usr/bin/env python3
import base64
import html
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import ipaddress

import requests as http_requests
from flask import Flask, Response, g, jsonify, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from flask_compress import Compress
except ImportError:
    Compress = None

from agent import generate_with_llm
from agent.agent import run_instruction
from drone_bridge import DroneBridgeManager
from drone_bridge_http import create_drone_bridge_blueprint
from manual_store import ManualStore
from security_hardening import input_validator, rate_limiter, secure_config

LIVE_SATELLITE_SOURCE_LOOKUP = {
    'goesEast': {
        'layer_id': 'GOES-East_ABI_GeoColor',
        'matrix_set': 'GoogleMapsCompatible_Level7',
    },
    'goesWest': {
        'layer_id': 'GOES-West_ABI_GeoColor',
        'matrix_set': 'GoogleMapsCompatible_Level7',
    },
    'himawari': {
        'layer_id': 'Himawari_AHI_Band13_Clean_Infrared',
        'matrix_set': 'GoogleMapsCompatible_Level6',
    },
    'viirs': {
        'layer_id': 'VIIRS_SNPP_DayNightBand',
        'matrix_set': 'GoogleMapsCompatible_Level7',
    },
}

app = Flask(__name__, static_folder='static', template_folder='templates')
secure_config(app)
app.config['COMPRESS_MIMETYPES'] = [
    'text/css',
    'text/html',
    'application/javascript',
    'application/json',
    'application/manifest+json',
    'image/svg+xml',
]
app.config['COMPRESS_LEVEL'] = int(os.environ.get('COMPRESS_LEVEL', '6'))
app.config['COMPRESS_MIN_SIZE'] = 1024
if Compress is not None:
    Compress(app)
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
manual_store = ManualStore(APP_ROOT)
drone_bridge = DroneBridgeManager()
app.register_blueprint(
    create_drone_bridge_blueprint(drone_bridge, service_label='site-bridge'),
    url_prefix='/api/drone/bridge',
)
journal_db_path = os.path.join(APP_ROOT, 'generated', 'journal.db')
visitor_db_path = os.path.join(APP_ROOT, 'generated', 'visitors.db')
ad_inquiries_db_path = os.path.join(APP_ROOT, 'generated', 'ad_inquiries.db')
auth_db_path = os.path.join(APP_ROOT, 'generated', 'auth.db')
monetization_config_path = os.path.join(APP_ROOT, 'generated', 'monetization_config.json')
JOURNAL_MODERATOR_SESSION_KEY = 'journal_moderator_active'
DEFAULT_JOURNAL_MODERATOR_PASSWORD_SHA256 = '47764a404bec5ab5b8746232de0c7850482d43fa0492f8f0b7fb6f7a5c47eb85'
os.makedirs(os.path.dirname(journal_db_path), exist_ok=True)


def _request_host_name():
    return ((request.host or '').split(':', 1)[0]).strip().lower()


def _is_local_request_host():
    host = _request_host_name()
    return host in {'', 'localhost', '127.0.0.1', '::1'} or host.endswith('.local')


def _execution_gate_error(payload):
    admin_token = os.environ.get('ADMIN_TOKEN')
    provided = request.headers.get('X-Admin-Token') or payload.get('admin_token')
    if admin_token:
        if provided != admin_token:
            return jsonify({'error': 'admin token required'}), 403
        return None
    if not _is_local_request_host():
        return jsonify({'error': 'execution locked on this deployment'}), 403
    return None

_PAGE_AD_CONFIGS = [
    {
        'paths': {'/', '/index'},
        'slot_key': 'ops',
        'title': 'Operations Tools and Hosting',
        'summary': 'Best fit for hosting, developer utilities, rugged devices, and operations software.',
        'keywords': ['hosting', 'developer tools', 'rugged devices'],
        'network_supported': True,
        'show_slot': True,
    },
    {
        'paths': {'/ai'},
        'slot_key': 'ai',
        'title': 'AI Tools and Automation',
        'summary': 'Best fit for model providers, automation tools, notebooks, and workflow software.',
        'keywords': ['AI tools', 'automation software', 'developer workflow'],
        'network_supported': True,
        'show_slot': True,
    },
    {
        'paths': {'/cyber'},
        'slot_key': 'cyber',
        'title': 'Cybersecurity and Privacy',
        'summary': 'Best fit for VPNs, endpoint protection, password managers, and privacy tools.',
        'keywords': ['VPN', 'security software', 'privacy tools'],
        'network_supported': True,
        'show_slot': True,
    },
    {
        'paths': {'/drone'},
        'slot_key': 'drone',
        'title': 'Drone Systems and FPV',
        'summary': 'Best fit for FPV hardware, batteries, chargers, field kits, and mapping accessories.',
        'keywords': ['FPV gear', 'drone batteries', 'flight accessories'],
        'network_supported': True,
        'show_slot': True,
        'secondary_lane': True,
    },
    {
        'paths': {'/map', '/live'},
        'slot_key': 'map',
        'title': 'Navigation and Mapping',
        'summary': 'Best fit for GPS tools, offline maps, route planning, and field navigation gear.',
        'keywords': ['GPS devices', 'offline maps', 'navigation tools'],
        'network_supported': True,
        'show_slot': True,
    },
    {
        'paths': {'/survival', '/survival/army-library', '/survival/army-library/offline', '/survival/army-library/binder', '/survival/meal-bible', '/culture'},
        'slot_key': 'survival',
        'title': 'Survival Gear and Preparedness',
        'summary': 'Best fit for water filtration, emergency food, lighting, shelter, and field medical kits.',
        'keywords': ['survival gear', 'emergency food', 'water filtration'],
        'network_supported': True,
        'show_slot': True,
        'secondary_lane': True,
    },
    {
        'paths': {'/radio'},
        'slot_key': 'radio',
        'title': 'Comms Gear and Radio',
        'summary': 'Best fit for radio hardware, scanners, antennas, signal tools, and comms accessories.',
        'keywords': ['radio gear', 'scanners', 'antennas'],
        'network_supported': True,
        'show_slot': True,
    },
    {
        'paths': {'/manuals', '/manuals/'},
        'slot_key': 'manuals',
        'title': 'Repair Manuals and Shop Tools',
        'summary': 'Best fit for repair tools, diagnostic devices, replacement parts, and service platforms.',
        'keywords': ['repair manuals', 'diagnostic tools', 'replacement parts'],
        'network_supported': True,
        'show_slot': True,
        'secondary_lane': True,
    },
    {
        'paths': {'/mechanics', '/mechanics/browser', '/mechanics/gallery', '/mechanics/blueprints'},
        'slot_key': 'mechanics',
        'title': 'Mechanics and Workshop',
        'summary': 'Best fit for shop equipment, fabrication tools, parts vendors, and garage software.',
        'keywords': ['shop tools', 'fabrication', 'garage equipment'],
        'network_supported': True,
        'show_slot': True,
    },
    {
        'paths': {'/bible'},
        'slot_key': 'bible',
        'title': 'Bible Study and Print Resources',
        'summary': 'Best fit for study guides, journals, print publishers, and faith media tools.',
        'keywords': ['Bible study', 'journals', 'publishers'],
        'network_supported': True,
        'show_slot': True,
    },
    {
        'paths': {'/truth'},
        'slot_key': 'truth',
        'title': 'Alternative Media and Publishing',
        'summary': 'Best fit for publishers, newsletters, podcast gear, and direct sponsor offers.',
        'keywords': ['publishing', 'newsletter tools', 'podcast gear'],
        'network_supported': False,
        'show_slot': True,
    },
    {
        'paths': {'/weapons', '/weapons/armory'},
        'slot_key': 'weapons',
        'title': 'Range Gear and Optics Sponsors',
        'summary': 'Best fit for direct sponsor deals such as safes, optics, training, and storage products.',
        'keywords': ['optics', 'range gear', 'training'],
        'network_supported': False,
        'show_slot': True,
    },
    {
        'paths': {'/advertise', '/ads'},
        'slot_key': 'advertise',
        'title': 'Advertising and Sponsorship',
        'summary': 'Keep the sales page focused on converting advertisers instead of showing another ad unit.',
        'keywords': ['sponsorship', 'media kit', 'advertising'],
        'network_supported': False,
        'show_slot': False,
    },
]
_DEFAULT_PAGE_AD_CONFIG = {
    'slot_key': 'ops',
    'title': 'Operations Tools and Hosting',
    'summary': 'Best fit for hosting, productivity software, and utility tools.',
    'keywords': ['hosting', 'productivity', 'utility tools'],
    'network_supported': True,
    'show_slot': True,
    'secondary_lane': False,
}


def _normalize_adsense_client_id(raw_value):
    if not isinstance(raw_value, str):
        return ''
    value = raw_value.strip()
    if not value:
        return ''
    if value.startswith('ca-pub-'):
        return value
    if value.startswith('pub-'):
        return f'ca-{value}'
    if value.isdigit():
        return f'ca-pub-{value}'
    return value


def _adsense_head_snippet(client_id):
    return (
        f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={html.escape(client_id)}" '
        'crossorigin="anonymous"></script>'
    )


def _normalize_slot_mapping(raw_value):
    if not isinstance(raw_value, dict):
        return {}
    slots = {}
    for key, value in raw_value.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            slots[key.strip().lower()] = value.strip()
    return slots


def _load_monetization_config():
    config = {
        'adsense_client_id': '',
        'default_slot': '',
        'slots': {},
    }
    try:
        with open(monetization_config_path, 'r', encoding='utf-8') as handle:
            raw_config = json.load(handle)
        if isinstance(raw_config, dict):
            config['adsense_client_id'] = _normalize_adsense_client_id(raw_config.get('adsense_client_id'))
            if isinstance(raw_config.get('default_slot'), str):
                config['default_slot'] = raw_config['default_slot'].strip()
            config['slots'] = _normalize_slot_mapping(raw_config.get('slots'))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    env_client_id = _normalize_adsense_client_id(os.environ.get('ADSENSE_CLIENT_ID', ''))
    env_default_slot = os.environ.get('ADSENSE_SLOT_DEFAULT', '').strip()
    if env_client_id:
        config['adsense_client_id'] = env_client_id
    if env_default_slot:
        config['default_slot'] = env_default_slot

    for page_config in _PAGE_AD_CONFIGS:
        slot_key = page_config['slot_key']
        env_slot = os.environ.get(f'ADSENSE_SLOT_{slot_key.upper()}', '').strip()
        if env_slot:
            config['slots'][slot_key] = env_slot
    return config


def _resolve_page_ad_config(path):
    normalized_path = (path or '/').rstrip('/') or '/'
    for page_config in _PAGE_AD_CONFIGS:
        if normalized_path in page_config['paths']:
            return page_config
    if normalized_path.startswith('/manuals/'):
        return {
            'paths': set(),
            'slot_key': 'manuals',
            'title': 'Repair Manuals and Shop Tools',
            'summary': 'Best fit for repair tools, diagnostic devices, replacement parts, and service platforms.',
            'keywords': ['repair manuals', 'diagnostic tools', 'replacement parts'],
            'network_supported': True,
            'show_slot': True,
            'secondary_lane': True,
        }
    if normalized_path.startswith('/mechanics/'):
        return {
            'paths': set(),
            'slot_key': 'mechanics',
            'title': 'Mechanics and Workshop',
            'summary': 'Best fit for shop equipment, fabrication tools, parts vendors, and garage software.',
            'keywords': ['shop tools', 'fabrication', 'garage equipment'],
            'network_supported': True,
            'show_slot': True,
        }
    return _DEFAULT_PAGE_AD_CONFIG


@app.context_processor
def inject_page_ad_config():
    page_ad_config = _resolve_page_ad_config(request.path)
    monetization_config = _load_monetization_config()
    page_ad_slot = monetization_config['slots'].get(page_ad_config['slot_key']) or monetization_config['default_slot']
    adsense_manual_enabled = bool(
        page_ad_config['show_slot']
        and page_ad_config['network_supported']
        and monetization_config['adsense_client_id']
        and page_ad_slot
    )
    adsense_auto_enabled = bool(
        page_ad_config['show_slot']
        and page_ad_config['network_supported']
        and monetization_config['adsense_client_id']
        and not page_ad_slot
    )
    return {
        'adsense_client_id': monetization_config['adsense_client_id'],
        'adsense_enabled': adsense_manual_enabled,
        'adsense_manual_enabled': adsense_manual_enabled,
        'adsense_auto_enabled': adsense_auto_enabled,
        'page_ad_slot': page_ad_slot,
        'page_ad_config': page_ad_config,
        'page_ad_config_path': monetization_config_path,
        'secondary_ad_lane_enabled': bool(page_ad_config.get('secondary_lane')),
    }


@app.after_request
def inject_adsense_head_code(response):
    if response.mimetype != 'text/html' or response.direct_passthrough:
        return _apply_runtime_response_policies(response)

    client_id = _load_monetization_config().get('adsense_client_id', '')
    if not client_id:
        return _apply_runtime_response_policies(response)

    html_body = response.get_data(as_text=True)
    if not html_body or '</head>' not in html_body:
        return _apply_runtime_response_policies(response)

    snippet = _adsense_head_snippet(client_id)
    if snippet in html_body:
        return _apply_runtime_response_policies(response)

    response.set_data(html_body.replace('</head>', f'    {snippet}\n  </head>', 1))
    return _apply_runtime_response_policies(response)


def _init_journal_db():
    import sqlite3

    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        author TEXT NOT NULL DEFAULT 'Anonymous',
        passage TEXT NOT NULL DEFAULT '',
        entry TEXT NOT NULL,
        created TEXT NOT NULL
    )''')
    for column_name, column_type, default_value in [
        ('author', 'TEXT', "'Anonymous'"),
        ('passage', 'TEXT', "''"),
    ]:
        try:
            cur.execute(f'ALTER TABLE journal ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}')
        except Exception:
            pass
    conn.commit()
    conn.close()


_init_journal_db()


def _init_ad_inquiries_db():
    import sqlite3

    conn = sqlite3.connect(ad_inquiries_db_path)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS ad_inquiries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL,
        contact_name TEXT NOT NULL,
        email TEXT NOT NULL,
        website TEXT,
        placement TEXT NOT NULL,
        budget TEXT,
        start_window TEXT,
        notes TEXT,
        created TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()


_init_ad_inquiries_db()


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

AUTH_USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$')
AUTH_STATE_KEY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$')
AUTH_MIN_PASSWORD_CHARS = 8
AUTH_MAX_PASSWORD_CHARS = 128
AUTH_MAX_STATE_KEYS = 256
AUTH_MAX_STATE_VALUE_CHARS = 400000
AUTH_MAX_STATE_TOTAL_CHARS = 1500000
AUTH_PASSWORD_HASH_METHOD = 'pbkdf2:sha256:600000'
AUTH_RECOVERY_KEY_LENGTH = 16
AUTH_RECOVERY_KEY_PATTERN = re.compile(r'^DCRK(?:-[A-Z2-7]{4}){4}$')
AUTH_RECOVERY_KEY_ALPHABET = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567')


def _init_auth_db():
    conn = sqlite3.connect(auth_db_path)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        username_lookup TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL
    )''')
    for col, typ in [('recovery_hash', 'TEXT'), ('recovery_generated_at', 'TEXT')]:
        try:
            cur.execute(f'ALTER TABLE users ADD COLUMN {col} {typ}')
        except Exception:
            pass
    cur.execute('''CREATE TABLE IF NOT EXISTS user_state (
        user_id INTEGER PRIMARY KEY,
        state_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_users_lookup ON users(username_lookup)')
    conn.commit()
    conn.close()


_init_auth_db()


def _auth_db_connection():
    conn = sqlite3.connect(auth_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _auth_timestamp():
    return datetime.utcnow().isoformat() + 'Z'


def _normalize_auth_username(value):
    if not isinstance(value, str):
        return None
    username = value.strip()
    if not AUTH_USERNAME_PATTERN.fullmatch(username):
        return None
    return username


def _normalize_auth_password(value):
    if not isinstance(value, str):
        return None
    if len(value) < AUTH_MIN_PASSWORD_CHARS or len(value) > AUTH_MAX_PASSWORD_CHARS:
        return None
    return value


def _generate_recovery_key():
    raw_key = base64.b32encode(secrets.token_bytes(10)).decode('ascii').rstrip('=').upper()
    return 'DCRK-' + '-'.join(raw_key[index:index + 4] for index in range(0, AUTH_RECOVERY_KEY_LENGTH, 4))


def _normalize_recovery_key(value):
    if not isinstance(value, str):
        return None
    compact = re.sub(r'[^A-Z2-7]', '', value.strip().upper())
    if compact.startswith('DCRK'):
        compact = compact[4:]
    if len(compact) != AUTH_RECOVERY_KEY_LENGTH:
        return None
    if any(char not in AUTH_RECOVERY_KEY_ALPHABET for char in compact):
        return None
    normalized = 'DCRK-' + '-'.join(compact[index:index + 4] for index in range(0, AUTH_RECOVERY_KEY_LENGTH, 4))
    return normalized if AUTH_RECOVERY_KEY_PATTERN.fullmatch(normalized) else None


def _issue_recovery_key(user_id):
    recovery_key = _generate_recovery_key()
    recovery_generated_at = _auth_timestamp()
    conn = _auth_db_connection()
    conn.execute(
        'UPDATE users SET recovery_hash = ?, recovery_generated_at = ?, updated_at = ? WHERE id = ?',
        (
            generate_password_hash(recovery_key, method=AUTH_PASSWORD_HASH_METHOD),
            recovery_generated_at,
            recovery_generated_at,
            int(user_id),
        ),
    )
    conn.commit()
    conn.close()
    return recovery_key, recovery_generated_at


def _auth_public_user(row):
    if not row:
        return None
    return {
        'id': int(row['id']),
        'username': row['username'],
        'created_at': row['created_at'],
        'last_login_at': row['last_login_at'],
        'has_recovery_key': bool(row['recovery_hash']),
        'recovery_generated_at': row['recovery_generated_at'] or '',
    }


def _load_user_by_id(user_id):
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    if user_id <= 0:
        return None

    conn = _auth_db_connection()
    row = conn.execute(
        'SELECT id, username, created_at, last_login_at, recovery_hash, recovery_generated_at FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    conn.close()
    return _auth_public_user(row)


def _current_auth_user():
    cached_user = getattr(g, 'current_user', None)
    if cached_user is not None:
        return cached_user

    user_id = session.get('auth_user_id')
    user = _load_user_by_id(user_id) if user_id else None
    if user is None and user_id:
        session.pop('auth_user_id', None)
    g.current_user = user
    return user


def _journal_moderator_password_sha256():
    configured = input_validator.validate_string(
        os.environ.get('BIBLE_JOURNAL_MODERATOR_PASSWORD_SHA256'),
        max_length=128,
    )
    return (configured or DEFAULT_JOURNAL_MODERATOR_PASSWORD_SHA256).strip().lower()


def _is_journal_moderator():
    return bool(session.get(JOURNAL_MODERATOR_SESSION_KEY))


def _verify_journal_moderator_password(password):
    candidate = input_validator.validate_string(password, max_length=256)
    if not candidate:
        return False
    digest = hashlib.sha256(candidate.encode('utf-8')).hexdigest()
    return secrets.compare_digest(digest, _journal_moderator_password_sha256())


def _user_state_bundle(user_id):
    conn = _auth_db_connection()
    row = conn.execute(
        'SELECT state_json, updated_at FROM user_state WHERE user_id = ?',
        (int(user_id),),
    ).fetchone()
    conn.close()
    if not row:
        return {}, ''

    try:
        payload = json.loads(row['state_json']) if row['state_json'] else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    sanitized = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            sanitized[key] = value
    return sanitized, row['updated_at'] or ''


def _save_user_state_bundle(user_id, storage):
    updated_at = _auth_timestamp()
    state_json = json.dumps(storage, separators=(',', ':'), sort_keys=True)
    conn = _auth_db_connection()
    conn.execute(
        '''INSERT INTO user_state(user_id, state_json, updated_at)
           VALUES(?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at''',
        (int(user_id), state_json, updated_at),
    )
    conn.commit()
    conn.close()
    return updated_at


def _sanitize_auth_state_payload(storage):
    if not isinstance(storage, dict):
        return None, 'storage must be an object'

    sanitized = {}
    total_chars = 0
    for key, value in storage.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return None, 'storage keys and values must be strings'
        if not AUTH_STATE_KEY_PATTERN.fullmatch(key):
            continue
        if len(value) > AUTH_MAX_STATE_VALUE_CHARS:
            return None, f'storage value too large for {key}'
        sanitized[key] = value
        total_chars += len(key) + len(value)
        if len(sanitized) > AUTH_MAX_STATE_KEYS or total_chars > AUTH_MAX_STATE_TOTAL_CHARS:
            return None, 'storage payload too large'
    return sanitized, None


def _auth_bootstrap_payload():
    user = _current_auth_user()
    storage = {}
    updated_at = ''
    if user:
        storage, updated_at = _user_state_bundle(user['id'])
        session.pop('auth_state_restore_pending', None)
    return {
        'authenticated': bool(user),
        'user': user,
        'storage': storage,
        'updated_at': updated_at,
    }


@app.context_processor
def inject_auth_context():
    payload = _auth_bootstrap_payload()
    return {
        'auth_user': payload['user'],
        'auth_bootstrap': payload,
    }

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
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://unpkg.com https://pagead2.googlesyndication.com https://partner.googleadservices.com https://googleads.g.doubleclick.net https://*.adtrafficquality.google",
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

DEFAULT_MAX_REQUEST_BYTES = 2 * 1024 * 1024
UPLOAD_MAX_REQUEST_BYTES = 64 * 1024 * 1024
DEFAULT_PAGE_RATE_LIMIT = (1000, 60)
READ_HEAVY_CACHE_CONTROL = 'public, max-age=300, stale-while-revalidate=60'
REQUEST_RATE_LIMITS = {
    ('GET', '/'): (240, 60),
    ('GET', '/ai'): (180, 60),
    ('GET', '/cyber'): (180, 60),
    ('GET', '/map'): (120, 60),
    ('GET', '/live'): (120, 60),
    ('GET', '/survival'): (180, 60),
    ('GET', '/culture'): (180, 60),
    ('GET', '/survival/meal-bible'): (180, 60),
    ('GET', '/survival/army-library'): (120, 60),
    ('GET', '/survival/army-library/offline'): (90, 60),
    ('GET', '/survival/army-library/binder'): (90, 60),
    ('GET', '/advertise'): (120, 60),
    ('GET', '/weapons'): (120, 60),
    ('GET', '/weapons/armory'): (120, 60),
    ('GET', '/mechanics'): (120, 60),
    ('GET', '/mechanics/browser'): (120, 60),
    ('GET', '/mechanics/gallery'): (120, 60),
    ('GET', '/mechanics/blueprints'): (120, 60),
    ('GET', '/manuals'): (120, 60),
    ('GET', '/bible'): (120, 60),
    ('GET', '/drone'): (120, 60),
    ('GET', '/radio'): (120, 60),
    ('GET', '/schooling'): (120, 60),
    ('GET', '/truth'): (120, 60),
    ('GET', '/api/topline-intel'): (120, 60),
    ('GET', '/api/online'): (120, 60),
    ('GET', '/api/auth/session'): (60, 60),
    ('GET', '/api/auth/state'): (120, 60),
    ('GET', '/api/bible'): (60, 60),
    ('GET', '/api/cyber/headers'): (30, 60),
    ('GET', '/api/manuals/search'): (120, 60),
    ('POST', '/api/auth/register'): (10, 300),
    ('POST', '/api/auth/login'): (20, 300),
    ('POST', '/api/auth/logout'): (30, 300),
    ('POST', '/api/auth/recovery-key'): (20, 300),
    ('POST', '/api/auth/reset-password'): (10, 300),
    ('POST', '/api/auth/state'): (120, 60),
    ('POST', '/api/advertise/inquiry'): (10, 300),
    ('POST', '/api/journal/add'): (10, 300),
    ('POST', '/api/journal/moderation/login'): (10, 300),
    ('POST', '/api/journal/moderation/logout'): (20, 300),
    ('POST', '/api/journal/moderation/delete'): (20, 300),
    ('POST', '/api/ping'): (120, 60),
    ('POST', '/api/ai'): (30, 60),
    ('POST', '/api/truth/summary'): (30, 60),
    ('POST', '/api/share'): (30, 60),
    ('POST', '/api/cyber/encrypt'): (30, 60),
    ('POST', '/run'): (20, 60),
    ('POST', '/agent'): (20, 60),
    ('POST', '/api/projects/delete'): (20, 60),
    ('POST', '/api/projects/download'): (20, 60),
}
REQUEST_RATE_LIMIT_PREFIXES = {
    ('GET', '/manuals/'): (90, 60),
    ('GET', '/mechanics/'): (120, 60),
    ('GET', '/share/'): (60, 60),
    ('GET', '/download/'): (20, 60),
    ('GET', '/api/share/'): (60, 60),
}
PAGE_CACHE_CONTROLS = {
    '/': READ_HEAVY_CACHE_CONTROL,
    '/ai': READ_HEAVY_CACHE_CONTROL,
    '/cyber': READ_HEAVY_CACHE_CONTROL,
    '/map': READ_HEAVY_CACHE_CONTROL,
    '/live': READ_HEAVY_CACHE_CONTROL,
    '/survival': READ_HEAVY_CACHE_CONTROL,
    '/survival/meal-bible': READ_HEAVY_CACHE_CONTROL,
    '/survival/army-library': READ_HEAVY_CACHE_CONTROL,
    '/survival/army-library/offline': READ_HEAVY_CACHE_CONTROL,
    '/survival/army-library/binder': READ_HEAVY_CACHE_CONTROL,
    '/advertise': READ_HEAVY_CACHE_CONTROL,
    '/weapons': READ_HEAVY_CACHE_CONTROL,
    '/weapons/armory': READ_HEAVY_CACHE_CONTROL,
    '/mechanics': READ_HEAVY_CACHE_CONTROL,
    '/mechanics/browser': READ_HEAVY_CACHE_CONTROL,
    '/mechanics/gallery': READ_HEAVY_CACHE_CONTROL,
    '/mechanics/blueprints': READ_HEAVY_CACHE_CONTROL,
    '/drone': READ_HEAVY_CACHE_CONTROL,
    '/radio': READ_HEAVY_CACHE_CONTROL,
    '/schooling': READ_HEAVY_CACHE_CONTROL,
    '/truth': READ_HEAVY_CACHE_CONTROL,
    '/culture': READ_HEAVY_CACHE_CONTROL,
    '/manuals': READ_HEAVY_CACHE_CONTROL,
}
PAGE_CACHE_PREFIX_CONTROLS = {
    '/manuals/': READ_HEAVY_CACHE_CONTROL,
    '/mechanics/': READ_HEAVY_CACHE_CONTROL,
}
SENSITIVE_NO_STORE_PATHS = {
    '/bible',
}
LONG_CACHE_STATIC_EXTENSIONS = (
    '.css',
    '.js',
    '.svg',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.webp',
    '.woff',
    '.woff2',
    '.ico',
)
ALLOWED_RUN_LANGUAGES = {'python', 'java', 'html', 'css'}
ALLOWED_AI_PROVIDERS = {'pollinations', 'huggingface'}
ALLOWED_ENCRYPTION_ALGOS = {'all', 'sha256', 'sha512', 'md5', 'sha1', 'base64', 'base64_decode'}
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
DISALLOWED_PRIVATE_HOSTS = {
    'localhost',
    '0.0.0.0',
    '127.0.0.1',
    '::1',
    'host.docker.internal',
    'metadata.google.internal',
}
MAX_CODE_CHARS = 120000
MAX_AGENT_INSTRUCTION_CHARS = 8000
MAX_PROJECT_NAME_CHARS = 120
MAX_SHARE_SNIPPET_CHARS = 120000
MAX_PROMPT_CHARS = 8000
MAX_SUMMARY_QUERY_CHARS = 4000
MAX_CYBER_TEXT_CHARS = 20000
MAX_PROJECT_BATCH = 25


def _request_size_limit_for(path):
    if path == '/api/manuals/upload':
        return UPLOAD_MAX_REQUEST_BYTES
    return DEFAULT_MAX_REQUEST_BYTES


def _cache_control_for_path(path):
    cache_control = PAGE_CACHE_CONTROLS.get(path)
    if cache_control:
        return cache_control
    for prefix, value in PAGE_CACHE_PREFIX_CONTROLS.items():
        if path.startswith(prefix):
            return value
    return None


def _request_rate_limit_for(method, path):
    scoped_limit = REQUEST_RATE_LIMITS.get((method, path))
    if scoped_limit:
        return scoped_limit
    for (prefix_method, prefix_path), value in REQUEST_RATE_LIMIT_PREFIXES.items():
        if method == prefix_method and path.startswith(prefix_path):
            return value
    return None


def _json_payload():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _bounded_text(value, max_length, allow_empty=True):
    if not isinstance(value, str):
        return '' if allow_empty else None
    if len(value) > max_length:
        return None
    return value


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _sanitize_project_name(value):
    safe_value = input_validator.sanitize_filename(value, max_length=MAX_PROJECT_NAME_CHARS)
    if not safe_value:
        return None
    return safe_value


def _append_auth_session(user_id, remember, restore_pending=False):
    session['auth_user_id'] = int(user_id)
    session.permanent = bool(remember)
    if restore_pending:
        session['auth_state_restore_pending'] = True
    else:
        session.pop('auth_state_restore_pending', None)


def _clear_auth_session():
    session.pop('auth_user_id', None)
    session.pop('auth_state_restore_pending', None)
    session.permanent = False
    g.current_user = None


def _validated_live_satellite_frames(raw_frames):
    if not isinstance(raw_frames, list) or not raw_frames or len(raw_frames) > 8:
        return None
    validated = []
    for frame in raw_frames:
        if not isinstance(frame, str):
            return None
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z', frame):
            validated.append(frame)
            continue
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', frame):
            validated.append(frame)
            continue
        return None
    return validated


def _validated_live_satellite_tiles(raw_tiles):
    if not isinstance(raw_tiles, list) or not raw_tiles or len(raw_tiles) > 8:
        return None
    validated = []
    for tile in raw_tiles:
        if not isinstance(tile, dict):
            return None
        try:
            z = int(tile.get('z'))
            x = int(tile.get('x'))
            y = int(tile.get('y'))
        except (TypeError, ValueError):
            return None
        if not (0 <= z <= 22):
            return None
        max_tile = (2 ** z) - 1
        if not (0 <= x <= max_tile and 0 <= y <= max_tile):
            return None
        validated.append({'z': z, 'x': x, 'y': y})
    return validated


def _live_satellite_probe_url(source_key, frame_time, tile):
    source = LIVE_SATELLITE_SOURCE_LOOKUP.get(source_key)
    if not source:
        return None
    return (
        'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/'
        f"{source['layer_id']}/default/{frame_time}/{source['matrix_set']}/{tile['z']}/{tile['y']}/{tile['x']}.png"
    )


def _live_satellite_tile_available(source_key, frame_time, tile):
    url = _live_satellite_probe_url(source_key, frame_time, tile)
    if not url:
        return False
    try:
        response = http_requests.head(
            url,
            timeout=3,
            headers={'User-Agent': 'futurecodedelta/1.0'},
        )
    except http_requests.RequestException:
        return False
    return response.status_code == 200


def _validated_project_names(raw_values):
    if not isinstance(raw_values, list) or not raw_values:
        return None
    if len(raw_values) > MAX_PROJECT_BATCH:
        return None
    validated = []
    for value in raw_values:
        safe_value = _sanitize_project_name(value)
        if not safe_value:
            return None
        validated.append(safe_value)
    return validated


def _has_allowed_extension(filename, allowed_extensions):
    if not filename:
        return False
    return os.path.splitext(filename.lower())[1] in allowed_extensions


def _sandbox_environment(tempdir):
    return {
        'HOME': tempdir,
        'LANG': 'C.UTF-8',
        'LC_ALL': 'C.UTF-8',
        'PATH': os.environ.get('PATH', ''),
        'PYTHONDONTWRITEBYTECODE': '1',
        'TMPDIR': tempdir,
    }


def _subprocess_preexec_limits():
    try:
        import resource

        memory_limit = 256 * 1024 * 1024
        file_limit = 10 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if hasattr(resource, 'RLIMIT_AS'):
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    except Exception:
        pass


def _subprocess_kwargs(tempdir, timeout_seconds):
    kwargs = {
        'capture_output': True,
        'cwd': tempdir,
        'env': _sandbox_environment(tempdir),
        'text': True,
        'timeout': timeout_seconds,
    }
    if os.name != 'nt':
        kwargs['preexec_fn'] = _subprocess_preexec_limits
    return kwargs


def _is_public_scan_target(hostname):
    if not hostname:
        return False
    lowered = hostname.lower()
    if lowered in DISALLOWED_PRIVATE_HOSTS or lowered.endswith(('.local', '.internal', '.lan', '.home')):
        return False
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(hostname, None)
            if info[4]
        }
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        parsed_ip = ipaddress.ip_address(address)
        if (
            parsed_ip.is_private
            or parsed_ip.is_loopback
            or parsed_ip.is_link_local
            or parsed_ip.is_reserved
            or parsed_ip.is_multicast
            or parsed_ip.is_unspecified
        ):
            return False
    return True


def _normalize_public_scan_url(raw_value):
    if not isinstance(raw_value, str):
        return None
    candidate = raw_value.strip()
    if not candidate:
        return None
    if not candidate.startswith(('http://', 'https://')):
        candidate = 'https://' + candidate
    validated = input_validator.validate_url(candidate)
    if not validated:
        return None
    parsed = urlparse(validated)
    if parsed.scheme not in {'http', 'https'} or not _is_public_scan_target(parsed.hostname):
        return None
    return validated


def _append_vary_header(response, value):
    existing = response.headers.get('Vary', '')
    values = [item.strip() for item in existing.split(',') if item.strip()]
    if value not in values:
        values.append(value)
        response.headers['Vary'] = ', '.join(values)


def _apply_runtime_response_policies(response):
    _append_vary_header(response, 'Accept-Encoding')
    if session.get('auth_user_id'):
        _append_vary_header(response, 'Cookie')

    if hasattr(g, 'rate_limit_limit'):
        response.headers['X-RateLimit-Limit'] = str(g.rate_limit_limit)
        response.headers['X-RateLimit-Remaining'] = str(max(g.rate_limit_remaining, 0))
    if getattr(g, 'rate_limit_retry_after', 0):
        response.headers['Retry-After'] = str(int(g.rate_limit_retry_after))

    if request.path.startswith('/api/') or request.path in SENSITIVE_NO_STORE_PATHS:
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
    elif getattr(g, 'current_user', None) and response.mimetype == 'text/html':
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
    elif request.path.startswith('/static/') and request.path.endswith(LONG_CACHE_STATIC_EXTENSIONS):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    else:
        cache_control = _cache_control_for_path(request.path)
        if cache_control and response.status_code == 200:
            response.headers['Cache-Control'] = cache_control

    if request.method in {'GET', 'HEAD'} and response.status_code == 200 and not response.direct_passthrough:
        if response.mimetype in {
            'text/html',
            'text/css',
            'application/javascript',
            'application/json',
            'application/manifest+json',
        }:
            response.add_etag()
            response.make_conditional(request)

    return response

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

    moderation_enabled = _is_journal_moderator()
    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute(
        '''SELECT id, COALESCE(author, 'Anonymous'), COALESCE(passage, ''), entry, created
           FROM journal
           ORDER BY created DESC
           LIMIT 200'''
    )
    rows = [
        {
            'id': row[0],
            'author': row[1],
            'passage': row[2],
            'entry': row[3],
            'created': row[4],
        }
        for row in cur.fetchall()
    ]
    conn.close()
    return jsonify({'ok': True, 'entries': rows, 'moderation_enabled': moderation_enabled})


@app.route('/api/journal/add', methods=['POST'])
def api_journal_add():
    user = _current_auth_user()
    if not user:
        return jsonify({'ok': False, 'error': 'sign in to post to the public journal'}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict(flat=True)

    entry = input_validator.validate_string(data.get('entry'), max_length=5000)
    if not entry:
        return jsonify({'ok': False, 'error': 'Entry required'}), 400

    passage = input_validator.validate_string(data.get('passage'), max_length=160) or ''
    author = input_validator.validate_string(user.get('username'), max_length=80) or 'Anonymous'

    import sqlite3
    from datetime import datetime

    created_at = datetime.utcnow().isoformat() + 'Z'
    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO journal (author, passage, entry, created) VALUES (?, ?, ?, ?)',
        (author, passage, entry, created_at),
    )
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({
        'ok': True,
        'entry': {
            'id': entry_id,
            'author': author,
            'passage': passage,
            'entry': entry,
            'created': created_at,
        },
    }), 201


@app.route('/api/journal/moderation/login', methods=['POST'])
def api_journal_moderation_login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict(flat=True)

    password = data.get('password')
    if not _verify_journal_moderator_password(password):
        return jsonify({'ok': False, 'error': 'invalid moderation password'}), 403

    session[JOURNAL_MODERATOR_SESSION_KEY] = True
    session.modified = True
    return jsonify({'ok': True, 'moderation_enabled': True})


@app.route('/api/journal/moderation/logout', methods=['POST'])
def api_journal_moderation_logout():
    session.pop(JOURNAL_MODERATOR_SESSION_KEY, None)
    session.modified = True
    return jsonify({'ok': True, 'moderation_enabled': False})


@app.route('/api/journal/moderation/delete', methods=['POST'])
def api_journal_moderation_delete():
    if not _is_journal_moderator():
        return jsonify({'ok': False, 'error': 'moderation access required'}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict(flat=True)

    try:
        entry_id = int(data.get('id', 0))
    except (TypeError, ValueError):
        entry_id = 0
    if entry_id <= 0:
        return jsonify({'ok': False, 'error': 'valid entry id required'}), 400

    import sqlite3

    conn = sqlite3.connect(journal_db_path)
    cur = conn.cursor()
    cur.execute('DELETE FROM journal WHERE id = ?', (entry_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()

    if not deleted:
        return jsonify({'ok': False, 'error': 'entry not found'}), 404

    return jsonify({'ok': True, 'deleted_id': entry_id})


@app.route('/api/advertise/inquiry', methods=['POST'])
def api_advertise_inquiry():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict(flat=True)

    company = input_validator.validate_string(data.get('company'), max_length=160)
    contact_name = input_validator.validate_string(data.get('contact_name'), max_length=160)
    email = input_validator.validate_email(data.get('email'))
    placement = input_validator.validate_string(data.get('placement'), max_length=120)
    budget = input_validator.validate_string(data.get('budget'), max_length=80) or ''
    start_window = input_validator.validate_string(data.get('start_window'), max_length=120) or ''
    notes = input_validator.validate_string(data.get('notes'), max_length=2000) or ''
    website_value = (data.get('website') or '').strip()
    website = input_validator.validate_url(website_value) if website_value else ''

    if not company:
        return jsonify({'ok': False, 'error': 'company is required'}), 400
    if not contact_name:
        return jsonify({'ok': False, 'error': 'contact name is required'}), 400
    if not email:
        return jsonify({'ok': False, 'error': 'valid email is required'}), 400
    if not placement:
        return jsonify({'ok': False, 'error': 'placement is required'}), 400
    if website_value and not website:
        return jsonify({'ok': False, 'error': 'valid website is required'}), 400

    import sqlite3

    conn = sqlite3.connect(ad_inquiries_db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO ad_inquiries
           (company, contact_name, email, website, placement, budget, start_window, notes, created)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            company,
            contact_name,
            email,
            website,
            placement,
            budget,
            start_window,
            notes,
            datetime.utcnow().isoformat() + 'Z',
        ),
    )
    inquiry_id = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'id': inquiry_id})


@app.route('/api/auth/session', methods=['GET'])
def api_auth_session():
    user = _current_auth_user()
    return jsonify({
        'ok': True,
        'authenticated': bool(user),
        'user': user,
    })


@app.route('/api/auth/register', methods=['POST'])
def api_auth_register():
    payload = _json_payload()
    username = _normalize_auth_username(payload.get('username'))
    password = _normalize_auth_password(payload.get('password'))
    remember = _coerce_bool(payload.get('remember'))

    if not username:
        return jsonify({
            'ok': False,
            'error': 'login id must be 3-64 characters using letters, numbers, dot, underscore, or dash',
        }), 400
    if not password:
        return jsonify({
            'ok': False,
            'error': f'passcode must be {AUTH_MIN_PASSWORD_CHARS}-{AUTH_MAX_PASSWORD_CHARS} characters',
        }), 400

    lookup = username.lower()
    now = _auth_timestamp()
    conn = _auth_db_connection()
    existing = conn.execute('SELECT id FROM users WHERE username_lookup = ?', (lookup,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'ok': False, 'error': 'login id already exists'}), 409

    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO users(username, username_lookup, password_hash, created_at, updated_at, last_login_at)
           VALUES(?, ?, ?, ?, ?, ?)''',
        (username, lookup, generate_password_hash(password, method=AUTH_PASSWORD_HASH_METHOD), now, now, now),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

    recovery_key, recovery_generated_at = _issue_recovery_key(user_id)

    _append_auth_session(user_id, remember, restore_pending=False)
    user = _load_user_by_id(user_id)
    g.current_user = user
    return jsonify({
        'ok': True,
        'authenticated': True,
        'user': user,
        'recovery_key': recovery_key,
        'recovery_generated_at': recovery_generated_at,
    })


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    payload = _json_payload()
    username = _normalize_auth_username(payload.get('username'))
    password = _normalize_auth_password(payload.get('password'))
    remember = _coerce_bool(payload.get('remember'))

    if not username or not password:
        return jsonify({'ok': False, 'error': 'login id and passcode are required'}), 400

    conn = _auth_db_connection()
    row = conn.execute(
        'SELECT id, username, password_hash, created_at, last_login_at FROM users WHERE username_lookup = ?',
        (username.lower(),),
    ).fetchone()
    if not row or not check_password_hash(row['password_hash'], password):
        conn.close()
        return jsonify({'ok': False, 'error': 'invalid login id or passcode'}), 401

    now = _auth_timestamp()
    conn.execute('UPDATE users SET updated_at = ?, last_login_at = ? WHERE id = ?', (now, now, row['id']))
    conn.commit()
    conn.close()

    _append_auth_session(row['id'], remember, restore_pending=True)
    user = _load_user_by_id(row['id'])
    g.current_user = user
    return jsonify({'ok': True, 'authenticated': True, 'user': user})


@app.route('/api/auth/recovery-key', methods=['POST'])
def api_auth_recovery_key():
    user = _current_auth_user()
    if not user:
        return jsonify({'ok': False, 'error': 'authentication required'}), 401

    recovery_key, recovery_generated_at = _issue_recovery_key(user['id'])
    user = _load_user_by_id(user['id'])
    g.current_user = user
    return jsonify({
        'ok': True,
        'user': user,
        'recovery_key': recovery_key,
        'recovery_generated_at': recovery_generated_at,
    })


@app.route('/api/auth/reset-password', methods=['POST'])
def api_auth_reset_password():
    payload = _json_payload()
    username = _normalize_auth_username(payload.get('username'))
    recovery_key = _normalize_recovery_key(payload.get('recovery_key'))
    password = _normalize_auth_password(payload.get('password'))
    remember = _coerce_bool(payload.get('remember'))

    if not username or not recovery_key or not password:
        return jsonify({'ok': False, 'error': 'login id, recovery key, and new passcode are required'}), 400

    conn = _auth_db_connection()
    row = conn.execute(
        'SELECT id, recovery_hash FROM users WHERE username_lookup = ?',
        (username.lower(),),
    ).fetchone()
    if not row or not row['recovery_hash'] or not check_password_hash(row['recovery_hash'], recovery_key):
        conn.close()
        return jsonify({'ok': False, 'error': 'invalid login id or recovery key'}), 401

    now = _auth_timestamp()
    conn.execute(
        'UPDATE users SET password_hash = ?, updated_at = ?, last_login_at = ? WHERE id = ?',
        (generate_password_hash(password, method=AUTH_PASSWORD_HASH_METHOD), now, now, row['id']),
    )
    conn.commit()
    conn.close()

    rotated_recovery_key, recovery_generated_at = _issue_recovery_key(row['id'])
    _append_auth_session(row['id'], remember, restore_pending=True)
    user = _load_user_by_id(row['id'])
    g.current_user = user
    return jsonify({
        'ok': True,
        'authenticated': True,
        'user': user,
        'recovery_key': rotated_recovery_key,
        'recovery_generated_at': recovery_generated_at,
    })


@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    _clear_auth_session()
    return jsonify({'ok': True, 'authenticated': False})


@app.route('/api/auth/state', methods=['GET'])
def api_auth_state():
    user = _current_auth_user()
    if not user:
        return jsonify({'ok': False, 'error': 'authentication required'}), 401

    storage, updated_at = _user_state_bundle(user['id'])
    return jsonify({'ok': True, 'storage': storage, 'updated_at': updated_at})


@app.route('/api/auth/state', methods=['POST'])
def api_auth_state_save():
    user = _current_auth_user()
    if not user:
        return jsonify({'ok': False, 'error': 'authentication required'}), 401

    if session.get('auth_state_restore_pending'):
        storage, updated_at = _user_state_bundle(user['id'])
        return jsonify({'ok': True, 'updated_at': updated_at, 'keys': len(storage), 'restored': True})

    payload = _json_payload()
    storage, error = _sanitize_auth_state_payload(payload.get('storage', {}))
    if error:
        return jsonify({'ok': False, 'error': error}), 400

    updated_at = _save_user_state_bundle(user['id'], storage)
    return jsonify({'ok': True, 'updated_at': updated_at, 'keys': len(storage)})


@app.route('/api/advertise/inquiries', methods=['GET'])
def api_advertise_inquiries():
    admin_token = os.environ.get('ADMIN_TOKEN')
    provided = request.headers.get('X-Admin-Token') or request.args.get('admin_token')
    if admin_token and provided != admin_token:
        return jsonify({'ok': False, 'error': 'admin token required'}), 403

    import sqlite3

    conn = sqlite3.connect(ad_inquiries_db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''
        SELECT id, company, contact_name, email, website, placement, budget, start_window, notes, created
        FROM ad_inquiries
        ORDER BY created DESC
    ''')
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({'ok': True, 'inquiries': rows})


import re as _re
_TOKEN_RE = _re.compile(r'^[0-9a-f\-]{8,72}$')


@app.route('/api/ping', methods=['POST'])
def api_ping():
    import sqlite3, time
    data = _json_payload()
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


@app.before_request
def protect_runtime_requests():
    if request.endpoint == 'static' or request.method == 'OPTIONS':
        return None

    g.current_user = _current_auth_user()

    content_length = request.content_length or 0
    request_limit = _request_size_limit_for(request.path)
    if content_length > request_limit:
        g.rate_limit_retry_after = 0
        return jsonify({
            'ok': False,
            'error': f'request too large (max {request_limit} bytes)',
        }), 413

    global_limit, global_window = DEFAULT_PAGE_RATE_LIMIT
    allowed, remaining, retry_after = rate_limiter.check_limit(limit=global_limit, window=global_window)
    g.rate_limit_limit = global_limit
    g.rate_limit_remaining = remaining
    g.rate_limit_retry_after = retry_after
    if not allowed:
        return jsonify({'ok': False, 'error': 'rate limit exceeded'}), 429

    scoped_limit = _request_rate_limit_for(request.method, request.path)
    if not scoped_limit:
        return None

    limit, window = scoped_limit
    allowed, remaining, retry_after = rate_limiter.check_limit(
        endpoint=f'{request.method}:{request.path}',
        limit=limit,
        window=window,
    )
    g.rate_limit_limit = limit
    g.rate_limit_remaining = remaining
    g.rate_limit_retry_after = retry_after
    if not allowed:
        return jsonify({'ok': False, 'error': 'rate limit exceeded'}), 429
    return None


@app.after_request
def apply_security_headers(response):
    for header_name, header_value in SECURITY_HEADERS.items():
        response.headers[header_name] = header_value
    if request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    response.headers['X-DNS-Prefetch-Control'] = 'off'
    response.headers['X-Permitted-Cross-Domain-Policies'] = 'none'

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
    admin_token = os.environ.get('ADMIN_TOKEN')
    return render_template(
        'index.html',
        admin_token_set=bool(admin_token),
        execution_locked=not admin_token and not _is_local_request_host(),
    )


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


@app.route('/ads.txt')
def ads_txt():
    response = send_file(os.path.join(APP_ROOT, 'static', 'ads.txt'), mimetype='text/plain')
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
    data = _json_payload()
    gate_error = _execution_gate_error(data)
    if gate_error is not None:
        return gate_error

    language = input_validator.validate_string(data.get('language'), max_length=16, allow_unicode=False) or 'python'
    if language not in ALLOWED_RUN_LANGUAGES:
        return jsonify({'error': 'Unsupported language'}), 400

    code = _bounded_text(data.get('code', ''), MAX_CODE_CHARS)
    if code is None:
        return jsonify({'error': f'Code too large (max {MAX_CODE_CHARS} chars)'}), 400

    run_id = uuid.uuid4().hex[:8]
    tempdir = tempfile.mkdtemp(prefix=f'delta_{run_id}_')
    try:
        if language == 'python':
            script_path = os.path.join(tempdir, 'script.py')
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(code)
            py = _which(['python3', 'python'])
            try:
                proc = subprocess.run([py, '-I', '-B', script_path], **_subprocess_kwargs(tempdir, 5))
                return jsonify({'stdout': proc.stdout, 'stderr': proc.stderr, 'returncode': proc.returncode})
            except subprocess.TimeoutExpired:
                return jsonify({'error': 'Execution timed out'}), 504

        elif language == 'java':
            java_file = os.path.join(tempdir, 'Main.java')
            with open(java_file, 'w', encoding='utf-8') as f:
                f.write(code)
            javac = _which(['javac'])
            java = _which(['java'])
            try:
                compile_proc = subprocess.run([javac, 'Main.java'], **_subprocess_kwargs(tempdir, 10))
            except subprocess.TimeoutExpired:
                return jsonify({'error': 'Compilation timed out'}), 504
            if compile_proc.returncode != 0:
                return jsonify({'compile_error': compile_proc.stderr})
            try:
                run_proc = subprocess.run([java, '-Djava.awt.headless=true', '-cp', '.', 'Main'], **_subprocess_kwargs(tempdir, 5))
                return jsonify({'stdout': run_proc.stdout, 'stderr': run_proc.stderr, 'returncode': run_proc.returncode})
            except subprocess.TimeoutExpired:
                return jsonify({'error': 'Execution timed out'}), 504

        elif language == 'html':
            css = _bounded_text(data.get('css', ''), MAX_CODE_CHARS)
            js = _bounded_text(data.get('js', ''), MAX_CODE_CHARS)
            if css is None or js is None:
                return jsonify({'error': f'Preview payload too large (max {MAX_CODE_CHARS} chars per section)'}), 400
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
            html_code = _bounded_text(data.get('html', ''), MAX_CODE_CHARS)
            if html_code is None:
                return jsonify({'error': f'HTML preview too large (max {MAX_CODE_CHARS} chars)'}), 400
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
    data = _json_payload()
    gate_error = _execution_gate_error(data)
    if gate_error is not None:
        return gate_error

    instruction = input_validator.validate_string(data.get('instruction'), max_length=MAX_AGENT_INSTRUCTION_CHARS)
    project_name = _sanitize_project_name(data.get('project_name')) if data.get('project_name') else None
    execute = _coerce_bool(data.get('execute', False))
    confirm = _coerce_bool(data.get('confirm', False))
    use_llm = _coerce_bool(data.get('use_llm', False))
    llm_model = input_validator.validate_string(data.get('llm_model'), max_length=80, allow_unicode=False) if data.get('llm_model') else None

    if not instruction:
        return jsonify({'error': 'instruction is required'}), 400

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
    data = _json_payload()
    names = _validated_project_names(data.get('projects', []))
    if not names:
        return jsonify({'ok': False, 'error': 'No projects specified'}), 400
    base_dir = _generated_root()
    deleted = []
    errors = []
    for name in names:
        project_dir = os.path.join(base_dir, name)
        if os.path.isdir(project_dir):
            shutil.rmtree(project_dir)
            deleted.append(name)
        else:
            errors.append(f'{name} not found')
    return jsonify({'ok': True, 'deleted': deleted, 'errors': errors})


@app.route('/download/<project_name>', methods=['GET'])
def download_project(project_name):
    safe_project_name = _sanitize_project_name(project_name)
    if not safe_project_name:
        return jsonify({'error': 'invalid project name'}), 400

    base_dir = _generated_root()
    project_dir = os.path.join(base_dir, safe_project_name)
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
    return send_file(mem, mimetype='application/zip', as_attachment=True, download_name=f'{safe_project_name}.zip')


@app.route('/api/projects/download', methods=['POST'])
def download_multiple():
    """Download multiple projects as a single zip."""
    data = _json_payload()
    names = _validated_project_names(data.get('projects', []))
    if not names:
        return jsonify({'ok': False, 'error': 'No projects specified'}), 400
    base_dir = _generated_root()
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            project_dir = os.path.join(base_dir, name)
            if os.path.isdir(project_dir):
                for root, dirs, files in os.walk(project_dir):
                    for f in files:
                        abs_path = os.path.join(root, f)
                        arcname = os.path.join(name, os.path.relpath(abs_path, project_dir))
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
    data = _json_payload()
    prompt = input_validator.validate_string(data.get('prompt'), max_length=MAX_PROMPT_CHARS)
    if not prompt:
        return jsonify({'ok': False, 'error': 'prompt is required'}), 400

    model = input_validator.validate_string(data.get('model'), max_length=80, allow_unicode=False) or 'openai'
    provider = input_validator.validate_string(data.get('provider'), max_length=32, allow_unicode=False) or 'pollinations'
    max_tokens = input_validator.validate_integer(data.get('max_tokens', 512), min_val=1, max_val=2048)
    temperature = input_validator.validate_float(data.get('temperature', 0.7), min_val=0.0, max_val=2.0)
    if provider not in ALLOWED_AI_PROVIDERS or max_tokens is None or temperature is None:
        return jsonify({'ok': False, 'error': 'invalid model settings'}), 400

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
    data = _json_payload()
    query = input_validator.validate_string(data.get('query'), max_length=MAX_SUMMARY_QUERY_CHARS)
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
    data = _json_payload()
    code = _bounded_text(data.get('code', ''), MAX_SHARE_SNIPPET_CHARS)
    language = input_validator.validate_string(data.get('language'), max_length=16, allow_unicode=False) or 'python'
    css = _bounded_text(data.get('css', ''), MAX_SHARE_SNIPPET_CHARS)
    js = _bounded_text(data.get('js', ''), MAX_SHARE_SNIPPET_CHARS)
    if language not in {'python', 'java', 'html', 'css', 'web'} or code is None or css is None or js is None:
        return jsonify({'ok': False, 'error': 'invalid share payload'}), 400
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
    admin_token = os.environ.get('ADMIN_TOKEN')
    return render_template(
        'index.html',
        admin_token_set=bool(admin_token),
        execution_locked=not admin_token and not _is_local_request_host(),
    )


# ── Cyber Security Tools ─────────────────────────────────────────────

@app.route('/api/cyber/encrypt', methods=['POST'])
def encrypt_text():
    """Encrypt/hash text using various algorithms (all local, no data sent externally)."""
    data = _json_payload()
    text = _bounded_text(data.get('text', ''), MAX_CYBER_TEXT_CHARS)
    algo = input_validator.validate_string(data.get('algorithm'), max_length=32, allow_unicode=False) or 'sha256'
    if not text:
        return jsonify({'ok': False, 'error': 'text required'}), 400
    if algo not in ALLOWED_ENCRYPTION_ALGOS:
        return jsonify({'ok': False, 'error': f'Unknown algorithm: {algo}'}), 400

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
    url = _normalize_public_scan_url(request.args.get('url', ''))
    if not url:
        return jsonify({'ok': False, 'error': 'public http(s) url parameter required'}), 400

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


@app.route('/api/map/live-satellite-availability', methods=['POST'])
def map_live_satellite_availability():
    payload = _json_payload()
    source_key = _bounded_text(payload.get('sourceKey'), 32, allow_empty=False)
    if source_key not in LIVE_SATELLITE_SOURCE_LOOKUP:
        return jsonify({'ok': False, 'error': 'invalid source'}), 400

    frames = _validated_live_satellite_frames(payload.get('frames'))
    tiles = _validated_live_satellite_tiles(payload.get('tiles'))
    if frames is None or tiles is None:
        return jsonify({'ok': False, 'error': 'invalid availability probe payload'}), 400

    available_frames = [
        frame_time for frame_time in frames
        if all(_live_satellite_tile_available(source_key, frame_time, tile) for tile in tiles)
    ]
    if not available_frames and frames:
        available_frames = frames[-1:]

    response = jsonify({'ok': True, 'frames': available_frames})
    response.headers['Cache-Control'] = 'no-store'
    return response


ARMY_SURVIVAL_MANUALS = [
    {
        'slug': 'fm3-05-70',
        'title': 'FM 3-05.70 Survival',
        'edition': 'Department of the Army · 2002',
        'category': 'survival',
        'focus': 'modern survival doctrine',
        'summary': 'Modern Army survival field manual covering will to survive, emergency priorities, water, shelter, fire, food procurement, health, navigation, and recovery basics.',
        'topics': ['survival doctrine', 'water', 'shelter', 'fire', 'recovery'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/fm3-05.70%2802%29.pdf',
    },
    {
        'slug': 'fm21-76',
        'title': 'FM 21-76 Survival',
        'edition': 'Department of the Army · 1992',
        'category': 'survival',
        'focus': 'classic survival manual',
        'summary': 'Classic U.S. Army survival manual with extensive chapters on immediate action, water, shelter, food, signaling, first aid, navigation, and environmental survival.',
        'topics': ['core survival', 'signaling', 'food', 'first aid', 'navigation'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM21-76%2892%29.pdf',
    },
    {
        'slug': 'fm3-50-3',
        'title': 'FM 3-50.3 Survival, Evasion, and Recovery',
        'edition': 'Department of the Army · 2007',
        'category': 'recovery',
        'focus': 'sere and recovery',
        'summary': 'Army survival, evasion, and recovery field guidance focused on isolation planning, evasion movement, signaling, capture avoidance, and reintegration.',
        'topics': ['SERE', 'evasion', 'recovery', 'signaling', 'planning'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/fm3-50.3%2807%29.pdf',
    },
    {
        'slug': 'fm21-26',
        'title': 'FM 21-26 Map Reading and Land Navigation',
        'edition': 'Department of the Army · 1993',
        'category': 'navigation',
        'focus': 'land navigation',
        'summary': 'Army map reading and land navigation manual for route planning, terrain association, compass work, resection, pace count, and night movement.',
        'topics': ['land navigation', 'maps', 'compass', 'terrain', 'route planning'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM21-26%2893%29.pdf',
    },
    {
        'slug': 'fm21-26-1956',
        'title': 'FM 21-26 Map Reading and Land Navigation (1956 Edition)',
        'edition': 'Department of the Army · 1956',
        'category': 'navigation',
        'focus': 'legacy land navigation reference',
        'summary': 'Legacy Army land-navigation manual covering contour interpretation, compass work, route cards, map symbols, and foundational map-reading discipline from an earlier doctrinal edition.',
        'topics': ['legacy map reading', 'map symbols', 'compass work', 'route cards', 'terrain association'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/Fm21-26%2856%29.pdf',
    },
    {
        'slug': 'fm31-70',
        'title': 'FM 31-70 Basic Cold Weather Manual',
        'edition': 'Department of the Army · 1959',
        'category': 'climate',
        'focus': 'cold weather operations',
        'summary': 'Army cold weather operations manual covering movement, clothing, shelters, medical hazards, weapons care, and survival in snow, ice, and subzero conditions.',
        'topics': ['cold weather', 'arctic', 'shelter', 'movement', 'medical hazards'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM31-70%281959%29.pdf',
    },
    {
        'slug': 'fm21-75',
        'title': 'FM 21-75 Combat Skills of the Soldier',
        'edition': 'Department of the Army · 1984',
        'category': 'fieldcraft',
        'focus': 'fieldcraft and soldier skills',
        'summary': 'Army fieldcraft manual with camouflage, first aid fundamentals, movement, fighting positions, NBC basics, and small-unit soldier skills useful in austere survival contexts.',
        'topics': ['fieldcraft', 'first aid', 'camouflage', 'movement', 'soldier skills'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/Fm21-75_15%2884%29.pdf',
    },
    {
        'slug': 'fm31-72',
        'title': 'FM 31-72 Military Mountaineering',
        'edition': 'Department of the Army · 1964',
        'category': 'mobility',
        'focus': 'mountaineering',
        'summary': 'Army military mountaineering manual covering climbing systems, rope work, mountain movement, evacuation, and unit training in steep terrain.',
        'topics': ['mountaineering', 'rope systems', 'mountain movement', 'evacuation', 'terrain'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM31-72%281964%29.pdf',
    },
    {
        'slug': 'fm90-6',
        'title': 'FM 90-6 Mountain Operations',
        'edition': 'Department of the Army · 1980',
        'category': 'mobility',
        'focus': 'mountain operations',
        'summary': 'Army mountain operations field guide covering movement, sustainment, communications, bivouac discipline, and tactical survival in high-angle terrain.',
        'topics': ['mountain operations', 'movement', 'bivouac', 'sustainment', 'terrain'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM90-6%281980%29.pdf',
    },
    {
        'slug': 'fm20-3',
        'title': 'FM 20-3 Camouflage, Concealment, and Decoys',
        'edition': 'Department of the Army · 1999',
        'category': 'fieldcraft',
        'focus': 'camouflage and concealment',
        'summary': 'Army camouflage and concealment manual focused on signature reduction, field discipline, visual deception, and staying hard to detect in static or moving positions.',
        'topics': ['camouflage', 'concealment', 'decoys', 'signature reduction', 'field discipline'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM20-3(99).pdf',
    },
    {
        'slug': 'fm90-10-1',
        'title': 'FM 90-10-1 An Infantryman\'s Guide to Urban Combat',
        'edition': 'Department of the Army · 1982',
        'category': 'fieldcraft',
        'focus': 'urban movement and combat fieldcraft',
        'summary': 'Army urban combat field manual covering movement through built-up areas, observation, breaching considerations, concealment, room-clearing discipline, and small-unit survival-adjacent fieldcraft in dense terrain.',
        'topics': ['urban movement', 'cover and concealment', 'observation', 'breaching', 'small-unit tactics'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM90-10-1%2882%29.pdf',
    },
    {
        'slug': 'fm3-4c2',
        'title': 'FM 3-4 NBC Protection',
        'edition': 'Department of the Army · 1996',
        'category': 'protection',
        'focus': 'NBC protection',
        'summary': 'Army NBC protection manual covering contamination avoidance, protective posture, decontamination, mask discipline, and operating under CBRN threat conditions.',
        'topics': ['NBC', 'CBRN', 'decontamination', 'protective posture', 'contamination avoidance'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM3-4C2%2896%29.pdf',
    },
    {
        'slug': 'fm90-3',
        'title': 'FM 90-3 Desert Operations',
        'edition': 'Department of the Army · 1977',
        'category': 'climate',
        'focus': 'desert survival and operations',
        'summary': 'Army desert operations manual covering heat management, water discipline, movement, equipment care, navigation, and survival in arid environments.',
        'topics': ['desert', 'heat', 'water discipline', 'movement', 'equipment care'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM90-3%2877%29.pdf',
    },
    {
        'slug': 'fm3-100-4',
        'title': 'FM 3-100.4 Environmental Considerations in Military Operations',
        'edition': 'Department of the Army · 2001',
        'category': 'climate',
        'focus': 'environmental planning and protection',
        'summary': 'Army environmental operations manual covering risk assessment, waste control, hazardous-material handling, water and soil protection, and integrating environmental constraints into mission planning.',
        'topics': ['environmental protection', 'risk assessment', 'waste management', 'hazardous materials', 'planning'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/fm3-100.4%2801%29.pdf',
    },
    {
        'slug': 'fm3-97-6',
        'title': 'FM 3-97.6 Mountain Operations',
        'edition': 'Department of the Army · 2000',
        'category': 'mobility',
        'focus': 'modern mountain operations',
        'summary': 'Modern Army mountain operations manual covering mission planning, technical movement, sustainment, casualty evacuation, and survival in vertical terrain.',
        'topics': ['mountain operations', 'technical movement', 'evacuation', 'planning', 'sustainment'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/fm3-97.6%2800%29.pdf',
    },
    {
        'slug': 'fm21-11',
        'title': 'FM 21-11 First Aid for Soldiers',
        'edition': 'Department of the Army · 1954',
        'category': 'medical',
        'focus': 'field first aid',
        'summary': 'Army first aid manual covering casualty assessment, bleeding control, fractures, burns, shock, carries, and immediate care before trained medical support arrives.',
        'topics': ['first aid', 'casualty care', 'bleeding control', 'fractures', 'shock'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM21-11%2854%29.pdf',
    },
    {
        'slug': 'atp4-44',
        'title': 'ATP 4-44 Water Support Operations',
        'edition': 'Department of the Army · 2015',
        'category': 'water',
        'focus': 'water support and sanitation',
        'summary': 'Army water support manual covering water source development, purification, storage, distribution, testing, and sustainment planning for field operations.',
        'topics': ['water support', 'purification', 'distribution', 'storage', 'sanitation'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/ATP4-44%2815%29.pdf',
    },
    {
        'slug': 'fm7-8',
        'title': 'FM 7-8 The Infantry Rifle Platoon and Squad',
        'edition': 'Department of the Army · 1992',
        'category': 'fieldcraft',
        'focus': 'small-unit fieldcraft',
        'summary': 'Army infantry platoon and squad manual covering patrolling, movement, security, bivouac discipline, battle drills, field reporting, and small-unit survival-adjacent fieldcraft.',
        'topics': ['small-unit tactics', 'patrolling', 'movement', 'security', 'fieldcraft'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM7-8%2892%29.pdf',
    },
    {
        'slug': 'fm7-85',
        'title': 'FM 7-85 Ranger Unit Operations',
        'edition': 'Department of the Army · 1987',
        'category': 'fieldcraft',
        'focus': 'ranger patrolling and fieldcraft',
        'summary': 'Army Ranger operations manual covering patrolling, raids, reconnaissance, movement planning, sustainment under austere conditions, and aggressive small-unit fieldcraft.',
        'topics': ['ranger operations', 'patrolling', 'raids', 'reconnaissance', 'sustainment'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/fm7-85%2887%29.pdf',
    },
    {
        'slug': 'fm7-0',
        'title': 'FM 7-0 Training the Force',
        'edition': 'Department of the Army · 2002',
        'category': 'fieldcraft',
        'focus': 'training readiness and standards',
        'summary': 'Army training doctrine focused on planning, executing, and assessing training to standard, building leader proficiency, and sustaining soldier readiness for field conditions and combat tasks.',
        'topics': ['training management', 'readiness', 'leader development', 'standards', 'battle drills'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM7-0%2802%29.pdf',
    },
    {
        'slug': 'fm4-02-17',
        'title': 'FM 4-02.17 Preventive Medicine Services',
        'edition': 'Department of the Army · 2000',
        'category': 'medical',
        'focus': 'preventive medicine and sanitation',
        'summary': 'Army preventive medicine field manual covering sanitation programs, disease prevention, field hygiene, environmental health, vectors, and sustaining healthy operations in austere environments.',
        'topics': ['preventive medicine', 'sanitation', 'field hygiene', 'disease prevention', 'environmental health'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM-4-02.17%2800%29.pdf',
    },
    {
        'slug': 'fm4-02-6',
        'title': 'FM 4-02.6 The Medical Company',
        'edition': 'Department of the Army · 2002',
        'category': 'medical',
        'focus': 'medical company support and sanitation',
        'summary': 'Army medical company manual covering treatment-team organization, evacuation support, sanitation responsibilities, preventive medicine links, and sustaining casualty care in field conditions.',
        'topics': ['medical company', 'casualty care', 'evacuation', 'sanitation', 'treatment teams'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM4-02.6%2802%29.pdf',
    },
    {
        'slug': 'fm90-8',
        'title': 'FM 90-8 Counterguerilla Operations',
        'edition': 'Department of the Army · 1986',
        'category': 'protection',
        'focus': 'counterguerilla patrol and security operations',
        'summary': 'Army counterguerilla manual covering tracking, area control, ambush and counterambush, base defense, population security, and operating against irregular forces in austere terrain.',
        'topics': ['counterguerilla', 'tracking', 'area security', 'patrolling', 'base defense'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/FM90-8%2886%29.pdf',
    },
    {
        'slug': 'atp4-41',
        'title': 'ATP 4-41 Army Field Feeding and Class I Operations',
        'edition': 'Department of the Army · 2015',
        'category': 'sustainment',
        'focus': 'field feeding and food sustainment',
        'summary': 'Army field feeding manual covering Class I planning, sanitation-linked feeding operations, ration management, kitchen discipline, and sustainment support in field conditions.',
        'topics': ['field feeding', 'Class I', 'ration planning', 'kitchen discipline', 'sustainment'],
        'source_url': 'https://www.bits.de/NRANEU/others/amd-us-archive/ATP4-41%2815%29.pdf',
    },
]

ARMY_SURVIVAL_MANUAL_CATEGORIES = [
    {'slug': 'survival', 'label': 'Core Survival'},
    {'slug': 'recovery', 'label': 'Recovery / SERE'},
    {'slug': 'medical', 'label': 'Medical'},
    {'slug': 'water', 'label': 'Water'},
    {'slug': 'sustainment', 'label': 'Sustainment'},
    {'slug': 'navigation', 'label': 'Navigation'},
    {'slug': 'climate', 'label': 'Climate'},
    {'slug': 'fieldcraft', 'label': 'Fieldcraft'},
    {'slug': 'protection', 'label': 'Protection'},
    {'slug': 'mobility', 'label': 'Mobility'},
]

ARMY_SURVIVAL_OFFLINE_BUNDLES = [
    {
        'slug': 'full-shelf',
        'title': 'Download Entire Shelf',
        'summary': 'Every verified public manual in the Army survival shelf packed into one offline archive for local storage.',
        'scope': 'all',
        'categories': [],
    },
    {
        'slug': 'medical-water',
        'title': 'Medical + Water Sustainment Pack',
        'summary': 'First aid, water, recovery, and survival doctrine for casualty response, purification, and sustainment basics.',
        'scope': '',
        'categories': ['medical', 'water', 'sustainment', 'survival', 'recovery'],
    },
    {
        'slug': 'navigation-climate',
        'title': 'Navigation + Climate Environment Pack',
        'summary': 'Route finding, mountain movement, cold-weather and desert survival, plus environmental protection guidance for sustained movement through hard terrain.',
        'scope': '',
        'categories': ['navigation', 'climate', 'mobility'],
    },
    {
        'slug': 'fieldcraft-protection',
        'title': 'Fieldcraft + Protection Readiness Pack',
        'summary': 'Camouflage, urban movement, Ranger and infantry fieldcraft, counterguerilla security, training doctrine, and protection references for readiness in unstable terrain.',
        'scope': '',
        'categories': ['fieldcraft', 'protection'],
    },
]

ARMY_SURVIVAL_LIBRARY_PDF_DIR = os.path.join(APP_ROOT, 'static', 'manuals', 'army-library')


def _get_army_survival_manual(manual_slug):
    normalized_slug = (manual_slug or '').strip().lower()
    for manual in ARMY_SURVIVAL_MANUALS:
        if manual['slug'] == normalized_slug:
            return manual
    return None


def _get_army_survival_manuals_for_categories(category_slugs):
    normalized_categories = {
        (category_slug or '').strip().lower()
        for category_slug in category_slugs
        if (category_slug or '').strip()
    }
    if not normalized_categories:
        return []
    return [manual for manual in ARMY_SURVIVAL_MANUALS if manual['category'] in normalized_categories]


def _army_survival_manual_filename(manual):
    filename_root = secure_filename(manual.get('title') or manual.get('slug') or 'army-survival-manual')
    if not filename_root:
        filename_root = 'army-survival-manual'
    return f'{filename_root}.pdf'


def _army_survival_manual_local_path(manual):
    return os.path.join(ARMY_SURVIVAL_LIBRARY_PDF_DIR, f"{manual['slug']}.pdf")


def _army_survival_manual_local_exists(manual):
    return os.path.isfile(_army_survival_manual_local_path(manual))


def _ordered_unique_normalized(values):
    ordered = []
    seen = set()
    for raw_value in values:
        normalized = (raw_value or '').strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _army_survival_requested_scope():
    return (request.args.get('scope') or '').strip().lower()


def _army_survival_requested_categories():
    return _ordered_unique_normalized([
        *request.args.getlist('category'),
        *((request.args.get('categories') or '').split(',')),
    ])


def _army_survival_requested_manual_slugs():
    ordered = []
    seen = set()
    for raw_value in [*request.args.getlist('manual'), *((request.args.get('manuals') or '').split(','))]:
        manual = _get_army_survival_manual(raw_value)
        if manual is None or manual['slug'] in seen:
            continue
        seen.add(manual['slug'])
        ordered.append(manual['slug'])
    return ordered


def _selected_army_survival_manuals_from_request(default_to_all=False):
    requested_scope = _army_survival_requested_scope()
    requested_categories = set(_army_survival_requested_categories())
    requested_manuals = set(_army_survival_requested_manual_slugs())

    if requested_scope != 'all' and not requested_categories and not requested_manuals:
        return list(ARMY_SURVIVAL_MANUALS) if default_to_all else []

    selected = []
    for manual in ARMY_SURVIVAL_MANUALS:
        if requested_scope == 'all' or manual['category'] in requested_categories or manual['slug'] in requested_manuals:
            selected.append(manual)
    return selected


def _army_survival_selection_url(endpoint, scope='', categories=None, manual_slugs=None):
    params = []
    if scope:
        params.append(('scope', scope))
    for category_slug in categories or []:
        params.append(('category', category_slug))
    for manual_slug in manual_slugs or []:
        params.append(('manual', manual_slug))

    base_url = url_for(endpoint)
    if not params:
        return base_url
    return f'{base_url}?{urlencode(params, doseq=True)}'


def _army_survival_download_url(scope='', categories=None, manual_slugs=None):
    return _army_survival_selection_url(
        'survival_army_library_download',
        scope=scope,
        categories=categories,
        manual_slugs=manual_slugs,
    )


def _army_survival_binder_url(scope='', categories=None, manual_slugs=None):
    return _army_survival_selection_url(
        'survival_army_library_binder',
        scope=scope,
        categories=categories,
        manual_slugs=manual_slugs,
    )


def _group_army_survival_manuals_by_category(manuals):
    grouped = []
    for category in ARMY_SURVIVAL_MANUAL_CATEGORIES:
        category_manuals = [manual for manual in manuals if manual['category'] == category['slug']]
        if category_manuals:
            grouped.append({
                **category,
                'manuals': category_manuals,
                'count': len(category_manuals),
            })
    return grouped


@app.route('/survival')
def survival_page():
    return render_template('survival.html')


@app.route('/survival/meal-bible')
def survival_meal_bible_page():
    return render_template('survival_meal_bible_page.html')


@app.route('/survival/army-library')
def survival_army_library_page():
    requested_slug = request.args.get('manual', '')
    requested_topic = (request.args.get('topic') or '').strip()
    active_manual = _get_army_survival_manual(requested_slug) or ARMY_SURVIVAL_MANUALS[0]
    return render_template(
        'survival_army_library.html',
        manuals=ARMY_SURVIVAL_MANUALS,
        manual_categories=ARMY_SURVIVAL_MANUAL_CATEGORIES,
        active_manual=active_manual,
        active_topic=requested_topic,
        active_manual_binder_url=_army_survival_binder_url(manual_slugs=[active_manual['slug']]),
        full_shelf_binder_url=_army_survival_binder_url(scope='all'),
    )


@app.route('/survival/army-library/offline')
def survival_army_library_offline_page():
    category_lookup = {entry['slug']: entry['label'] for entry in ARMY_SURVIVAL_MANUAL_CATEGORIES}
    bundles = []

    for bundle in ARMY_SURVIVAL_OFFLINE_BUNDLES:
        bundle_manuals = (
            list(ARMY_SURVIVAL_MANUALS)
            if bundle.get('scope') == 'all'
            else _get_army_survival_manuals_for_categories(bundle.get('categories', []))
        )
        bundles.append({
            **bundle,
            'manuals': bundle_manuals,
            'manual_count': len(bundle_manuals),
            'category_labels': [category_lookup[slug] for slug in bundle.get('categories', []) if slug in category_lookup],
            'download_url': _army_survival_download_url(
                scope=bundle.get('scope', ''),
                categories=bundle.get('categories', []),
            ),
            'binder_url': _army_survival_binder_url(
                scope=bundle.get('scope', ''),
                categories=bundle.get('categories', []),
            ),
        })

    return render_template(
        'survival_army_library_offline.html',
        manuals=ARMY_SURVIVAL_MANUALS,
        manual_categories=ARMY_SURVIVAL_MANUAL_CATEGORIES,
        bundles=bundles,
        total_manuals=len(ARMY_SURVIVAL_MANUALS),
        total_categories=len(ARMY_SURVIVAL_MANUAL_CATEGORIES),
        full_shelf_download_url=_army_survival_download_url(scope='all'),
        full_shelf_binder_url=_army_survival_binder_url(scope='all'),
    )


@app.route('/survival/army-library/binder')
def survival_army_library_binder():
    requested_scope = _army_survival_requested_scope()
    requested_categories = _army_survival_requested_categories()
    requested_manual_slugs = _army_survival_requested_manual_slugs()
    selected_manuals = _selected_army_survival_manuals_from_request(default_to_all=True)
    category_lookup = {entry['slug']: entry['label'] for entry in ARMY_SURVIVAL_MANUAL_CATEGORIES}
    category_labels = [category_lookup[slug] for slug in requested_categories if slug in category_lookup]
    full_shelf_requested = requested_scope == 'all' or (not requested_categories and not requested_manual_slugs)

    if full_shelf_requested:
        binder_title = 'Full Shelf Field Binder'
        binder_summary = 'Printable reference index for the full public Army survival shelf, grouped by category and laid out for quick reader jumps, direct downloads, and hard-copy briefing use.'
        selection_download_url = _army_survival_download_url(scope='all')
    elif requested_categories and not requested_manual_slugs:
        binder_title = f"{' / '.join(category_labels)} Field Binder"
        binder_summary = 'Printable reference index for the selected Army manual categories, grouped into a cleaner field binder format with quick reader shortcuts.'
        selection_download_url = _army_survival_download_url(categories=requested_categories)
    elif requested_manual_slugs and not requested_categories:
        binder_title = 'Selected Army Manual Binder'
        binder_summary = 'Printable reference index for the selected Army manuals, staged as a compact binder with summaries, topic shortcuts, and direct download links.'
        selection_download_url = _army_survival_download_url(manual_slugs=requested_manual_slugs)
    else:
        binder_title = 'Mixed Selection Field Binder'
        binder_summary = 'Printable reference index for a mixed Army manual selection built from both category filters and explicit manual picks.'
        selection_download_url = _army_survival_download_url(
            categories=requested_categories,
            manual_slugs=requested_manual_slugs,
        )

    return render_template(
        'survival_army_library_binder.html',
        manuals=selected_manuals,
        manual_groups=_group_army_survival_manuals_by_category(selected_manuals),
        total_manuals=len(selected_manuals),
        total_categories=len({manual['category'] for manual in selected_manuals}),
        category_labels=category_labels,
        binder_title=binder_title,
        binder_summary=binder_summary,
        selection_download_url=selection_download_url,
        full_shelf_download_url=_army_survival_download_url(scope='all'),
        full_shelf_binder_url=_army_survival_binder_url(scope='all'),
        primary_reader_url=url_for('survival_army_library_page', manual=selected_manuals[0]['slug']) if selected_manuals else url_for('survival_army_library_page'),
    )


@app.route('/survival/army-library/<manual_slug>/pdf')
def survival_army_library_pdf(manual_slug):
    manual = _get_army_survival_manual(manual_slug)
    if manual is None:
        return jsonify({'ok': False, 'error': 'manual not found'}), 404

    attachment_requested = (request.args.get('download') or '').strip().lower() in {'1', 'true', 'yes'}
    local_pdf_path = _army_survival_manual_local_path(manual)
    if os.path.isfile(local_pdf_path):
        response = send_file(
            local_pdf_path,
            mimetype='application/pdf',
            as_attachment=attachment_requested,
            download_name=_army_survival_manual_filename(manual),
            conditional=True,
            max_age=21600,
        )
        response.headers['X-Army-PDF-Source'] = 'local'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    try:
        upstream = http_requests.get(
            manual['source_url'],
            timeout=(10, 30),
            stream=True,
            headers={'User-Agent': 'FutureCodeDelta-ArmyLibrary/1.0'},
        )
        upstream.raise_for_status()
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'upstream fetch failed: {exc}'}), 502

    def generate_pdf_stream():
        try:
            for chunk in upstream.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    disposition = 'attachment' if attachment_requested else 'inline'
    response = Response(generate_pdf_stream(), mimetype='application/pdf')
    response.headers['Content-Disposition'] = f'{disposition}; filename="{_army_survival_manual_filename(manual)}"'
    response.headers['Cache-Control'] = 'public, max-age=21600'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Army-PDF-Source'] = 'remote'

    content_length = upstream.headers.get('Content-Length')
    if content_length:
        response.headers['Content-Length'] = content_length

    return response


@app.route('/survival/army-library/download')
def survival_army_library_download():
    selected_manuals = _selected_army_survival_manuals_from_request()
    if not selected_manuals:
        return jsonify({'ok': False, 'error': 'select at least one manual'}), 400

    archive_file = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode='w+b')
    downloaded = []
    failed = []

    with zipfile.ZipFile(archive_file, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        for manual in selected_manuals:
            local_pdf_path = _army_survival_manual_local_path(manual)
            if os.path.isfile(local_pdf_path):
                archive.write(local_pdf_path, _army_survival_manual_filename(manual))
                downloaded.append({'manual': manual, 'source': 'local'})
                continue

            upstream = None
            try:
                upstream = http_requests.get(
                    manual['source_url'],
                    timeout=(10, 30),
                    stream=True,
                    headers={'User-Agent': 'FutureCodeDelta-ArmyLibrary/1.0'},
                )
                upstream.raise_for_status()

                with archive.open(_army_survival_manual_filename(manual), mode='w') as zipped_entry:
                    for chunk in upstream.iter_content(chunk_size=65536):
                        if chunk:
                            zipped_entry.write(chunk)
                downloaded.append({'manual': manual, 'source': 'remote'})
            except Exception as exc:
                failed.append({'title': manual['title'], 'error': str(exc)})
            finally:
                if upstream is not None:
                    upstream.close()

        readme_lines = [
            'DELTA ARMY SURVIVAL LIBRARY BUNDLE',
            '',
            'Included manuals:',
        ]
        readme_lines.extend(
            f"- {entry['manual']['title']} ({entry['manual']['edition']}) [{'hosted on FutureCodeDelta' if entry['source'] == 'local' else 'fetched from public mirror'}]"
            for entry in downloaded
        )

        if failed:
            readme_lines.extend(['', 'Failed downloads:'])
            readme_lines.extend(f'- {entry["title"]}: {entry["error"]}' for entry in failed)

        archive.writestr('README.txt', '\n'.join(readme_lines) + '\n')

    if not downloaded:
        archive_file.close()
        return jsonify({'ok': False, 'error': 'all selected manuals failed to download'}), 502

    archive_file.seek(0)
    response = send_file(
        archive_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name='delta_army_survival_library_bundle.zip',
    )
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route('/advertise')
@app.route('/ads')
def advertise_page():
    return render_template('advertise.html')


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
    return render_template(
        'bible.html',
        admin_token_set=bool(admin_token),
        journal_moderation_enabled=_is_journal_moderator(),
    )


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


@app.route('/culture')
def culture_build_page():
    return render_template('culture_build.html')


@app.route('/schooling')
def schooling_page():
    return render_template('schooling.html')


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

    brand = input_validator.validate_string(request.form.get('brand'), max_length=80)
    model = input_validator.validate_string(request.form.get('model'), max_length=80) or ''
    year = input_validator.validate_string(request.form.get('year'), max_length=16) or ''
    default_title = ' '.join(part for part in [brand or '', model, year] if part).strip()
    title = input_validator.validate_string(request.form.get('title'), max_length=200) or default_title
    description = input_validator.validate_string(request.form.get('description'), max_length=4000) or ''
    license_name = input_validator.validate_string(request.form.get('license'), max_length=200) or ''
    source_url_value = (request.form.get('source_url') or '').strip()
    source_url = input_validator.validate_url(source_url_value) if source_url_value else ''

    if not brand:
        return jsonify({'ok': False, 'error': 'brand required'}), 400
    if source_url_value and not source_url:
        return jsonify({'ok': False, 'error': 'valid source_url required'}), 400

    pdf = request.files.get('pdf')
    images = request.files.getlist('images')
    if len(images) > 12:
        return jsonify({'ok': False, 'error': 'too many images'}), 400

    mid = uuid.uuid4().hex
    upload_dir = os.path.join(APP_ROOT, 'static', 'manuals', 'uploads', mid)
    image_dir = os.path.join(APP_ROOT, 'static', 'manuals', 'images', mid)
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(image_dir, exist_ok=True)

    pdf_rel = None
    if pdf and pdf.filename:
        fname = secure_filename(pdf.filename)
        if not _has_allowed_extension(fname, {'.pdf'}):
            return jsonify({'ok': False, 'error': 'pdf must be a .pdf file'}), 400
        save_path = os.path.join(upload_dir, fname)
        pdf.save(save_path)
        pdf_rel = f'/static/manuals/uploads/{mid}/{fname}'

    image_rels = []
    for img in images:
        if img and img.filename:
            iname = secure_filename(img.filename)
            if not _has_allowed_extension(iname, ALLOWED_IMAGE_EXTENSIONS):
                return jsonify({'ok': False, 'error': 'invalid image extension'}), 400
            save_path = os.path.join(image_dir, iname)
            img.save(save_path)
            image_rels.append(f'/static/manuals/images/{mid}/{iname}')

    new_id = manual_store.add_manual(brand=brand, model=model, year=year, title=title,
                                     description=description, license=license_name,
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
    payload = _json_payload()
    provided = request.headers.get('X-Admin-Token') or payload.get('admin_token')
    if admin_token and provided != admin_token:
        return jsonify({'ok': False, 'error': 'admin token required'}), 403

    brand = input_validator.validate_string(payload.get('brand'), max_length=80) or ''
    model = input_validator.validate_string(payload.get('model'), max_length=80) or ''
    year = input_validator.validate_string(payload.get('year'), max_length=16) or ''
    query = input_validator.validate_string(payload.get('query') or f"{brand} {model}", max_length=240)
    limit = input_validator.validate_integer(payload.get('limit', 6), min_val=1, max_val=20)
    download = _coerce_bool(payload.get('download', False))

    if not query or limit is None:
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
