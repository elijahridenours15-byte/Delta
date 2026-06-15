import os
import sqlite3
import json
import uuid
from datetime import datetime

try:
    from PIL import Image
except Exception:
    Image = None


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
        self.ensure_seed_data()

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

    def ensure_seed_data(self):
        """Seed military vehicle entries, replacing any legacy civilian demo data."""
        try:
            conn = self._connect()
            cur = conn.cursor()
            # Remove legacy civilian demo entries
            cur.execute(
                "DELETE FROM manuals WHERE id IN ('demo1','demo2','demo3') "
                "OR brand IN ('Toyota','Ford','Honda','Chevrolet','Jeep')"
            )
            conn.commit()
            cur.execute('SELECT COUNT(*) FROM manuals')
            total = cur.fetchone()[0]
            if total > 0:
                conn.close()
                return
            # Batch-insert all military seed entries in a single transaction
            now = datetime.utcnow().isoformat() + 'Z'
            rows = [
                (
                    item['mid'], item['brand'], item['model'], item['year'],
                    item['title'], item['description'],
                    'Public Domain / US DoD', None, None,
                    json.dumps([]), now,
                )
                for item in _mil_seed_entries()
            ]
            cur.executemany(
                'INSERT OR IGNORE INTO manuals '
                '(id,brand,model,year,title,description,license,source_url,pdf_path,image_paths,created) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                rows,
            )
            conn.commit()
            conn.close()
        except Exception:
            pass  # Never crash app startup due to seed failure

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
        if Image is None:
            return []

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


