"""Reconciliação local e atômica dos vínculos vindos da escola."""

import os
from urllib.parse import urlparse

from app.core.database import get_db
from app.services.school_directory import SchoolDirectoryError, load_household


def _demo_allowed(email: str) -> bool:
    if (os.getenv('APP_ENV', 'production').lower() not in {'development', 'test'}
            or urlparse(os.getenv('BASE_URL', '')).hostname not in {'localhost', '127.0.0.1', '::1'}):
        raise SchoolDirectoryError('Demonstração permitida somente em ambiente local')
    allowed = {item.strip().lower() for item in os.getenv('SCHOOL_DEMO_EMAILS', '').split(',') if item.strip()}
    return email.strip().lower() in allowed


def refresh_parent(parent_id: int, email: str) -> bool:
    """Falha fechada. Em SQL, a consulta escolar é a fonte autoritativa dos vínculos."""
    mode = os.getenv('SCHOOL_DIRECTORY_MODE', 'off').lower()
    if mode == 'demo':
        return _demo_allowed(email)
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
