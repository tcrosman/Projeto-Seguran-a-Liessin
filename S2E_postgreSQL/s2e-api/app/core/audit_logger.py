from datetime import datetime
import json
import os
from app.core.database import get_db

def log_operacao(usuario, acao, detalhes, ip=None):
    """Registra log em arquivo texto com IP opcional"""
    def safe(value):
        return str(value or '').replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')[:500]

    os.makedirs('logs', exist_ok=True)
    ip_str = f" [{safe(ip)}]" if ip else ""
    with open('logs/system.log', 'a', encoding='utf-8') as f:
        f.write(f"[{datetime.now()}]{ip_str} {safe(usuario)} - {safe(acao)}: {safe(detalhes)}\n")

def log_aluno(aluno_id, usuario_id, acao, dados_antigos=None, dados_novos=None):
    """Registra auditoria de aluno no banco"""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO logs_alunos (aluno_id, usuario_id, acao, dados_antigos, dados_novos, data_hora)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            aluno_id, 
            usuario_id, 
            acao,
            json.dumps(dados_antigos, default=str) if dados_antigos else None,
            json.dumps(dados_novos, default=str) if dados_novos else None,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
