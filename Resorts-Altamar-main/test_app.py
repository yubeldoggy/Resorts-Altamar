import concurrent.futures
import http.client
import json
import threading
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import app


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = app.DATABASE, app.PBKDF2_ITERATIONS, app.STATION_FILE
        app.DATABASE = Path(self.temp.name) / 'test.sqlite3'
        app.PBKDF2_ITERATIONS = 1000  # Solo para que las pruebas sean rápidas.
        # Estación válida por defecto: Arica.
        app.STATION_FILE = Path(self.temp.name) / 'estacion.json'
        app.STATION_FILE.write_text('{"hotel": "1"}', encoding='utf-8')
        app.initialize()
        self.users = {role: self.user(rut, password) for rut, _, role, password in app.TEST_ACCOUNTS}
        self.data = {'guest': 'Cliente de prueba', 'hotel_id': 1,
                     'arrival': date.today().isoformat(),
                     'departure': (date.today() + timedelta(days=2)).isoformat(),
                     'adults': 2, 'children': 1, 'phone': '+56 9 1234 5678', 'email': 'huesped@correo.cl'}

    def tearDown(self):
        app.DATABASE, app.PBKDF2_ITERATIONS, app.STATION_FILE = self.original
        self.temp.cleanup()

    def user(self, rut, password):
        return app.session_user(app.login({'rut': rut, 'password': password})[0])

    def actions(self):
        with app.connection() as db:
            return [r[0] for r in db.execute('SELECT action FROM audit ORDER BY id')]


class ReservationTests(BaseTest):
    def test_twenty_hotels(self):
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hotels').fetchone()[0], 20)

    def test_create_and_cancel_releases_inventory(self):
        rid = app.create_reservation(self.data, self.users['recepcion'])['id']
        app.cancel_reservation(rid, self.users['recepcion'])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nights').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM reservations').fetchone()[0], 'Cancelada')

    def test_concurrent_requests_cannot_overbook(self):
        def attempt(_):
            try:
                app.create_reservation(self.data, self.users['recepcion'])
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(results), 5)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nights').fetchone()[0], 10)

    def test_departure_day_is_available(self):
        for _ in range(5):
            app.create_reservation(self.data, self.users['recepcion'])
        following = {**self.data, 'arrival': self.data['departure'],
                     'departure': (date.today() + timedelta(days=3)).isoformat()}
        self.assertTrue(app.create_reservation(following, self.users['recepcion'])['id'])

    def test_invalid_dates_hotel_and_guest(self):
        for data in [{**self.data, 'departure': self.data['arrival']},
                     {**self.data, 'hotel_id': 999}, {**self.data, 'guest': ''},
                     {**self.data, 'guest': '<script>alert(1)</script>'}]:
            with self.assertRaises(ValueError):
                app.create_reservation(data, self.users['cliente'] if data['hotel_id'] == 999 else self.users['recepcion'])


