from datetime import datetime
import json
from app.core.database import get_db
from app.core.logging_config import obter

_log = obter('s2e.audit')

def log_operacao(usuario, acao, detalhes, ip=None):
    """Registra uma ação na trilha de auditoria (arquivo rotacionado + stdout).

    Antes isto abria 'logs/system.log' em append a cada chamada: sem rotação, o arquivo crescia
    sem limite, e dois workers escrevendo ao mesmo tempo podiam intercalar linhas.
    """
    ip_str = f" [{ip}]" if ip else ""
    _log.info("%s%s - %s: %s", usuario, ip_str, acao, detalhes)

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