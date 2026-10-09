"""Fluxo real em PostgreSQL descartável; nunca usa um endereço remoto."""

import os
import re
import secrets
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

from werkzeug.security import generate_password_hash


class PostgresFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.getenv('SECUREEDU_TEST_DATABASE_URL', '')
        parsed = urlparse(url)
        if (os.getenv('APP_ENV') != 'test' or parsed.hostname not in {'localhost', '127.0.0.1'}
                or parsed.path != '/secureedu_test'):
            raise unittest.SkipTest('Requer PostgreSQL descartável local secureedu_test')
        os.environ['DATABASE_URL'] = url
        os.environ['DATABASE_SSLMODE'] = 'disable'
        os.environ['SECRET_KEY'] = secrets.token_hex(32)
        os.environ['BASE_URL'] = 'http://localhost:8002'
        os.environ['SESSION_COOKIE_SECURE'] = 'false'
        os.environ['SCHOOL_DIRECTORY_MODE'] = 'demo'
        cls.tmp = tempfile.TemporaryDirectory(prefix='secureedu-test-')
        os.environ['UPLOAD_FOLDER'] = cls.tmp.name
        from app import create_app
        from app.core.database import get_db
        cls.get_db = staticmethod(get_db)
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        assert cls.app.test_client().get('/healthz').status_code == 204
        with patch('app.core.database.get_db', side_effect=RuntimeError('test outage')):
            assert cls.app.test_client().get('/healthz').status_code == 503

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'tmp'):
            cls.tmp.cleanup()

    def setUp(self):
        marker = secrets.token_hex(5)
        self.staff_names = {role: f'{role}_{marker}' for role in ('admin', 'basico', 'vigia')}
        self.parent_email = f'parent_{marker}@example.test'
        os.environ['SCHOOL_DEMO_EMAILS'] = self.parent_email
        self.child_name = f'Aluno Teste {marker}'
        self.staff_ids = []
        self.parent_id = None
        self.student_id = None
        self.staff_password = 'Senha@123456'
        with self.get_db() as conn:
            for role, username in self.staff_names.items():
                row = conn.execute("""
                    INSERT INTO usuarios (username, password, role, email)
                    VALUES (%s, %s, %s, %s) RETURNING id
                """, (username, generate_password_hash(self.staff_password, method='pbkdf2:sha256'),
                      role, f'{username}@example.test')).fetchone()
                self.staff_ids.append(row['id'])
            self.student_id = conn.execute(
                "INSERT INTO alunos (nome, turma, serie) VALUES (%s, 'A', '1º ano EF') RETURNING id",
                (self.child_name,),
            ).fetchone()['id']

    def tearDown(self):
        with self.get_db() as conn:
            if self.student_id:
                conn.execute('DELETE FROM saidas WHERE aluno = %s', (self.student_id,))
                conn.execute('DELETE FROM solicitacoes_saida WHERE aluno_id = %s', (self.student_id,))
                conn.execute('DELETE FROM vinculos_pais_alunos WHERE aluno_id = %s', (self.student_id,))
                conn.execute('DELETE FROM alunos WHERE id = %s', (self.student_id,))
            if self.parent_id:
                conn.execute('DELETE FROM tokens_2fa WHERE responsavel_id = %s', (self.parent_id,))
                conn.execute('DELETE FROM reset_tokens_pais WHERE responsavel_id = %s', (self.parent_id,))
                conn.execute('DELETE FROM responsaveis WHERE id = %s', (self.parent_id,))
            for staff_id in self.staff_ids:
                conn.execute('DELETE FROM reset_tokens WHERE user_id = %s', (staff_id,))
                conn.execute('DELETE FROM usuarios WHERE id = %s', (staff_id,))

    def csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def staff_login(self, role):
        client = self.app.test_client()
        csrf = self.csrf(client, '/')
        response = client.post('/', data={
            'csrf_token': csrf, 'u': self.staff_names[role], 's': self.staff_password,
        })
        self.assertEqual(response.location, '/inicio')
        return client

    def test_signup_approval_2fa_request_reapproval_and_release(self):
        from app.api import pais
        from app.core.clock import school_now
        parent = self.app.test_client()
        admin = self.staff_login('admin')
        emails = []

        def capture_email(to, subject, html):
            emails.append((to, subject, html))
            return True

        with patch.object(pais, 'enviar_email', side_effect=capture_email), \
                patch('app.core.mailer.enviar_email', side_effect=capture_email), \
                patch.dict(os.environ, {'SCHOOL_DEMO_EMAILS': self.parent_email}):
            csrf = self.csrf(parent, '/pais/cadastro')
            response = parent.post('/pais/cadastro', data={
                'csrf_token': csrf, 'nome': 'Responsável de Teste',
                'email': self.parent_email, 'senha': self.staff_password,
                'confirmar': self.staff_password,
            })
            self.assertEqual(response.status_code, 200)
            with self.get_db() as conn:
                self.parent_id = conn.execute(
                    'SELECT id FROM responsaveis WHERE email = %s', (self.parent_email,),
                ).fetchone()['id']
                conn.execute(
                    'INSERT INTO vinculos_pais_alunos (responsavel_id, aluno_id) VALUES (%s, %s)',
                    (self.parent_id, self.student_id),
                )
            csrf = self.csrf(admin, '/admin/responsaveis')
            response = admin.post(f'/admin/responsaveis/{self.parent_id}/aprovar', data={'csrf_token': csrf})
            self.assertEqual(response.location, '/admin/responsaveis')

            csrf = self.csrf(parent, '/pais/login')
            response = parent.post('/pais/login', data={
                'csrf_token': csrf, 'email': self.parent_email, 'senha': self.staff_password,
            })
            self.assertEqual(response.location, '/pais/verificar')
            sent_codes = [re.search(r'>(\d{6})</div>', body) for to, _, body in emails if to == self.parent_email]
            codes = [match.group(1) for match in sent_codes if match]
            self.assertEqual(len(codes), 1)
            with self.get_db() as conn:
                stored = conn.execute(
                    'SELECT token FROM tokens_2fa WHERE responsavel_id = %s', (self.parent_id,),
                ).fetchone()['token']
            self.assertNotEqual(stored, codes[0])
            csrf = self.csrf(parent, '/pais/verificar')
            response = parent.post('/pais/verificar', data={'csrf_token': csrf, 'codigo': codes[0]})
            self.assertEqual(response.location, '/pais/dashboard')
            self.assertIn(self.child_name, parent.get('/pais/dashboard').get_data(as_text=True))
            self.assertNotIn('/pais/solicitar/999999', parent.get('/pais/dashboard').get_data(as_text=True))
            self.assertEqual(parent.get('/pais/solicitar/999999').location, '/pais/dashboard')

            day = (school_now() + timedelta(days=2)).strftime('%Y-%m-%d')
            csrf = self.csrf(parent, f'/pais/solicitar/{self.student_id}')
            response = parent.post(f'/pais/solicitar/{self.student_id}', data={
                'csrf_token': csrf, 'data_solicitada': day, 'horario': '12:00',
                'motivo': 'Consulta', 'tipo_saida': 'acompanhado', 'acompanhante': 'Responsável de Teste',
            })
            self.assertEqual(response.location, '/pais/minhas_solicitacoes')
            with self.get_db() as conn:
                request_id = conn.execute(
                    'SELECT id FROM solicitacoes_saida WHERE aluno_id = %s', (self.student_id,),
                ).fetchone()['id']

            basic = self.staff_login('basico')
            csrf = self.csrf(basic, '/admin/solicitacoes')
            self.assertEqual(basic.get('/registrar_saida').status_code, 403)
            response = basic.post(f'/admin/solicitacoes/{request_id}/aprovar', data={'csrf_token': csrf})
            self.assertEqual(response.location, '/admin/solicitacoes')
            with self.get_db() as conn:
                exit_row = conn.execute(
                    'SELECT id, solicitacao_id FROM saidas WHERE aluno = %s', (self.student_id,),
                ).fetchone()
            self.assertEqual(exit_row['solicitacao_id'], request_id)
            csrf = self.csrf(parent, f'/pais/editar_solicitacao/{request_id}')
            response = parent.post(f'/pais/editar_solicitacao/{request_id}', data={
                'csrf_token': csrf, 'data_solicitada': day, 'horario': '13:00',
                'motivo': 'Consulta', 'tipo_saida': 'acompanhado', 'acompanhante': 'Responsável de Teste',
            })
            self.assertEqual(response.location, '/pais/minhas_solicitacoes')
            with self.get_db() as conn:
                status = conn.execute(
                    'SELECT status FROM solicitacoes_saida WHERE id = %s', (request_id,),
                ).fetchone()['status']
                pending = conn.execute(
                    'SELECT 1 FROM saidas WHERE solicitacao_id = %s', (request_id,),
                ).fetchone()
            self.assertEqual(status, 'aguardando')
            self.assertIsNone(pending)

            # Uma segunda solicitação é rejeitada pelo perfil básico.
            response = basic.post(f'/admin/solicitacoes/{request_id}/rejeitar', data={'csrf_token': csrf})
            self.assertEqual(response.status_code, 400)  # token de outro cliente
            basic_csrf = self.csrf(basic, '/admin/solicitacoes')
            response = basic.post(f'/admin/solicitacoes/{request_id}/rejeitar', data={'csrf_token': basic_csrf})
            self.assertEqual(response.location, '/admin/solicitacoes')

            # A portaria só pode liberar uma saída pendente da data atual.
            with self.get_db() as conn:
                exit_id = conn.execute("""
                    INSERT INTO saidas (aluno, data_saida, horario, motivo, status)
                    VALUES (%s, %s, '12:00', 'Teste de liberação', 'pendente') RETURNING id
                """, (self.student_id, school_now().strftime('%Y-%m-%d'))).fetchone()['id']
            guard = self.staff_login('vigia')
            self.assertEqual(guard.get('/admin/solicitacoes').status_code, 403)
            guard_csrf = self.csrf(guard, '/saidas')
            released = guard.post(f'/concluir_saida/{exit_id}', data={'csrf_token': guard_csrf})
            self.assertEqual(released.location, '/saidas')
            completed_page = guard.get('/saidas').get_data(as_text=True)
            self.assertIn(self.child_name, completed_page)
            self.assertRegex(completed_page, r'Concluídas\s*<span class="section-count">1</span>')
            self.assertNotIn(f'/concluir_saida/{exit_id}', completed_page)
            with self.get_db() as conn:
                released_status = conn.execute('SELECT status FROM saidas WHERE id = %s', (exit_id,)).fetchone()['status']
            self.assertEqual(released_status, 'concluida')
            self.assertEqual(len([item for item in emails if item[0] == self.parent_email and 'liberada' in item[1]]), 1)

            # Uma saída de data anterior vira não realizada ao consultar a lista.
            with self.get_db() as conn:
                expired_id = conn.execute("""
                    INSERT INTO saidas (aluno, data_saida, horario, motivo, status)
                    VALUES (%s, %s, '12:00', 'Teste de expiração', 'pendente') RETURNING id
                """, (self.student_id, (school_now() - timedelta(days=2)).strftime('%Y-%m-%d'))).fetchone()['id']
            admin.get('/saidas')
            with self.get_db() as conn:
                expired_status = conn.execute('SELECT status FROM saidas WHERE id = %s', (expired_id,)).fetchone()['status']
            self.assertEqual(expired_status, 'nao_realizada')
            self.assertEqual(parent.get(f'/pais/historico/{self.student_id}').status_code, 200)
            self.assertEqual(parent.get('/pais/historico/999999').location, '/pais/dashboard')

    def test_rate_limit_is_shared_and_staff_reset_revokes_session(self):
        from app.core.rate_limit import is_limited, record_failure, clear_failures
        marker = secrets.token_hex(8)
        try:
            for _ in range(5):
                record_failure('audit_test', marker)
            self.assertTrue(is_limited('audit_test', marker))
        finally:
            clear_failures('audit_test', marker)
        self.assertFalse(is_limited('audit_test', marker))

        from app.api import web
        client = self.staff_login('admin')
        sent = []

        def capture_email(to, subject, html):
            sent.append(html)
            return True

        staff_email = f"{self.staff_names['admin']}@example.test"
        with patch('app.core.mailer.enviar_email', side_effect=capture_email):
            csrf = self.csrf(client, '/esqueci_senha')
            response = client.post('/esqueci_senha', data={'csrf_token': csrf, 'email': staff_email})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(sent), 1)
        match = re.search(r'/resetar_senha/([A-Za-z0-9_-]+)', sent[0])
        self.assertIsNotNone(match)
        raw_token = match.group(1)
        with self.get_db() as conn:
            stored = conn.execute(
                'SELECT token FROM reset_tokens WHERE user_id = %s', (self.staff_ids[0],),
            ).fetchone()['token']
        self.assertNotEqual(stored, raw_token)
        csrf = self.csrf(client, f'/resetar_senha/{raw_token}')
        response = client.post(f'/resetar_senha/{raw_token}', data={
            'csrf_token': csrf, 'senha': 'NovaSenha@123456', 'confirma': 'NovaSenha@123456',
        })
        self.assertEqual(response.location, '/?resetado=1')
        self.assertEqual(client.get('/configuracoes').location, '/')

    def test_school_sql_syncs_children_and_revokes_stale_links(self):
        from app.services.school_sync import refresh_parent
        marker = secrets.token_hex(8)
        source_parent_id = f'parent_{marker}'
        source_student_ids = [f'student_{marker}_1', f'student_{marker}_2']
        parent_id = None
        local_student_ids = []
        with tempfile.TemporaryDirectory(prefix='secureedu-school-sql-') as query_dir:
            Path(query_dir, 'responsavel.sql').write_text(
                "SELECT external_id AS external_parent_id, email, active "
                "FROM school_test_parents WHERE email = %(email)s", encoding='utf-8')
            Path(query_dir, 'filhos.sql').write_text(
                "SELECT external_id AS external_student_id, name AS student_name, "
                "class_name, grade FROM school_test_children "
                "WHERE parent_external_id = %(external_parent_id)s ORDER BY external_id", encoding='utf-8')
            with self.get_db() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS school_test_parents '
                             '(external_id TEXT PRIMARY KEY, email TEXT UNIQUE, active BOOLEAN NOT NULL)')
                conn.execute('CREATE TABLE IF NOT EXISTS school_test_children '
                             '(external_id TEXT PRIMARY KEY, parent_external_id TEXT, '
                             'name TEXT, class_name TEXT, grade TEXT)')
                parent_id = conn.execute("""
                    INSERT INTO responsaveis (email, nome, password_hash, status)
                    VALUES (%s, 'Responsável SQL', 'hash', 'aprovado') RETURNING id
                """, (self.parent_email,)).fetchone()['id']
                conn.execute('INSERT INTO school_test_parents VALUES (%s, %s, TRUE)',
                             (source_parent_id, self.parent_email))
                for external_id in source_student_ids:
                    conn.execute('INSERT INTO school_test_children VALUES (%s, %s, %s, %s, %s)',
                                 (external_id, source_parent_id, f'Aluno {external_id}', 'A', '1º ano EF'))
            try:
                with patch.dict(os.environ, {
                    'SCHOOL_DIRECTORY_MODE': 'sql',
                    'SCHOOL_SQL_DATABASE_URL': os.environ['SECUREEDU_TEST_DATABASE_URL'],
                    'SCHOOL_SQL_SSLMODE': 'disable',
                    'SCHOOL_SQL_QUERY_DIR': query_dir,
                }):
                    self.assertTrue(refresh_parent(parent_id, self.parent_email))
                    with self.get_db() as conn:
                        local_student_ids = [r['aluno_id'] for r in conn.execute(
                            'SELECT aluno_id FROM vinculos_pais_alunos WHERE responsavel_id = %s',
                            (parent_id,),
                        ).fetchall()]
                    self.assertEqual(len(local_student_ids), 2)
                    parent_client = self.app.test_client()
                    with parent_client.session_transaction() as state:
                        state.update(pai_id=parent_id, pai_email=self.parent_email,
                                     pai_nome='Responsável SQL', auth_version=0)
                    self.assertEqual(parent_client.get('/pais/dashboard').status_code, 200)
                    with self.get_db() as conn:
                        conn.execute('DELETE FROM school_test_children WHERE external_id = %s',
                                     (source_student_ids[1],))
                    self.assertTrue(refresh_parent(parent_id, self.parent_email))
                    with self.get_db() as conn:
                        linked = conn.execute(
                            'SELECT aluno_id FROM vinculos_pais_alunos WHERE responsavel_id = %s',
                            (parent_id,),
                        ).fetchall()
                        preserved = conn.execute('SELECT id FROM alunos WHERE id = %s',
                                                 (local_student_ids[1],)).fetchone()
                    self.assertEqual(len(linked), 1)
                    self.assertIsNotNone(preserved)
                    with self.get_db() as conn:
                        conn.execute('UPDATE school_test_parents SET active = FALSE WHERE external_id = %s',
                                     (source_parent_id,))
                    self.assertFalse(refresh_parent(parent_id, self.parent_email))
                    self.assertEqual(parent_client.get('/pais/dashboard').location, '/pais/login')
            finally:
                with self.get_db() as conn:
                    conn.execute('DELETE FROM vinculos_pais_alunos WHERE responsavel_id = %s', (parent_id,))
                    conn.execute('DELETE FROM alunos WHERE school_external_id = ANY(%s)', (source_student_ids,))
                    conn.execute('DELETE FROM responsaveis WHERE id = %s', (parent_id,))
                    conn.execute('DELETE FROM school_test_children WHERE parent_external_id = %s',
                                 (source_parent_id,))
                    conn.execute('DELETE FROM school_test_parents WHERE external_id = %s',
                                 (source_parent_id,))

    def test_prepare_remote_demo_marks_only_selected_fake_family(self):
        import prepare_demo
        from app.services.school_sync import refresh_parent

        with self.get_db() as conn:
            self.parent_id = conn.execute("""
                INSERT INTO responsaveis (email, nome, password_hash, status)
                VALUES (%s, 'Responsável Fictício', 'hash', 'aprovado') RETURNING id
            """, (self.parent_email,)).fetchone()['id']
            conn.execute('INSERT INTO vinculos_pais_alunos (responsavel_id, aluno_id) VALUES (%s, %s)',
                         (self.parent_id, self.student_id))
        with patch.dict(os.environ, {
            'APP_ENV': 'test', 'BASE_URL': 'https://portal.example.test',
            'SCHOOL_DIRECTORY_MODE': 'demo', 'SCHOOL_DEMO_REMOTE_ALLOWED': 'true',
            'SCHOOL_DEMO_EMAILS': self.parent_email,
        }), patch('sys.argv', ['prepare_demo.py', '--email', self.parent_email]), \
                patch('builtins.input', return_value='MARCAR FICTICIOS'), \
                patch('builtins.print'):
            self.assertFalse(refresh_parent(self.parent_id, self.parent_email))
            prepare_demo.main()
            self.assertTrue(refresh_parent(self.parent_id, self.parent_email))
        with self.get_db() as conn:
            parent_flag = conn.execute('SELECT is_demo FROM responsaveis WHERE id = %s',
                                       (self.parent_id,)).fetchone()['is_demo']
            child_flag = conn.execute('SELECT is_demo FROM alunos WHERE id = %s',
                                      (self.student_id,)).fetchone()['is_demo']
        self.assertTrue(parent_flag)
        self.assertTrue(child_flag)


if __name__ == '__main__':
    unittest.main()
