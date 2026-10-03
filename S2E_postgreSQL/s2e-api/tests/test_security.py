"""Testes isolados: não conectam ao PostgreSQL nem enviam e-mail."""

import io
import os
import re
import secrets
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import Flask, session
from flask_wtf.csrf import CSRFProtect
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash
from PIL import Image

from app.api import middleware, pais, web
from app.core.tokens import digest_token
from app.core.rate_limit import login_identity
from app.core.validators import validar_agendamento, validar_upload_documento, validar_upload_imagem
from app.services.school_directory import SchoolDirectoryError, _query
from app.services.school_sync import _demo_allowed


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeDb:
    def __init__(self):
        self.role = 'admin'
        self.parent_active = True
        self.users = {
            'admin': {'id': 1, 'username': 'admin', 'role': 'admin', 'auth_version': 0, 'password': generate_password_hash('Senha@123456', method='pbkdf2:sha256')},
            'basico': {'id': 2, 'username': 'basico', 'role': 'basico', 'auth_version': 0, 'password': generate_password_hash('Senha@123456', method='pbkdf2:sha256')},
            'vigia': {'id': 3, 'username': 'vigia', 'role': 'vigia', 'auth_version': 0, 'password': generate_password_hash('Senha@123456', method='pbkdf2:sha256')},
        }

    def execute(self, sql, params=()):
        if 'FROM usuarios WHERE id' in sql:
            user = next((u for u in self.users.values() if u['id'] == params[0]), None)
            return Cursor({'role': user['role'], 'auth_version': user['auth_version']} if user else None)
        if 'FROM usuarios WHERE LOWER(TRIM(username))' in sql:
            return Cursor(self.users.get(params[0].lower()))
        if 'FROM responsaveis WHERE id' in sql:
            return Cursor({'id': params[0], 'email': 'parent@example.test', 'auth_version': 0} if self.parent_active else None)
        if 'FROM alunos a' in sql and 'vinculos_pais_alunos' in sql:
            return Cursor(None)
        if 'FROM saidas s' in sql or 'FROM solicitacoes_saida ss' in sql:
            return Cursor(rows=[])
        return Cursor()


class ParentAuthDb:
    def __init__(self):
        self.parent = {
            'id': 10, 'nome': 'Responsável', 'email': 'parent@example.test',
            'password_hash': generate_password_hash('Senha@123456', method='pbkdf2:sha256'),
            'status': 'aprovado', 'auth_version': 0,
        }
        self.token_hash = None
        self.used = False

    def execute(self, sql, params=()):
        if 'FROM responsaveis WHERE email' in sql:
            return Cursor(self.parent)
        if "FROM responsaveis WHERE id" in sql:
            return Cursor({'auth_version': 0})
        if 'FROM tokens_2fa WHERE responsavel_id' in sql and 'criado_em' in sql:
            return Cursor({'exists': 1} if self.token_hash else None)
        if sql.startswith('DELETE FROM tokens_2fa'):
            self.token_hash = None
            return Cursor()
        if sql.startswith('INSERT INTO tokens_2fa'):
            self.token_hash = params[1]
            self.used = False
            return Cursor()
        if 'SELECT id, expires_at FROM tokens_2fa' in sql:
            if params[1] == self.token_hash and not self.used:
                return Cursor({'id': 7, 'expires_at': datetime.now() + timedelta(minutes=5)})
            return Cursor()
        if sql.startswith('UPDATE tokens_2fa SET usado'):
            if self.used:
                return Cursor()
            self.used = True
            return Cursor({'id': 7})
        return Cursor()


class FlowDb:
    def __init__(self):
        self.actions = []
        self.future = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')

    def execute(self, sql, params=()):
        self.actions.append((sql, params))
        if 'FROM solicitacoes_saida ss' in sql and "status = 'aguardando'" in sql:
            return Cursor({
                'id': 12, 'aluno_id': 5, 'data_solicitada': self.future,
                'horario_solicitado': '12:00', 'motivo': 'Consulta',
                'tipo_saida': 'acompanhado', 'acompanhante': 'Responsável',
                'responsavel_nome': 'Responsável', 'responsavel_email': 'parent@example.test',
                'aluno_nome': 'Aluno',
            })
        if 'SELECT 1 FROM saidas WHERE aluno' in sql:
            return Cursor()
        if 'SELECT s.data_saida, s.horario' in sql:
            return Cursor({'data_saida': datetime.now().strftime('%Y-%m-%d'),
                           'horario': '12:00', 'aluno_id': 5, 'aluno_nome': 'Aluno'})
        if 'SELECT r.email' in sql:
            return Cursor(rows=[])
        return Cursor()


