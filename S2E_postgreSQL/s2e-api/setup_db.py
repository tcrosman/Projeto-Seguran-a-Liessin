import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

from app.core.passwords import verificar_forca

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

sys.path.insert(0, str(Path(__file__).parent))

from app.core.migrations import reset_database, run_migrations
from app.core.database import migrate_database, aplicar_chaves_estrangeiras, get_db


def main():
    parser = argparse.ArgumentParser(description="Prepara o banco PostgreSQL do SecureEdu.")
    parser.add_argument(
        '--reset', action='store_true',
        help='APAGA as tabelas existentes antes de recriá-las (somente desenvolvimento).',
    )
    args = parser.parse_args()

    if args.reset:
        confirmacao = os.getenv('CONFIRM_RESET_DATABASE', '')
        if confirmacao != 'APAGAR-TODOS-OS-DADOS':
            raise SystemExit(
                "Reset recusado. Para confirmar a exclusão, defina "
                "CONFIRM_RESET_DATABASE=APAGAR-TODOS-OS-DADOS e execute novamente."
            )
        print("Confirmação recebida: recriando o banco PostgreSQL...")
        reset_database()
    else:
        print("Aplicando schema e migrações sem apagar dados...")

    migrate_database()
    run_migrations()
    aplicar_chaves_estrangeiras()

    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as total FROM usuarios").fetchone()['total']
        if total == 0:
            senha_inicial = os.getenv('INITIAL_ADMIN_PASSWORD', '')
            erro_senha = verificar_forca(senha_inicial)
            if erro_senha:
                raise SystemExit(
                    "Banco preparado, mas o administrador não foi criado: defina "
                    f"INITIAL_ADMIN_PASSWORD com uma senha forte ({erro_senha.lower()})."
                )
            senha_hash = generate_password_hash(senha_inicial, method='pbkdf2:sha256')
            conn.execute(
                "INSERT INTO usuarios (username, password, role, email) VALUES (%s, %s, %s, %s)",
                ('admin', senha_hash, 'admin', os.getenv('INITIAL_ADMIN_EMAIL', 'admin@secureedu.com'))
            )
            print("Usuário administrador inicial criado. A senha não foi exibida.")

    print("Banco de dados preparado com sucesso!")


if __name__ == '__main__':
    main()
