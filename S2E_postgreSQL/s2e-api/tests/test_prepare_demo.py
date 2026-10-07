"""O preparo de demonstração nunca associa um aluno escolar por suposição."""

import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

import prepare_demo


class _Result:
    def __init__(self, one=None, many=None):
        self.one = one
        self.many = many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class _Db:
    def __init__(self, external_id=None):
        self.external_id = external_id
        self.writes = []

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        if normalized.startswith('SELECT id, nome, status, school_external_id FROM responsaveis'):
            return _Result(one={'id': 9, 'nome': 'Responsável Fictício',
                                'status': 'aprovado', 'school_external_id': self.external_id})
        if normalized.startswith('SELECT a.id, a.nome, a.school_external_id'):
            return _Result(many=[])
        if normalized.startswith('SELECT status, school_external_id FROM responsaveis'):
            return _Result(one={'status': 'aprovado', 'school_external_id': self.external_id})
        if normalized.startswith('SELECT aluno_id FROM vinculos_pais_alunos'):
            return _Result(many=[])
        self.writes.append((normalized, params))
        if normalized.startswith('INSERT INTO alunos'):
            return _Result(one={'id': 42})
        return _Result()


class PrepareDemoTests(unittest.TestCase):
    def run_command(self, db, confirmation='MARCAR FICTICIOS'):
        @contextlib.contextmanager
        def connection():
            yield db

        settings = {
            'SCHOOL_DIRECTORY_MODE': 'demo',
            'SCHOOL_DEMO_REMOTE_ALLOWED': 'true',
            'SCHOOL_DEMO_EMAILS': 'parent@example.test',
            'BASE_URL': 'https://example.test',
        }
        with patch.dict(os.environ, settings), patch.object(sys, 'argv', [
                'prepare_demo.py', '--email', 'parent@example.test', '--create-demo-child']), \
                patch.object(prepare_demo, 'get_db', connection), \
                patch('builtins.input', return_value=confirmation), \
                contextlib.redirect_stdout(io.StringIO()):
            prepare_demo.main()

    def test_creates_three_synthetic_children_after_confirmation(self):
        db = _Db()
        self.run_command(db)
        self.assertEqual(len(db.writes), 8)
        self.assertIn('demo:director:9:child-1', db.writes[0][1])
        self.assertIn('TESTE — Aluno Fictício 1', db.writes[0][1])
        self.assertEqual(db.writes[1][1], (9, 42))
        self.assertIn('demo:director:9:child-2', db.writes[2][1])
        self.assertIn('demo:director:9:child-3', db.writes[4][1])
        self.assertEqual(db.writes[-1][1], ([42, 42, 42],))

    def test_no_confirmation_makes_no_changes(self):
        db = _Db()
        self.run_command(db, confirmation='')
        self.assertEqual(db.writes, [])

    def test_official_parent_is_refused(self):
        db = _Db(external_id='school:real-parent')
        with self.assertRaises(SystemExit):
            self.run_command(db)
        self.assertEqual(db.writes, [])


if __name__ == '__main__':
    unittest.main()
