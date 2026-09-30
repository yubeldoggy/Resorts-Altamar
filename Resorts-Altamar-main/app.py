"""Base local de Resorts Altamar. Solo requiere Python 3.11 o posterior."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import webbrowser
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / 'altamar.sqlite3'
# RF-05 / RNF-03: cada estación de trabajo declara el hotel físico donde está instalada.
STATION_FILE = ROOT / 'estacion.json'
# Tarifas de demostración por noche (CLP), según región. No son precios reales.
REGION_RATES = {'Norte': 85000, 'Centro': 95000, 'Sur': 90000, 'Austral': 110000}
# Servicios adicionales iniciales de cada hotel (RF-07). Precios de demostración.
DEFAULT_SERVICES = [('Spa', 45000), ('Tour guiado', 30000),
                    ('Servicio a la habitación', 15000), ('Programa de millas', 0)]
STAY_STATUSES = ('Confirmada', 'Alojado')
MAX_ADVANCE_DAYS = 730  # No se aceptan reservas con más de 2 años de anticipación.
REGISTER_LIMIT, REGISTER_WINDOW = 10, 600  # Registros públicos por equipo cada 10 minutos.
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


class InputError(ValueError):
    """Error causado por datos del usuario: su mensaje se muestra tal cual.

    Cualquier otro error (de Python o de la base) se responde con un mensaje genérico,
    para no revelar detalles internos."""


def to_int(value, message='Dato numérico no válido.', low=0, high=2**53):
    """Convierte un número entero enviado por el usuario, con límites. Rechaza decimales,
    booleanos, infinitos, notación científica y dígitos que no sean 0-9."""
    if isinstance(value, bool):
        raise InputError(message)
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r'-?[0-9]{1,16}', value.strip()):
        number = int(value.strip())
    else:
        raise InputError(message)
    if not low <= number <= high:
        raise InputError(message)
    return number


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
        raise InputError('Escribe un RUT válido, por ejemplo 12.345.678-5.')
    body, digit = ''.join(match.groups()[:3]).lstrip('0'), match.group(4)
    if len(body) < 7 or rut_check_digit(body) != digit:
        raise InputError('El RUT no es válido: revisa el dígito verificador.')
    return f'{body}-{digit}'


def clean_name(value):
    """Nombre de 2 a 80 caracteres: letras (incluye tildes y ñ), espacios, apóstrofo, guion o punto."""
    name = ' '.join(value.split()) if isinstance(value, str) else ''
    if (not 2 <= len(name) <= 80 or sum(c.isalpha() for c in name) < 2
            or not all(c.isalpha() or c in " '-." for c in name)):
        raise InputError('Escribe un nombre válido de 2 a 80 caracteres (solo letras, espacios, apóstrofo o guion).')
    return name


def check_password(password, rut=''):
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise InputError(f'La contraseña debe tener entre {PASSWORD_MIN} y {PASSWORD_MAX} caracteres.')
    if not any(c.isalpha() for c in password) or not any(c in '0123456789' for c in password):
        raise InputError('La contraseña debe combinar letras y números.')
    if password.lower() in COMMON_PASSWORDS or (rut and rut.split('-')[0] in password.replace('.', '')):
        raise InputError('Esa contraseña es muy fácil de adivinar (es común o contiene tu RUT). Elige otra.')
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


def start_session(db, user_id, station=None, station_ms=None):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec='seconds')
    db.execute('DELETE FROM sessions WHERE expires<=?', (now_text(),))
    db.execute('INSERT INTO sessions(token_hash,user_id,expires,hotel_id,station_ms) VALUES(?,?,?,?,?)',
               (token_hash(token), user_id, expires, station['id'] if station else None, station_ms))
    return token


def detect_station(db):
    """RF-05 / RNF-03: identifica el hotel físico de esta estación leyendo estacion.json.

    Devuelve el hotel (o None si no está configurado) y el tiempo que tomó, en milisegundos."""
    started = time.perf_counter()
    try:
        value = json.loads(STATION_FILE.read_text(encoding='utf-8')).get('hotel')
    except (OSError, ValueError, AttributeError):
        value = None
    station = None
    if value is not None:
        wanted = str(value).strip().casefold()
        for hotel in db.execute('SELECT id, name, region FROM hotels'):
            if wanted in (str(hotel['id']), hotel['name'].casefold(),
                          hotel['name'].casefold().removeprefix('altamar ')):
                station = dict(hotel)
                break
    return station, round((time.perf_counter() - started) * 1000, 2)


def local_hotel(user):
    """Hotel al que queda limitada la recepción: el de su estación (None = sin estación configurada)."""
    return (user.get('station') or {}).get('id') if user['role'] == 'recepcion' else None


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
            CREATE TABLE IF NOT EXISTS services(
                id INTEGER PRIMARY KEY, hotel_id INTEGER NOT NULL REFERENCES hotels(id),
                name TEXT NOT NULL, price INTEGER NOT NULL CHECK(price >= 0),
                active INTEGER NOT NULL DEFAULT 1, UNIQUE(hotel_id, name));
            CREATE TABLE IF NOT EXISTS reservation_services(
                id INTEGER PRIMARY KEY, reservation_id INTEGER NOT NULL REFERENCES reservations(id),
                service_id INTEGER NOT NULL REFERENCES services(id), name TEXT NOT NULL,
                price INTEGER NOT NULL, quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 20),
                added_by INTEGER REFERENCES users(id), at TEXT NOT NULL);
        ''')
        # Bases creadas por versiones anteriores: se agregan las columnas nuevas.
        def add_columns(table, new_columns):
            existing = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            for column, definition in new_columns:
                if column not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        add_columns('reservations', [('user_id', 'INTEGER REFERENCES users(id)'),
                                     ('created_by', 'INTEGER REFERENCES users(id)'),
                                     ('rate', 'INTEGER'), ('total', 'INTEGER')])
        add_columns('hotels', [('rate', 'INTEGER')])
        add_columns('sessions', [('hotel_id', 'INTEGER REFERENCES hotels(id)'), ('station_ms', 'REAL')])
        if not db.execute('SELECT 1 FROM hotels LIMIT 1').fetchone():
            regions = {
                'Norte': ['Arica', 'Iquique', 'Antofagasta', 'Caldera', 'La Serena'],
                'Centro': ['Concón', 'Viña del Mar', 'Valparaíso', 'Algarrobo', 'Pichilemu'],
                'Sur': ['Pucón', 'Villarrica', 'Valdivia', 'Puerto Varas', 'Frutillar'],
                'Austral': ['Castro', 'Chaitén', 'Coyhaique', 'Puerto Natales', 'Punta Arenas'],
            }
            db.executemany('INSERT INTO hotels(name,region) VALUES(?,?)',
                           [('Altamar ' + city, region) for region, cities in regions.items() for city in cities])
        db.executemany('UPDATE hotels SET rate=? WHERE rate IS NULL AND region=?',
                       [(rate, region) for region, rate in REGION_RATES.items()])
        db.execute('UPDATE reservations SET rate=(SELECT rate FROM hotels h WHERE h.id=hotel_id) WHERE rate IS NULL')
        if not db.execute('SELECT 1 FROM services LIMIT 1').fetchone():
            db.executemany('INSERT INTO services(hotel_id,name,price) VALUES(?,?,?)',
                           [(h[0], name, price) for h in db.execute('SELECT id FROM hotels').fetchall()
                            for name, price in DEFAULT_SERVICES])
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
                station, station_ms = (None, None)
                if user['role'] in ('recepcion', 'gerente'):
                    station, station_ms = detect_station(db)
                token = start_session(db, user['id'], station, station_ms)
                audit(db, 'inicio_sesion', user['id'],
                      f"Estación: {station['name']} ({station_ms} ms)" if station else '')
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
    return token, session_user(token)


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
            raise InputError('Ya existe una cuenta con ese RUT.')
        if creator:
            audit(db, 'cliente_creado', creator['id'], f'{rut} · {name}')
            token = None
        else:
            audit(db, 'registro_cliente', uid, rut)
            token = start_session(db, uid)
    return token, {'id': uid, 'rut': rut, 'name': name, 'role': 'cliente', 'station': None}


_register_log, _register_lock = {}, threading.Lock()


def allow_registration(ip, now=None):
    """Limita los registros públicos por equipo: frena la creación masiva de cuentas
    y el uso del registro para averiguar qué RUT ya tienen cuenta."""
    now = time.monotonic() if now is None else now
    with _register_lock:
        recent = [t for t in _register_log.get(ip, []) if now - t < REGISTER_WINDOW]
        allowed = len(recent) < REGISTER_LIMIT
        if allowed:
            recent.append(now)
        _register_log[ip] = recent
    return allowed


def session_user(token):
    if not token:
        return None
    with connection() as db:
        row = db.execute('''SELECT u.id, u.rut, u.name, u.role, s.station_ms,
            h.id station_id, h.name station_name, h.region station_region
            FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN hotels h ON h.id=s.hotel_id
            WHERE s.token_hash=? AND s.expires>?''', (token_hash(token), now_text())).fetchone()
    if not row:
        return None
    user = {k: row[k] for k in ('id', 'rut', 'name', 'role')}
    user['station'] = ({'id': row['station_id'], 'name': row['station_name'],
                        'region': row['station_region'], 'ms': row['station_ms']}
                       if row['station_id'] else None)
    return user


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

class NoAvailability(InputError):
    def __init__(self, alternatives):
        super().__init__('No quedan habitaciones para esas fechas. Prueba otro hotel de la misma región.')
        self.alternatives = alternatives


def available_room(db, hotel, arrival, departure):
    occupied = {r[0] for r in db.execute(
        'SELECT DISTINCT room FROM nights WHERE hotel_id=? AND night>=? AND night<?',
        (hotel, arrival, departure))}
    return next((n for n in range(1, 6) if n not in occupied), None)


def alternative_hotels(db, hotel, arrival, departure):
    return [dict(h) for h in db.execute('''SELECT id,name,region FROM hotels
        WHERE region=(SELECT region FROM hotels WHERE id=?) AND id<>? ORDER BY id''', (hotel, hotel))
        if available_room(db, h['id'], arrival, departure) is not None]


def check_stay(arrival, departure):
    if arrival < date.today() or not 1 <= (departure - arrival).days <= 60:
        raise InputError('La llegada debe ser hoy o después y la estadía debe durar entre 1 y 60 noches.')
    if arrival > date.today() + timedelta(days=MAX_ADVANCE_DAYS):
        raise InputError('Solo se aceptan reservas con hasta 2 años de anticipación.')


def reservation_for_user(db, rid, user):
    row = db.execute('SELECT * FROM reservations WHERE id=?', (rid,)).fetchone()
    # Un cliente solo accede a sus reservas; recepción, solo a las del hotel de su estación.
    if (not row or (user['role'] == 'cliente' and row['user_id'] != user['id'])
            or (local_hotel(user) and row['hotel_id'] != local_hotel(user))):
        raise InputError('La reserva no existe.')
    return row


def clp(amount):
    return '$' + f'{amount:,}'.replace(',', '.')


def folio_data(db, row):
    """RF-06: cuenta de la estadía = noches × tarifa + servicios contratados."""
    nights = (date.fromisoformat(row['departure']) - date.fromisoformat(row['arrival'])).days
    rate = row['rate'] if row['rate'] is not None else db.execute(
        'SELECT rate FROM hotels WHERE id=?', (row['hotel_id'],)).fetchone()[0]
    lines = [dict(r) for r in db.execute('''SELECT id, name, price, quantity, price*quantity subtotal
        FROM reservation_services WHERE reservation_id=? ORDER BY id''', (row['id'],))]
    lodging = nights * rate
    services_total = sum(line['subtotal'] for line in lines)
    return {'id': row['id'], 'status': row['status'], 'hotel_id': row['hotel_id'], 'nights': nights,
            'rate': rate, 'lodging': lodging, 'services': lines, 'services_total': services_total,
            'total': lodging + services_total, 'charged': row['total']}


def modify_dates(rid, data, user):
    try:
        arrival, departure = date.fromisoformat(data['arrival']), date.fromisoformat(data['departure'])
    except (KeyError, ValueError, TypeError):
        raise InputError('Selecciona fechas válidas.')
    check_stay(arrival, departure)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = reservation_for_user(db, rid, user)
        if row['status'] != 'Confirmada':
            raise InputError('Solo se pueden modificar reservas confirmadas.')
        # Si no hay cupo, el rollback conserva la reserva y sus noches originales.
        db.execute('DELETE FROM nights WHERE reservation_id=?', (rid,))
        room = available_room(db, row['hotel_id'], arrival.isoformat(), departure.isoformat())
        if room is None:
            raise NoAvailability(alternative_hotels(db, row['hotel_id'], arrival.isoformat(), departure.isoformat()))
        db.execute('UPDATE reservations SET arrival=?,departure=?,room=? WHERE id=?',
                   (arrival.isoformat(), departure.isoformat(), room, rid))
        db.executemany('INSERT INTO nights VALUES(?,?,?,?)',
                       [(row['hotel_id'], room, (arrival + timedelta(days=i)).isoformat(), rid)
                        for i in range((departure - arrival).days)])
        audit(db, 'fechas_modificadas', user['id'], f'ALT-{rid:04d}')
    return {'message': 'Fechas actualizadas. Habitación ' + str(room) + '.'}


def reception_action(rid, action, user):
    if user['role'] not in ('recepcion', 'gerente'):
        raise AccessError('Solo el personal puede registrar entradas y salidas.')
    transitions = {'checkin': ('Confirmada', 'Alojado'), 'checkout': ('Alojado', 'Finalizada')}
    if action not in transitions:
        raise InputError('Operación no válida.')
    expected, new_status = transitions[action]
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = reservation_for_user(db, rid, user)
        if row['status'] != expected:
            raise InputError('El estado actual no permite esta operación.')
        if action == 'checkin':
            if not row['arrival'] <= date.today().isoformat() < row['departure']:
                raise InputError('El check-in debe realizarse durante las fechas de la estadía.')
            if db.execute("SELECT 1 FROM reservations WHERE hotel_id=? AND room=? AND status='Alojado'",
                          (row['hotel_id'], row['room'])).fetchone():
                raise InputError('La habitación aún tiene un huésped alojado. Registra su salida primero.')
        db.execute('UPDATE reservations SET status=? WHERE id=?', (new_status, rid))
        if action == 'checkin':
            audit(db, action, user['id'], f'ALT-{rid:04d}')
            return {'message': 'Check-in registrado.'}
        # Check-out: se calcula y guarda el cobro final, y se liberan los cupos.
        folio = folio_data(db, row)
        db.execute('UPDATE reservations SET total=? WHERE id=?', (folio['total'], rid))
        db.execute('DELETE FROM nights WHERE reservation_id=?', (rid,))
        audit(db, action, user['id'], f'ALT-{rid:04d} · cobro {clp(folio["total"])}')
    nights = f'{folio["nights"]} noche' + ('' if folio['nights'] == 1 else 's')
    return {'message': f'Check-out registrado. Total cobrado: {clp(folio["total"])} '
                       f'({nights}: {clp(folio["lodging"])} + servicios: {clp(folio["services_total"])}). '
                       'Habitación liberada.', 'total': folio['total']}


def list_reservations(user):
    query = '''SELECT r.id, r.hotel_id, r.guest, r.arrival, r.departure, r.room, r.status, r.user_id,
        r.total, h.name hotel, h.region FROM reservations r JOIN hotels h ON h.id=r.hotel_id'''
    params = ()
    if user['role'] == 'cliente':
        query, params = query + ' WHERE r.user_id=?', (user['id'],)
    elif local_hotel(user):
        # RF-05: la recepción ve los datos locales del hotel de su estación.
        query, params = query + ' WHERE r.hotel_id=?', (local_hotel(user),)
    with connection() as db:
        return [dict(r) for r in db.execute(query + ' ORDER BY r.id DESC', params)]


def create_reservation(data, user):
    if user['role'] not in ('cliente', 'recepcion'):
        raise AccessError('Tu rol no permite crear reservas.')
    client_id, guest = None, None
    if user['role'] == 'cliente':
        guest = user['name']  # El cliente siempre reserva a su nombre.
    elif data.get('client_id') not in (None, ''):
        try:
            client_id = to_int(data['client_id'], 'Selecciona un cliente válido.')
        except (TypeError, ValueError):
            raise InputError('Selecciona un cliente válido.')
    else:
        guest = clean_name(data.get('guest', ''))
    try:
        # RF-05: la recepción reserva en el hotel de su estación, aunque el formulario diga otro.
        hotel = local_hotel(user) or to_int(data['hotel_id'], 'Selecciona un hotel válido.')
        arrival, departure = date.fromisoformat(data['arrival']), date.fromisoformat(data['departure'])
    except (KeyError, ValueError, TypeError):
        raise InputError('Selecciona un hotel y fechas válidas.')
    check_stay(arrival, departure)
    with connection() as db:
        # El bloqueo precede a la consulta: dos solicitudes no pueden tomar el mismo cupo.
        db.execute('BEGIN IMMEDIATE')
        owner = user['id']
        if client_id is not None:
            client = db.execute("SELECT id, name FROM users WHERE id=? AND role='cliente'", (client_id,)).fetchone()
            if not client:
                raise InputError('El cliente no existe.')
            owner, guest = client['id'], client['name']
        hotel_row = db.execute('SELECT name FROM hotels WHERE id=?', (hotel,)).fetchone()
        if not hotel_row:
            raise InputError('El hotel no existe.')
        room = available_room(db, hotel, arrival.isoformat(), departure.isoformat())
        if room is None:
            raise NoAvailability(alternative_hotels(db, hotel, arrival.isoformat(), departure.isoformat()))
        # La tarifa se copia al reservar: un cambio de precio posterior no altera esta reserva.
        rid = db.execute('''INSERT INTO reservations(hotel_id,guest,arrival,departure,room,user_id,created_by,rate)
            VALUES(?,?,?,?,?,?,?,(SELECT rate FROM hotels WHERE id=?))''',
                         (hotel, guest, arrival.isoformat(), departure.isoformat(),
                          room, owner, user['id'], hotel)).lastrowid
        db.executemany('INSERT INTO nights VALUES(?,?,?,?)',
                       [(hotel, room, (arrival + timedelta(days=i)).isoformat(), rid)
                        for i in range((departure - arrival).days)])
        audit(db, 'reserva_creada', user['id'], f'ALT-{rid:04d} · {hotel_row["name"]} · {guest}')
        return {'message': f'Reserva ALT-{rid:04d} creada. Habitación {room}.', 'id': rid}


def cancel_reservation(rid, user):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = reservation_for_user(db, rid, user)
        if row['status'] != 'Confirmada':
            raise InputError('Solo se pueden cancelar reservas confirmadas.')
        db.execute("UPDATE reservations SET status='Cancelada' WHERE id=?", (rid,))
        db.execute('DELETE FROM nights WHERE reservation_id=?', (rid,))
        audit(db, 'reserva_cancelada', user['id'], f'ALT-{rid:04d}')
    return {'message': 'Reserva cancelada. La habitación vuelve a estar disponible.'}


# ---------- Servicios adicionales (RF-07) y cuenta (RF-06) ----------
# Los servicios viven en la base de datos: se crean, cambian o retiran con la app funcionando,
# sin modificar el código ni detener el sistema central.

def clean_service_name(value):
    name = ' '.join(value.split()) if isinstance(value, str) else ''
    if not 2 <= len(name) <= 60 or not all(c.isalnum() or c in " '-.,()/&" for c in name):
        raise InputError('Escribe un nombre de servicio de 2 a 60 caracteres (letras, números y signos simples).')
    return name


def clean_price(value):
    return to_int(value, 'El precio debe ser un número entero entre 0 y 5.000.000 pesos, sin puntos.', 0, 5_000_000)


def list_services(user, hotel_id):
    with connection() as db:
        if not db.execute('SELECT 1 FROM hotels WHERE id=?', (hotel_id,)).fetchone():
            raise InputError('El hotel no existe.')
        # Gerencia ve también los servicios retirados, para poder reactivarlos.
        active = '' if user['role'] == 'gerente' else ' AND active=1'
        return [dict(r) for r in db.execute(
            f'SELECT id, hotel_id, name, price, active FROM services WHERE hotel_id=?{active} ORDER BY name',
            (hotel_id,))]


def create_service(data, user):
    if user['role'] != 'gerente':
        raise AccessError('Solo gerencia configura los servicios adicionales.')
    try:
        hotel_id = to_int(data.get('hotel_id'), 'Selecciona un hotel válido.')
    except (TypeError, ValueError):
        raise InputError('Selecciona un hotel válido.')
    name, price = clean_service_name(data.get('name')), clean_price(data.get('price'))
    with connection() as db:
        hotel = db.execute('SELECT name FROM hotels WHERE id=?', (hotel_id,)).fetchone()
        if not hotel:
            raise InputError('El hotel no existe.')
        try:
            sid = db.execute('INSERT INTO services(hotel_id,name,price) VALUES(?,?,?)',
                             (hotel_id, name, price)).lastrowid
        except sqlite3.IntegrityError:
            raise InputError('Ese hotel ya tiene un servicio con ese nombre.')
        audit(db, 'servicio_creado', user['id'], f'{hotel["name"]} · {name} · {clp(price)}')
    return {'message': f'Servicio “{name}” agregado.', 'id': sid}


def update_service(data, user):
    """Configurar (nombre y precio) o retirar/reactivar un servicio."""
    if user['role'] != 'gerente':
        raise AccessError('Solo gerencia configura los servicios adicionales.')
    try:
        sid = to_int(data.get('id'), 'Servicio no válido.')
    except (TypeError, ValueError):
        raise InputError('Servicio no válido.')
    with connection() as db:
        service = db.execute('''SELECT s.*, h.name hotel FROM services s JOIN hotels h ON h.id=s.hotel_id
            WHERE s.id=?''', (sid,)).fetchone()
        if not service:
            raise InputError('El servicio no existe.')
        name = clean_service_name(data['name']) if 'name' in data else service['name']
        price = clean_price(data['price']) if 'price' in data else service['price']
        active = (1 if data['active'] in (True, 1, '1', 'true') else 0) if 'active' in data else service['active']
        try:
            db.execute('UPDATE services SET name=?, price=?, active=? WHERE id=?', (name, price, active, sid))
        except sqlite3.IntegrityError:
            raise InputError('Ese hotel ya tiene un servicio con ese nombre.')
        action = ('servicio_retirado' if service['active'] and not active else
                  'servicio_reactivado' if active and not service['active'] else 'servicio_actualizado')
        audit(db, action, user['id'], f'{service["hotel"]} · {name} · {clp(price)}')
    return {'message': 'Servicio retirado.' if action == 'servicio_retirado' else 'Servicio actualizado.'}


def folio(rid, user):
    with connection() as db:
        return folio_data(db, reservation_for_user(db, rid, user))


def add_reservation_service(data, user):
    """RF-04 / RF-07: el cliente (o el personal) contrata un servicio para una estadía."""
    try:
        rid = to_int(data.get('reservation_id'), 'Reserva no válida.')
        sid = to_int(data.get('service_id'), 'Selecciona un servicio válido.')
        quantity = to_int(data.get('quantity', 1), 'La cantidad debe estar entre 1 y 20.', 1, 20)
    except (TypeError, ValueError):
        raise InputError('Selecciona un servicio válido.')
    if not 1 <= quantity <= 20:
        raise InputError('La cantidad debe estar entre 1 y 20.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = reservation_for_user(db, rid, user)
        if row['status'] not in STAY_STATUSES:
            raise InputError('Solo se pueden contratar servicios en reservas confirmadas o alojadas.')
        service = db.execute('SELECT * FROM services WHERE id=? AND hotel_id=? AND active=1',
                             (sid, row['hotel_id'])).fetchone()
        if not service:
            raise InputError('Ese servicio no está disponible en este hotel.')
        # El precio se copia al contratar: si gerencia lo cambia después, la cuenta no se altera.
        db.execute('''INSERT INTO reservation_services(reservation_id,service_id,name,price,quantity,added_by,at)
            VALUES(?,?,?,?,?,?,?)''', (rid, sid, service['name'], service['price'], quantity, user['id'], now_text()))
        audit(db, 'servicio_contratado', user['id'], f'ALT-{rid:04d} · {service["name"]} × {quantity}')
    return {'message': f'{service["name"]} agregado a la cuenta.'}


def remove_reservation_service(data, user):
    try:
        line_id = to_int(data.get('id'), 'Servicio no válido.')
    except (TypeError, ValueError):
        raise InputError('Servicio no válido.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        line = db.execute('SELECT * FROM reservation_services WHERE id=?', (line_id,)).fetchone()
        if not line:
            raise InputError('El servicio no existe.')
        row = reservation_for_user(db, line['reservation_id'], user)
        if row['status'] not in STAY_STATUSES:
            raise InputError('La cuenta de esta reserva ya está cerrada.')
        db.execute('DELETE FROM reservation_services WHERE id=?', (line_id,))
        audit(db, 'servicio_quitado', user['id'], f'ALT-{row["id"]:04d} · {line["name"]}')
    return {'message': f'{line["name"]} quitado de la cuenta.'}


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

    def not_allowed(self):
        self.reply(405, {'error': 'Método no permitido.'})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_HEAD = not_allowed

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
            elif path == '/api/clients':
                rows = list_clients(user)
            elif path == '/api/audit':
                rows = list_audit(user)
            elif path in ('/api/services', '/api/folio'):
                query = parse_qs(urlsplit(self.path).query)
                key = 'hotel_id' if path == '/api/services' else 'id'
                try:
                    number = to_int(query.get(key, [''])[0], 'Solicitud no válida.')
                except ValueError:
                    raise InputError('Solicitud no válida.')
                rows = list_services(user, number) if path == '/api/services' else folio(number, user)
            else:
                return self.reply(404, {'error': 'Página no encontrada.'})
            self.reply(200, rows)
        except AccessError as error:
            self.reply(error.status, {'error': str(error)})
        except InputError as error:
            self.reply(400, {'error': str(error)})
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self.reply(400, {'error': 'Los datos enviados no son válidos.'})
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
            length = to_int(self.headers.get('Content-Length', '0'), 'El formulario está vacío o es demasiado grande.')
            if not 0 < length <= 4096:
                raise InputError('El formulario está vacío o es demasiado grande.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise InputError('Formulario inválido.')
            if self.path == '/api/login':
                token, user = login(data)
                return self.reply(200, {'user': user}, cookie=session_cookie(token, SESSION_HOURS * 3600))
            if self.path == '/api/register':
                if not allow_registration(self.client_address[0]):
                    raise AccessError('Demasiados intentos de registro desde este equipo. Espera 10 minutos.', 429)
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
            elif self.path == '/api/cancel':
                result = cancel_reservation(to_int(data.get('id', 0), 'Reserva no válida.'), user)
            elif self.path == '/api/modify-dates':
                result = modify_dates(to_int(data.get('id', 0), 'Reserva no válida.'), data, user)
            elif self.path in ('/api/checkin', '/api/checkout'):
                result = reception_action(to_int(data.get('id', 0), 'Reserva no válida.'), self.path.rsplit('/', 1)[1], user)
            elif self.path == '/api/clients':
                _, client = register_client(data, creator=user)
                result = {'message': f'Cliente {client["name"]} registrado.', 'client': client}
            elif self.path == '/api/services':
                result = create_service(data, user)
            elif self.path == '/api/services/update':
                result = update_service(data, user)
            elif self.path == '/api/reservation-services':
                result = add_reservation_service(data, user)
            elif self.path == '/api/reservation-services/remove':
                result = remove_reservation_service(data, user)
            else:
                return self.reply(404, {'error': 'Operación no encontrada.'})
            self.reply(200, result)
        except AccessError as error:
            self.reply(error.status, {'error': str(error)})
        except NoAvailability as error:
            self.reply(409, {'error': str(error), 'alternatives': error.alternatives})
        except InputError as error:
            self.reply(400, {'error': str(error)})
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            # Errores de Python (JSON mal formado, tipos inesperados…): mensaje genérico.
            self.reply(400, {'error': 'Los datos enviados no son válidos.'})
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
