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
    def __init__(self, conflict=False):
        self.conflict = conflict
        self.inserted = []

    def execute(self, sql, params=()):
        if 'SELECT username, email FROM usuarios' in sql:
            return SimpleNamespace(fetchall=lambda: [{'username': 'existing'}] if self.conflict else [])
        if 'SELECT email FROM responsaveis' in sql:
            return SimpleNamespace(fetchall=lambda: [])
        if 'INSERT INTO usuarios' in sql:
            self.inserted.append(params)
        return SimpleNamespace(fetchall=lambda: [])


class PrepareDirectorStaffTests(unittest.TestCase):
    def run_script(self, connection, emails):
        @contextmanager
        def fake_db():
            yield connection

        fake_sys = SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True),
        )
        args = ['prepare_director_staff.py', '--basic-email', emails[0],
                '--advanced-email', emails[1], '--security-email', emails[2]]
        output = io.StringIO()
        with patch.dict(os.environ, {
            'SCHOOL_DIRECTORY_MODE': 'demo', 'SCHOOL_DEMO_REMOTE_ALLOWED': 'true',
            'BASE_URL': 'https://example.test',
        }), patch('sys.argv', args), patch.object(prepare_director_staff, 'sys', fake_sys), \
                patch.object(prepare_director_staff, 'get_db', fake_db), \
                patch('builtins.input', return_value='CRIAR TESTE PATRICK'), redirect_stdout(output):
            prepare_director_staff.main()
        return output.getvalue()

    def test_creates_only_three_named_accounts_with_distinct_passwords(self):
        connection = FakeConnection()
        output = self.run_script(connection, [f'{role}@example.test' for role in ('basic', 'advanced', 'security')])
        self.assertEqual([row[0] for row in connection.inserted],
                         ['teste_patrick_basico', 'teste_patrick_avancado', 'teste_patrick_seguranca'])
        self.assertEqual([row[2] for row in connection.inserted], ['basico', 'admin', 'vigia'])
        displayed = [line.split(': ', 1)[1] for line in output.splitlines()
                     if line.startswith('teste_patrick_') and ': ' in line]
        self.assertEqual(len(displayed), 3)
        self.assertEqual(len(set(displayed)), 3)
        for row, password in zip(connection.inserted, displayed):
            self.assertTrue(check_password_hash(row[1], password))

    def test_conflict_does_not_modify_existing_accounts(self):
        connection = FakeConnection(conflict=True)
        with self.assertRaisesRegex(SystemExit, 'já existe'):
            self.run_script(connection, [f'{role}@example.test' for role in ('basic', 'advanced', 'security')])
        self.assertEqual(connection.inserted, [])

    def test_rejects_reused_email(self):
        connection = FakeConnection()
        with self.assertRaises(SystemExit):
            self.run_script(connection, ['same@example.test', 'same@example.test', 'security@example.test'])
        self.assertEqual(connection.inserted, [])


if __name__ == '__main__':
    unittest.main()
