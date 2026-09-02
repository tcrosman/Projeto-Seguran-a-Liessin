"""Limpeza e expiração periódicas de dados.

Antes, cada uma dessas escritas rodava dentro de rotas GET de listagem (`/saidas`,
`/admin/solicitacoes`, `/pais/minhas_solicitacoes`, ...). Isso significava um DELETE/UPDATE em
tabela inteira a cada carregamento de página, por qualquer usuário, competindo por lock com as
outras requests — amplificação de escrita num caminho que deveria ser só de leitura.

Agora roda fora do ciclo de request: uma thread de fundo no próprio app (ver
`iniciar_agendador`) e/ou o script `manutencao.py` na raiz, para quem preferir cron externo.
"""
import os
import threading
import time
from datetime import timedelta

from app.core.audit_logger import DIAS_RETENCAO_AUDITORIA
from app.core.database import get_db, expirar_saidas_nao_liberadas
from app.core.tempo import agora, carimbo
from app.core.logging_config import obter

_log = obter()

# Chave do advisory lock do Postgres. Com vários workers do gunicorn (e possivelmente um cron
# externo em paralelo), só um executa a manutenção por vez; os demais saem na hora.
_LOCK_KEY = 8021977

INTERVALO_PADRAO_SEG = int(os.getenv('MANUTENCAO_INTERVALO_SEG', 600))
DIAS_RETENCAO = int(os.getenv('MANUTENCAO_DIAS_RETENCAO', 30))


def executar_manutencao(dias_retencao: int = DIAS_RETENCAO) -> dict:
    """Roda todas as limpezas. Idempotente. Devolve quantas linhas cada etapa afetou."""
    resultado = {}
    with get_db() as conn:
        # Lock de TRANSAÇÃO, não de sessão: o Postgres o libera sozinho no commit e no rollback.
        # Com pg_try_advisory_lock (sessão) um erro no meio deixava o lock preso na conexão, que
        # voltava ao pool ainda segurando-o — e nenhuma manutenção rodava mais naquele worker.
        # Pior: o unlock no finally rodava sobre uma transação já abortada, levantava
        # InFailedSqlTransaction e mascarava o erro original.
        obtido = conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (_LOCK_KEY,)).fetchone()['ok']
        if not obtido:
            return {'ignorado': 'outra instância já está executando a manutenção'}

        # Saídas aprovadas cujo dia passou sem a segurança liberar.
        resultado['saidas_expiradas'] = expirar_saidas_nao_liberadas(conn)

        # Corte pela data da escola, não pelo CURRENT_DATE do banco (UTC) — mesma razão de
        # expirar_saidas_nao_liberadas: data_saida está no fuso de quem preencheu.
        corte = (agora() - timedelta(days=dias_retencao)).strftime('%Y-%m-%d')
        resultado['saidas_antigas_removidas'] = conn.execute(
            "DELETE FROM saidas WHERE status = 'concluida' AND data_saida < %s", (corte,)
        ).rowcount

        resultado['solicitacoes_antigas_removidas'] = conn.execute("""
            DELETE FROM solicitacoes_saida
            WHERE status IN ('aprovado', 'rejeitado')
              AND criado_em < NOW() - make_interval(days => %s)
        """, (dias_retencao,)).rowcount

        # Tokens vencidos não têm mais utilidade e são material sensível parado no banco.
        resultado['tokens_2fa_removidos'] = conn.execute(
            "DELETE FROM tokens_2fa WHERE expires_at < NOW() OR usado = TRUE"
        ).rowcount
        resultado['reset_tokens_pais_removidos'] = conn.execute(
            "DELETE FROM reset_tokens_pais WHERE expires_at < NOW()"
        ).rowcount
        # reset_tokens.expires_at é TEXT ('YYYY-MM-DD HH:MM:SS'). Comparação textual em vez de
        # ::timestamp: o formato é ISO com zero à esquerda, então a ordem lexicográfica é a
        # cronológica, e uma linha malformada deixa de abortar toda a manutenção — é o mesmo
        # tratamento que saidas.data_saida já recebe no resto do código.
        resultado['reset_tokens_removidos'] = conn.execute(
            "DELETE FROM reset_tokens WHERE expires_at < TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')"
        ).rowcount

        # A auditoria tem prazo próprio, bem mais longo que o das solicitações — ver
        # DIAS_RETENCAO_AUDITORIA em app/core/audit_logger.py. Podar aqui evita que a tabela
        # cresça sem limite, sem encurtar a trilha para o prazo de 30 dias das outras limpezas.
        resultado['auditoria_removida'] = conn.execute(
            "DELETE FROM auditoria WHERE criado_em < NOW() - make_interval(days => %s)",
            (DIAS_RETENCAO_AUDITORIA,)
        ).rowcount

        # Varredura global do rate limit: rate_limit.registrar_falha só poda a própria chave,
        # então chaves que nunca mais voltam ficariam para trás.
        resultado['rate_limit_removidos'] = conn.execute(
            "DELETE FROM rate_limit_falhas WHERE criado_em < NOW() - INTERVAL '1 day'"
        ).rowcount
    return resultado


_agendador_ativo = False
_agendador_lock = threading.Lock()


def iniciar_agendador(intervalo_seg: int = INTERVALO_PADRAO_SEG):
    """Sobe uma thread daemon que roda a manutenção periodicamente.

    Existe para o sistema continuar correto sem depender de configurar um cron: as saídas
    precisam ser expiradas para as telas não mostrarem como 'pendente' algo de ontem. Com o
    advisory lock, ter a thread nos dois workers do gunicorn não duplica trabalho.

    No máximo uma thread por processo: create_app() é chamado mais de uma vez em vários pontos
    (run_migrations.py, create_admin.py, testes), e sem essa trava cada chamada somava uma thread.
    """
    global _agendador_ativo
    with _agendador_lock:
        if _agendador_ativo:
            return
        _agendador_ativo = True

    def _loop():
        while True:
            try:
                resumo = executar_manutencao()
                if any(isinstance(v, int) and v for v in resumo.values()):
                    _log.info(f"[MANUTENCAO] {carimbo()} {resumo}")
            except Exception as e:
                _log.warning(f"[MANUTENCAO] Falhou (será tentado de novo em {intervalo_seg}s): {e}")
            time.sleep(intervalo_seg)

    threading.Thread(target=_loop, name='manutencao', daemon=True).start()
