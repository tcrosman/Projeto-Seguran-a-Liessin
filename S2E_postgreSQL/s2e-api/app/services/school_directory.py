"""Consultas de leitura fornecidas pela escola; nunca escreve no PostgreSQL escolar."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor


class SchoolDirectoryError(Exception):
    """Integração indisponível ou resposta fora do contrato; não expõe dados externos."""


@dataclass(frozen=True)
class SchoolStudent:
    external_id: str
    name: str
    class_name: str
    grade: str


@dataclass(frozen=True)
class SchoolHousehold:
    external_parent_id: str
    email: str
    students: tuple[SchoolStudent, ...]


def _query(filename: str, parameter: str) -> str:
    folder = os.getenv('SCHOOL_SQL_QUERY_DIR', '').strip()
    if not folder:
        raise SchoolDirectoryError('Consultas da escola não configuradas')
    try:
        sql = (Path(folder) / filename).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError) as exc:
        raise SchoolDirectoryError('Consulta da escola indisponível') from exc
    statement = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--')).strip()
    if (not statement.upper().startswith(('SELECT ', 'WITH '))
            or ';' in statement.rstrip(';') or f'%({parameter})s' not in statement):
        raise SchoolDirectoryError('Consulta da escola fora do contrato')
    return sql


def _required_text(row, key, max_length=255):
    value = row.get(key)
    if value is None or not str(value).strip() or len(str(value)) > max_length:
        raise SchoolDirectoryError('Resposta da escola fora do contrato')
    return str(value).strip()


def load_household(email: str) -> SchoolHousehold | None:
    """Lê um responsável e sua lista *completa* de filhos ativos na mesma transação."""
    url = os.getenv('SCHOOL_SQL_DATABASE_URL', '').strip()
    parsed = urlparse(url)
    if parsed.scheme not in {'postgres', 'postgresql'} or not parsed.hostname:
        raise SchoolDirectoryError('Banco de consulta da escola não configurado')
    sslmode = os.getenv('SCHOOL_SQL_SSLMODE', 'verify-full')
    local_test = (os.getenv('APP_ENV') == 'test'
                  and parsed.hostname in {'localhost', '127.0.0.1', '::1'})
    if sslmode not in {'verify-ca', 'verify-full'} and not (local_test and sslmode == 'disable'):
        raise SchoolDirectoryError('TLS do banco escolar não configurado com segurança')
    parent_sql = _query('responsavel.sql', 'email')
    children_sql = _query('filhos.sql', 'external_parent_id')
    try:
        connection = psycopg2.connect(url, sslmode=sslmode, connect_timeout=5,
                                      application_name='secureedu-school-readonly')
        try:
            connection.set_session(readonly=True)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                cursor.execute(parent_sql, {'email': email})
                parents = cursor.fetchmany(2)
                if not parents:
                    return None
                if len(parents) != 1:
                    raise SchoolDirectoryError('Identidade escolar ambígua')
                parent = parents[0]
                external_id = _required_text(parent, 'external_parent_id', 128)
                source_email = _required_text(parent, 'email').lower()
                if source_email != email.strip().lower() or parent.get('active') is not True:
                    return None
                cursor.execute(children_sql, {'external_parent_id': external_id})
                rows = cursor.fetchall()
                if len(rows) > 30:
                    raise SchoolDirectoryError('Quantidade de vínculos fora do contrato')
                students = tuple(SchoolStudent(
                    external_id=_required_text(row, 'external_student_id', 128),
                    name=_required_text(row, 'student_name'),
                    class_name=_required_text(row, 'class_name', 100),
                    grade=_required_text(row, 'grade', 100),
                ) for row in rows)
                if len({student.external_id for student in students}) != len(students):
                    raise SchoolDirectoryError('Vínculos escolares duplicados')
                return SchoolHousehold(external_id, source_email, students)
        finally:
            connection.close()
    except SchoolDirectoryError:
        raise
    except (psycopg2.Error, OSError, ValueError) as exc:
        raise SchoolDirectoryError('Consulta da escola indisponível') from exc
