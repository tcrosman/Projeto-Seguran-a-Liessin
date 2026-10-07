"""Marca uma conta já aprovada e seus alunos fictícios para teste remoto.

Uso no servidor, após a migração e antes de convidar o diretor:
    sudo -u secureedu .venv/bin/python prepare_demo.py --email EMAIL_DE_TESTE

Não cria senha, não altera outras contas e exige confirmação interativa.
"""

import argparse
import os
from urllib.parse import urlparse

from app.core.database import get_db


def main():
    parser = argparse.ArgumentParser(description='Preparar conta fictícia para demonstração')
    parser.add_argument('--email', required=True, help='E-mail da conta fictícia já aprovada')
    parser.add_argument('--create-demo-children', action='store_true',
                        help='Criar três alunos sintéticos se a conta ainda não tiver filhos')
    # Compatibilidade com o comando usado na preparação anterior.
    parser.add_argument('--create-demo-child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    email = args.email.strip().lower()
    allowed = {item.strip().lower() for item in os.getenv('SCHOOL_DEMO_EMAILS', '').split(',') if item.strip()}
    base = urlparse(os.getenv('BASE_URL', ''))
    if (os.getenv('SCHOOL_DIRECTORY_MODE', '').lower() != 'demo'
            or os.getenv('SCHOOL_DEMO_REMOTE_ALLOWED', '').lower() != 'true'
            or base.scheme != 'https' or email not in allowed):
        parser.error('Modo de demonstração remota e e-mail autorizado não configurados')

    with get_db() as conn:
        account = conn.execute("""
            SELECT id, nome, status, school_external_id FROM responsaveis WHERE lower(email) = %s
        """, (email,)).fetchone()
        if not account or account['status'] != 'aprovado':
            parser.error('Conta fictícia não encontrada ou ainda não aprovada')
        if account['school_external_id'] and not account['school_external_id'].startswith('demo:'):
            parser.error('A conta tem identificador escolar oficial; operação recusada')
        children = conn.execute("""
            SELECT a.id, a.nome, a.school_external_id
            FROM vinculos_pais_alunos v JOIN alunos a ON a.id = v.aluno_id
            WHERE v.responsavel_id = %s ORDER BY a.id
        """, (account['id'],)).fetchall()
    create_children = args.create_demo_children or args.create_demo_child
    if not children and not create_children:
        parser.error('A conta não tem alunos vinculados; prepare os dados fictícios antes')
    if any(row['school_external_id'] and not row['school_external_id'].startswith('demo:')
           for row in children):
        parser.error('Há aluno com identificador escolar oficial; operação recusada')

    print(f"Conta selecionada: ID {account['id']} — {account['nome']} ({email})")
    if children:
        print('Alunos vinculados que serão marcados como fictícios:')
        for row in children:
            print(f"  ID {row['id']} — {row['nome']}")
    else:
        print('Nenhum aluno vinculado. Serão criados três alunos sintéticos:')
        print('  TESTE — Aluno Fictício 1 (6º ano EF, turma A)')
        print('  TESTE — Aluno Fictício 2 (8º ano EF, turma B)')
        print('  TESTE — Aluno Fictício 3 (3º ano EM, turma A)')
    if input('Se todos são fictícios, digite MARCAR FICTICIOS: ').strip() != 'MARCAR FICTICIOS':
        print('Cancelado sem alterações.')
        return

    ids = [row['id'] for row in children]
    with get_db() as conn:
        current_account = conn.execute("""
            SELECT status, school_external_id FROM responsaveis WHERE id = %s FOR UPDATE
        """, (account['id'],)).fetchone()
        if (not current_account or current_account['status'] != 'aprovado'
                or (current_account['school_external_id']
                    and not current_account['school_external_id'].startswith('demo:'))):
            raise RuntimeError('A conta mudou durante a confirmação; operação cancelada')
        current = conn.execute("""
            SELECT aluno_id FROM vinculos_pais_alunos
            WHERE responsavel_id = %s ORDER BY aluno_id FOR UPDATE
        """, (account['id'],)).fetchall()
        if [row['aluno_id'] for row in current] != ids:
            raise RuntimeError('Os vínculos mudaram durante a confirmação; operação cancelada')
        if create_children and not ids:
            demo_children = (
                ('child-1', 'TESTE — Aluno Fictício 1', 'A', '6º ano EF'),
                ('child-2', 'TESTE — Aluno Fictício 2', 'B', '8º ano EF'),
                ('child-3', 'TESTE — Aluno Fictício 3', 'A', '3º ano EM'),
            )
            for suffix, name, class_group, grade in demo_children:
                external_id = f"demo:director:{account['id']}:{suffix}"
                child = conn.execute("""
                    INSERT INTO alunos (school_external_id, nome, turma, serie, is_demo)
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT (school_external_id) DO NOTHING
                    RETURNING id
                """, (external_id, name, class_group, grade)).fetchone()
                if not child:
                    raise RuntimeError('Aluno sintético já existe; operação cancelada para evitar duplicidade')
                ids.append(child['id'])
                conn.execute("""
                    INSERT INTO vinculos_pais_alunos (responsavel_id, aluno_id)
                    VALUES (%s, %s)
                """, (account['id'], child['id']))
        else:
            current_children = conn.execute("""
                SELECT id, school_external_id FROM alunos WHERE id = ANY(%s) FOR UPDATE
            """, (ids,)).fetchall()
            if len(current_children) != len(ids) or any(
                    row['school_external_id'] and not row['school_external_id'].startswith('demo:')
                    for row in current_children):
                raise RuntimeError('Um aluno mudou durante a confirmação; operação cancelada')
        conn.execute('UPDATE responsaveis SET is_demo = TRUE WHERE id = %s AND status = %s',
                     (account['id'], 'aprovado'))
        conn.execute('UPDATE alunos SET is_demo = TRUE WHERE id = ANY(%s)', (ids,))
    print('Conta e alunos fictícios preparados para a demonstração.')


if __name__ == '__main__':
    main()
