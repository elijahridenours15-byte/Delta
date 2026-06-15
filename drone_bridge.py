from __future__ import annotations

import base64
import io
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageDraw = None

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    cv2 = None

try:
    from djitellopy import Tello  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Tello = None


def _now() -> float:
    return time.time()


FALLBACK_JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAIAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDyKiiipEf/2Q=='
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _meters_to_latlng(home_lat: float, home_lng: float, east_m: float, north_m: float) -> tuple[float, float]:
    lat = home_lat + (north_m / 111132.0)
    cos_lat = math.cos(math.radians(home_lat)) or 1e-6
    lng = home_lng + (east_m / (111320.0 * cos_lat))
    return round(lat, 6), round(lng, 6)


def _placeholder_frame(title: str, lines: list[str], accent: tuple[int, int, int] = (139, 157, 106)) -> bytes:
    if Image is None or ImageDraw is None:
        return FALLBACK_JPEG
    image = Image.new('RGB', (1280, 720), (10, 13, 8))
    draw = ImageDraw.Draw(image)
    draw.rectangle((28, 28, 1252, 692), outline=accent, width=3)
    draw.text((56, 70), title[:48], fill=(240, 245, 228))
    y = 148
    for line in lines[:7]:
        draw.text((56, y), line[:92], fill=(190, 204, 169))
        y += 64
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=82)
    return output.getvalue()


@dataclass
class BaseBridgeSession:
    name: str
    bridge_kind: str
    home_lat: float = 33.7490
    home_lng: float = -84.3880
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = 'starting'
    error: str = ''
    message: str = ''
    started_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    last_frame_at: float = 0.0
    latest_jpeg: Optional[bytes] = None

    def snapshot(self) -> dict[str, Any]:
        return {
            'id': self.session_id,
            'name': self.name,
            'bridge_kind': self.bridge_kind,
            'status': self.status,
            'error': self.error,
            'message': self.message,
            'updated_at': self.updated_at,
            'last_frame_at': self.last_frame_at,
            'feed_url': f'/api/drone/bridge/feed/{self.session_id}.mjpg',
            'preview_url': f'/api/drone/bridge/feed/{self.session_id}.jpg',
            'telemetry': self.telemetry_snapshot(),
            'has_video': bool(self.latest_jpeg),
        }

    def telemetry_snapshot(self) -> dict[str, Any]:
        return {
            'lat': self.home_lat,
            'lng': self.home_lng,
            'alt': 0.0,
            'speed': 0.0,
            'distance': 0,
            'battery': 0,
            'signal': 0,
            'sats': 0,
        }

    def latest_or_placeholder(self) -> bytes:
        if self.latest_jpeg:
            return self.latest_jpeg
        lines = [
            f'Status: {self.status.upper()}',
            self.message or 'Waiting for video frames.',
        ]
        if self.error:
            lines.append(self.error)
        return _placeholder_frame(self.name, lines)

    def close(self) -> None:
        raise NotImplementedError


class RtspRelaySession(BaseBridgeSession):
    def __init__(self, name: str, url: str):
        super().__init__(name=name, bridge_kind='rtsp')
        self.url = url
        self._capture = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> tuple[bool, str]:
        if cv2 is None:
            self.status = 'error'
            self.error = 'opencv-python-headless is not installed'
            self.message = 'Install opencv-python-headless to enable RTSP relay sessions.'
            return False, self.error
        capture = cv2.VideoCapture(self.url)
        if not capture or not capture.isOpened():
            self.status = 'error'
            self.error = 'Unable to open RTSP relay source'
            self.message = 'Check the RTSP URL and confirm the source is reachable from this host.'
            try:
                capture.release()
            except Exception:
                pass
            return False, self.error
        self._capture = capture
        self.status = 'connected'
        self.message = 'RTSP relay active'
        self.updated_at = _now()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        return True, ''

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            if self._capture is None:
                time.sleep(0.15)
                continue
            ok, frame = self._capture.read()
            if not ok or frame is None:
                self.status = 'degraded'
                self.message = 'Waiting for RTSP frames'
                self.updated_at = _now()
                time.sleep(0.2)
                continue
            ok, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if ok:
                with self._lock:
                    self.latest_jpeg = encoded.tobytes()
                    self.last_frame_at = _now()
                    self.updated_at = self.last_frame_at
                    self.status = 'connected'
                    self.message = 'RTSP frames flowing'
            time.sleep(0.05)

    def telemetry_snapshot(self) -> dict[str, Any]:
        return {
            'lat': self.home_lat,
            'lng': self.home_lng,
            'alt': 0.0,
            'speed': 0.0,
            'distance': 0,
            'battery': 0,
            'signal': 100 if self.status == 'connected' else 0,
            'sats': 0,
        }

    def close(self) -> None:
        self._stop_event.set()
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None
        self.status = 'closed'
        self.updated_at = _now()


