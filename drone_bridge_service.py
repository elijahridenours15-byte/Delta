from __future__ import annotations

import os

from flask import Flask, jsonify

from drone_bridge import DroneBridgeManager
from drone_bridge_http import create_drone_bridge_blueprint

DEFAULT_BRIDGE_CORS_ORIGINS = ','.join((
    'https://futurecodedelta.org',
    'http://127.0.0.1:5000',
    'http://127.0.0.1:5060',
    'http://127.0.0.1:5080',
))


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        BRIDGE_API_TOKEN=os.environ.get('BRIDGE_API_TOKEN', '').strip(),
        BRIDGE_CORS_ORIGINS=os.environ.get('BRIDGE_CORS_ORIGINS', DEFAULT_BRIDGE_CORS_ORIGINS),
        BRIDGE_SERVICE_LABEL=os.environ.get('BRIDGE_SERVICE_LABEL', 'remote-bridge'),
    )
    if config:
        app.config.update(config)

    bridge_manager = DroneBridgeManager()
    app.register_blueprint(
        create_drone_bridge_blueprint(
            bridge_manager,
            api_token=app.config['BRIDGE_API_TOKEN'],
            cors_origins=app.config['BRIDGE_CORS_ORIGINS'],
            service_label=app.config['BRIDGE_SERVICE_LABEL'],
        ),
        url_prefix='/api/drone/bridge',
    )

    @app.route('/healthz')
    def healthz():
        return jsonify({
            'ok': True,
            'service': app.config['BRIDGE_SERVICE_LABEL'],
            'bridge_prefix': '/api/drone/bridge',
        })

    return app


app = create_app()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5081'))
    app.run(host='0.0.0.0', port=port)