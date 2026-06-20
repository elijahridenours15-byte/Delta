#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import paramiko

HOST = "access-5020269590.webspace-host.com"
USER = "a750702"
SERVICE_PREFIX = "futurecodedelta/ionos/web-deploy"
DEFAULT_SERVICE = f"{SERVICE_PREFIX}/primary"
DEFAULT_PORT = 22
REGISTRY_PATH = Path.home() / "Library" / "Application Support" / "batcode-playground" / "ionos_deploy_keys.json"

ROOT = Path(__file__).resolve().parent.parent
ARMY_LIBRARY_PDF_DIR = ROOT / "static" / "manuals" / "army-library"
CULTURE_RANK_DIR = ROOT / "static" / "culture-ranks"
RADIO_UPLOAD_DIR = ROOT / "static" / "manuals" / "uploads" / "radio"
UPLOADS = [
    (Path("run.py"), "run.py"),
    (Path("security_hardening.py"), "security_hardening.py"),
    (Path("ionos-backend/.htaccess"), ".htaccess"),
    (Path("manual_store.py"), "manual_store.py"),
    (Path("drone_bridge.py"), "drone_bridge.py"),
    (Path("drone_bridge_http.py"), "drone_bridge_http.py"),
    (Path("templates/ai.html"), "templates/ai.html"),
    (Path("templates/admin_manuals.html"), "templates/admin_manuals.html"),
    (Path("templates/bible.html"), "templates/bible.html"),
    (Path("templates/cyber.html"), "templates/cyber.html"),
    (Path("templates/drone.html"), "templates/drone.html"),
    (Path("templates/advertise.html"), "templates/advertise.html"),
    (Path("templates/index.html"), "templates/index.html"),
    (Path("templates/live_map.html"), "templates/live_map.html"),
    (Path("templates/manuals.html"), "templates/manuals.html"),
    (Path("templates/manuals_brand.html"), "templates/manuals_brand.html"),
    (Path("templates/manuals_detail.html"), "templates/manuals_detail.html"),
    (Path("templates/map.html"), "templates/map.html"),
    (Path("templates/mechanics.html"), "templates/mechanics.html"),
    (Path("templates/mechanics_blueprint.html"), "templates/mechanics_blueprint.html"),
    (Path("templates/mechanics_blueprints.html"), "templates/mechanics_blueprints.html"),
    (Path("templates/mechanics_browser.html"), "templates/mechanics_browser.html"),
    (Path("templates/mechanics_gallery.html"), "templates/mechanics_gallery.html"),
    (Path("templates/radio.html"), "templates/radio.html"),
    (Path("templates/schooling.html"), "templates/schooling.html"),
    (Path("templates/weapons.html"), "templates/weapons.html"),
    (Path("templates/weapons_armory.html"), "templates/weapons_armory.html"),
    (Path("templates/survival.html"), "templates/survival.html"),
    (Path("templates/survival_army_library.html"), "templates/survival_army_library.html"),
    (Path("templates/survival_army_library_offline.html"), "templates/survival_army_library_offline.html"),
    (Path("templates/survival_army_library_binder.html"), "templates/survival_army_library_binder.html"),
    (Path("templates/survival_meal_bible_page.html"), "templates/survival_meal_bible_page.html"),
    (Path("templates/_meal_bible_initial_grid.html"), "templates/_meal_bible_initial_grid.html"),
    (Path("templates/_meal_bible_initial_detail.html"), "templates/_meal_bible_initial_detail.html"),
    (Path("templates/_culture_build_styles.html"), "templates/_culture_build_styles.html"),
    (Path("templates/culture_build.html"), "templates/culture_build.html"),
    (Path("templates/truth.html"), "templates/truth.html"),
    (Path("templates/_page_ad.html"), "templates/_page_ad.html"),
    (Path("templates/_radio_footer.html"), "templates/_radio_footer.html"),
    (Path("templates/_top_nav.html"), "templates/_top_nav.html"),
    (Path("generated/monetization_config.json"), "generated/monetization_config.json"),
    (Path("static/sw.js"), "static/sw.js"),
    (Path("static/offline.html"), "static/offline.html"),
    (Path("static/ads.txt"), "static/ads.txt"),
    (Path("static/auth.js"), "static/auth.js"),
    (Path("static/BingSiteAuth.xml"), "static/BingSiteAuth.xml"),
    (Path("static/robots.txt"), "static/robots.txt"),
    (Path("static/style.css"), "static/style.css"),
    (Path("static/map-layer-catalog.js"), "static/map-layer-catalog.js"),
    (Path("static/scripture-hotspots.js"), "static/scripture-hotspots.js"),
    (Path("static/survival_loadout.js"), "static/survival_loadout.js"),
    (Path("static/survival_meal_bible.js"), "static/survival_meal_bible.js"),
    (Path("static/vendor/leaflet/leaflet.css"), "static/vendor/leaflet/leaflet.css"),
    (Path("static/vendor/leaflet/leaflet.js"), "static/vendor/leaflet/leaflet.js"),
    (Path("static/vendor/leaflet/images/marker-icon.png"), "static/vendor/leaflet/images/marker-icon.png"),
    (Path("static/vendor/leaflet/images/marker-icon-2x.png"), "static/vendor/leaflet/images/marker-icon-2x.png"),
    (Path("static/vendor/leaflet/images/marker-shadow.png"), "static/vendor/leaflet/images/marker-shadow.png"),
    (Path("static/vendor/leaflet/images/layers.png"), "static/vendor/leaflet/images/layers.png"),
    (Path("static/vendor/leaflet/images/layers-2x.png"), "static/vendor/leaflet/images/layers-2x.png"),
    (Path("static/vendor/html2canvas.min.js"), "static/vendor/html2canvas.min.js"),
    (Path("static/sitemap.xml"), "static/sitemap.xml"),
]
UPLOADS.extend(
    (path.relative_to(ROOT), path.relative_to(ROOT).as_posix())
    for path in sorted(ARMY_LIBRARY_PDF_DIR.glob("*.pdf"))
)
UPLOADS.extend(
    (path.relative_to(ROOT), path.relative_to(ROOT).as_posix())
    for path in sorted(CULTURE_RANK_DIR.glob("*"))
)
UPLOADS.extend(
    (path.relative_to(ROOT), path.relative_to(ROOT).as_posix())
    for path in sorted(p for p in RADIO_UPLOAD_DIR.rglob("*") if p.is_file())
)
CHECK_URLS = [
    "https://futurecodedelta.org/",
    "https://futurecodedelta.org/ai",
    "https://futurecodedelta.org/bible",
    "https://futurecodedelta.org/cyber",
    "https://futurecodedelta.org/drone",
    "https://futurecodedelta.org/advertise",
    "https://futurecodedelta.org/map",
    "https://futurecodedelta.org/radio",
    "https://futurecodedelta.org/weapons",
    "https://futurecodedelta.org/weapons/armory",
    "https://futurecodedelta.org/survival",
    "https://futurecodedelta.org/survival/army-library",
    "https://futurecodedelta.org/survival/army-library/offline",
    "https://futurecodedelta.org/survival/army-library/binder",
    "https://futurecodedelta.org/truth",
    "https://futurecodedelta.org/sw.js",
    "https://futurecodedelta.org/BingSiteAuth.xml",
    "https://futurecodedelta.org/robots.txt",
    "https://futurecodedelta.org/ads.txt",
    "https://futurecodedelta.org/static/auth.js",
    "https://futurecodedelta.org/static/offline.html",
    "https://futurecodedelta.org/static/map-layer-catalog.js",
    "https://futurecodedelta.org/static/scripture-hotspots.js",
    "https://futurecodedelta.org/static/survival_loadout.js",
    "https://futurecodedelta.org/static/survival_meal_bible.js",
    "https://futurecodedelta.org/static/culture-ranks/iran-general.svg",
    "https://futurecodedelta.org/static/culture-ranks/iran-chief-warrant-officer.svg",
    "https://futurecodedelta.org/static/culture-ranks/iran-private.svg",
    "https://futurecodedelta.org/static/culture-ranks/china-general.png",
    "https://futurecodedelta.org/static/culture-ranks/china-master-sergeant-first-class.png",
    "https://futurecodedelta.org/static/culture-ranks/china-private.png",
    "https://futurecodedelta.org/static/culture-ranks/usa-general.svg",
    "https://futurecodedelta.org/static/culture-ranks/usa-sergeant-major-of-the-army.svg",
    "https://futurecodedelta.org/static/manuals/army-library/fm21-76.pdf",
    "https://futurecodedelta.org/static/vendor/leaflet/leaflet.js",
    "https://futurecodedelta.org/static/vendor/html2canvas.min.js",
    "https://futurecodedelta.org/sitemap.xml",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy weapons and armor updates to the live IONOS webspace.",
    )
    subparsers = parser.add_subparsers(dest="command")

    deploy_parser = subparsers.add_parser("deploy", help="upload files to the live site")
    deploy_parser.add_argument(
        "--service",
        help="macOS Keychain service name to use for the saved password",
    )
    deploy_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the upload plan and keychain service without connecting",
    )
    deploy_parser.add_argument(
        "--save-key",
        action="store_true",
        help="prompt for a password, save it as a new Keychain entry, then deploy with it",
    )
    deploy_parser.add_argument(
        "--label",
        default="deploy",
        help="short label used when saving a new Keychain entry",
    )
    deploy_parser.add_argument(
        "--note",
        default="",
        help="optional note recorded in the local deploy-key registry",
    )

    save_key_parser = subparsers.add_parser("save-key", help="save a new deploy password to macOS Keychain")
    save_key_parser.add_argument(
        "--label",
        default="manual",
        help="short label used to name the new Keychain entry",
    )
    save_key_parser.add_argument(
        "--note",
        default="",
        help="optional note recorded in the local deploy-key registry",
    )

    subparsers.add_parser("list-keys", help="list saved deploy-key metadata without secrets")

    args = parser.parse_args()
    if args.command is None:
        args.command = "deploy"
        args.service = None
        args.dry_run = False
        args.save_key = False
        args.label = "deploy"
        args.note = ""
    return args


