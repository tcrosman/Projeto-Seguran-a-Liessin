import json
import os

from app.core.database import get_db
from app.core.tempo import carimbo
from app.core.logging_config import obter

_log = obter('s2e.audit')

# Retenção da trilha de auditoria no banco. Separada — e muito mais longa — que
# MANUTENCAO_DIAS_RETENCAO (30 dias), que é o prazo para apagar solicitações já revisadas. Se a
# auditoria seguisse aquele prazo, a resposta para "quem aprovou a saída daquele aluno?" sumiria
# junto com a solicitação, que é exatamente o registro que precisa sobreviver a ela.
DIAS_RETENCAO_AUDITORIA = int(os.getenv('AUDITORIA_DIAS_RETENCAO', 365))


def _persistir(conn, usuario, acao, detalhes, ip):
    conn.execute(
        "INSERT INTO auditoria (usuario, acao, detalhes, ip) VALUES (%s, %s, %s, %s)",
        (usuario, acao, detalhes, ip),
    )


def log_operacao(usuario, acao, detalhes, ip=None, conn=None):
    """Registra uma ação na trilha de auditoria: arquivo rotacionado, stdout e tabela `auditoria`.

    O arquivo sozinho não basta: `logs/system.log` é rotatado, fica fora das cópias de backup do
    banco e some junto com o disco se a máquina for perdida — e a trilha de quem autorizou a saída
    de um menor é justamente o que precisa sobreviver a isso. Por isso a linha também vai para o
    banco. (A decisão nasceu numa hospedagem de disco efêmero, onde o arquivo sumia a cada deploy;
    o servidor atual tem disco persistente, mas as outras razões continuam de pé.)

    `conn` é obrigatório quando quem chama já está dentro de um `with get_db()`: sem ele, esta
    função pediria uma segunda conexão ao pool enquanto a primeira ainda está retida e, com
    DB_POOL_MAX pequeno e várias requests simultâneas, o pool trava (mesmo cuidado documentado em
    app/core/rate_limit.py).

    Gravar a auditoria nunca derruba a operação que está sendo auditada: a falha é registrada no
    log e a request segue. Com `conn`, isso passa por SAVEPOINT — um INSERT que falhasse dentro da
    transação de quem chama abortaria a transação inteira, desfazendo a aprovação já feita.
    """
    ip_str = f" [{ip}]" if ip else ""
    _log.info("%s%s - %s: %s", usuario, ip_str, acao, detalhes)

    try:
        if conn is not None:
            conn.execute("SAVEPOINT auditoria")
            try:
                _persistir(conn, usuario, acao, detalhes, ip)
                conn.execute("RELEASE SAVEPOINT auditoria")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT auditoria")
                raise
        else:
            with get_db() as proprio:
                _persistir(proprio, usuario, acao, detalhes, ip)
    except Exception as e:
        _log.warning("[AUDITORIA] Falha ao gravar no banco (%s: %s) — registro só em log: %s",
                     acao, detalhes, e)


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
            carimbo()
        ))