class ReceptionTests(BaseTest):
    def test_modify_dates_replaces_nights(self):
        rid = app.create_reservation(self.data, self.users['cliente'])['id']
        changed = {**self.data, 'departure': (date.today() + timedelta(days=3)).isoformat()}
        app.modify_dates(rid, changed, self.users['cliente'])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nights WHERE reservation_id=?', (rid,)).fetchone()[0], 3)
        self.assertEqual(app.list_reservations(self.users['cliente'])[0]['departure'], changed['departure'])

    def test_failed_modification_preserves_original(self):
        rid = app.create_reservation(self.data, self.users['cliente'])['id']
        original = app.list_reservations(self.users['cliente'])[0]
        future = {**self.data, 'arrival': self.data['departure'],
                  'departure': (date.today() + timedelta(days=4)).isoformat()}
        for _ in range(5):
            app.create_reservation(future, self.users['recepcion'])
        with self.assertRaises(app.NoAvailability):
            app.modify_dates(rid, future, self.users['cliente'])
        self.assertEqual(app.list_reservations(self.users['cliente'])[0], original)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nights WHERE reservation_id=?', (rid,)).fetchone()[0], 2)

    def test_alternatives_are_available_and_same_region(self):
        for hotel in (1, 2):
            for _ in range(5):
                app.create_reservation({**self.data, 'hotel_id': hotel}, self.users['cliente'])
        with self.assertRaises(app.NoAvailability) as error:
            app.create_reservation(self.data, self.users['cliente'])
        self.assertEqual({h['id'] for h in error.exception.alternatives}, {3, 4, 5})
        self.assertTrue(all(h['region'] == 'Norte' for h in error.exception.alternatives))

    def test_no_regional_alternative(self):
        for hotel in range(1, 6):
            for _ in range(5):
                app.create_reservation({**self.data, 'hotel_id': hotel}, self.users['cliente'])
        with self.assertRaises(app.NoAvailability) as error:
            app.create_reservation(self.data, self.users['cliente'])
        self.assertEqual(error.exception.alternatives, [])

    def test_reception_cycle_and_closed_reservations(self):
        rid = app.create_reservation(self.data, self.users['cliente'])['id']
        staff = self.users['recepcion']
        with self.assertRaises(ValueError):
            app.reception_action(rid, 'checkout', staff)
        app.reception_action(rid, 'checkin', staff)
        self.assertEqual(app.list_reservations(staff)[0]['status'], 'Alojado')
        with self.assertRaises(ValueError):
            app.cancel_reservation(rid, staff)
        with self.assertRaises(ValueError):
            app.modify_dates(rid, self.data, staff)
        app.reception_action(rid, 'checkout', staff)
        self.assertEqual(app.list_reservations(staff)[0]['status'], 'Finalizada')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nights').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            app.reception_action(rid, 'checkin', staff)

    def test_client_permissions(self):
        rid = app.create_reservation(self.data, self.users['recepcion'])['id']
        with self.assertRaises(ValueError):
            app.modify_dates(rid, self.data, self.users['cliente'])
        own = app.create_reservation(self.data, self.users['cliente'])['id']
        for action in ('checkin', 'checkout'):
            with self.assertRaises(app.AccessError):
                app.reception_action(own, action, self.users['cliente'])

    def test_future_checkin_rejected(self):
        future = {**self.data, 'arrival': (date.today() + timedelta(days=1)).isoformat()}
        rid = app.create_reservation(future, self.users['cliente'])['id']
        with self.assertRaises(ValueError):
            app.reception_action(rid, 'checkin', self.users['recepcion'])

    def test_cancelled_reservation_cannot_be_modified(self):
        rid = app.create_reservation(self.data, self.users['cliente'])['id']
        app.cancel_reservation(rid, self.users['cliente'])
        with self.assertRaises(ValueError):
            app.modify_dates(rid, self.data, self.users['cliente'])


class StationTests(BaseTest):
    """RF-05 / RNF-03: la estación identifica su hotel y adapta los datos de recepción."""

    def set_station(self, value):
        app.STATION_FILE.write_text('{"hotel": "%s"}' % value, encoding='utf-8')
        return {role: self.user(rut, password) for rut, _, role, password in app.TEST_ACCOUNTS}

    def test_station_detected_fast_for_staff_only(self):
        users = self.set_station('Altamar Pucón')
        for role in ('recepcion', 'gerente'):
            station = users[role]['station']
            self.assertEqual(station['name'], 'Altamar Pucón')
            self.assertLess(station['ms'], 1000)  # RNF-03: menos de 1 segundo.
        self.assertIsNone(users['cliente']['station'])

    def test_station_accepts_id_or_city(self):
        self.assertEqual(self.set_station('11')['recepcion']['station']['name'], 'Altamar Pucón')
        self.assertEqual(self.set_station('valdivia')['recepcion']['station']['name'], 'Altamar Valdivia')
        with self.assertRaises(app.AccessError):
            self.set_station('Hotel inexistente')

    def test_reception_works_only_with_local_hotel(self):
        other = app.create_reservation(self.data, self.users['recepcion'])['id']  # Arica, sin estación.
        users = self.set_station('Altamar Pucón')
        reception = users['recepcion']
        local = app.create_reservation({**self.data, 'hotel_id': 1}, reception)['id']
        rows = app.list_reservations(reception)
        self.assertEqual([r['id'] for r in rows], [local])
        self.assertEqual(rows[0]['hotel'], 'Altamar Pucón')  # El hotel lo fija la estación.
        for action in (lambda: app.cancel_reservation(other, reception),
                       lambda: app.reception_action(other, 'checkin', reception),
                       lambda: app.folio(other, reception)):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(len(app.list_reservations(users['gerente'])), 2)  # Gerencia ve la cadena.


