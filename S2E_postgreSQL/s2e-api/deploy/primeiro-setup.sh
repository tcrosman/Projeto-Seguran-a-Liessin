#!/usr/bin/env bash
#
# PRIMEIRO deploy, num banco PostgreSQL vazio. Roda UMA vez.
#
#   sudo -u secureedu bash /opt/secureedu/S2E_postgreSQL/s2e-api/deploy/primeiro-setup.sh
#
# Por que isto existe em vez de simplesmente subir o serviço:
#
# As tabelas base (alunos, saidas, usuarios) são criadas SÓ dentro de reset_database()
# — app/core/migrations.py:38. A migrate_database(), que roda a cada boot da
# aplicação (app/__init__.py:133), já assume que elas existem e emite direto
# "ALTER TABLE alunos ADD COLUMN telefone TEXT". Num banco vazio isso estoura com
#     relation "alunos" does not exist
# e, como a falha de migração não é engolida de propósito, o serviço não sobe.
#
# Ou seja: num banco novo é obrigatório passar uma vez pelo --reset. Daí em diante,
# use deploy/deploy.sh, que nunca usa --reset.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/secureedu/S2E_postgreSQL/s2e-api}"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

[[ -f .env ]] || { echo "Falta o .env. Copie de deploy/env.producao.example." >&2; exit 1; }

echo "==> Conferindo se o banco está mesmo vazio"

# Guarda-corpo: "--reset" APAGA alunos, saidas, usuarios e tudo que depende delas.
# Se este script for rodado por engano num banco em produção, é perda total de dados.
# Por isso ele mesmo confere antes, em vez de confiar na atenção de quem digitou.
TABELAS="$("$VENV/bin/python" - <<'PY'
import os, sys
from urllib.parse import urlparse
from dotenv import load_dotenv
import psycopg2

load_dotenv('.env')
u = urlparse(os.environ['DATABASE_URL'])
c = psycopg2.connect(host=u.hostname, port=u.port or 5432, dbname=u.path.lstrip('/'),
                     user=u.username, password=u.password, sslmode='require')
cur = c.cursor()
cur.execute("""SELECT count(*) FROM information_schema.tables
               WHERE table_schema='public' AND table_name IN ('alunos','saidas','usuarios')""")
print(cur.fetchone()[0])
PY
)"

if [[ "$TABELAS" != "0" ]]; then
    echo >&2
    echo "    O banco JÁ TEM as tabelas do SecureEdu ($TABELAS de 3 encontradas)." >&2
    echo "    Este script apagaria todos os dados. Abortando." >&2
    echo >&2
    echo "    Para apenas aplicar migrações num banco existente:" >&2
    echo "      cd $APP_DIR && .venv/bin/python setup_db.py" >&2
    exit 1
fi
echo "    banco vazio, ok"

echo "==> Verificando a senha inicial do administrador"
if ! grep -q '^INITIAL_ADMIN_PASSWORD=' .env; then
    echo "    ERRO: defina INITIAL_ADMIN_PASSWORD no .env antes de continuar." >&2
    echo "    Sem ela o schema é criado, mas nenhum administrador — e não há como" >&2
    echo "    fazer o primeiro login." >&2
    exit 1
fi

echo "==> Criando schema, chaves estrangeiras e o primeiro administrador"
# A confirmação vai só nesta invocação, não no .env: assim ela não fica em disco
# esperando para tornar um "setup_db.py --reset" acidental em perda de dados.
CONFIRM_RESET_DATABASE=APAGAR-TODOS-OS-DADOS "$VENV/bin/python" setup_db.py --reset

echo
echo "Pronto. Agora:"
echo "  sudo systemctl enable --now secureedu"
echo
echo "Depois do primeiro login, troque a senha do admin pela interface e REMOVA"
echo "as linhas INITIAL_ADMIN_PASSWORD e INITIAL_ADMIN_EMAIL do .env."
