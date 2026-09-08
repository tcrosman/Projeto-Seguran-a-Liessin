#!/usr/bin/env bash
#
# Prepara o PostgreSQL local do VPS para o SecureEdu:
#   1. cria o banco e o usuário da aplicação (sem SUPERUSER);
#   2. garante que o servidor aceita TLS;
#   3. prova, com uma conexão real, que sslmode=require funciona.
#
# O passo 2 não é opcional. app/core/database.py monta a conexão campo a campo com
# sslmode='require' fixo no código — ele ignora por completo o que estiver na query
# string da DATABASE_URL. Um Postgres sem TLS recusa a conexão e a aplicação sequer
# sobe. O certificado autoassinado basta: sslmode=require cifra o transporte mas não
# valida a cadeia (isso seria verify-full), e aqui o tráfego nem sai da máquina.
#
# Uso:
#   sudo bash deploy/postgres-setup.sh
#
# Idempotente: pode rodar de novo sem estragar nada.

set -euo pipefail

DB_NAME="${DB_NAME:-secureedu}"
DB_USER="${DB_USER:-secureedu}"

if [[ $EUID -ne 0 ]]; then
    echo "Rode com sudo: sudo bash deploy/postgres-setup.sh" >&2
    exit 1
fi

psql_su() { sudo -u postgres psql -v ON_ERROR_STOP=1 -Atqc "$1"; }

echo "==> 1/4  Usuário e banco"

if [[ -z "${DB_PASSWORD:-}" ]]; then
    read -rsp "Senha para o usuário '$DB_USER' do Postgres: " DB_PASSWORD; echo
    read -rsp "Repita a senha: " DB_PASSWORD2; echo
    [[ "$DB_PASSWORD" == "$DB_PASSWORD2" ]] || { echo "As senhas não conferem." >&2; exit 1; }
fi
[[ -n "$DB_PASSWORD" ]] || { echo "Senha vazia." >&2; exit 1; }

# NOSUPERUSER/NOCREATEDB explícitos: a aplicação só precisa de DDL nas próprias
# tabelas (setup_db.py cria o schema), nada além disso. Se um dia houver injeção de
# SQL, o estrago fica contido neste banco.
if [[ "$(psql_su "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")" == "1" ]]; then
    echo "    usuário '$DB_USER' já existe — atualizando a senha"
    psql_su "ALTER ROLE \"$DB_USER\" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '$DB_PASSWORD'" >/dev/null
else
    psql_su "CREATE ROLE \"$DB_USER\" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '$DB_PASSWORD'" >/dev/null
    echo "    usuário '$DB_USER' criado"
fi

if [[ "$(psql_su "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" == "1" ]]; then
    echo "    banco '$DB_NAME' já existe"
else
    sudo -u postgres createdb -O "$DB_USER" -E UTF8 "$DB_NAME"
    echo "    banco '$DB_NAME' criado"
fi

# O dono do banco já pode tudo dentro dele; esta linha só fecha o schema public para
# os demais papéis, comportamento que o Postgres 15+ já adota por padrão.
psql_su "REVOKE ALL ON SCHEMA public FROM PUBLIC" >/dev/null 2>&1 || true
sudo -u postgres psql -v ON_ERROR_STOP=1 -qc \
    "GRANT ALL ON SCHEMA public TO \"$DB_USER\"" "$DB_NAME" >/dev/null

echo "==> 2/4  TLS no servidor"

SSL_ATUAL="$(psql_su 'SHOW ssl')"
if [[ "$SSL_ATUAL" == "on" ]]; then
    # O pacote do Debian/Ubuntu já sobe com ssl=on usando o certificado
    # ssl-cert-snakeoil. Nesse caso não há nada a fazer.
    echo "    já está ligado (ssl=on) — nada a fazer"
else
    PGDATA="$(psql_su 'SHOW data_directory')"
    PGCONF="$(psql_su 'SHOW config_file')"
    echo "    ssl=off — gerando certificado autoassinado em $PGDATA"

    openssl req -new -x509 -days 3650 -nodes -text \
        -out "$PGDATA/server.crt" -keyout "$PGDATA/server.key" \
        -subj "/CN=$(hostname -f 2>/dev/null || hostname)" 2>/dev/null

    # O Postgres se recusa a iniciar se a chave privada for legível por outros.
    chmod 600 "$PGDATA/server.key"
    chown postgres:postgres "$PGDATA/server.key" "$PGDATA/server.crt"

    # ALTER SYSTEM grava em postgresql.auto.conf, que tem precedência sobre o
    # postgresql.conf — assim um upgrade do pacote não desfaz o ajuste.
    psql_su "ALTER SYSTEM SET ssl = 'on'" >/dev/null
    psql_su "ALTER SYSTEM SET ssl_cert_file = '$PGDATA/server.crt'" >/dev/null
    psql_su "ALTER SYSTEM SET ssl_key_file = '$PGDATA/server.key'" >/dev/null
    systemctl reload postgresql
    sleep 2
    echo "    configurado ($PGCONF)"
fi

echo "==> 3/4  Escuta apenas em localhost"

LISTEN="$(psql_su 'SHOW listen_addresses')"
echo "    listen_addresses = $LISTEN"
if [[ "$LISTEN" != "localhost" && "$LISTEN" != "127.0.0.1" ]]; then
    echo "    AVISO: o Postgres não está restrito a localhost. Como o ufw bloqueia a"
    echo "    porta 5432, ninguém de fora alcança — mas o correto é"
    echo "    ALTER SYSTEM SET listen_addresses = 'localhost'; e reiniciar."
fi

echo "==> 4/4  Conferindo uma conexão com sslmode=require"

# É exatamente o que app/core/database.py vai fazer. Se este passo falhar, a
# aplicação não sobe — melhor descobrir agora do que no primeiro start.
export PGPASSWORD="$DB_PASSWORD"
RESULTADO="$(psql "postgresql://$DB_USER@127.0.0.1:5432/$DB_NAME?sslmode=require" \
    -Atqc "SELECT 'conectado'" 2>&1)" || {
        echo
        echo "FALHOU: $RESULTADO" >&2
        echo "A aplicação usa sslmode=require e não vai subir assim." >&2
        exit 1
    }
unset PGPASSWORD
echo "    OK — $RESULTADO com TLS"

echo
echo "Pronto. Use esta linha no .env (sem aspas, sem comentário na mesma linha):"
echo
echo "DATABASE_URL=postgresql://$DB_USER:<A-SENHA-QUE-VOCE-DIGITOU>@localhost:5432/$DB_NAME"
echo
echo "Se a senha tiver @ : / ou #, ela precisa ser percent-encoded na URL,"
echo "porque database.py usa urlparse() para separar os campos."
