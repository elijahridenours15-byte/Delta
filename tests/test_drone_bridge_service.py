from drone_bridge_service import create_app


def test_bridge_service_health_and_capabilities():
    app = create_app({
        'TESTING': True,
        'BRIDGE_API_TOKEN': '',
        'BRIDGE_CORS_ORIGINS': 'https://futurecodedelta.org',
        'BRIDGE_SERVICE_LABEL': 'test-bridge',
    })
    client = app.test_client()

    health_response = client.get('/healthz')
    capabilities_response = client.get('/api/drone/bridge/capabilities')

    assert health_response.status_code == 200
    assert health_response.get_json()['service'] == 'test-bridge'

    capabilities = capabilities_response.get_json()
    assert capabilities_response.status_code == 200
    assert capabilities['ok'] is True
    assert capabilities['service']['label'] == 'test-bridge'
    assert 'tello' in capabilities['capabilities']
    assert 'rtsp' in capabilities['capabilities']


def test_bridge_service_enforces_optional_token_and_cors():
    app = create_app({
        'TESTING': True,
        'BRIDGE_API_TOKEN': 'field-token',
        'BRIDGE_CORS_ORIGINS': 'https://futurecodedelta.org',
        'BRIDGE_SERVICE_LABEL': 'secure-bridge',
    })
    client = app.test_client()

    forbidden = client.get('/api/drone/bridge/sessions', headers={'Origin': 'https://futurecodedelta.org'})
    allowed = client.get(
        '/api/drone/bridge/sessions?token=field-token',
        headers={'Origin': 'https://futurecodedelta.org'},
    )
    preflight = client.open(
        '/api/drone/bridge/sessions',
        method='OPTIONS',
        headers={'Origin': 'https://futurecodedelta.org'},
    )

    assert forbidden.status_code == 403
    assert forbidden.headers['Access-Control-Allow-Origin'] == 'https://futurecodedelta.org'

    assert allowed.status_code == 200
    assert allowed.get_json()['ok'] is True
    assert allowed.headers['Access-Control-Allow-Origin'] == 'https://futurecodedelta.org'

    assert preflight.status_code == 204
    assert preflight.headers['Access-Control-Allow-Origin'] == 'https://futurecodedelta.org'
    assert 'X-Bridge-Token' in preflight.headers['Access-Control-Allow-Headers']