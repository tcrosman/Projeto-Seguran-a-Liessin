"""Rate limit de tentativas de autenticação, persistido no banco.

Substitui os dicionários em memória que existiam dentro de `register_routes`/`register_parent_routes`.
Eles tinham três problemas: sumiam a cada restart, não eram compartilhados entre os workers do
gunicorn (com `--workers 2`, o limite efetivo era o dobro do configurado) e cresciam sem limpeza.

Cada tentativa falha vira uma linha em `rate_limit_falhas`. O bloqueio é derivado dessas linhas,
sempre com as comparações de tempo feitas no próprio Postgres — `criado_em` é TIMESTAMP sem
fuso e comparar com o relógio local do processo daria diferença de horas.

## Escopos separados

Chaves diferentes (IP, usuário, e-mail) e finalidades diferentes moram em escopos distintos, para
que uma falha num fluxo não bloqueie outro. Em particular, errar o código de 2FA não pode
consumir o limite de tentativas de senha.

## Por que o limite por IP é mais frouxo que o limite por conta

Escola é rede NAT: dezenas de responsáveis saem pelo mesmo IP público. Um limite de 5 por IP
tranca todo mundo por causa de um usuário desastrado — ou de um atacante que queira causar isso
de propósito. Então a defesa principal é por conta (usuário/e-mail/token), estreita, e o limite
por IP é só uma rede de segurança contra varredura distribuída, com folga suficiente para não
atingir uso legítimo.
"""
from app.core.database import get_db

# (max_falhas, janela_minutos, bloqueio_minutos)
LOGIN_CONTA = ('login_conta', 5, 15, 5)
LOGIN_IP = ('login_ip', 30, 15, 5)
PAIS_LOGIN_CONTA = ('pais_login_conta', 5, 15, 5)
PAIS_LOGIN_IP = ('pais_login_ip', 30, 15, 5)
# 2FA tem escopo próprio: o contador por token (tokens_2fa.tentativas) já limita a força bruta
# por conta, então aqui só resta a rede de segurança por IP.
PAIS_2FA_IP = ('pais_2fa_ip', 30, 15, 5)


def esta_bloqueado(regra, chave):
    """True se `chave` estourou o limite de `regra` e o bloqueio ainda está de pé."""
    escopo, max_falhas, janela_min, bloqueio_min = regra
    if not chave:
        return False
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS falhas,
                      COALESCE(MAX(criado_em) > NOW() - make_interval(mins => %s), FALSE) AS recente
               FROM rate_limit_falhas
               WHERE escopo = %s AND chave = %s
                 AND criado_em > NOW() - make_interval(mins => %s)""",
            (bloqueio_min, escopo, str(chave), janela_min),
        ).fetchone()

        if not row or row['falhas'] < max_falhas:
            return False
        if row['recente']:
            return True
        # Passou o tempo de bloqueio sem novas tentativas: zera para o usuário legítimo
        # recomeçar com o limite cheio.
        conn.execute("DELETE FROM rate_limit_falhas WHERE escopo = %s AND chave = %s", (escopo, str(chave)))
        return False


def registrar_falha(regra, chave):
    """Contabiliza uma tentativa falha."""
    escopo, _max, janela_min, _bloqueio = regra
    if not chave:
        return
    with get_db() as conn:
        conn.execute(
            "INSERT INTO rate_limit_falhas (escopo, chave) VALUES (%s, %s)", (escopo, str(chave))
        )
        # Poda o que já saiu da janela desta chave — mantém a tabela pequena sem varredura global.
        conn.execute(
            """DELETE FROM rate_limit_falhas
               WHERE escopo = %s AND chave = %s AND criado_em < NOW() - make_interval(mins => %s)""",
            (escopo, str(chave), janela_min),
        )


def limpar(regra, chave):
    """Zera o histórico de falhas — chamado após uma autenticação bem-sucedida."""
    escopo = regra[0]
    if not chave:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM rate_limit_falhas WHERE escopo = %s AND chave = %s", (escopo, str(chave)))
