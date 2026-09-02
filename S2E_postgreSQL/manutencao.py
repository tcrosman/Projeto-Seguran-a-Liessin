"""Executa a limpeza/expiração de dados uma vez e sai — para agendar via cron.

    python manutencao.py

O app já roda isso periodicamente numa thread de fundo (app/core/maintenance.py), então este
script é opcional: serve para quem prefere um cron externo (Render Cron Job, pg_cron, crontab).
Rodar os dois em paralelo é seguro — um advisory lock garante uma execução por vez.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2e-api'))

from app.core.maintenance import executar_manutencao

if __name__ == "__main__":
    for etapa, valor in executar_manutencao().items():
        print(f"  {etapa}: {valor}")