class ServiceTests(BaseTest):
    """RF-06 (cobro), RF-07 (servicios por hotel) y RF-04 (contratar servicios)."""

    def services(self, hotel=1, role='gerente'):
        return {s['name']: s for s in app.list_services(self.users[role], hotel)}

    def test_default_services_and_rates(self):
        self.assertEqual(set(self.services()), {'Spa', 'Tour guiado', 'Servicio a la habitación', 'Programa de millas'})
        rid = app.create_reservation(self.data, self.users['cliente'])['id']
        folio = app.folio(rid, self.users['cliente'])
        self.assertEqual((folio['nights'], folio['rate'], folio['total']), (2, 85000, 170000))

    def test_manager_creates_configures_and_retires(self):
        manager = self.users['gerente']
        sid = app.create_service({'hotel_id': 1, 'name': 'Kayak', 'price': '20000'}, manager)['id']
        app.update_service({'id': sid, 'price': 25000}, manager)
        self.assertEqual(self.services()['Kayak']['price'], 25000)
        app.update_service({'id': sid, 'active': False}, manager)
        self.assertNotIn('Kayak', self.services(role='cliente'))  # Retirado: ya no se ofrece.
        self.assertIn('Kayak', self.services())  # Gerencia lo sigue viendo para reactivarlo.
        for bad in ({'hotel_id': 1, 'name': 'Kayak', 'price': 1}, {'hotel_id': 1, 'name': 'X', 'price': 1},
                    {'hotel_id': 1, 'name': 'Buceo', 'price': '-5'}, {'hotel_id': 99, 'name': 'Buceo', 'price': 1}):
            with self.assertRaises(ValueError):
                app.create_service(bad, manager)

    def test_only_manager_configures(self):
        for role in ('cliente', 'recepcion'):
            with self.assertRaises(app.AccessError):
                app.create_service({'hotel_id': 1, 'name': 'Kayak', 'price': 1}, self.users[role])
            with self.assertRaises(app.AccessError):
                app.update_service({'id': 1, 'active': False}, self.users[role])

    def test_client_contracts_and_checkout_charges(self):
        client, staff = self.users['cliente'], self.users['recepcion']
        rid = app.create_reservation(self.data, client)['id']
        spa = self.services(role='cliente')['Spa']
        app.add_reservation_service({'reservation_id': rid, 'service_id': spa['id'], 'quantity': 2}, client)
        app.update_service({'id': spa['id'], 'price': 99999}, self.users['gerente'])  # No altera lo contratado.
        self.assertEqual(app.folio(rid, client)['total'], 170000 + 90000)
        app.reception_action(rid, 'checkin', staff)
        result = app.reception_action(rid, 'checkout', staff)
        self.assertEqual(result['total'], 260000)
        self.assertEqual(app.list_reservations(client)[0]['total'], 260000)
        with self.assertRaises(ValueError):  # Cuenta cerrada.
            app.add_reservation_service({'reservation_id': rid, 'service_id': spa['id']}, client)
        self.assertIn('servicio_contratado', self.actions())

    def test_service_rules(self):
        client = self.users['cliente']
        rid = app.create_reservation(self.data, client)['id']
        other_hotel = self.services(hotel=2)['Spa']['id']
        with self.assertRaises(ValueError):  # Servicio de otro hotel.
            app.add_reservation_service({'reservation_id': rid, 'service_id': other_hotel}, client)
        foreign = app.create_reservation(self.data, self.users['recepcion'])['id']
        spa = self.services()['Spa']['id']
        with self.assertRaises(ValueError):  # Reserva ajena.
            app.add_reservation_service({'reservation_id': foreign, 'service_id': spa}, client)
        app.add_reservation_service({'reservation_id': rid, 'service_id': spa}, client)
        line = app.folio(rid, client)['services'][0]['id']
        app.remove_reservation_service({'id': line}, client)
        self.assertEqual(app.folio(rid, client)['services'], [])