def _mil_seed_entries():
    """Generate military vehicle DB seed entries (2000-2026)."""
    def _yr(brand, model, start, end, desc, mid_pfx=None):
        pfx = mid_pfx or ('mil-' + brand.lower().replace(' ', '')[:10] + '-' + model.lower().replace(' ', '').replace('/', '').replace('×', 'x')[:14])
        return [
            {
                'brand': brand, 'model': model, 'year': str(y),
                'title': f'{brand} {model} {y}',
                'description': desc,
                'image_paths': [],
                'mid': f'{pfx}-{y}',
            }
            for y in range(max(2000, start), min(2026, end) + 1)
        ]

    out = []
    # ── GROUND COMBAT ─────────────────────────────────────────────────────────
    out += _yr('AM General', 'HMMWV M1114', 2000, 2022,
        'Up-Armored HMMWV. 6.5L turbocharged V8 diesel, 4-speed TH400 automatic, 4×4, GVW 12,100 lb. Ballistic glass, armored cab, A/B-kit armor package, M2 .50-cal or MK19 roof mount.')
    out += _yr('AM General', 'HMMWV M1151A1', 2006, 2026,
        'Expanded Capacity Vehicle HMMWV. Enhanced underbody blast protection, 6.5L V8 diesel, 4×4, payload 2,500 lb, roof-mounted weapon system capable, ECV armor kit.')
    out += _yr('AM General', 'LSSV', 2000, 2016,
        'Light Service Support Vehicle. Commercial off-the-shelf pickup platform for logistics and support roles. 6.5L diesel or gasoline V8, 4×4.')
    out += _yr('Oshkosh', 'M-ATV', 2009, 2026,
        'MRAP All-Terrain Vehicle. TAK-4i independent suspension, Caterpillar C7 diesel 370 hp, monocoque V-hull crew capsule, 7-ton GVW, 4×4 wheeled.')
    out += _yr('Oshkosh', 'JLTV L-ATV', 2016, 2026,
        'Joint Light Tactical Vehicle. 6.6L Duramax turbodiesel, IWS independent wheel suspension, 3.5-ton payload, 70 mph road speed, B-kit armor capable, 4×4.')
    out += _yr('Oshkosh', 'FMTV A2', 2000, 2026,
        'Family of Medium Tactical Vehicles. Caterpillar C7 7.2L diesel, Allison 3000SP automatic, CTI central tire inflation, variants 2.5-10 ton payload, 6×6 wheeled.')
    out += _yr('Oshkosh', 'HEMTT A4', 2000, 2026,
        'Heavy Expanded Mobility Tactical Truck. Detroit Diesel Series 60, Allison HD4060P automatic, 8×8 wheeled, 15-ton payload, 65,000 lb GVW, tanker/wrecker/cargo variants.')
    out += _yr('BAE Systems', 'M2A3 Bradley IFV', 2000, 2026,
        'Infantry Fighting Vehicle. Cummins VTA-903T 600 hp diesel, 25mm M242 Bushmaster, TOW missile launcher, 7 dismounts, tracked, 33-ton combat weight.')
    out += _yr('BAE Systems', 'M3A3 Bradley CFV', 2000, 2026,
        'Cavalry Fighting Vehicle. Same M2A3 chassis, optimized for scout/recon, TOW missiles, no rear ramp dismount troop bay, 2-man crew + 2 scouts.')
    out += _yr('BAE Systems', 'AMPV', 2020, 2026,
        'Armored Multi-Purpose Vehicle. BAE turbodiesel 675 hp, tracked, replaces M113, 2 crew + 6 dismounts, improved blast protection, C2/medevac/mortar variants.')
    out += _yr('BAE Systems', 'RG31 Nyala', 2006, 2020,
        'South African V-hull MRAP. 7.2L diesel, 4×4 wheeled, 6-seat blast capsule, mine-protected floor, used in OIF/OEF by US/coalition forces.')
    out += _yr('BAE Systems', 'M113A3 APC', 2000, 2015,
        'Armored Personnel Carrier. Chrysler 75M V8 diesel, 11.4-ton, tracked, aluminum hull, amphibious, 2 crew + 11 infantry, widely replaced by AMPV.')
    out += _yr('General Dynamics', 'M1A2 SEP Abrams', 2000, 2026,
        'Main Battle Tank SEP. Honeywell AGT1500 gas turbine 1,500 hp, 120mm M256A1 smoothbore, composite Chobham/DU armor, 68-ton combat weight, tracked.')
    out += _yr('General Dynamics', 'M1A2 SEPv3 Abrams', 2017, 2026,
        'System Enhancement Package v3 MBT. New auxiliary power unit, improved FLIR, commander thermal viewer, USB-3 architecture, Trophy APS capable, 120mm cannon.')
    out += _yr('General Dynamics', 'Stryker ICV', 2003, 2026,
        'Infantry Carrier Vehicle. Caterpillar C7 diesel 350 hp, 8×8 wheeled, double V-hull, 9 dismounts + 2 crew, slat/appliqué armor options, 62 mph road speed.')
    out += _yr('General Dynamics', 'Stryker MGS', 2008, 2026,
        'Mobile Gun System. 105mm M68A2 rifled cannon, autoloader magazine, 350 hp diesel, 8×8 wheeled, direct-fire support for Stryker brigade combat teams.')
    out += _yr('Force Protection', 'Cougar 4x4 MRAP', 2004, 2019,
        'Category I MRAP. Caterpillar C7 diesel, V-hull monocoque capsule, 4×4 wheeled, 4-seat blast protected crew compartment, M2/MK19/M240 gun mount provisions.')
    out += _yr('Force Protection', 'Cougar 6x6 MRAP', 2004, 2019,
        'Category II MRAP. Caterpillar C9 diesel, 6×6 wheeled, enlarged 7-seat blast capsule, EOD and medevac variants, husky/buffalo family member.')
    out += _yr('Navistar', 'MaxxPro MRAP', 2007, 2019,
        'International MaxxPro. MaxxForce 9 diesel, V-hull under-body blast deflection, 4×4, Category I MRAP, add-on armor kit, 1 driver + 5 passengers.')
    out += _yr('Navistar', 'MaxxPro Dash', 2010, 2019,
        'Reduced height MaxxPro variant. Improved off-road mobility, lower center of gravity, same MaxxForce diesel, Category I MRAP, OIF/OEF deployed.')
    out += _yr('Polaris', 'MRZR-D4', 2014, 2026,
        'Ultra-Light Combat Vehicle. 4-cyl turbocharged diesel, 4×4, 1,500 lb payload, 14.5 in ground clearance, air-transportable, special operations forces primary vehicle.')
    out += _yr('Textron', 'M1117 Guardian ASV', 2000, 2020,
        'Armored Security Vehicle. Cummins 6CTA diesel, Allison automatic, 4×4 wheeled, M2 .50-cal + MK19, V-hull, military police and convoy escort platform.')

    # ── AVIATION ──────────────────────────────────────────────────────────────
    out += _yr('Boeing', 'AH-64D Apache Longbow', 2000, 2012,
        'Attack helicopter. Two GE T700-GE-701C turboshafts 1,890 shp each, 30mm M230 chain gun, 16× Hellfire or 76× Hydra rockets, Longbow millimeter-wave radar dome.')
    out += _yr('Boeing', 'AH-64E Apache Guardian', 2012, 2026,
        'Block III attack helicopter. Two GE T700-GE-701D 2,000 shp each, improved drivetrain, UAS teaming Level IV autonomy, 30mm cannon, Hellfire/Stinger/Spike NLOS.')
    out += _yr('Boeing', 'CH-47D Chinook', 2000, 2012,
        'Heavy-lift tandem rotor helicopter. Two Lycoming T55-L-712 turboshafts 3,750 shp each, 26,000 lb useful load, triple-hook cargo system, 55 troops or 24 litters.')
    out += _yr('Boeing', 'CH-47F Chinook', 2007, 2026,
        'Modernized Chinook. Two Honeywell T55-GA-714A 4,733 shp each, CAAS digital cockpit, 21,000 lb sling load, multi-mode digital automatic flight control system.')
    out += _yr('Sikorsky', 'UH-60L Black Hawk', 2000, 2009,
        'Utility transport helicopter. Two GE T700-GE-701C 1,940 shp each, 11 troops or 8,000 lb external load, ESSS stub wing pylons, HOIST, 178 kt max speed.')
    out += _yr('Sikorsky', 'UH-60M Black Hawk', 2007, 2026,
        'Upgraded Black Hawk. Two GE T700-GE-701D 2,000 shp each, wide-chord composite blades, Rockwell Collins EFIS cockpit, FADEC, 9,000 lb sling load capability.')
    out += _yr('Bell', 'UH-1Y Venom', 2008, 2026,
        'USMC utility helicopter. Two GE T700-GE-401C 1,828 shp each, four-blade composite rotor, 8,000 lb sling load, 4 crew + 10 troops, H-1 upgrade commonality with AH-1Z.')
    out += _yr('Bell', 'AH-1Z Viper', 2010, 2026,
        'USMC attack helicopter. Two GE T700-GE-401C, four-blade semi-rigid rotor, 20mm M197 tri-barrel, Hellfire/Zuni/Sidewinder/APKWS, NTS targeting system, USMC primary attack helo.')
    out += _yr('Lockheed Martin', 'F-22A Raptor', 2005, 2026,
        '5th-gen air superiority fighter. Two P&W F119-PW-100 35,000 lbf with A/B, supercruise at Mach 1.8, stealth LO signature, 20mm M61A2 Vulcan, AIM-120/AIM-9.')
    out += _yr('Lockheed Martin', 'F-35A Lightning II', 2015, 2026,
        'CTOL multi-role 5th-gen fighter. P&W F135-PW-100 43,000 lbf, 25mm GAU-22/A internal gun, EOTS/EODAS/APG-81 AESA, LO stealth, USAF primary tactical fighter.')
    out += _yr('Lockheed Martin', 'F-35B Lightning II', 2015, 2026,
        'STOVL 5th-gen fighter. P&W F135-PW-600 with LiftSystem shaft-driven lift fan, 18,000 lbf vertical thrust, USMC/UK/Italy STOVL primary strike aircraft.')
    out += _yr('Lockheed Martin', 'F-35C Lightning II', 2019, 2026,
        'CV naval 5th-gen fighter. P&W F135-PW-400, reinforced undercarriage, catapult launch bar, folding wingtips, USN carrier-based primary strike/air superiority.')
    out += _yr('Boeing', 'F-15EX Eagle II', 2021, 2026,
        'Advanced 4th-gen fighter. Two P&W F100-PW-229EEP 29,000 lbf each, 8 wing pylons + 5 centerline, 29,500 lb max payload, Raytheon EPAWSS EW, USAF air superiority.')
    out += _yr('General Atomics', 'MQ-9A Reaper', 2007, 2026,
        'MALE UCAV. Honeywell TPE331-10T turboprop 900 shp, 50,000 ft service ceiling, 14+ hr endurance, 4× AGM-114 Hellfire, 2× GBU-12, Lynx multi-mode radar, USAF/CIA primary.')
    out += _yr('Northrop Grumman', 'RQ-4B Global Hawk', 2004, 2026,
        'HALE strategic ISR UAV. Rolls-Royce AE3007H turbofan 7,050 lbf, 60,000 ft cruise, 30+ hr endurance, SAR/EO/IR/SIGINT integrated suite, signals intelligence mission aircraft.')
    out += _yr('Northrop Grumman', 'MQ-4C Triton', 2018, 2026,
        'BAMS maritime HALE UAV. RR AE3007H turbofan, 360° sensor sweep, multi-INT maritime surface search, 30+ hr endurance, USN Broad Area Maritime Surveillance program.')

    # ── NAVAL ─────────────────────────────────────────────────────────────────
    out += _yr('Huntington Ingalls', 'DDG-51 Arleigh Burke', 2000, 2026,
        'Guided-missile destroyer. Two GE LM2500-30 gas turbines COGAG, 100,000 shp, 30+ kt, Mk 41 VLS 96 cells, Aegis AN/SPY-1D(V) radar, Phalanx CIWS, 5in/62 gun.')
    out += _yr('Huntington Ingalls', 'LCS-1 Freedom', 2008, 2026,
        'Freedom-class Littoral Combat Ship (monohull). CODAG propulsion, 47-kt sprint speed, Mk 110 57mm gun, reconfigurable mission module bays, 40 crew core.')
    out += _yr('General Dynamics', 'LCS-2 Independence', 2010, 2026,
        'Independence-class trimaran LCS. COGAG propulsion, 44-kt sprint, large flight deck for two MH-60 helicopters, MQ-8 capable, reconfigurable mission modules.')
    out += _yr('General Dynamics', 'Virginia-class SSN', 2004, 2026,
        'Fast-attack nuclear submarine. S9G PWR reactor, GE steam turbines, 25+ kt submerged, 12× VLS Tomahawk TLAM, four 533mm torpedo tubes Mk 48 ADCAP, 135 crew.')
    out += _yr('Huntington Ingalls', 'CVN-78 Ford-class', 2017, 2026,
        'Nuclear aircraft carrier. Two A1B PWR reactors, EMALS electromagnetic catapult, AAG arresting gear, 90 aircraft, 4.5-acre flight deck, AN/SPY-3 MFR, 4,539 crew.')

    return out
