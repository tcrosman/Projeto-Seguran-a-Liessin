"""Prepara, de forma idempotente, as quatro contas da homologação do diretor.

As senhas são lidas exclusivamente de variáveis de ambiente e nunca são exibidas.
Depois deste script, execute prepare_demo.py para vincular os três alunos fictícios.
"""

import os
from urllib.parse import urlparse

from werkzeug.security import generate_password_hash

from app.core.database import get_db
from app.schemas.user_schema import UserSchema


DIRECTOR_EMAIL = "patrick.moreno@liessin.com.br"
STAFF = (
    ("teste_diretor_admin", "admin", "DEMO_ADMIN_PASSWORD"),
    ("teste_diretor_basico", "basico", "DEMO_BASIC_PASSWORD"),
    ("teste_diretor_portaria", "vigia", "DEMO_GATE_PASSWORD"),
)


def _password(name):
    value = os.getenv(name, "")
    error = UserSchema._check_password_strength(value)
    if error:
        raise SystemExit(f"{name}: {error}")
    return generate_password_hash(value, method="pbkdf2:sha256")


def main():
    base = urlparse(os.getenv("BASE_URL", ""))
    allowed = {item.strip().lower() for item in os.getenv("SCHOOL_DEMO_EMAILS", "").split(",") if item.strip()}
    if (os.getenv("SCHOOL_DIRECTORY_MODE", "").lower() != "demo"
            or os.getenv("SCHOOL_DEMO_REMOTE_ALLOWED", "").lower() != "true"
            or base.scheme != "https" or DIRECTOR_EMAIL not in allowed):
        raise SystemExit("Ambiente de demonstração remota não está configurado para o diretor")

    staff_passwords = [(username, role, _password(env_name)) for username, role, env_name in STAFF]
    parent_password = _password("DEMO_PARENT_PASSWORD")
    if input("Digite PREPARAR DIRETOR para criar/atualizar as contas de teste: ").strip() != "PREPARAR DIRETOR":
        print("Cancelado sem alterações.")
        return

    with get_db() as conn:
        for username, role, password_hash in staff_passwords:
            conn.execute("""
                INSERT INTO usuarios (username, password, role, email)
                VALUES (%s, %s, %s, '')
                ON CONFLICT (username) DO UPDATE SET
                    password = EXCLUDED.password,
                    role = EXCLUDED.role,
                    auth_version = usuarios.auth_version + 1
            """, (username, password_hash, role))

        existing = conn.execute("""
            SELECT school_external_id FROM responsaveis WHERE lower(email) = %s FOR UPDATE
        """, (DIRECTOR_EMAIL,)).fetchone()
        if existing and existing["school_external_id"] and not existing["school_external_id"].startswith("demo:"):
            raise RuntimeError("O e-mail já pertence a uma conta escolar oficial; operação recusada")
        conn.execute("""
            INSERT INTO responsaveis
                (email, nome, password_hash, status, school_external_id, is_demo)
            VALUES (%s, %s, %s, 'aprovado', %s, TRUE)
            ON CONFLICT (email) DO UPDATE SET
                nome = EXCLUDED.nome,
                password_hash = EXCLUDED.password_hash,
                status = 'aprovado',
                school_external_id = EXCLUDED.school_external_id,
                is_demo = TRUE,
                auth_version = responsaveis.auth_version + 1
        """, (
            DIRECTOR_EMAIL,
            "TESTE — Patrick Moreno (Responsável)",
            parent_password,
            "demo:director:patrick-moreno",
        ))

    print("Contas preparadas sem exibir senhas:")
    for username, role, _ in STAFF:
        print(f"  {username} ({role})")
    print(f"  {DIRECTOR_EMAIL} (responsável com verificação por e-mail)")


if __name__ == "__main__":
    main()
