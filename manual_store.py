import os
import sqlite3
import json
import uuid
from datetime import datetime
from PIL import Image
import io


class ManualStore:
    def __init__(self, app_root):
        self.app_root = app_root
        gen_dir = os.path.join(self.app_root, 'generated')
        os.makedirs(gen_dir, exist_ok=True)
        self.db_path = os.path.join(gen_dir, 'manuals.db')
        self.upload_dir = os.path.join(self.app_root, 'static', 'manuals', 'uploads')
        self.image_dir = os.path.join(self.app_root, 'static', 'manuals', 'images')
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.image_dir, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS manuals (
            id TEXT PRIMARY KEY,
            brand TEXT,
            model TEXT,
            year TEXT,
            title TEXT,
            description TEXT,
            license TEXT,
            source_url TEXT,
            pdf_path TEXT,
            image_paths TEXT,
            created TEXT
        )
        ''')
        conn.commit()
        conn.close()

    def add_manual(self, brand, model, year, title, description, license, source_url, pdf_path, image_paths, mid=None):
        if mid is None:
            mid = uuid.uuid4().hex
        created = datetime.utcnow().isoformat() + 'Z'
        imgs_json = json.dumps(image_paths or [])
        conn = self._connect()
        cur = conn.cursor()
        cur.execute('''INSERT INTO manuals (id,brand,model,year,title,description,license,source_url,pdf_path,image_paths,created)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (mid, brand, model, year, title, description, license, source_url, pdf_path, imgs_json, created))
        conn.commit()
        conn.close()
        return mid

    def search_manuals(self, brand=None, model=None, year=None, q=None):
        conn = self._connect()
        cur = conn.cursor()
        clauses = []
        params = []
        if brand:
            clauses.append('brand = ?')
            params.append(brand)
        if model:
            clauses.append('model = ?')
            params.append(model)
        if year:
            clauses.append('year = ?')
            params.append(year)
        if q:
            clauses.append('(title LIKE ? OR description LIKE ?)')
            params.extend([f'%{q}%', f'%{q}%'])
        where = 'WHERE ' + ' AND '.join(clauses) if clauses else ''
        sql = f'SELECT * FROM manuals {where} ORDER BY created DESC'
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r['image_paths'] = json.loads(r.get('image_paths') or '[]')
        conn.close()
        return rows

    def list_brands(self):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT brand FROM manuals WHERE brand IS NOT NULL ORDER BY brand')
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows

    def list_models(self, brand):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT model FROM manuals WHERE brand = ? ORDER BY model', (brand,))
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows

    def list_years(self, brand, model):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT year FROM manuals WHERE brand = ? AND model = ? ORDER BY year', (brand, model))
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows

    def get_manual(self, mid):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute('SELECT * FROM manuals WHERE id = ?', (mid,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        out = dict(row)
        out['image_paths'] = json.loads(out.get('image_paths') or '[]')
        return out

    def create_thumbnails_for_manual(self, mid, sizes=((400,300),(1200,800))):
        """Create thumbnails for all images in a manual's image folder.

        Returns a list of created thumbnail relative URLs.
        """
        folder = os.path.join(self.image_dir, mid)
        if not os.path.isdir(folder):
            return []

        created = []
        for fname in sorted(os.listdir(folder)):
            src_path = os.path.join(folder, fname)
            if not os.path.isfile(src_path):
                continue
            # skip thumbnails we already created
            if fname.startswith('thumb_'):
                continue
            try:
                with Image.open(src_path) as im:
                    for w, h in sizes:
                        thumb_name = f"thumb_{w}x{h}_{fname}"
                        thumb_path = os.path.join(folder, thumb_name)
                        if os.path.exists(thumb_path):
                            created.append(f'/static/manuals/images/{mid}/{thumb_name}')
                            continue
                        thumb = im.copy()
                        thumb.thumbnail((w, h), Image.LANCZOS)
                        # Preserve format when possible
                        fmt = im.format if im.format else 'JPEG'
                        save_kwargs = {}
                        if fmt.upper() in ('JPEG', 'JPG'):
                            save_kwargs = {'quality': 85, 'optimize': True}
                        thumb.save(thumb_path, format=fmt, **save_kwargs)
                        created.append(f'/static/manuals/images/{mid}/{thumb_name}')
            except Exception:
                # Skip problematic images
                continue
        return created