class TelloBridgeSession(BaseBridgeSession):
    def __init__(self, name: str, home_lat: float, home_lng: float):
        super().__init__(name=name, bridge_kind='tello', home_lat=home_lat, home_lng=home_lng)
        self._tello = None
        self._frame_reader = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._telemetry = {
            'lat': home_lat,
            'lng': home_lng,
            'alt': 0.0,
            'speed': 0.0,
            'distance': 0,
            'battery': 0,
            'signal': 0,
            'sats': 0,
        }

    def start(self) -> tuple[bool, str]:
        if Tello is None:
            self.status = 'error'
            self.error = 'djitellopy is not installed'
            self.message = 'Install djitellopy to enable the Tello bridge.'
            return False, self.error
        try:
            tello = Tello()
            tello.connect()
            try:
                tello.streamon()
            except Exception:
                pass
            self._tello = tello
            try:
                self._frame_reader = tello.get_frame_read()
            except Exception:
                self._frame_reader = None
            self.status = 'connected'
            self.message = 'Tello link established'
            self.updated_at = _now()
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()
            return True, ''
        except Exception as exc:
            self.status = 'error'
            self.error = str(exc)
            self.message = 'Unable to connect to the Tello bridge target.'
            return False, self.error

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            if self._tello is None:
                time.sleep(0.15)
                continue
            try:
                state = self._tello.get_current_state() or {}
            except Exception as exc:
                self.status = 'degraded'
                self.message = 'Waiting for Tello telemetry'
                self.error = str(exc)
                self.updated_at = _now()
                time.sleep(0.25)
                continue

            battery = max(0, min(100, int(_safe_float(state.get('bat'), 0))))
            height_cm = max(_safe_float(state.get('h'), _safe_float(state.get('tof'), 0)), 0.0)
            east_cm = _safe_float(state.get('x'), 0.0)
            north_cm = _safe_float(state.get('y'), 0.0)
            lat, lng = _meters_to_latlng(self.home_lat, self.home_lng, east_cm / 100.0, north_cm / 100.0)
            speed = math.sqrt(
                _safe_float(state.get('vgx'), 0.0) ** 2 +
                _safe_float(state.get('vgy'), 0.0) ** 2 +
                _safe_float(state.get('vgz'), 0.0) ** 2
            ) / 100.0
            telemetry = {
                'lat': lat,
                'lng': lng,
                'alt': round(height_cm / 100.0, 1),
                'speed': round(speed, 1),
                'distance': int(math.sqrt(east_cm ** 2 + north_cm ** 2) / 100.0),
                'battery': battery,
                'signal': 100 if battery else 0,
                'sats': 10,
            }
            with self._lock:
                self._telemetry = telemetry
                self.updated_at = _now()
                self.error = ''
                self.status = 'connected'
                self.message = 'Tello telemetry active'

            frame = getattr(self._frame_reader, 'frame', None)
            if frame is not None and cv2 is not None:
                ok, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if ok:
                    with self._lock:
                        self.latest_jpeg = encoded.tobytes()
                        self.last_frame_at = _now()
                        self.updated_at = self.last_frame_at
            time.sleep(0.12)

    def telemetry_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._telemetry)

    def close(self) -> None:
        self._stop_event.set()
        if self._tello is not None:
            try:
                self._tello.streamoff()
            except Exception:
                pass
            try:
                self._tello.end()
            except Exception:
                pass
        self._tello = None
        self.status = 'closed'
        self.updated_at = _now()


class DroneBridgeManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, BaseBridgeSession] = {}

    def capabilities(self) -> dict[str, Any]:
        return {
            'tello': {
                'available': Tello is not None,
                'video_available': cv2 is not None,
            },
            'rtsp': {
                'available': cv2 is not None,
            },
        }

    def sessions_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [session.snapshot() for session in sorted(sessions, key=lambda item: item.started_at)]

    def connect_tello(self, name: str, home_lat: float, home_lng: float) -> dict[str, Any]:
        session = TelloBridgeSession(name=name, home_lat=home_lat, home_lng=home_lng)
        ok, error = session.start()
        if not ok:
            return {'ok': False, 'error': error, 'session': session.snapshot()}
        with self._lock:
            self._sessions[session.session_id] = session
        return {'ok': True, 'session': session.snapshot()}

    def open_rtsp(self, name: str, url: str) -> dict[str, Any]:
        session = RtspRelaySession(name=name, url=url)
        ok, error = session.start()
        if not ok:
            return {'ok': False, 'error': error, 'session': session.snapshot()}
        with self._lock:
            self._sessions[session.session_id] = session
        return {'ok': True, 'session': session.snapshot()}

    def get_session(self, session_id: str) -> Optional[BaseBridgeSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def close_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def latest_frame(self, session_id: str) -> Optional[bytes]:
        session = self.get_session(session_id)
        if session is None:
            return None
        return session.latest_or_placeholder()

    def mjpeg_chunks(self, session_id: str):
        while True:
            session = self.get_session(session_id)
            if session is None:
                break
            frame = session.latest_or_placeholder()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n'
                   b'Cache-Control: no-cache\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.15)