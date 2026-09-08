#!/usr/bin/env bash
#
# Atualiza o SecureEdu no VPS para a versão mais recente do repositório.
#
#   sudo -u secureedu bash /opt/secureedu/S2E_postgreSQL/s2e-api/deploy/deploy.sh
#
# Etapas: git pull -> dependências -> migrações -> restart -> healthcheck.
# Se o healthcheck falhar, o script volta para o commit anterior automaticamente.
#
# Para o PRIMEIRO deploy num banco vazio, use antes:
#   deploy/primeiro-setup.sh
# Este script nunca roda "setup_db.py --reset", que apagaria todos os dados.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/secureedu/S2E_postgreSQL/s2e-api}"
REPO_DIR="${REPO_DIR:-/opt/secureedu}"
VENV="$APP_DIR/.venv"
SERVICO="${SERVICO:-secureedu}"
URL_SAUDE="${URL_SAUDE:-http://127.0.0.1:8002/}"

cd "$APP_DIR"

echo "==> 1/6  Estado atual"
COMMIT_ANTERIOR="$(git -C "$REPO_DIR" rev-parse HEAD)"
echo "    commit atual: $(git -C "$REPO_DIR" log -1 --oneline)"

# Um arquivo alterado à mão no servidor seria perdido pelo pull, silenciosamente.
if ! git -C "$REPO_DIR" diff --quiet || ! git -C "$REPO_DIR" diff --cached --quiet; then
    echo "    ERRO: há alterações não commitadas no servidor." >&2
    git -C "$REPO_DIR" status --short >&2
    echo "    Resolva antes de continuar (git checkout -- <arquivo>, ou commit)." >&2
    exit 1
fi

echo "==> 2/6  Baixando a nova versão"
git -C "$REPO_DIR" pull --ff-only
echo "    commit novo:  $(git -C "$REPO_DIR" log -1 --oneline)"

if [[ "$(git -C "$REPO_DIR" rev-parse HEAD)" == "$COMMIT_ANTERIOR" ]]; then
    echo "    nada mudou — seguindo mesmo assim para reaplicar dependências e migrações"
fi

echo "==> 3/6  Dependências"
# requirements.txt do s2e-api, NUNCA o de S2E_postgreSQL/: aquele é legado, tem BOM
# UTF-8 e pina Flask 2.3.3, que conflita com o flask>=3.0 exigido pelo código.
"$VENV/bin/pip" install --quiet --upgrade-strategy only-if-needed -r "$APP_DIR/requirements.txt"
echo "    ok"

echo "==> 4/6  Migrações do banco"
# Sem --reset. As migrações são idempotentes (CREATE TABLE IF NOT EXISTS, ALTER
# tolerante) e rodam também no boot da aplicação; aqui é para falhar cedo, com a
# mensagem na tela, em vez de o serviço não subir depois.
if ! "$VENV/bin/python" setup_db.py; then
    echo >&2
    echo "    ERRO nas migrações. Se a mensagem for 'relation \"alunos\" does not exist'," >&2
    echo "    o banco está vazio e este é o primeiro deploy: use deploy/primeiro-setup.sh." >&2
    exit 1
fi

echo "==> 5/6  Reiniciando o serviço"
sudo systemctl restart "$SERVICO"

echo "==> 6/6  Healthcheck"
# A raiz é a tela de login: responde 200 sem autenticação e só devolve isso se o
# Flask subiu, o pool do banco conectou e as migrações passaram.
#
# O cabeçalho X-Forwarded-Proto é obrigatório aqui. Estamos batendo direto no
# gunicorn, por http, e com FORCE_HTTPS=true o Flask-Talisman responde 302 para
# https a qualquer request que ele considere não-cifrada. Sem simular o que o nginx
# envia, o healthcheck receberia 302 sempre e este script reverteria todo deploy,
# inclusive os que subiram perfeitamente.
OK=0
for tentativa in $(seq 1 15); do
    CODIGO="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
        -H 'X-Forwarded-Proto: https' "$URL_SAUDE" || echo 000)"
    if [[ "$CODIGO" == "200" ]]; then
        OK=1
        echo "    HTTP 200 na tentativa $tentativa"
        break
    fi
    sleep 2
done

if [[ "$OK" -ne 1 ]]; then
    echo >&2
    # A reversão desfaz o CÓDIGO, não as migrações que já rodaram no passo 4. As
    # migrações deste projeto são só aditivas (CREATE IF NOT EXISTS, ADD COLUMN), então
    # a versão anterior convive com colunas a mais sem problema. Se algum dia entrar uma
    # migração destrutiva, isto aqui deixa de bastar e o caminho é restaurar o backup.
    echo "    FALHOU (último código: ${CODIGO:-000}). Revertendo o código para $COMMIT_ANTERIOR." >&2
    git -C "$REPO_DIR" reset --hard "$COMMIT_ANTERIOR"
    "$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
    sudo systemctl restart "$SERVICO"
    echo "    revertido. Logs do erro:" >&2
    journalctl -u "$SERVICO" -n 40 --no-pager >&2
    exit 1
fi

echo
echo "Deploy concluído: $(git -C "$REPO_DIR" log -1 --oneline)"
systemctl --no-pager status "$SERVICO" | head -5