class SecurityTests(unittest.TestCase):
    def setUp(self):
        os.environ['SECRET_KEY'] = secrets.token_hex(32)
        templates = Path(__file__).resolve().parents[1] / 'app' / 'templates'
        self.app = Flask(__name__, template_folder=str(templates), static_folder=None)
        self.app.secret_key = os.environ['SECRET_KEY']
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True, BASE_URL='https://portalsecureedu.com')
        CSRFProtect(self.app)
        web.register_routes(self.app)
        pais.register_parent_routes(self.app)
        self.db = FakeDb()

        @contextmanager
        def get_fake_db():
            yield self.db

        self.patches = [
            patch.object(middleware, 'get_db', get_fake_db),
            patch.object(web, 'get_db', get_fake_db),
            patch.object(pais, 'get_db', get_fake_db),
            patch.object(web, 'log_operacao'),
            patch.object(middleware, 'refresh_parent', return_value=True),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = self.app.test_client()

    def csrf(self, path='/'):
        body = self.client.get(path).get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]+)"', body)
        self.assertIsNotNone(match)
        return match.group(1)

    def session_as(self, role):
        user = self.db.users[role]
        with self.client.session_transaction() as state:
            state.update(user_id=user['id'], role=role, username=role, auth_version=0)

    def test_staff_login_and_logout_for_all_roles(self):
        for role in ('admin', 'basico', 'vigia'):
            with self.subTest(role=role):
                self.client = self.app.test_client()
                csrf = self.csrf()
                with patch.object(web, 'is_limited', return_value=False), patch.object(web, 'clear_failures'):
                    response = self.client.post('/', data={'csrf_token': csrf, 'u': role, 's': 'Senha@123456'})
                self.assertEqual(response.status_code, 302)
                with self.client.session_transaction() as state:
                    self.assertEqual(state['role'], role)
                csrf = self.csrf()
                response = self.client.post('/logout', data={'csrf_token': csrf})
                self.assertEqual(response.status_code, 302)
                with self.client.session_transaction() as state:
                    self.assertNotIn('user_id', state)

    def test_role_checks_on_direct_urls(self):
        for role, forbidden in (
            ('basico', '/registrar_saida'),
            ('basico', '/configuracoes'),
            ('vigia', '/cadastro_aluno'),
            ('vigia', '/admin/solicitacoes'),
        ):
            with self.subTest(role=role, path=forbidden):
                self.client = self.app.test_client()
                self.session_as(role)
                self.assertEqual(self.client.get(forbidden).status_code, 403)
        self.client = self.app.test_client()
        self.session_as('admin')
        self.assertEqual(self.client.get('/concluir_saida/1').status_code, 405)

    def test_parent_idor_and_revocation(self):
        with self.client.session_transaction() as state:
            state.update(pai_id=10, pai_nome='Responsável', pai_email='parent@example.test', auth_version=0)
        response = self.client.get('/pais/solicitar/999')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/uploads/photos/outro-aluno.png').status_code, 403)
        self.db.parent_active = False
        response = self.client.get('/pais/dashboard')
        self.assertEqual(response.location, '/pais/login')

    def test_csrf_rejects_state_changes(self):
        self.session_as('admin')
        response = self.client.post('/concluir_saida/1')
        self.assertEqual(response.status_code, 400)

    def test_upload_rejects_content_mismatch(self):
        fake_image = FileStorage(stream=io.BytesIO(b'<script>alert(1)</script>'), filename='foto.png')
        self.assertFalse(validar_upload_imagem(fake_image)[0])
        fake_pdf = FileStorage(stream=io.BytesIO(b'%PDF-not-a-complete-pdf'), filename='x.pdf')
        self.assertFalse(validar_upload_documento(fake_pdf)[0])
        png_bytes = io.BytesIO()
        Image.new('RGB', (1, 1)).save(png_bytes, format='PNG')
        png_bytes.seek(0)
        mislabeled = FileStorage(stream=png_bytes, filename='foto.jpg')
        self.assertFalse(validar_upload_imagem(mislabeled)[0])

    def test_schedule_and_token_digest(self):
        self.assertIsNotNone(validar_agendamento('2000-01-01', '12:00'))
        self.assertIsNotNone(validar_agendamento('2030-99-01', '12:00'))
        self.assertIsNone(validar_agendamento((datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d'), '12:00'))
        self.assertNotEqual(digest_token('123456'), '123456')
        self.assertNotEqual(login_identity('127.0.0.1', 'admin'), login_identity('127.0.0.1', 'vigia'))
        self.assertEqual(login_identity('203.0.113.10', 'admin'), login_identity('203.0.113.10', 'vigia'))

    def test_school_demo_guard(self):
        with patch.dict(os.environ, {'SCHOOL_DEMO_EMAILS': 'parent@example.test',
                                     'APP_ENV': 'production', 'BASE_URL': 'https://portalsecureedu.com'}):
            with self.assertRaises(SchoolDirectoryError):
                _demo_allowed('parent@example.test')
        with patch.dict(os.environ, {'SCHOOL_DEMO_EMAILS': 'parent@example.test',
                                     'APP_ENV': 'test', 'BASE_URL': 'http://localhost:8002'}):
            self.assertTrue(_demo_allowed('parent@example.test'))
            self.assertFalse(_demo_allowed('other@example.test'))

    def test_school_query_files_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix='secureedu-query-test-') as folder:
            query_file = Path(folder, 'responsavel.sql')
            with patch.dict(os.environ, {'SCHOOL_SQL_QUERY_DIR': folder}):
                with self.assertRaises(SchoolDirectoryError):
                    _query('responsavel.sql', 'email')
                query_file.write_text('DELETE FROM responsaveis WHERE email = %(email)s', encoding='utf-8')
                with self.assertRaises(SchoolDirectoryError):
                    _query('responsavel.sql', 'email')
                query_file.write_text('SELECT 1; DELETE FROM responsaveis WHERE email = %(email)s', encoding='utf-8')
                with self.assertRaises(SchoolDirectoryError):
                    _query('responsavel.sql', 'email')
                query_file.write_text('SELECT %(email)s AS email', encoding='utf-8')
                self.assertEqual(_query('responsavel.sql', 'email'), 'SELECT %(email)s AS email')

    def test_example_secret_is_rejected(self):
        from app import create_app
        with patch.dict(os.environ, {'SECRET_KEY': 'substitua-por-uma-chave-aleatoria-de-no-minimo-32-caracteres'}):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_parent_2fa_single_email_and_single_use(self):
        parent_db = ParentAuthDb()

        @contextmanager
        def get_parent_db():
            yield parent_db

        with patch.object(pais, 'get_db', get_parent_db), \
                patch.object(pais, 'is_limited', return_value=False), \
                patch.object(pais, 'record_failure'), \
                patch.object(pais, 'clear_failures'), \
                patch.object(pais, 'refresh_parent', return_value=True), \
                patch.object(pais, 'enviar_email', return_value=True) as mailer, \
                patch.object(pais.secrets, 'choice', return_value='1'):
            for _ in range(2):
                csrf = self.csrf('/pais/login')
                response = self.client.post('/pais/login', data={
                    'csrf_token': csrf, 'email': 'parent@example.test', 'senha': 'Senha@123456',
                })
                self.assertEqual(response.location, '/pais/verificar')
            self.assertEqual(mailer.call_count, 1)
            self.assertEqual(parent_db.token_hash, digest_token('111111'))
            csrf = self.csrf('/pais/verificar')
            response = self.client.post('/pais/verificar', data={'csrf_token': csrf, 'codigo': '111111'})
            self.assertEqual(response.location, '/pais/dashboard')
            self.assertTrue(parent_db.used)
            with self.client.session_transaction() as state:
                state.clear()
                state.update(pai_temp_id=10, pai_temp_email='parent@example.test', pai_temp_nome='Responsável', pai_temp_auth_version=0)
            csrf = self.csrf('/pais/verificar')
            response = self.client.post('/pais/verificar', data={'csrf_token': csrf, 'codigo': '111111'})
            self.assertEqual(response.status_code, 200)
            with self.client.session_transaction() as state:
                self.assertNotIn('pai_id', state)

    def test_basic_can_approve_and_reject_but_not_release(self):
        flow = FlowDb()

        @contextmanager
        def get_flow_db():
            yield flow

        self.session_as('basico')
        csrf = self.csrf()
        with patch.object(web, 'get_db', get_flow_db):
            approved = self.client.post('/admin/solicitacoes/12/aprovar', data={'csrf_token': csrf})
            self.assertEqual(approved.location, '/admin/solicitacoes')
            inserted = [params for sql, params in flow.actions if 'INSERT INTO saidas' in sql]
            self.assertEqual(len(inserted), 1)
            self.assertEqual(inserted[0][-1], 12)
            rejected = self.client.post('/admin/solicitacoes/12/rejeitar', data={'csrf_token': csrf})
            self.assertEqual(rejected.location, '/admin/solicitacoes')
            self.assertTrue(any("status = 'rejeitado'" in sql for sql, _ in flow.actions))
        self.assertEqual(self.client.post('/concluir_saida/1', data={'csrf_token': csrf}).status_code, 403)

    def test_guard_releases_a_pending_exit(self):
        flow = FlowDb()

        @contextmanager
        def get_flow_db():
            yield flow

        self.session_as('vigia')
        csrf = self.csrf()
        with patch.object(web, 'get_db', get_flow_db):
            response = self.client.post('/concluir_saida/1', data={'csrf_token': csrf})
        self.assertEqual(response.location, '/saidas')
        self.assertTrue(any("status = 'concluida'" in sql for sql, _ in flow.actions))
        self.assertEqual(self.client.post('/admin/solicitacoes/12/aprovar', data={'csrf_token': csrf}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
