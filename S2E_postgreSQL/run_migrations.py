import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2e-api'))

from app.core.database import aplicar_migracoes

# Passo de release: roda as migrações UMA vez, antes de qualquer worker subir.
#
# Não constrói o app de propósito. create_app() faria o mesmo trabalho de schema pelo caminho do
# boot (o que dobraria a execução), subiria a thread de manutenção e exigiria as variáveis
# SCHOOL_SQL_* só para depois encerrar o processo. Aqui só o banco importa — as migrações são
# idempotentes e protegidas por advisory lock, então repetir o comando é inofensivo.
#
# No Render, ligue este arquivo ao preDeployCommand (ver render.yaml). Com ele garantido, dá
# para desligar as migrações do boot com MIGRACOES_NO_BOOT=false.
print("Rodando migracoes...")
aplicar_migracoes()
print("Migracoes concluidas.")
