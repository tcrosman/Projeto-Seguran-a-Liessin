# -*- coding: utf-8 -*-
"""Dublês compartilhados pelos testes.

Ficam aqui, e não no conftest: um teste que faz `from tests.apoio import X` faz o Python
executar o conftest uma segunda vez, sob outro nome de módulo — e com isso as variáveis de
ambiente que ele define no topo eram reescritas no meio da suíte, mascarando justamente os
testes de configuração.

O `BancoFalso` mede a profundidade de aninhamento das conexões. Isso não é enfeite: o pool tem
DB_POOL_MAX pequeno (5 por padrão) e um `with get_db()` aberto dentro de outro retém duas
conexões ao mesmo tempo — com algumas requests simultâneas, o pool trava. É um defeito que não
aparece em teste funcional nenhum, só sob carga, então é medido aqui.
"""
from contextlib import contextmanager


class CursorFalso:
    def __init__(self, linhas, rowcount):
        self._linhas = list(linhas)
        self.rowcount = rowcount

    def fetchone(self):
        return self._linhas[0] if self._linhas else None

    def fetchall(self):
        return list(self._linhas)


class ConexaoFalsa:
    def __init__(self, banco):
        self._banco = banco

    def execute(self, sql, params=None):
        normal = " ".join(sql.split())
        self._banco.executados.append((normal, params))
        for fragmento, erro in self._banco.falhas:
            if fragmento in normal:
                raise erro
        for fragmento, linhas, rowcount, params_com in self._banco.respostas:
            if fragmento not in normal:
                continue
            # params_com distingue chamadas com o mesmo SQL e parâmetros diferentes — é o caso do
            # rate_limit, em que todos os escopos passam pela mesma query.
            if params_com is not None and params_com not in (params or ()):
                continue
            return CursorFalso(linhas, rowcount)
        # INSERT ... RETURNING sempre devolve linha no Postgres quando o comando dá certo; sem
        # este padrão, todo teste que passa por um INSERT com RETURNING precisaria declarar a
        # resposta só para o fetchone() não estourar.
        if "RETURNING" in normal:
            return CursorFalso([{'id': 1}], 1)
        return CursorFalso([], 1)

    def commit(self):
        pass

    def rollback(self):
        pass


class BancoFalso:
    """Substitui get_db(). `respostas` casa por fragmento de SQL, primeiro que casar vence."""

    def __init__(self):
        self.respostas = []
        self.falhas = []
        self.executados = []
        self.profundidade = 0
        self.profundidade_maxima = 0

    def falhar_em(self, fragmento, erro=None):
        """Faz o comando que contiver `fragmento` levantar — para testar caminhos de erro."""
        self.falhas.append((fragmento, erro or RuntimeError(f"falha simulada em {fragmento}")))

    def responder(self, fragmento, linhas=(), rowcount=1, params_com=None):
        # Inserido no início para uma resposta mais específica poder sobrepor uma anterior.
        self.respostas.insert(0, (fragmento, linhas, rowcount, params_com))

    @contextmanager
    def get_db(self):
        self.profundidade += 1
        self.profundidade_maxima = max(self.profundidade_maxima, self.profundidade)
        try:
            yield ConexaoFalsa(self)
        finally:
            self.profundidade -= 1

    # --- consultas sobre o que foi executado ---

    def sql_com(self, *fragmentos):
        """Comandos executados que contêm todos os fragmentos."""
        return [(s, p) for s, p in self.executados if all(f in s for f in fragmentos)]

    def auditorias(self):
        """(acao, detalhes, ip) de cada INSERT na tabela de auditoria."""
        return [(p[1], p[2], p[3]) for _s, p in self.sql_com("INSERT INTO auditoria")]

    def acoes_auditadas(self):
        return [acao for acao, _d, _ip in self.auditorias()]

    def bloquear(self, escopo):
        """Faz o rate_limit responder "estourado" para um escopo, sem afetar os outros."""
        self.responder("FROM rate_limit_falhas", [{'falhas': 999, 'recente': True}],
                       params_com=escopo)

    def tentativas_registradas(self, escopo=None):
        """Escopo/chave de cada tentativa contabilizada pelo rate_limit."""
        linhas = [p for _s, p in self.sql_com("INSERT INTO rate_limit_falhas")]
        return [(e, c) for e, c in linhas if escopo is None or e == escopo]


class CorreioFalso:
    """Substitui enviar_email_async. Sem isto, um teste de rota abre SMTP e manda e-mail de
    verdade para o endereço do fixture — aconteceu na primeira execução desta suíte."""

    def __init__(self):
        self.enviados = []

    def __call__(self, destinatario, assunto, corpo_html, fallback_log=None):
        self.enviados.append((destinatario, assunto, corpo_html))

    def destinatarios(self):
        return [d for d, _a, _c in self.enviados]


class DiretorioFalso:
    """Banco SQL da escola. `alunos` é {ra: {...}}."""

    def __init__(self, alunos=None, quebrado=False):
        self.alunos = alunos or {}
        self.quebrado = quebrado
        self.buscas = []
        self.limites = []

    def _talvez_quebrar(self):
        if self.quebrado:
            raise RuntimeError("banco SQL da escola indisponível")

    def list_students(self, limite=None):
        self._talvez_quebrar()
        ordenados = sorted(self.alunos.values(), key=lambda a: a["nome"])
        return [dict(a) for a in ordenados][:limite] if limite else [dict(a) for a in ordenados]

    def search_students(self, query, limite=None):
        """Assinatura espelha a do client real, `limite` incluído: um dublê que aceita menos do
        que o original esconde justamente o erro de chamada que se quer pegar."""
        self.buscas.append(query)
        self.limites.append(limite)
        self._talvez_quebrar()
        q = query.strip().lower()
        achados = [dict(a) for a in self.alunos.values() if q in a["nome"].lower()]
        return achados[:limite] if limite else achados

    def get_students_by_ras(self, ras):
        self._talvez_quebrar()
        return {ra: dict(self.alunos[ra]) for ra in ras if ra in self.alunos}

    def get_student(self, ra):
        self._talvez_quebrar()
        return dict(self.alunos[ra]) if ra in self.alunos else None

    def get_students_for_guardian_email(self, email):
        self._talvez_quebrar()
        alvo = email.strip().lower()
        return [dict(a) for a in self.alunos.values()
                if alvo in a.get("responsaveis_email", [])]

    def responsavel_reconhecido(self, email):
        self._talvez_quebrar()
        alvo = email.strip().lower()
        return any(alvo in a.get("responsaveis_email", []) for a in self.alunos.values())

    def get_guardian_emails_for_ra(self, ra):
        self._talvez_quebrar()
        return list(self.alunos.get(ra, {}).get("responsaveis_email", []))
