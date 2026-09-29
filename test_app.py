import concurrent.futures
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import app


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = app.DATABASE, app.PBKDF2_ITERATIONS
        app.DATABASE = Path(self.temp.name) / 'test.sqlite3'
        app.PBKDF2_ITERATIONS = 1000  # Solo para que las pruebas sean rápidas.
        app.initialize()
        self.users = {role: self.user(rut, password) for rut, _, role, password in app.TEST_ACCOUNTS}
        self.data = {'guest': 'Cliente de prueba', 'hotel_id': 1,
                     'arrival': date.today().isoformat(),
                     'departure': (date.today() + timedelta(days=2)).isoformat()}

    def tearDown(self):
        app.DATABASE, app.PBKDF2_ITERATIONS = self.original
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
                app.create_reservation(data, self.users['recepcion'])

    def test_services_and_receipt_totals(self):
        rid = app.create_reservation({**self.data, 'services': [
            {'id': 'spa', 'quantity': 2}, {'id': 'tour', 'quantity': 1},
        ]}, self.users['recepcion'])['id']
        reservation = app.list_reservations(self.users['recepcion'])[0]
        self.assertEqual(reservation['id'], rid)
        self.assertEqual(reservation['nights'], 2)
        self.assertEqual(reservation['lodging_total'], 160_000)
        self.assertEqual(reservation['services_total'], 120_000)
        self.assertEqual(reservation['total'], 280_000)
        self.assertEqual({service['id'] for service in reservation['services']}, {'spa', 'tour'})

    def test_services_can_be_replaced_and_cleared(self):
        rid = app.create_reservation(self.data, self.users['recepcion'])['id']
        result = app.update_reservation_services(
            rid, [{'id': 'room', 'quantity': 2}], self.users['recepcion'])
        self.assertEqual(result['total'], 196_000)
        self.assertEqual(app.list_reservations(self.users['recepcion'])[0]['services_total'], 36_000)
        app.update_reservation_services(rid, [], self.users['recepcion'])
        self.assertEqual(app.list_reservations(self.users['recepcion'])[0]['services'], [])

    def test_services_validate_quantities_and_permissions(self):
        rid = app.create_reservation(self.data, self.users['recepcion'])['id']
        for items in [None, [{'id': 'unknown', 'quantity': 1}],
                      [{'id': 'spa', 'quantity': 11}], [{'id': 'spa', 'quantity': True}]]:
            with self.assertRaises(ValueError):
                app.update_reservation_services(rid, items, self.users['recepcion'])
        with self.assertRaises(app.AccessError):
            app.update_reservation_services(rid, [], self.users['gerente'])
        with self.assertRaises(ValueError):
            app.update_reservation_services(rid, [], self.users['cliente'])
        app.cancel_reservation(rid, self.users['recepcion'])
        with self.assertRaises(ValueError):
            app.update_reservation_services(rid, [], self.users['recepcion'])


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


if __name__ == '__main__':
    unittest.main()
