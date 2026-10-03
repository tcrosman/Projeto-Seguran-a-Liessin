"""Reconciliação local e atômica dos vínculos vindos da escola."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from app.core.database import get_db
from app.services.school_directory import SchoolDirectoryError, load_household


def _demo_allowed(email: str, parent_id: int | None = None) -> bool:
    environment = os.getenv('APP_ENV', 'production').lower()
    base = urlparse(os.getenv('BASE_URL', ''))
    allowed = {item.strip().lower() for item in os.getenv('SCHOOL_DEMO_EMAILS', '').split(',') if item.strip()}
    normalized_email = email.strip().lower()
    if normalized_email not in allowed:
        return False
    if environment in {'development', 'test'} and base.hostname in {'localhost', '127.0.0.1', '::1'}:
        return True
    if (environment not in {'test', 'staging', 'production'} or base.scheme != 'https'
            or os.getenv('SCHOOL_DEMO_REMOTE_ALLOWED', 'false').lower() != 'true'
            or parent_id is None):
        raise SchoolDirectoryError('Demonstração remota não autorizada')
    # No site público, a lista de e-mails sozinha não basta: a conta e todos
    # os alunos ligados a ela precisam estar explicitamente marcados como fake.
    with get_db() as conn:
        account = conn.execute("""
            SELECT is_demo FROM responsaveis
            WHERE id = %s AND lower(email) = %s AND status = 'aprovado'
        """, (parent_id, normalized_email)).fetchone()
        non_demo_child = conn.execute("""
            SELECT 1 FROM vinculos_pais_alunos v
            JOIN alunos a ON a.id = v.aluno_id
            WHERE v.responsavel_id = %s AND a.is_demo = FALSE LIMIT 1
        """, (parent_id,)).fetchone()
    return bool(account and account['is_demo'] is True and not non_demo_child)


def refresh_parent(parent_id: int, email: str) -> bool:
    """Falha fechada. Em SQL, a consulta escolar é a fonte autoritativa dos vínculos."""
    mode = os.getenv('SCHOOL_DIRECTORY_MODE', 'off').lower()
    if mode == 'demo':
        return _demo_allowed(email, parent_id)
    if mode != 'sql':
        raise SchoolDirectoryError('Diretório escolar não configurado')
    household = load_household(email)
    if household is None:
        return False

    with get_db() as conn:
        parent = conn.execute(
            'SELECT school_external_id FROM responsaveis WHERE id = %s FOR UPDATE',
            (parent_id,),
        ).fetchone()
        if not parent or (parent['school_external_id'] is not None
                          and parent['school_external_id'] != household.external_parent_id):
            return False
        conn.execute(
            'UPDATE responsaveis SET school_external_id = %s WHERE id = %s',
            (household.external_parent_id, parent_id),
        )
        student_ids = []
        for student in household.students:
            row = conn.execute("""
                INSERT INTO alunos (school_external_id, nome, turma, serie)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (school_external_id) DO UPDATE SET
                    nome = EXCLUDED.nome, turma = EXCLUDED.turma, serie = EXCLUDED.serie
                RETURNING id
            """, (student.external_id, student.name, student.class_name, student.grade)).fetchone()
            student_ids.append(row['id'])
            conn.execute("""
                INSERT INTO vinculos_pais_alunos (responsavel_id, aluno_id)
                VALUES (%s, %s) ON CONFLICT (responsavel_id, aluno_id) DO NOTHING
            """, (parent_id, row['id']))
        # A consulta precisa retornar a lista completa. Remove inclusive vínculos
        # manuais antigos que a fonte escolar não confirmou, sem apagar alunos/histórico.
        if student_ids:
            conn.execute(
                'DELETE FROM vinculos_pais_alunos WHERE responsavel_id = %s AND NOT (aluno_id = ANY(%s))',
                (parent_id, student_ids),
            )
        else:
            conn.execute('DELETE FROM vinculos_pais_alunos WHERE responsavel_id = %s', (parent_id,))
    return True