def registry_entries() -> list[dict[str, str]]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def write_registry(entries: list[dict[str, str]]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def slugify_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower())
    slug = slug.strip("-")
    return slug or "deploy"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_service_name(label: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{SERVICE_PREFIX}/{stamp}-{slugify_label(label)}"


def find_registry_entry(service: str) -> dict[str, str] | None:
    for entry in registry_entries():
        if entry.get("service") == service:
            return entry
    return None


def latest_registry_entry() -> dict[str, str] | None:
    entries = registry_entries()
    if not entries:
        return None
    return max(entries, key=lambda entry: entry.get("created_at", ""))


def save_registry_entry(service: str, label: str, note: str, status: str) -> None:
    entries = registry_entries()
    entry = next((item for item in entries if item.get("service") == service), None)
    timestamp = now_iso()
    if entry is None:
        entries.append(
            {
                "service": service,
                "label": label,
                "note": note,
                "status": status,
                "created_at": timestamp,
                "updated_at": timestamp,
                "host": HOST,
                "user": USER,
            }
        )
    else:
        entry["label"] = label
        entry["note"] = note
        entry["status"] = status
        entry["updated_at"] = timestamp
    write_registry(entries)


def update_registry_status(service: str, status: str, note: str | None = None) -> None:
    entries = registry_entries()
    entry = next((item for item in entries if item.get("service") == service), None)
    if entry is None:
        return
    entry["status"] = status
    entry["updated_at"] = now_iso()
    if note is not None:
        entry["note"] = note
    write_registry(entries)


def list_keys() -> None:
    entries = sorted(registry_entries(), key=lambda entry: entry.get("created_at", ""), reverse=True)
    if not entries:
        print("No saved deploy-key metadata found.")
        print(f"Registry path: {REGISTRY_PATH}")
        return

    print(f"Registry path: {REGISTRY_PATH}")
    print("Saved deploy keys:")
    for entry in entries:
        label = entry.get("label", "")
        service = entry.get("service", "")
        status = entry.get("status", "unknown")
        created_at = entry.get("created_at", "")
        note = entry.get("note", "")
        line = f"  {created_at}  [{status}]  {label}  ->  {service}"
        print(line.rstrip())
        if note:
            print(f"    note: {note}")


def load_keychain_password(service: str) -> str | None:
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                USER,
                "-s",
                service,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.rstrip("\n") or None


def save_keychain_password(service: str, password: str, label: str, note: str) -> None:
    command = [
        "security",
        "add-generic-password",
        "-U",
        "-a",
        USER,
        "-s",
        service,
        "-l",
        label,
        "-w",
        password,
    ]
    if note:
        command.extend(["-j", note])
    subprocess.run(command, check=True, capture_output=True, text=True)


def prompt_for_password(service: str) -> str:
    prompt = f"IONOS password for {USER}@{HOST} [{service}]: "
    password = getpass.getpass(prompt)
    if not password:
        raise SystemExit("No password provided.")
    return password


def resolve_service(service: str | None) -> str:
    if service:
        return service
    latest = latest_registry_entry()
    if latest is not None and latest.get("service"):
        return latest["service"]
    return DEFAULT_SERVICE


def print_plan(service: str) -> None:
    print()
    print("Delta IONOS deploy")
    print(f"Host: {HOST}")
    print(f"User: {USER}")
    print(f"Keychain service: {service}")
    print(f"Registry path: {REGISTRY_PATH}")
    print("Files:")
    for local_path, remote_path in UPLOADS:
        print(f"  {local_path} -> {remote_path}")
    print("Post-upload chmod: index.cgi -> 755")
    print()


def ensure_remote_directory(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    remote_dir = PurePosixPath(remote_path).parent
    if str(remote_dir) in {"", "."}:
        return

    current = PurePosixPath()
    for part in remote_dir.parts:
        if part in {"", "."}:
            continue
        current = current / part
        current_path = str(current)
        try:
            sftp.stat(current_path)
        except OSError:
            sftp.mkdir(current_path)


def upload_files(password: str) -> None:
    transport = paramiko.Transport((HOST, DEFAULT_PORT))
    try:
        transport.connect(username=USER, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            for local_path, remote_path in UPLOADS:
                ensure_remote_directory(sftp, remote_path)
                sftp.put(str(ROOT / local_path), remote_path)
                print(f"Uploaded {remote_path}")
            sftp.chmod("index.cgi", 0o755)
            print("Set index.cgi to 755")
        finally:
            sftp.close()
    finally:
        transport.close()


def fetch_status(url: str) -> int | str:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # pragma: no cover - network dependent
        return f"error: {exc}"


def verify_live_site() -> None:
    print()
    print("Verifying live responses...")
    time.sleep(2)
    for url in CHECK_URLS:
        status = fetch_status(url)
        marker = "OK" if status == 200 else "FAIL"
        print(f"  [{marker}] {url} -> {status}")


def create_and_store_key(label: str, note: str) -> tuple[str, str]:
    service = build_service_name(label)
    password = prompt_for_password(service)
    save_keychain_password(service, password, label, note)
    save_registry_entry(service, label, note, "saved")
    print(f"Saved new Keychain entry: {service}")
    return service, password


def main() -> int:
    args = parse_args()
    if args.command == "list-keys":
        list_keys()
        return 0

    if args.command == "save-key":
        create_and_store_key(args.label, args.note)
        return 0

    service = resolve_service(args.service)
    print_plan(service)
    if args.dry_run:
        return 0

    if args.save_key:
        service, password = create_and_store_key(args.label, args.note)
    else:
        password = load_keychain_password(service)
        if password:
            print(f"Using password from macOS Keychain service: {service}")
        else:
            print(f"No saved password found for Keychain service: {service}")
            password = prompt_for_password(service)

    try:
        upload_files(password)
    except paramiko.AuthenticationException:
        update_registry_status(service, "auth-failed")
        print("Authentication failed for the supplied credential.", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - network dependent
        update_registry_status(service, "error", str(exc))
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1

    update_registry_status(service, "deployed")
    verify_live_site()
    print()
    print("Deploy finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())