class AccessTests(BaseTest):
    def test_rut_validation(self):
        for valid in ['11.111.111-1', '11111111-1', '111111111', '12.345.678-5', ' 22.222.222-2 ']:
            self.assertRegex(app.clean_rut(valid), r'^\d{7,8}-[0-9K]$')
        for invalid in ['11.111.111-2', '12.345.678-K', 'abc', '', '1-9', None, '11.111.111-1-1']:
            with self.assertRaises(ValueError):
                app.clean_rut(invalid)

    def test_passwords_are_stored_hashed(self):
        with app.connection() as db:
            stored = [r[0] for r in db.execute('SELECT password_hash FROM users')]
        self.assertEqual(len(stored), 3)
        for value, (_, _, _, password) in zip(stored, app.TEST_ACCOUNTS):
            self.assertNotIn(password, value)
            self.assertTrue(value.startswith('pbkdf2_sha256$'))
        self.assertEqual(len(set(stored)), 3)

    def test_three_roles(self):
        self.assertEqual({u['role'] for u in self.users.values()}, {'cliente', 'recepcion', 'gerente'})

    def test_wrong_password_and_unknown_rut_are_rejected(self):
        for rut, password in [('11.111.111-1', 'incorrecta'), ('12.345.678-5', 'Cliente#2026')]:
            with self.assertRaises(app.AccessError) as error:
                app.login({'rut': rut, 'password': password})
            self.assertEqual(error.exception.status, 401)

    def test_logout_invalidates_session(self):
        token, _ = app.login({'rut': '11111111-1', 'password': 'Cliente#2026'})
        self.assertIsNotNone(app.session_user(token))
        app.logout(token)
        self.assertIsNone(app.session_user(token))
        self.assertIsNone(app.session_user('token-inventado'))

    def test_account_locks_after_five_failures(self):
        for _ in range(app.MAX_FAILURES - 1):
            with self.assertRaises(app.AccessError) as error:
                app.login({'rut': '11.111.111-1', 'password': 'incorrecta'})
            self.assertEqual(error.exception.status, 401)
        for password in ['incorrecta', 'Cliente#2026']:  # Bloqueada incluso con la clave correcta.
            with self.assertRaises(app.AccessError) as error:
                app.login({'rut': '11.111.111-1', 'password': password})
            self.assertEqual(error.exception.status, 429)
        with app.connection() as db:
            db.execute("UPDATE login_attempts SET locked_until='2000-01-01T00:00:00'")
        self.assertTrue(app.login({'rut': '11.111.111-1', 'password': 'Cliente#2026'})[0])

    def test_unknown_rut_also_locks(self):
        for _ in range(app.MAX_FAILURES):
            with self.assertRaises(app.AccessError):
                app.login({'rut': '12.345.678-5', 'password': 'loquesea1'})
        with self.assertRaises(app.AccessError) as error:
            app.login({'rut': '12.345.678-5', 'password': 'loquesea1'})
        self.assertEqual(error.exception.status, 429)

    def test_client_only_sees_and_cancels_own_reservations(self):
        other = app.create_reservation(self.data, self.users['recepcion'])['id']
        self.assertEqual(app.list_reservations(self.users['cliente']), [])
        with self.assertRaises(ValueError):
            app.cancel_reservation(other, self.users['cliente'])
        own = app.create_reservation({**self.data, 'guest': 'Otro nombre'}, self.users['cliente'])['id']
        rows = app.list_reservations(self.users['cliente'])
        self.assertEqual([r['id'] for r in rows], [own])
        self.assertEqual(rows[0]['guest'], 'Cliente de prueba')
        app.cancel_reservation(own, self.users['cliente'])

    def test_manager_sees_all_and_cannot_create(self):
        app.create_reservation(self.data, self.users['recepcion'])
        app.create_reservation(self.data, self.users['cliente'])
        self.assertEqual(len(app.list_reservations(self.users['gerente'])), 2)
        with self.assertRaises(app.AccessError):
            app.create_reservation(self.data, self.users['gerente'])


