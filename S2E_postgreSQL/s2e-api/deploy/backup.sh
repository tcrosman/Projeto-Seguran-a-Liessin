#!/usr/bin/env bash
#
# Backup diário do banco do SecureEdu.
#
# Isto NÃO é opcional: os anexos das saídas (atestados, autorizações assinadas) são
# gravados na coluna BYTEA de `saidas_documentos`, dentro do Postgres — não em
# arquivo no disco (app/api/web.py, INSERT junto com a saída). O mesmo vale para a
# trilha de auditoria da tabela `auditoria`, que a LGPD exige preservar. Perder o
# banco é perder os documentos e a auditoria junto.
#
# Uso manual:  sudo -u secureedu bash deploy/backup.sh
# Automático:  deploy/secureedu-backup.timer (diário, 03:20)

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/secureedu/S2E_postgreSQL/s2e-api}"
DEST="${BACKUP_DIR:-/var/backups/secureedu}"
RETENCAO_DIAS="${BACKUP_RETENCAO_DIAS:-14}"

# A DATABASE_URL vem do mesmo .env que a aplicação usa — uma fonte só, para o backup
# não continuar apontando para o banco antigo depois de uma troca.
# shellcheck disable=SC1091
set -a
source "$APP_DIR/.env"
set +a

[[ -n "${DATABASE_URL:-}" ]] || { echo "DATABASE_URL não encontrada em $APP_DIR/.env" >&2; exit 1; }

mkdir -p "$DEST"
# Os dumps contêm hashes de senha, e-mails de responsáveis e os documentos dos
# alunos. Só o dono lê.
chmod 700 "$DEST"

CARIMBO="$(date +%Y-%m-%d_%H%M)"
ARQUIVO="$DEST/secureedu_$CARIMBO.sql.gz"
PARCIAL="$ARQUIVO.parcial"

# Escreve num .parcial e só renomeia no fim: um dump interrompido (disco cheio,
# reboot) nunca fica com nome de backup válido, que é o tipo de arquivo que só se
# descobre inútil no dia da restauração.
if ! pg_dump --format=plain --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$PARCIAL"; then
    rm -f "$PARCIAL"
    echo "pg_dump falhou — nenhum backup gerado" >&2
    exit 1
fi

mv "$PARCIAL" "$ARQUIVO"
chmod 600 "$ARQUIVO"

TAMANHO="$(du -h "$ARQUIVO" | cut -f1)"
echo "backup gerado: $ARQUIVO ($TAMANHO)"

# Rotação. -mtime +N remove o que tem mais de N dias.
REMOVIDOS="$(find "$DEST" -maxdepth 1 -name 'secureedu_*.sql.gz' -mtime "+$RETENCAO_DIAS" -print -delete | wc -l)"
[[ "$REMOVIDOS" -gt 0 ]] && echo "removidos $REMOVIDOS backup(s) com mais de $RETENCAO_DIAS dias"

# Sanidade: um dump que não contém as tabelas base não serve para restaurar nada.
# Barato de conferir aqui, caro de descobrir depois.
if ! gzip -dc "$ARQUIVO" | grep -qE 'CREATE TABLE public\.(usuarios|saidas)'; then
    echo "AVISO: o dump não contém as tabelas esperadas. Verifique." >&2
    exit 1
fi

echo "total em $DEST: $(find "$DEST" -name 'secureedu_*.sql.gz' | wc -l) arquivo(s), $(du -sh "$DEST" | cut -f1)"
