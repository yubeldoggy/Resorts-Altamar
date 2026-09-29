"""Base local de Resorts Altamar. Solo requiere Python 3.11 o posterior."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import webbrowser
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / 'altamar.sqlite3'
ROLES = ('cliente', 'recepcion', 'gerente')
PBKDF2_ITERATIONS = 600_000
SESSION_COOKIE = 'altamar_session'
SESSION_HOURS = 8
PASSWORD_MIN, PASSWORD_MAX = 8, 64
MAX_FAILURES, LOCK_MINUTES = 5, 5
# Claves demasiado comunes: se rechazan aunque cumplan el largo y la mezcla de letras y números.
COMMON_PASSWORDS = {
    'password1', 'password123', 'contraseña1', 'contrasena1', 'qwerty123', 'abc12345', 'abcd1234',
    'admin123', 'administrador1', 'altamar1', 'altamar123', 'altamar2026', 'hotel123', 'resort123',
    'chile123', 'chile2026', 'iloveyou1', 'welcome1', 'bienvenido1', 'passw0rd', 'a1b2c3d4', '1q2w3e4r',
}
# Cuentas iniciales: RUT ficticios con dígito verificador válido.
TEST_ACCOUNTS = [
    ('11.111.111-1', 'Cliente de prueba', 'cliente', 'Cliente#2026'),
    ('22.013.635-3', 'Damián Sandoval', 'recepcion', 'gNvFAxfNppb4'),
    ('16.091.233-2', 'Eduardo Salinas', 'gerente', 'w3TJXFFSRXCT'),
]
NIGHTLY_RATE = 80_000
SERVICES = (
    {'id': 'spa', 'name': 'Spa', 'unit_price': 35_000},
    {'id': 'tour', 'name': 'Tour', 'unit_price': 50_000},
    {'id': 'room', 'name': 'Servicio a la habitación', 'unit_price': 18_000},
)
SERVICES_BY_ID = {service['id']: service for service in SERVICES}


class AccessError(Exception):
    """Sesión ausente o vencida (401), rol sin permiso (403) o demasiados intentos (429)."""
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


# ---------- Validaciones ----------

def rut_check_digit(body):
    total, factor = 0, 2
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    result = 11 - total % 11
    return '0' if result == 11 else 'K' if result == 10 else str(result)


def clean_rut(value):
    """Acepta 12.345.678-5, 12345678-5 o 123456785. Devuelve 12345678-5 o lanza ValueError."""
    match = isinstance(value, str) and re.fullmatch(
        r'(\d{1,2})\.?(\d{3})\.?(\d{3})-?([0-9K])', value.strip().upper(), re.ASCII)
    if not match:
        raise ValueError('Escribe un RUT válido, por ejemplo 12.345.678-5.')
    body, digit = ''.join(match.groups()[:3]).lstrip('0'), match.group(4)
    if len(body) < 7 or rut_check_digit(body) != digit:
        raise ValueError('El RUT no es válido: revisa el dígito verificador.')
    return f'{body}-{digit}'


def clean_name(value):
    """Nombre de 2 a 80 caracteres: letras (incluye tildes y ñ), espacios, apóstrofo, guion o punto."""
    name = ' '.join(value.split()) if isinstance(value, str) else ''
    if (not 2 <= len(name) <= 80 or sum(c.isalpha() for c in name) < 2
            or not all(c.isalpha() or c in " '-." for c in name)):
        raise ValueError('Escribe un nombre válido de 2 a 80 caracteres (solo letras, espacios, apóstrofo o guion).')
    return name


def check_password(password, rut=''):
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ValueError(f'La contraseña debe tener entre {PASSWORD_MIN} y {PASSWORD_MAX} caracteres.')
    if not any(c.isalpha() for c in password) or not any(c in '0123456789' for c in password):
        raise ValueError('La contraseña debe combinar letras y números.')
    if password.lower() in COMMON_PASSWORDS or (rut and rut.split('-')[0] in password.replace('.', '')):
        raise ValueError('Esa contraseña es muy fácil de adivinar (es común o contiene tu RUT). Elige otra.')
    return password


# ---------- Contraseñas, sesiones y bitácora ----------

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return f'pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, digest = stored.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                        bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except (ValueError, AttributeError):
        return False


_DUMMY_HASHES = {}


def dummy_hash():
    # Si el RUT no existe se verifica igual contra un hash falso: la respuesta tarda lo mismo.
    if PBKDF2_ITERATIONS not in _DUMMY_HASHES:
        _DUMMY_HASHES[PBKDF2_ITERATIONS] = hash_password(secrets.token_hex(16))
    return _DUMMY_HASHES[PBKDF2_ITERATIONS]


def token_hash(token):
    # En la base solo se guarda el hash del token, nunca el token.
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def now_text():
    return datetime.now().isoformat(timespec='seconds')


def public_user(row):
    return {'id': row['id'], 'rut': row['rut'], 'name': row['name'], 'role': row['role']}


def audit(db, action, user_id=None, detail=''):
    db.execute('INSERT INTO audit(at,user_id,action,detail) VALUES(?,?,?,?)',
               (now_text(), user_id, action, detail))


def start_session(db, user_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec='seconds')
    db.execute('DELETE FROM sessions WHERE expires<=?', (now_text(),))
    db.execute('INSERT INTO sessions VALUES(?,?,?)', (token_hash(token), user_id, expires))
    return token


@contextmanager
def connection():
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS hotels(
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, rut TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('cliente','recepcion','gerente')),
                password_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                expires TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts(
                rut TEXT PRIMARY KEY, failures INTEGER NOT NULL, locked_until TEXT);
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS reservations(
                id INTEGER PRIMARY KEY, hotel_id INTEGER NOT NULL REFERENCES hotels(id),
                guest TEXT NOT NULL, arrival TEXT NOT NULL, departure TEXT NOT NULL,
                room INTEGER NOT NULL CHECK(room BETWEEN 1 AND 5),
                status TEXT NOT NULL DEFAULT 'Confirmada', user_id INTEGER REFERENCES users(id),
                created_by INTEGER REFERENCES users(id), CHECK(departure > arrival));
            CREATE TABLE IF NOT EXISTS nights(
                hotel_id INTEGER NOT NULL REFERENCES hotels(id), room INTEGER NOT NULL,
                night TEXT NOT NULL, reservation_id INTEGER NOT NULL REFERENCES reservations(id),
                PRIMARY KEY(hotel_id, room, night));
            CREATE TABLE IF NOT EXISTS reservation_services(
                reservation_id INTEGER NOT NULL REFERENCES reservations(id),
                service_id TEXT NOT NULL, service_name TEXT NOT NULL, unit_price INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 10),
                PRIMARY KEY(reservation_id, service_id));
        ''')
        # Bases creadas por versiones anteriores: se agregan las columnas nuevas.
        columns = {r[1] for r in db.execute('PRAGMA table_info(reservations)')}
        for column in ('user_id', 'created_by'):
            if column not in columns:
                db.execute(f'ALTER TABLE reservations ADD COLUMN {column} INTEGER REFERENCES users(id)')
        if not db.execute('SELECT 1 FROM hotels LIMIT 1').fetchone():
            regions = {
                'Norte': ['Arica', 'Iquique', 'Antofagasta', 'Caldera', 'La Serena'],
                'Centro': ['Concón', 'Viña del Mar', 'Valparaíso', 'Algarrobo', 'Pichilemu'],
                'Sur': ['Pucón', 'Villarrica', 'Valdivia', 'Puerto Varas', 'Frutillar'],
                'Austral': ['Castro', 'Chaitén', 'Coyhaique', 'Puerto Natales', 'Punta Arenas'],
            }
            db.executemany('INSERT INTO hotels(name,region) VALUES(?,?)',
                           [('Altamar ' + city, region) for region, cities in regions.items() for city in cities])
        if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            db.executemany('INSERT INTO users(rut,name,role,password_hash) VALUES(?,?,?,?)',
                           [(clean_rut(rut), name, role, hash_password(password))
                            for rut, name, role, password in TEST_ACCOUNTS])


# ---------- Acceso ----------

def login(data):
    rut = clean_rut(data.get('rut'))
    password = data.get('password')
    outcome, user, token = 'fail', None, None
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        attempt = db.execute('SELECT * FROM login_attempts WHERE rut=?', (rut,)).fetchone()
        if attempt and (attempt['locked_until'] or '') > now_text():
            outcome = 'locked'
            audit(db, 'acceso_bloqueado', None, rut)
        else:
            user = db.execute('SELECT * FROM users WHERE rut=?', (rut,)).fetchone()
            valid = (isinstance(password, str) and 1 <= len(password) <= 128
                     and verify_password(password, user['password_hash'] if user else dummy_hash()))
            if user and valid:
                outcome = 'ok'
                db.execute('DELETE FROM login_attempts WHERE rut=?', (rut,))
                token = start_session(db, user['id'])
                audit(db, 'inicio_sesion', user['id'])
            else:
                # El bloqueo aplica exista o no el RUT: así no revela qué cuentas existen.
                failures = (attempt['failures'] if attempt else 0) + 1
                locked_until = None
                if failures >= MAX_FAILURES:
                    locked_until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec='seconds')
                    outcome, failures = 'locked', 0
                db.execute('''INSERT INTO login_attempts VALUES(?,?,?) ON CONFLICT(rut)
                    DO UPDATE SET failures=excluded.failures, locked_until=excluded.locked_until''',
                           (rut, failures, locked_until))
                audit(db, 'acceso_fallido', user['id'] if user else None,
                      rut + (f' · bloqueado {LOCK_MINUTES} min' if locked_until else ''))
    # Se responde después de guardar: el contador de intentos no se pierde con el error.
    if outcome == 'locked':
        raise AccessError(f'Demasiados intentos fallidos. Espera {LOCK_MINUTES} minutos e inténtalo de nuevo.', 429)
    if outcome == 'fail':
        raise AccessError('RUT o contraseña incorrectos.', 401)
    return token, public_user(user)


def register_client(data, creator=None):
    """Registro público (creator=None) o cliente creado por recepción."""
    if creator and creator['role'] != 'recepcion':
        raise AccessError('Tu rol no permite registrar clientes.')
    rut = clean_rut(data.get('rut'))
    name = clean_name(data.get('name'))
    password = check_password(data.get('password'), rut)
    with connection() as db:
        try:
            uid = db.execute('INSERT INTO users(rut,name,role,password_hash) VALUES(?,?,?,?)',
                             (rut, name, 'cliente', hash_password(password))).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError('Ya existe una cuenta con ese RUT.')
        if creator:
            audit(db, 'cliente_creado', creator['id'], f'{rut} · {name}')
            token = None
        else:
            audit(db, 'registro_cliente', uid, rut)
            token = start_session(db, uid)
    return token, {'id': uid, 'rut': rut, 'name': name, 'role': 'cliente'}


def session_user(token):
    if not token:
        return None
    with connection() as db:
        row = db.execute('''SELECT u.id, u.rut, u.name, u.role FROM sessions s
            JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?''',
                         (token_hash(token), now_text())).fetchone()
    return dict(row) if row else None


def logout(token):
    user = session_user(token)
    if token:
        with connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(token),))
            if user:
                audit(db, 'cierre_sesion', user['id'])


def list_clients(user):
    if user['role'] != 'recepcion':
        raise AccessError('Tu rol no permite ver clientes.')
    with connection() as db:
        return [dict(r) for r in db.execute("SELECT id, rut, name FROM users WHERE role='cliente' ORDER BY name")]


def list_audit(user):
    if user['role'] != 'gerente':
        raise AccessError('Solo gerencia puede ver la actividad de seguridad.')
    with connection() as db:
        return [dict(r) for r in db.execute('''SELECT a.at, a.action, a.detail, u.name, u.role
            FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 100''')]


# ---------- Reservas ----------

def normalize_services(items):
    if not isinstance(items, list) or len(items) > len(SERVICES):
        raise ValueError('Selecciona servicios válidos.')
    normalized, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Selecciona servicios válidos.')
        service_id, quantity = item.get('id'), item.get('quantity')
        if (service_id not in SERVICES_BY_ID or service_id in seen or isinstance(quantity, bool)
                or not isinstance(quantity, int) or not 0 <= quantity <= 10):
            raise ValueError('Revisa las cantidades de los servicios (máximo 10 por tipo).')
        seen.add(service_id)
        if quantity:
            normalized.append((SERVICES_BY_ID[service_id], quantity))
    return normalized


def save_reservation_services(db, reservation_id, services):
    db.execute('DELETE FROM reservation_services WHERE reservation_id=?', (reservation_id,))
    db.executemany('''INSERT INTO reservation_services
        (reservation_id,service_id,service_name,unit_price,quantity) VALUES(?,?,?,?,?)''',
                   [(reservation_id, service['id'], service['name'], service['unit_price'], quantity)
                    for service, quantity in services])


def service_catalog():
    return [dict(service) for service in SERVICES]


def list_reservations(user):
    query = '''SELECT r.id, r.guest, r.arrival, r.departure, r.room, r.status, r.user_id,
        h.name hotel, h.region FROM reservations r JOIN hotels h ON h.id=r.hotel_id'''
    params = ()
    if user['role'] == 'cliente':
        query, params = query + ' WHERE r.user_id=?', (user['id'],)
    with connection() as db:
        rows = [dict(r) for r in db.execute(query + ' ORDER BY r.id DESC', params)]
        if not rows:
            return rows
        ids = [row['id'] for row in rows]
        placeholders = ','.join('?' for _ in ids)
        services = db.execute(f'''SELECT reservation_id,service_id,name,unit_price,quantity FROM (
            SELECT reservation_id,service_id,service_name name,unit_price,quantity
            FROM reservation_services WHERE reservation_id IN ({placeholders}))
            ORDER BY reservation_id,service_id''', ids).fetchall()
    services_by_reservation = {}
    for service in services:
        services_by_reservation.setdefault(service['reservation_id'], []).append({
            'id': service['service_id'], 'name': service['name'],
            'unit_price': service['unit_price'], 'quantity': service['quantity'],
        })
    for row in rows:
        row['nights'] = (date.fromisoformat(row['departure']) - date.fromisoformat(row['arrival'])).days
        row['nightly_rate'] = NIGHTLY_RATE
        row['lodging_total'] = row['nights'] * NIGHTLY_RATE
        row['services'] = services_by_reservation.get(row['id'], [])
        row['services_total'] = sum(item['unit_price'] * item['quantity'] for item in row['services'])
        row['total'] = row['lodging_total'] + row['services_total']
    return rows


def create_reservation(data, user):
    if user['role'] not in ('cliente', 'recepcion'):
        raise AccessError('Tu rol no permite crear reservas.')
    services = normalize_services(data.get('services', []))
    client_id, guest = None, None
    if user['role'] == 'cliente':
        guest = user['name']  # El cliente siempre reserva a su nombre.
    elif data.get('client_id') not in (None, ''):
        try:
            client_id = int(data['client_id'])
        except (TypeError, ValueError):
            raise ValueError('Selecciona un cliente válido.')
    else:
        guest = clean_name(data.get('guest', ''))
    try:
        hotel = int(data['hotel_id'])
        arrival, departure = date.fromisoformat(data['arrival']), date.fromisoformat(data['departure'])
    except (KeyError, ValueError, TypeError):
        raise ValueError('Selecciona un hotel y fechas válidas.')
    if arrival < date.today() or not 1 <= (departure - arrival).days <= 60:
        raise ValueError('La llegada debe ser hoy o después y la estadía debe durar entre 1 y 60 noches.')
    with connection() as db:
        # El bloqueo precede a la consulta: dos solicitudes no pueden tomar el mismo cupo.
        db.execute('BEGIN IMMEDIATE')
        owner = user['id']
        if client_id is not None:
            client = db.execute("SELECT id, name FROM users WHERE id=? AND role='cliente'", (client_id,)).fetchone()
            if not client:
                raise ValueError('El cliente no existe.')
            owner, guest = client['id'], client['name']
        hotel_row = db.execute('SELECT name FROM hotels WHERE id=?', (hotel,)).fetchone()
        if not hotel_row:
            raise ValueError('El hotel no existe.')
        occupied = {r[0] for r in db.execute(
            'SELECT DISTINCT room FROM nights WHERE hotel_id=? AND night>=? AND night<?',
            (hotel, arrival.isoformat(), departure.isoformat()))}
        room = next((n for n in range(1, 6) if n not in occupied), None)
        if room is None:
            raise ValueError('No quedan habitaciones en ese hotel para esas fechas. Prueba otro hotel o rango de fechas.')
        rid = db.execute('''INSERT INTO reservations(hotel_id,guest,arrival,departure,room,user_id,created_by)
            VALUES(?,?,?,?,?,?,?)''', (hotel, guest, arrival.isoformat(), departure.isoformat(),
                                       room, owner, user['id'])).lastrowid
        db.executemany('INSERT INTO nights VALUES(?,?,?,?)',
                       [(hotel, room, (arrival + timedelta(days=i)).isoformat(), rid)
                        for i in range((departure - arrival).days)])
        save_reservation_services(db, rid, services)
        audit(db, 'reserva_creada', user['id'], f'ALT-{rid:04d} · {hotel_row["name"]} · {guest}')
        return {'message': f'Reserva ALT-{rid:04d} creada. Habitación {room}.', 'id': rid}


def update_reservation_services(rid, items, user):
    if user['role'] not in ('cliente', 'recepcion'):
        raise AccessError('Tu rol no permite modificar servicios.')
    services = normalize_services(items)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('''SELECT user_id,arrival,departure,status FROM reservations WHERE id=?''', (rid,)).fetchone()
        if not row or (user['role'] == 'cliente' and row['user_id'] != user['id']):
            raise ValueError('La reserva no existe.')
        if row['status'] == 'Cancelada':
            raise ValueError('No se pueden modificar servicios de una reserva cancelada.')
        save_reservation_services(db, rid, services)
        audit(db, 'servicios_actualizados', user['id'], f'ALT-{rid:04d}')
        nights = (date.fromisoformat(row['departure']) - date.fromisoformat(row['arrival'])).days
        total = nights * NIGHTLY_RATE + sum(service['unit_price'] * quantity
                                           for service, quantity in services)
    return {'message': 'Servicios de la reserva actualizados.', 'total': total}


def cancel_reservation(rid, user):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status, user_id FROM reservations WHERE id=?', (rid,)).fetchone()
        # Un cliente no puede ver ni cancelar reservas ajenas.
        if not row or (user['role'] == 'cliente' and row['user_id'] != user['id']):
            raise ValueError('La reserva no existe.')
        if row['status'] == 'Cancelada':
            raise ValueError('La reserva ya está cancelada.')
        db.execute("UPDATE reservations SET status='Cancelada' WHERE id=?", (rid,))
        db.execute('DELETE FROM nights WHERE reservation_id=?', (rid,))
        audit(db, 'reserva_cancelada', user['id'], f'ALT-{rid:04d}')
    return {'message': 'Reserva cancelada. La habitación vuelve a estar disponible.'}


def session_cookie(token, max_age):
    return f'{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}'


class Handler(BaseHTTPRequestHandler):
    # No se anuncia la versión de Python en la cabecera Server.
    server_version = 'Altamar'
    sys_version = ''

    def log_message(self, *_):
        pass

    def reply(self, status, data, mime='application/json; charset=utf-8', cookie=None):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def session_token(self):
        try:
            morsel = SimpleCookie(self.headers.get('Cookie', '')).get(SESSION_COOKIE)
        except CookieError:
            return None
        return morsel.value if morsel else None

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            files = {'/': ('index.html', 'text/html'), '/style.css': ('style.css', 'text/css'),
                     '/app.js': ('app.js', 'text/javascript'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if path in files:
                filename, mime = files[path]
                return self.reply(200, (ROOT / 'static' / filename).read_bytes(), mime + '; charset=utf-8')
            if not path.startswith('/api/'):
                return self.reply(404, {'error': 'Página no encontrada.'})
            user = session_user(self.session_token())
            if not user:
                return self.reply(401, {'error': 'Inicia sesión para continuar.'})
            if path == '/api/me':
                return self.reply(200, user)
            if path == '/api/hotels':
                with connection() as db:
                    rows = [dict(r) for r in db.execute('SELECT * FROM hotels ORDER BY id')]
            elif path == '/api/reservations':
                rows = list_reservations(user)
            elif path == '/api/services':
                rows = service_catalog()
            elif path == '/api/clients':
                rows = list_clients(user)
            elif path == '/api/audit':
                rows = list_audit(user)
            else:
                return self.reply(404, {'error': 'Página no encontrada.'})
            self.reply(200, rows)
        except AccessError as error:
            self.reply(error.status, {'error': str(error)})
        except Exception:
            # Nunca se muestran detalles internos al usuario.
            self.reply(500, {'error': 'Ocurrió un error inesperado. Inténtalo nuevamente.'})

    def do_POST(self):
        try:
            # Esta base es local; no acepta solicitudes de otros sitios.
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if host not in allowed or self.headers.get('Origin', 'http://' + host) != 'http://' + host:
                return self.reply(403, {'error': 'Origen no permitido.'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(415, {'error': 'Se requiere JSON.'})
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('El formulario está vacío o es demasiado grande.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Formulario inválido.')
            if self.path == '/api/login':
                token, user = login(data)
                return self.reply(200, {'user': user}, cookie=session_cookie(token, SESSION_HOURS * 3600))
            if self.path == '/api/register':
                token, user = register_client(data)
                return self.reply(200, {'user': user, 'message': 'Cuenta creada.'},
                                  cookie=session_cookie(token, SESSION_HOURS * 3600))
            if self.path == '/api/logout':
                logout(self.session_token())
                return self.reply(200, {'message': 'Sesión cerrada.'}, cookie=session_cookie('', 0))
            user = session_user(self.session_token())
            if not user:
                raise AccessError('Tu sesión expiró. Inicia sesión nuevamente.', 401)
            if self.path == '/api/reservations':
                result = create_reservation(data, user)
            elif self.path == '/api/reservation-services':
                result = update_reservation_services(int(data.get('id', 0)), data.get('services'), user)
            elif self.path == '/api/cancel':
                result = cancel_reservation(int(data.get('id', 0)), user)
            elif self.path == '/api/clients':
                _, client = register_client(data, creator=user)
                result = {'message': f'Cliente {client["name"]} registrado.', 'client': client}
            else:
                return self.reply(404, {'error': 'Operación no encontrada.'})
            self.reply(200, result)
        except AccessError as error:
            self.reply(error.status, {'error': str(error)})
        except (ValueError, TypeError, UnicodeDecodeError) as error:
            self.reply(400, {'error': str(error)})
        except sqlite3.Error:
            self.reply(409, {'error': 'No se pudo guardar la operación. Actualiza e inténtalo nuevamente.'})
        except Exception:
            self.reply(500, {'error': 'Ocurrió un error inesperado. Inténtalo nuevamente.'})


if __name__ == '__main__':
    import sys
    initialize()
    server = ThreadingHTTPServer(('127.0.0.1', 8080), Handler)
    print('Altamar: http://127.0.0.1:8080 — Ctrl+C para cerrar.', flush=True)
    if '--open' in sys.argv:
        webbrowser.open('http://127.0.0.1:8080')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