class RegistrationTests(BaseTest):
    new = {'rut': '12.345.678-5', 'name': 'María José Muñoz', 'password': 'Playa2026!'}

    def test_public_registration_creates_client_and_session(self):
        token, user = app.register_client(self.new)
        self.assertEqual(user['role'], 'cliente')
        self.assertEqual(app.session_user(token)['rut'], '12345678-5')
        self.assertTrue(self.user('12345678-5', 'Playa2026!'))

    def test_registration_cannot_choose_role(self):
        _, user = app.register_client({**self.new, 'role': 'gerente'})
        self.assertEqual(user['role'], 'cliente')

    def test_duplicate_rut_is_rejected(self):
        with self.assertRaises(ValueError):
            app.register_client({**self.new, 'rut': '11.111.111-1'})

    def test_password_policy(self):
        for password in ['corta1', 'sololetras', '12345678901', 'password123', 'Clave12345678', 'x' * 64 + '1']:
            with self.assertRaises(ValueError, msg=password):
                app.register_client({**self.new, 'password': password})
        # 'Clave12345678' se rechaza porque contiene el RUT 12345678.

    def test_name_validation(self):
        for name in ['A', '1234', 'Ana <b>', '  ']:
            with self.assertRaises(ValueError, msg=name):
                app.register_client({**self.new, 'name': name})
        self.assertEqual(app.clean_name("  Ana   O'Higgins-Pérez "), "Ana O'Higgins-Pérez")

    def test_reception_creates_client_and_books_for_them(self):
        _, client = app.register_client(self.new, creator=self.users['recepcion'])
        self.assertEqual([c['id'] for c in app.list_clients(self.users['recepcion'])
                          if c['rut'] == '12345678-5'], [client['id']])
        app.create_reservation({**self.data, 'client_id': client['id'], 'guest': ''}, self.users['recepcion'])
        mine = app.list_reservations(self.user('12.345.678-5', 'Playa2026!'))
        self.assertEqual([r['guest'] for r in mine], ['María José Muñoz'])

    def test_only_reception_manages_clients(self):
        for role in ('cliente', 'gerente'):
            with self.assertRaises(app.AccessError):
                app.register_client(self.new, creator=self.users[role])
            with self.assertRaises(app.AccessError):
                app.list_clients(self.users[role])
        with self.assertRaises(ValueError):
            app.create_reservation({**self.data, 'client_id': self.users['gerente']['id']}, self.users['recepcion'])


class AuditTests(BaseTest):
    def test_security_events_are_logged(self):
        with self.assertRaises(app.AccessError):
            app.login({'rut': '11.111.111-1', 'password': 'incorrecta'})
        rid = app.create_reservation(self.data, self.users['recepcion'])['id']
        app.cancel_reservation(rid, self.users['gerente'])
        actions = self.actions()
        for action in ('inicio_sesion', 'acceso_fallido', 'reserva_creada', 'reserva_cancelada'):
            self.assertIn(action, actions)

    def test_only_manager_reads_audit(self):
        self.assertTrue(app.list_audit(self.users['gerente']))
        for role in ('cliente', 'recepcion'):
            with self.assertRaises(app.AccessError):
                app.list_audit(self.users[role])


class InputHardeningTests(BaseTest):
    """Datos erróneos o maliciosos: se rechazan con un mensaje claro y sin detalles internos."""

    def test_to_int_rejects_weird_values(self):
        self.assertEqual(app.to_int('42'), 42)
        for bad in [None, True, 3.7, float('inf'), '', 'abc', '1e3', '0x10', '²', '١٢٣', '1 OR 1=1',
                    '9' * 40, [], {}, -1]:
            with self.assertRaises(app.InputError, msg=repr(bad)):
                app.to_int(bad)

    def test_prices_only_ascii_digits_in_range(self):
        manager = self.users['gerente']
        for price in ['٥٠', '10.5', 10.5, -1, 10**12, '1e3', True]:
            with self.assertRaises(ValueError, msg=repr(price)):
                app.create_service({'hotel_id': 1, 'name': 'Buceo', 'price': price}, manager)

    def test_reservation_too_far_ahead(self):
        far = date.today() + timedelta(days=app.MAX_ADVANCE_DAYS + 1)
        with self.assertRaises(app.InputError):
            app.create_reservation({**self.data, 'arrival': far.isoformat(),
                                    'departure': (far + timedelta(days=1)).isoformat()}, self.users['cliente'])

    def test_registration_rate_limit(self):
        ip = '10.0.0.99'
        self.assertTrue(all(app.allow_registration(ip, now=1000 + i) for i in range(app.REGISTER_LIMIT)))
        self.assertFalse(app.allow_registration(ip, now=1000 + app.REGISTER_LIMIT))
        self.assertTrue(app.allow_registration(ip, now=1000 + app.REGISTER_WINDOW + 20))


