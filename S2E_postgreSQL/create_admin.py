import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2e-api'))

from app import create_app
from app.core.database import get_db
from werkzeug.security import generate_password_hash


def main():
    password = os.getenv('INITIAL_ADMIN_PASSWORD', '')
    from app.schemas.user_schema import UserSchema
    error = UserSchema._check_password_strength(password)
    if error or len(password) < 12:
        raise SystemExit("INITIAL_ADMIN_PASSWORD precisa ter 12 caracteres e atender à política de senhas.")

    app = create_app()
    with app.app_context(), get_db() as conn:
        exists = conn.execute("SELECT id FROM usuarios WHERE username = 'admin'").fetchone()
        if exists:
            print("Usuário admin já existe; nenhuma alteração feita.")
            return
        conn.execute(
            "INSERT INTO usuarios (username, password, role, email) VALUES (%s, %s, %s, %s)",
            ('admin', generate_password_hash(password, method='pbkdf2:sha256'), 'admin', os.getenv('INITIAL_ADMIN_EMAIL', '')),
        )
    print("Usuário admin criado; a senha não será exibida.")


if __name__ == '__main__':
    main()
