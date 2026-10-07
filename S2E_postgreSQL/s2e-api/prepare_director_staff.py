"""Cria três contas fictícias e exclusivas do diretor, sem tocar nas contas homolog.

Executar no VPS com o e-mail do responsável fictício, após backup e implantação.
As senhas aleatórias aparecem uma única vez no terminal e nunca são gravadas em arquivo.
As contas de equipe não têm e-mail, evitando recuperação ambígua pelo único endereço do diretor.
"""

import argparse
import os
import secrets
import sys
from urllib.parse import urlparse

from werkzeug.security import generate_password_hash

from app.core.database import get_db


ACCOUNTS = (
    ('teste_patrick_basico', 'basico'),
    ('teste_patrick_avancado', 'admin'),
    ('teste_patrick_seguranca', 'vigia'),
)


def _password():
    # Prefixo garante a política de força; o restante vem de fonte criptográfica.
    return 'A1!' + secrets.token_urlsafe(24)


def main():
    parser = argparse.ArgumentParser(description='Criar acessos fictícios exclusivos do diretor')
    parser.add_argument('--parent-email', required=True,
                        help='E-mail da conta de pai fictício já aprovada e autorizada')
    args = parser.parse_args()
    parent_email = args.parent_email.strip().lower()

    if (os.getenv('SCHOOL_DIRECTORY_MODE', '').lower() != 'demo'
            or os.getenv('SCHOOL_DEMO_REMOTE_ALLOWED', '').lower() != 'true'
            or urlparse(os.getenv('BASE_URL', '')).scheme != 'https'):
        parser.error('A demonstração remota não está configurada; operação recusada')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Use um terminal interativo para evitar gravação acidental das senhas')

    allowed = {item.strip().lower() for item in os.getenv('SCHOOL_DEMO_EMAILS', '').split(',') if item.strip()}
    if parent_email not in allowed:
        parser.error('O responsável não está autorizado para demonstração remota')

    print('Serão criadas apenas estas contas de equipe, sem e-mail de recuperação:')
    for username, role in ACCOUNTS:
        print(f'  {username} ({role})')
    print(f'A conta de pai existente {parent_email} não terá a senha alterada.')
    if input('Para confirmar, digite CRIAR TESTE PATRICK: ').strip() != 'CRIAR TESTE PATRICK':
        print('Cancelado sem alterações.')
        return

    passwords = {username: _password() for username, _ in ACCOUNTS}
    with get_db() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (746392020,))
        parent = conn.execute('''
            SELECT id, status, is_demo, school_external_id
            FROM responsaveis WHERE lower(email) = %s FOR UPDATE
        ''', (parent_email,)).fetchone()
        if (not parent or parent['status'] != 'aprovado' or parent['is_demo'] is not True
                or (parent['school_external_id'] and not parent['school_external_id'].startswith('demo:'))):
            raise SystemExit('Conta de pai fictício não aprovada ou não verificada; nada foi criado.')
        children = conn.execute('''
            SELECT a.is_demo, a.school_external_id
            FROM vinculos_pais_alunos v JOIN alunos a ON a.id = v.aluno_id
            WHERE v.responsavel_id = %s
        ''', (parent['id'],)).fetchall()
        if not children or any(
            child['is_demo'] is not True or
            (child['school_external_id'] and not child['school_external_id'].startswith('demo:'))
            for child in children
        ):
            raise SystemExit('A conta de pai não tem somente filhos fictícios; nada foi criado.')
        usernames = [username for username, _ in ACCOUNTS]
        existing = conn.execute('''
            SELECT username FROM usuarios WHERE lower(username) = ANY(%s)
        ''', (usernames,)).fetchall()
        if existing:
            raise SystemExit('Nome de conta já existe; nenhuma conta foi criada ou alterada.')
        for username, role in ACCOUNTS:
            conn.execute('''
                INSERT INTO usuarios (username, password, role, email)
                VALUES (%s, %s, %s, %s)
            ''', (username, generate_password_hash(passwords[username], method='pbkdf2:sha256'),
                  role, None))

    print('\nContas criadas. Copie as senhas abaixo agora; elas não poderão ser exibidas novamente:')
    for username, _ in ACCOUNTS:
        print(f'{username}: {passwords[username]}')


if __name__ == '__main__':
    main()
