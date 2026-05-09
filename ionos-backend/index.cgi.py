import os
import sys
import tempfile
import traceback
from pathlib import Path
from wsgiref.handlers import CGIHandler


script_path = Path(os.environ.get('SCRIPT_FILENAME') or __file__)
ROOT = script_path.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def fail_with_error():
    sys.stderr.write(traceback.format_exc())
    print('Status: 500 Internal Server Error')
    print('Content-Type: text/plain; charset=utf-8')
    print()
    print('Delta Coding backend failed to start.')

deps_dir = ROOT / 'vendor'
if deps_dir.exists():
    sys.path.insert(0, str(deps_dir))


HTML_ROUTE_ALIASES = {
    '/index.html': '/',
    '/ai.html': '/ai',
    '/cyber.html': '/cyber',
    '/map.html': '/map',
    '/bible.html': '/bible',
    '/drone.html': '/drone',
    '/radio.html': '/radio',
    '/truth.html': '/truth',
}


try:
    runtime_root = Path(os.environ.get('DELTA_RUNTIME_DIR') or (Path(tempfile.gettempdir()) / 'delta-coding'))
    runtime_root.mkdir(parents=True, exist_ok=True)

    eggs_dir = runtime_root / 'python-eggs'
    eggs_dir.mkdir(parents=True, exist_ok=True)

    generated_dir = runtime_root / 'generated'
    generated_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault('OPEN_BROWSER', '0')
    os.environ.setdefault('FLASK_DEBUG', '0')
    os.environ.setdefault('PYTHON_EGG_CACHE', str(eggs_dir))
    os.environ.setdefault('DELTA_GENERATED_DIR', str(generated_dir))

    from run import app  # noqa: E402
    app.config['PROPAGATE_EXCEPTIONS'] = True

    def root_mounted_app(environ, start_response):
        path_info = environ.get('PATH_INFO') or ''
        script_name = environ.get('SCRIPT_NAME') or ''
        request_path = '/'

        for key in ('REDIRECT_URL', 'REDIRECT_REQUEST_URI', 'REQUEST_URI'):
            candidate = environ.get(key) or ''
            if candidate:
                request_path = candidate.split('?', 1)[0] or '/'
                break

        if path_info.startswith('/index.cgi'):
            path_info = path_info[len('/index.cgi'):] or '/'
        elif not path_info or path_info == '/':
            if request_path.startswith('/index.cgi'):
                request_path = request_path[len('/index.cgi'):] or '/'
            path_info = request_path or '/'

        path_info = HTML_ROUTE_ALIASES.get(path_info, path_info)

        if script_name.endswith('/index.cgi'):
            environ['SCRIPT_NAME'] = ''

        environ['PATH_INFO'] = path_info or '/'
        return app.wsgi_app(environ, start_response)

    CGIHandler().run(root_mounted_app)
except Exception:
    fail_with_error()