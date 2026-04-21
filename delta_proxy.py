#!/usr/bin/env python3
from pathlib import Path
import os
from typing import Optional, Tuple

import requests
from flask import Flask, Response, redirect, request, send_file


ROOT = Path(__file__).resolve().parent
DELTA_FILE = ROOT / "DELTA.html"
UPSTREAM_ORIGIN = "https://app.netlify.com"
UPSTREAM_HOST = "app.netlify.com"
DEFAULT_PORT = 8001

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

app = Flask(__name__)


def _local_origin() -> str:
    return request.host_url.rstrip("/")


def _upstream_url(path: str) -> str:
    cleaned_path = path.lstrip("/")
    target = f"{UPSTREAM_ORIGIN}/{cleaned_path}" if cleaned_path else f"{UPSTREAM_ORIGIN}/"
    query = request.query_string.decode("utf-8")
    if query:
        target = f"{target}?{query}"
    return target


def _rewrite_request_header(name: str, value: str) -> Optional[Tuple[str, str]]:
    lower_name = name.lower()
    if lower_name in HOP_BY_HOP_HEADERS or lower_name in {"content-length", "host", "accept-encoding"}:
        return None
    if lower_name == "origin":
        return name, UPSTREAM_ORIGIN
    if lower_name == "referer":
        return name, value.replace(_local_origin(), UPSTREAM_ORIGIN, 1)
    return name, value


def _upstream_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        rewritten = _rewrite_request_header(name, value)
        if rewritten is None:
            continue
        header_name, header_value = rewritten
        headers[header_name] = header_value
    headers["Host"] = UPSTREAM_HOST
    return headers


def _rewrite_location(value: str) -> str:
    if value.startswith(UPSTREAM_ORIGIN):
        suffix = value[len(UPSTREAM_ORIGIN):]
        return f"{_local_origin()}{suffix or '/'}"
    if value.startswith("/"):
        return f"{_local_origin()}{value}"
    return value


def _rewrite_set_cookie(value: str) -> str:
    rewritten_parts = []
    for part in value.split(";"):
        token = part.strip()
        lower_token = token.lower()
        if lower_token.startswith("domain="):
            continue
        if lower_token == "secure":
            continue
        if lower_token == "samesite=none":
            rewritten_parts.append("SameSite=Lax")
            continue
        rewritten_parts.append(token)
    return "; ".join(rewritten_parts)


def _proxy(path: str) -> Response:
    upstream_response = requests.request(
        method=request.method,
        url=_upstream_url(path),
        headers=_upstream_headers(),
        data=request.get_data(),
        allow_redirects=False,
        timeout=30,
    )

    response = Response(upstream_response.content, status=upstream_response.status_code)

    for name, value in upstream_response.headers.items():
        lower_name = name.lower()
        if lower_name in HOP_BY_HOP_HEADERS or lower_name in {"content-encoding", "content-length", "set-cookie"}:
            continue
        if lower_name == "location":
            value = _rewrite_location(value)
        response.headers.add(name, value)

    for cookie in upstream_response.raw.headers.getlist("Set-Cookie"):
        response.headers.add("Set-Cookie", _rewrite_set_cookie(cookie))

    return response


@app.route("/")
def root() -> Response:
    return redirect("/DELTA.html", code=302)


@app.route("/DELTA.html")
def delta_html() -> Response:
    return send_file(DELTA_FILE)


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def proxy(path: str) -> Response:
    return _proxy(path)


if __name__ == "__main__":
    if not DELTA_FILE.exists():
        raise SystemExit(f"Missing file: {DELTA_FILE}")
    port = int(os.environ.get("DELTA_PROXY_PORT", DEFAULT_PORT))
    app.run(host="127.0.0.1", port=port, debug=False)