"""Testa o preparo dos acessos sem conectar ao VPS ou a um banco real."""

import io
import os
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.security import check_password_hash

import prepare_director_staff


class FakeConnection:
    def __init__(self, conflict=False, parent_demo=True, child_demo=True):
        self.conflict = conflict
        self.parent_demo = parent_demo
        self.child_demo = child_demo
        self.inserted = []

    def execute(self, sql, params=()):
        if 'SELECT username FROM usuarios' in sql:
            return SimpleNamespace(fetchall=lambda: [{'username': 'existing'}] if self.conflict else [])
        if 'FROM responsaveis WHERE lower(email)' in sql:
            return SimpleNamespace(fetchone=lambda: {
                'id': 12, 'status': 'aprovado', 'is_demo': self.parent_demo,
                'school_external_id': 'demo:director:12',
            })
        if 'FROM vinculos_pais_alunos v' in sql:
            return SimpleNamespace(fetchall=lambda: [{
                'is_demo': self.child_demo, 'school_external_id': 'demo:director:12:child-1',
            }])
        if 'INSERT INTO usuarios' in sql:
            self.inserted.append(params)
        return SimpleNamespace(fetchall=lambda: [])


class PrepareDirectorStaffTests(unittest.TestCase):
    def run_script(self, connection, parent_email='patrick@example.test'):
        @contextmanager
        def fake_db():
            yield connection

        fake_sys = SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True),
        )
        args = ['prepare_director_staff.py', '--parent-email', parent_email]
        output = io.StringIO()
        with patch.dict(os.environ, {
            'SCHOOL_DIRECTORY_MODE': 'demo', 'SCHOOL_DEMO_REMOTE_ALLOWED': 'true',
            'BASE_URL': 'https://example.test', 'SCHOOL_DEMO_EMAILS': 'patrick@example.test',
        }), patch('sys.argv', args), patch.object(prepare_director_staff, 'sys', fake_sys), \
                patch.object(prepare_director_staff, 'get_db', fake_db), \
                patch('builtins.input', return_value='CRIAR TESTE PATRICK'), redirect_stdout(output):
            prepare_director_staff.main()
        return output.getvalue()

    def test_creates_only_three_named_accounts_with_distinct_passwords(self):
        connection = FakeConnection()
        output = self.run_script(connection)
        self.assertEqual([row[0] for row in connection.inserted],
                         ['teste_patrick_basico', 'teste_patrick_avancado', 'teste_patrick_seguranca'])
        self.assertEqual([row[2] for row in connection.inserted], ['basico', 'admin', 'vigia'])
        self.assertEqual([row[3] for row in connection.inserted], [None, None, None])
        displayed = [line.split(': ', 1)[1] for line in output.splitlines()
                     if line.startswith('teste_patrick_') and ': ' in line]
        self.assertEqual(len(displayed), 3)
        self.assertEqual(len(set(displayed)), 3)
        for row, password in zip(connection.inserted, displayed):
            self.assertTrue(check_password_hash(row[1], password))

    def test_conflict_does_not_modify_existing_accounts(self):
        connection = FakeConnection(conflict=True)
        with self.assertRaisesRegex(SystemExit, 'já existe'):
            self.run_script(connection)
        self.assertEqual(connection.inserted, [])

    def test_rejects_unverified_parent_or_child(self):
        connection = FakeConnection(parent_demo=False)
        with self.assertRaisesRegex(SystemExit, 'não aprovada'):
            self.run_script(connection)
        self.assertEqual(connection.inserted, [])
        connection = FakeConnection(child_demo=False)
        with self.assertRaisesRegex(SystemExit, 'filhos fictícios'):
            self.run_script(connection)
        self.assertEqual(connection.inserted, [])

    def test_rejects_parent_outside_demo_allowlist(self):
        connection = FakeConnection()
        with self.assertRaises(SystemExit):
            self.run_script(connection, parent_email='outro@example.test')
        self.assertEqual(connection.inserted, [])


if __name__ == '__main__':
    unittest.main()
