# -*- coding: utf-8 -*-
"""A11 — a notificação ao responsável se perdia em silêncio.

`_nome_e_emails_para_saida` engolia a exceção e devolvia `emails = []`, que é o mesmo valor de
"este aluno não tem responsável com e-mail cadastrado". `enviar_email_async` não devolve status.
Resultado: com o banco da escola fora do ar por cinco minutos durante as saídas, a liberação era
commitada, o flash dizia "Saída autorizada!", e nenhum responsável era avisado de que o filho
saiu da escola — sem nada na tela e sem nada na auditoria.

São duas situações com desfechos diferentes: uma a secretaria resolve cadastrando o e-mail, a
outra é para ligar para a família agora.
"""
import pytest
from unittest.mock import patch

from app.core.tempo import hoje
from app.services.school_sql_directory import SchoolSqlIndisponivel

SAIDA = {'data_saida': hoje(), 'horario': '12:00', 'ra': '2024001',
         'status': 'pendente', 'aluno_id_legado': None, 'aluno_nome_legado': None}


class _Diretorio:
    def __init__(self, emails=(), fora_do_ar=False, nome="Ana Beatriz Souza"):
        self._emails = list(emails)
        self._fora = fora_do_ar
        self._nome = nome

    def get_student(self, ra):
        if self._fora:
            raise SchoolSqlIndisponivel("banco da escola fora do ar")
        return {"ra": ra, "nome": self._nome}

    def get_guardian_emails_for_ra(self, ra):
        if self._fora:
            raise SchoolSqlIndisponivel("banco da escola fora do ar")
        return list(self._emails)


@pytest.fixture
def portaria(cliente, banco):
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=1)
    banco.responder("FROM saidas s LEFT JOIN alunos", [SAIDA])
    with cliente.session_transaction() as s:
        s['user_id'] = 9
        s['role'] = 'vigia'
        s['username'] = 'vigia_teste'
    return cliente


def _liberar(sessao, diretorio):
    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        return sessao.post("/concluir_saida/5", follow_redirects=True)


# ---------------------------------------------------------------- a tela deixa de mentir

def test_banco_da_escola_fora_do_ar_avisa_na_tela(portaria):
    """O cenário do diagnóstico: cinco minutos de indisponibilidade no meio das saídas."""
    corpo = _liberar(portaria, _Diretorio(fora_do_ar=True)).get_data(as_text=True)

    assert "NÃO foi possível notificar" in corpo
    assert "telefone" in corpo


def test_aluno_sem_email_cadastrado_recebe_outra_mensagem(portaria):
    """Distinguir importa: uma a secretaria resolve cadastrando o e-mail, a outra não."""
    corpo = _liberar(portaria, _Diretorio(emails=[])).get_data(as_text=True)

    assert "não há e-mail cadastrado" in corpo
    assert "indisponível" not in corpo


def test_a_tela_nao_diz_so_saida_autorizada_quando_ninguem_foi_avisado(portaria):
    corpo = _liberar(portaria, _Diretorio(fora_do_ar=True)).get_data(as_text=True)

    assert "Saída autorizada!" not in corpo


def test_com_responsaveis_notificados_a_mensagem_e_de_sucesso(portaria, correio):
    corpo = _liberar(portaria, _Diretorio(emails=["mae@teste.com"])).get_data(as_text=True)

    assert "Saída autorizada!" in corpo
    assert correio.destinatarios() == ["mae@teste.com"]


# ---------------------------------------------------------------- a auditoria registra o número

def test_a_auditoria_guarda_quantos_responsaveis_foram_avisados(portaria, banco):
    _liberar(portaria, _Diretorio(emails=["mae@teste.com", "pai@teste.com"]))

    acao, detalhes, _ip = [a for a in banco.auditorias() if a[0] == "CONCLUIU SAÍDA"][0]
    assert "responsáveis notificados: 2" in detalhes


def test_a_auditoria_registra_que_ninguem_foi_avisado(portaria, banco):
    """Sem esse número, a trilha não distinguia "avisei os dois" de "não avisei ninguém" — e é a
    segunda que alguém vai precisar reconstruir depois."""
    _liberar(portaria, _Diretorio(fora_do_ar=True))

    _acao, detalhes, _ip = [a for a in banco.auditorias() if a[0] == "CONCLUIU SAÍDA"][0]
    assert "responsáveis notificados: 0" in detalhes
    assert "indisponível" in detalhes


# ---------------------------------------------------------------- o que não pode mudar

def test_a_liberacao_acontece_mesmo_sem_conseguir_notificar(portaria, banco):
    """A saída já foi persistida antes desta etapa; uma falha aqui não pode desfazê-la."""
    _liberar(portaria, _Diretorio(fora_do_ar=True))

    assert banco.sql_com("UPDATE saidas SET status = 'concluida'") != []


def test_nenhum_email_e_enviado_com_o_nome_do_aluno_desconhecido(portaria, correio):
    """Antes, se algum e-mail saísse nesse estado, viria com "RA 2024001" no corpo."""
    _liberar(portaria, _Diretorio(fora_do_ar=True))

    assert correio.enviados == []
