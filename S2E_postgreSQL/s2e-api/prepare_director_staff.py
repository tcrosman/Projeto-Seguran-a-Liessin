"""Cria três contas fictícias e exclusivas do diretor, sem tocar nas contas homolog.

Executar no VPS com três e-mails distintos, após backup e implantação desta branch.
As senhas aleatórias aparecem uma única vez no terminal e nunca são gravadas em arquivo.
"""

import argparse
import os
import re
import secrets
import sys
from urllib.parse import urlparse

from werkzeug.security import generate_password_hash

from app.core.database import get_db


ACCOUNTS = (
    ('teste_patrick_basico', 'basico', 'basic_email'),
    ('teste_patrick_avancado', 'admin', 'advanced_email'),
    ('teste_patrick_seguranca', 'vigia', 'security_email'),
)


def _password():
    # Prefixo garante a política de força; o restante vem de fonte criptográfica.
    return 'A1!' + secrets.token_urlsafe(24)


def main():
    parser = argparse.ArgumentParser(description='Criar acessos fictícios exclusivos do diretor')
    parser.add_argument('--basic-email', required=True)
    parser.add_argument('--advanced-email', required=True)
    parser.add_argument('--security-email', required=True)
    args = parser.parse_args()

    if (os.getenv('SCHOOL_DIRECTORY_MODE', '').lower() != 'demo'
            or os.getenv('SCHOOL_DEMO_REMOTE_ALLOWED', '').lower() != 'true'
            or urlparse(os.getenv('BASE_URL', '')).scheme != 'https'):
        parser.error('A demonstração remota não está configurada; operação recusada')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Use um terminal interativo para evitar gravação acidental das senhas')

    emails = {name: getattr(args, name).strip().lower()
              for name in ('basic_email', 'advanced_email', 'security_email')}
    if (len(set(emails.values())) != 3
            or any(not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) for email in emails.values())):
        parser.error('Informe três e-mails válidos e distintos')

    print('Serão criadas apenas estas contas:')
    for username, role, email_key in ACCOUNTS:
        print(f'  {username} ({role}) — {emails[email_key]}')
    if input('Para confirmar, digite CRIAR TESTE PATRICK: ').strip() != 'CRIAR TESTE PATRICK':
        print('Cancelado sem alterações.')
        return

    passwords = {username: _password() for username, _, _ in ACCOUNTS}
    with get_db() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (746392020,))
        usernames = [username for username, _, _ in ACCOUNTS]
        requested_emails = list(emails.values())
        existing = conn.execute('''
            SELECT username, email FROM usuarios
            WHERE lower(username) = ANY(%s) OR lower(email) = ANY(%s)
        ''', (usernames, requested_emails)).fetchall()
        parent = conn.execute('''
            SELECT email FROM responsaveis WHERE lower(email) = ANY(%s)
        ''', (requested_emails,)).fetchall()
        if existing or parent:
            raise SystemExit('Nome ou e-mail já existe; nenhuma conta foi criada ou alterada.')
        for username, role, email_key in ACCOUNTS:
            conn.execute('''
                INSERT INTO usuarios (username, password, role, email)
                VALUES (%s, %s, %s, %s)
            ''', (username, generate_password_hash(passwords[username], method='pbkdf2:sha256'),
                  role, emails[email_key]))

    print('\nContas criadas. Copie as senhas abaixo agora; elas não poderão ser exibidas novamente:')
    for username, _, _ in ACCOUNTS:
        print(f'{username}: {passwords[username]}')


if __name__ == '__main__':
    main()
