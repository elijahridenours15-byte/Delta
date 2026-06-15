from __future__ import annotations

from typing import Iterable

from flask import Blueprint, Response, jsonify, request

from drone_bridge import DroneBridgeManager


def _normalize_origins(cors_origins: str | Iterable[str] | None) -> list[str]:
    if cors_origins is None:
        return []
    if isinstance(cors_origins, str):
        values = cors_origins.split(',')
    else:
        values = list(cors_origins)
    return [str(value).strip() for value in values if str(value).strip()]


def create_drone_bridge_blueprint(
    bridge_manager: DroneBridgeManager,
    *,
    api_token: str = '',
    cors_origins: str | Iterable[str] | None = None,
    service_label: str = 'site-bridge',
) -> Blueprint:
    allowed_origins = _normalize_origins(cors_origins)
    allow_any_origin = '*' in allowed_origins
    safe_name = ''.join(char if char.isalnum() else '_' for char in service_label) or 'bridge'
    blueprint = Blueprint(f'drone_bridge_{safe_name}', __name__)

    def add_cors_headers(response: Response) -> Response:
        if not allowed_origins:
            return response
        origin = (request.headers.get('Origin') or '').strip()
        if allow_any_origin:
            response.headers['Access-Control-Allow-Origin'] = '*'
        elif origin and origin in allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers.add('Vary', 'Origin')
        else:
            return response
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Bridge-Token'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Max-Age'] = '600'
        return response

    @blueprint.before_request
    def handle_preflight_and_auth():
        if request.method == 'OPTIONS':
            return add_cors_headers(Response(status=204))
        if request.endpoint == f'{blueprint.name}.bridge_healthz':
            return None
        if not api_token:
            return None
        provided_token = (request.headers.get('X-Bridge-Token') or request.args.get('token') or '').strip()
        if provided_token == api_token:
            return None
        response = jsonify({'ok': False, 'error': 'bridge token required'})
        response.status_code = 403
        return add_cors_headers(response)

    @blueprint.after_request
    def apply_cors(response: Response) -> Response:
        return add_cors_headers(response)

    @blueprint.route('/healthz')
    def bridge_healthz():
        return jsonify({
            'ok': True,
            'service': service_label,
            'capabilities': bridge_manager.capabilities(),
        })

    @blueprint.route('/capabilities')
    def capabilities():
        return jsonify({
            'ok': True,
            'capabilities': bridge_manager.capabilities(),
            'service': {
                'label': service_label,
                'token_required': bool(api_token),
                'cors_origins': allowed_origins or ['same-origin'],
            },
        })

    @blueprint.route('/sessions')
    def sessions():
        return jsonify({'ok': True, 'sessions': bridge_manager.sessions_snapshot()})

    @blueprint.route('/tello/connect', methods=['POST'])
    def tello_connect():
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or 'Tello Bridge').strip() or 'Tello Bridge'
        home = data.get('home') or {}
        try:
            home_lat = float(home.get('lat') or 33.7490)
            home_lng = float(home.get('lng') or -84.3880)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'invalid home coordinates'}), 400
        result = bridge_manager.connect_tello(name=name, home_lat=home_lat, home_lng=home_lng)
        return jsonify(result), (200 if result.get('ok') else 503)

    @blueprint.route('/rtsp/open', methods=['POST'])
    def rtsp_open():
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or 'RTSP Relay').strip() or 'RTSP Relay'
        url = (data.get('url') or '').strip()
        if not url:
            return jsonify({'ok': False, 'error': 'RTSP URL required'}), 400
        result = bridge_manager.open_rtsp(name=name, url=url)
        return jsonify(result), (200 if result.get('ok') else 503)

    @blueprint.route('/sessions/<session_id>/disconnect', methods=['POST'])
    def disconnect(session_id: str):
        if not bridge_manager.close_session(session_id):
            return jsonify({'ok': False, 'error': 'bridge session not found'}), 404
        return jsonify({'ok': True})

    @blueprint.route('/feed/<session_id>.jpg')
    def feed_jpg(session_id: str):
        frame = bridge_manager.latest_frame(session_id)
        if frame is None:
            return jsonify({'ok': False, 'error': 'bridge session not found'}), 404
        return Response(frame, mimetype='image/jpeg', headers={'Cache-Control': 'no-store, max-age=0'})

    @blueprint.route('/feed/<session_id>.mjpg')
    def feed_mjpg(session_id: str):
        if bridge_manager.get_session(session_id) is None:
            return jsonify({'ok': False, 'error': 'bridge session not found'}), 404
        return Response(
            bridge_manager.mjpeg_chunks(session_id),
            mimetype='multipart/x-mixed-replace; boundary=frame',
            headers={'Cache-Control': 'no-store, max-age=0'},
        )

    return blueprint