class HttpSecurityTests(BaseTest):
    """Pruebas a través del servidor HTTP real (puerto libre elegido por el sistema)."""

    def setUp(self):
        super().setUp()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cookie = self.request('POST', '/api/login', {'rut': '11.111.111-1', 'password': 'Cliente#2026'})[2]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        head = {'Host': f'127.0.0.1:{self.port}', 'Content-Type': 'application/json'}
        if getattr(self, 'cookie', None):
            head['Cookie'] = self.cookie
        head.update(headers or {})
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        conn.request(method, path, body=data, headers=head)
        response = conn.getresponse()
        text = response.read().decode('utf-8', 'replace')
        cookie = (response.getheader('Set-Cookie') or '').split(';')[0]
        conn.close()
        return response.status, text, cookie, response

    def test_availability_http_requires_session_and_validates_input(self):
        from urllib.parse import urlencode
        self.cookie = None
        path = '/api/availability?' + urlencode(self.data)
        self.assertEqual(self.request('GET', path)[0], 401)
        rut, _, _, password = next(a for a in app.TEST_ACCOUNTS if a[2] == 'cliente')
        self.cookie = self.request('POST', '/api/login', {'rut': rut, 'password': password})[2]
        status, body, _, _ = self.request('GET', path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['available'], 5)
        self.assertEqual(self.request('GET', '/api/availability?hotel_id=1')[0], 400)

    def assert_clean_400(self, status, text):
        self.assertEqual(status, 400, text)
        for leak in ('Expecting', 'invalid literal', 'codec', 'int()', 'Traceback', 'sqlite'):
            self.assertNotIn(leak, text)

    def test_malformed_bodies_get_generic_400(self):
        for body in [b'{', b'\xff\xfe\x00', b'[' * 2000 + b']' * 2000, b'{"id": 1e999}']:
            self.assert_clean_400(*self.request('POST', '/api/cancel', body)[:2])

    def test_weird_ids_never_cause_500(self):
        for value in ['abc', '1 OR 1=1', "1'; DROP TABLE users;--", '9' * 40, None, [], {'$gt': ''}]:
            status, text, *_ = self.request('POST', '/api/cancel', {'id': value})
            self.assert_clean_400(status, text)
        with app.connection() as db:
            self.assertTrue(db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 3)

    def test_cross_site_requests_rejected(self):
        for headers in [{'Origin': 'http://evil.com'}, {'Origin': 'null'}, {'Host': 'evil.com'}]:
            self.assertEqual(self.request('POST', '/api/cancel', {'id': 1}, headers)[0], 403)
        status = self.request('POST', '/api/cancel', b'id=1',
                              {'Content-Type': 'application/x-www-form-urlencoded'})[0]
        self.assertEqual(status, 415)

    def test_other_methods_and_paths(self):
        status, _, _, response = self.request('PUT', '/api/reservations')
        self.assertEqual(status, 405)
        self.assertIsNotNone(response.getheader('Content-Security-Policy'))
        for path in ['/app.py', '/../app.py', '/altamar.sqlite3', '/estacion.json', '/%2e%2e/app.py']:
            self.assertEqual(self.request('GET', path)[0], 404, path)
        self.cookie = "altamar_session=' OR '1'='1"
        self.assertEqual(self.request('GET', '/api/me')[0], 401)





class AvailabilityTests(BaseTest):
    def test_availability_does_not_book_and_tracks_cancellations(self):
        user = self.users['cliente']
        self.assertEqual(app.availability(self.data, user)['available'], 5)
        self.assertEqual(app.list_reservations(user), [])
        ids = [app.create_reservation(self.data, user)['id'] for _ in range(5)]
        self.assertEqual(app.availability(self.data, user)['available'], 0)
        app.cancel_reservation(ids[0], user)
        self.assertEqual(app.availability(self.data, user)['available'], 1)

    def test_full_hotel_suggests_regional_alternatives(self):
        user = self.users['cliente']
        self.assertNotIn('alternatives', app.availability(self.data, user))
        for hotel in (1, 2):
            for _ in range(5):
                app.create_reservation({**self.data, 'hotel_id': hotel}, user)
        result = app.availability(self.data, user)
        self.assertEqual(result['available'], 0)
        self.assertEqual({h['id'] for h in result['alternatives']}, {3, 4, 5})
        self.assertTrue(all(h['region'] == 'Norte' for h in result['alternatives']))
        self.assertEqual(len(app.list_reservations(user)), 10)  # consultar no reserva

    def test_invalid_queries_and_local_scope(self):
        for data in ({**self.data, 'hotel_id': 999}, {**self.data, 'arrival': 'bad'},
                     {**self.data, 'departure': self.data['arrival']}):
            with self.assertRaises(ValueError):
                app.availability(data, self.users['cliente'])
        with self.assertRaises(app.AccessError):
            app.availability({**self.data, 'hotel_id': 2}, self.users['recepcion'])

    def test_missing_station_denies_login_and_old_session(self):
        app.STATION_FILE.unlink()
        for rut, _, role, password in app.TEST_ACCOUNTS:
            if role == 'recepcion':
                with self.assertRaises(app.AccessError):
                    app.login({'rut': rut, 'password': password})
        with app.connection() as db:
            token = app.start_session(db, self.users['recepcion']['id'])
        self.assertIsNone(app.session_user(token))


