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
# 2FA por IP: rede de segurança contra varredura distribuída, larga por causa da rede NAT.
PAIS_2FA_IP = ('pais_2fa_ip', 30, 15, 5)

# 2FA por CONTA. `tokens_2fa.tentativas` sozinho não segurava força bruta: ao queimar o token
# depois de 5 erros a linha é APAGADA, e o login apaga os tokens abertos antes de inserir o
# novo — então não existia contador por conta que sobrevivesse à regeneração. Quem já tem a
# senha (vazamento, reúso) fazia login, 5 palpites, login de novo, mais 5, indefinidamente. O
# único freio restante era o limite por IP, que não vale nada com IPs rotativos — ou com um
# único /64 de IPv6, onde "por IP" não significa nada.
#
# Este contador vive fora do ciclo de vida do token: 10 erros acumulados em 1h trancam a conta
# por 30 min. Isso põe o teto em algumas centenas de palpites por dia contra um espaço de 10^6,
# e só quem já passou pela senha consegue somar falhas aqui — não dá para trancar a conta de um
# responsável sem tê-la.
PAIS_2FA_CONTA = ('pais_2fa_conta', 10, 60, 30)

# Fluxos sem senha para errar: recuperação e autocadastro. Aqui não existe "tentativa falha" —
# cada request já dispara um e-mail e, no autocadastro, uma consulta ao banco da escola. Então o
# que se conta é a tentativa em si (ver registrar_tentativa).
#
# O limite por conta é apertado porque quem pede a redefinição do próprio e-mail não precisa de
# mais que duas ou três tentativas; o por IP é folgado pela rede NAT da escola, e a janela é de
# uma hora porque o abuso aqui é volume ao longo do tempo, não rajada.
RESET_CONTA = ('reset_conta', 3, 15, 15)
RESET_IP = ('reset_ip', 20, 60, 15)
PAIS_RESET_CONTA = ('pais_reset_conta', 3, 15, 15)
PAIS_RESET_IP = ('pais_reset_ip', 20, 60, 15)
# Só por IP: quem varre e-mails para descobrir quais a escola reconhece usa um e-mail diferente
# a cada tentativa, então limitar por conta não seguraria nada.
PAIS_CADASTRO_IP = ('pais_cadastro_ip', 10, 60, 30)


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


def registrar_tentativa(regra, chave):
    """Contabiliza uma tentativa que não tem como "falhar" — pedido de redefinição de senha,
    autocadastro. É o mesmo contador de `registrar_falha`, com outro nome porque nesses fluxos o
    que se limita é o próprio pedido: ele já custa um e-mail enviado e, no autocadastro, uma
    consulta ao banco da instituição, mesmo quando o e-mail informado não existe.
    """
    registrar_falha(regra, chave)


def limpar(regra, chave):
    """Zera o histórico de falhas — chamado após uma autenticação bem-sucedida."""
    escopo = regra[0]
    if not chave:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM rate_limit_falhas WHERE escopo = %s AND chave = %s", (escopo, str(chave)))
