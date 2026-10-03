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
            SELECT id, nome, status FROM responsaveis WHERE lower(email) = %s
        """, (email,)).fetchone()
        if not account or account['status'] != 'aprovado':
            parser.error('Conta fictícia não encontrada ou ainda não aprovada')
        children = conn.execute("""
            SELECT a.id, a.nome, a.school_external_id
            FROM vinculos_pais_alunos v JOIN alunos a ON a.id = v.aluno_id
            WHERE v.responsavel_id = %s ORDER BY a.id
        """, (account['id'],)).fetchall()
    if not children:
        parser.error('A conta não tem alunos vinculados; prepare os dados fictícios antes')
    if any(row['school_external_id'] and not row['school_external_id'].startswith('demo:')
           for row in children):
        parser.error('Há aluno com identificador escolar oficial; operação recusada')

    print(f"Conta selecionada: ID {account['id']} — {account['nome']} ({email})")
    print('Alunos vinculados que serão marcados como fictícios:')
    for row in children:
        print(f"  ID {row['id']} — {row['nome']}")
    if input('Se todos são fictícios, digite MARCAR FICTICIOS: ').strip() != 'MARCAR FICTICIOS':
        print('Cancelado sem alterações.')
        return

    ids = [row['id'] for row in children]
    with get_db() as conn:
        current = conn.execute("""
            SELECT aluno_id FROM vinculos_pais_alunos
            WHERE responsavel_id = %s ORDER BY aluno_id FOR UPDATE
        """, (account['id'],)).fetchall()
        if [row['aluno_id'] for row in current] != ids:
            raise RuntimeError('Os vínculos mudaram durante a confirmação; operação cancelada')
        conn.execute('UPDATE responsaveis SET is_demo = TRUE WHERE id = %s AND status = %s',
                     (account['id'], 'aprovado'))
        conn.execute('UPDATE alunos SET is_demo = TRUE WHERE id = ANY(%s)', (ids,))
    print('Conta e alunos fictícios preparados para a demonstração.')


if __name__ == '__main__':
    main()
