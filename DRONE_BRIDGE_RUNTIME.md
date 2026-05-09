# Drone Bridge Runtime

This runtime is the part that should live on a machine with LAN access to your drones.

Use it when the public website stays on shared hosting but the actual Tello or RTSP bridge needs `djitellopy` and `opencv-python-headless` on a different box.

## What it serves

- `GET /healthz`
- `GET /api/drone/bridge/healthz`
- `GET /api/drone/bridge/capabilities`
- `GET /api/drone/bridge/sessions`
- `POST /api/drone/bridge/tello/connect`
- `POST /api/drone/bridge/rtsp/open`
- `POST /api/drone/bridge/sessions/<id>/disconnect`
- `GET /api/drone/bridge/feed/<id>.jpg`
- `GET /api/drone/bridge/feed/<id>.mjpg`

The public drone dashboard can point its `Bridge API Endpoint` field at this runtime.

## Local run

From the repo root:

```bash
./venv/bin/python drone_bridge_service.py
```

Default port: `5081`

## Environment

- `PORT`
- `BRIDGE_API_TOKEN`
- `BRIDGE_CORS_ORIGINS`
- `BRIDGE_SERVICE_LABEL`

Example:

```bash
export PORT=5081
export BRIDGE_API_TOKEN=replace-me
export BRIDGE_CORS_ORIGINS="https://futurecodedelta.org,http://127.0.0.1:5080"
export BRIDGE_SERVICE_LABEL="field-bridge"
./venv/bin/python drone_bridge_service.py
```

If `BRIDGE_API_TOKEN` is set, enter the same value into the drone page `Bridge Token` field. The dashboard will send the token in API requests and append it to image-feed URLs.

## Docker

Build and run the bridge runtime separately from the main site:

```bash
docker build -f Dockerfile.drone-bridge -t delta-drone-bridge .
docker run --rm -p 5081:8080 \
  -e BRIDGE_API_TOKEN=replace-me \
  -e BRIDGE_CORS_ORIGINS="https://futurecodedelta.org" \
  -e BRIDGE_SERVICE_LABEL="field-bridge" \
  delta-drone-bridge
```

## Operator flow

1. Run this bridge runtime on the same network as the drones or RTSP sources.
2. Open the live drone dashboard.
3. Set `Source Type` to `Bridge: Tello / RTSP`.
4. Set `Bridge API Endpoint` to the runtime host, for example `https://bridge.example.com`.
5. Set `Bridge Token` if the runtime requires one.
6. Save that setup as a bridge preset if you will reuse it.

## Important constraint

The shared IONOS webspace can safely host the UI and the fallback bridge routes, but real hardware bridging should run on a host you control with package support and network reachability to the drones.

If you use the live HTTPS site, expose the separate bridge runtime over HTTPS too. Raw `http://` LAN endpoints will be blocked by the browser as mixed content.