class GuestDetailsTests(BaseTest):
    """Datos de la reserva: adultos, niños, bebés, teléfono y correo."""

    def test_reservation_stores_guests_and_contact(self):
        rid = app.create_reservation({**self.data, 'phone': '9 8765 4321', 'email': ' Ana@Correo.CL '},
                                     self.users['cliente'])['id']
        row = next(r for r in app.list_reservations(self.users['cliente']) if r['id'] == rid)
        self.assertEqual((row['adults'], row['children']), (2, 1))
        self.assertEqual((row['phone'], row['email']), ('+56987654321', 'Ana@correo.cl'))

    def test_capacity_per_room(self):
        # 4 puestos de adulto; cada puesto libre admite 2 niños; siempre al menos 1 adulto.
        user = self.users['cliente']
        for adults, children in [(0, 2), (0, 0), (5, 0), (4, 1), (3, 3), (2, 5), (1, 7), (2, -1)]:
            with self.assertRaises(app.InputError, msg=(adults, children)):
                app.create_reservation({**self.data, 'adults': adults, 'children': children}, user)
        for adults, children in [(4, 0), (3, 2), (2, 4), (1, 6)]:
            self.assertTrue(app.create_reservation({**self.data, 'adults': adults, 'children': children}, user)['id'])

    def test_infants_need_one_adult_and_max_two(self):
        user = self.users['cliente']
        rid = app.create_reservation({**self.data, 'adults': 1, 'children': 6, 'infants': 2}, user)['id']
        row = next(r for r in app.list_reservations(user) if r['id'] == rid)
        self.assertEqual((row['adults'], row['children'], row['infants']), (1, 6, 2))
        self.assertTrue(app.create_reservation({**self.data, 'adults': 4, 'children': 0, 'infants': 2}, user)['id'])
        for change in ({'infants': 3}, {'infants': -1}, {'infants': '1.5'}, {'infants': None},
                       {'adults': 0, 'children': 0, 'infants': 1}):
            with self.assertRaises(app.InputError, msg=change):
                app.create_reservation({**self.data, **change}, user)

    def test_phone_must_be_mobile_or_landline(self):
        valid = {'+56 9 8765 4321': '+56987654321', '2 2421 3146': '+56224213146', '+56 32 212 3456': '+56322123456'}
        for raw, stored in valid.items():
            self.assertEqual(app.clean_phone(raw), stored)
        for raw in ('+56 8 6567 6788', '+56 1 2345 6789', '0 2421 31461', '+56 9 1234 567'):
            with self.assertRaises(app.InputError, msg=raw):
                app.clean_phone(raw)

    def test_contact_is_required_and_validated(self):
        bad = [{'phone': ''}, {'phone': '12345'}, {'phone': '+56 1 2345 6789'}, {'phone': '<b>9</b>'},
               {'email': ''}, {'email': 'sin-arroba'}, {'email': 'a@b'}, {'email': '<script>@x.cl'},
               {'adults': None}, {'adults': '2.5'}]
        for change in bad:
            with self.assertRaises(app.InputError, msg=change):
                app.create_reservation({**self.data, **change}, self.users['cliente'])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 0)

    def test_contact_not_written_to_audit_log(self):
        app.create_reservation(self.data, self.users['cliente'])
        with app.connection() as db:
            detail = db.execute("SELECT detail FROM audit WHERE action='reserva_creada'").fetchone()[0]
        self.assertIn('3 personas', detail)
        self.assertNotIn('huesped@correo.cl', detail)
        self.assertNotIn('912345678', detail)


if __name__ == '__main__':
    unittest.main()
