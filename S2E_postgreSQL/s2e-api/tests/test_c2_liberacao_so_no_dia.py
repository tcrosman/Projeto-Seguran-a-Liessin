# -*- coding: utf-8 -*-
"""C2 — /concluir_saida liberava saída de qualquer data.

O UPDATE filtrava só por `status='pendente'`, e /saidas aceita `?data=` arbitrária e desenha o
botão "Liberar" para os pendentes daquele dia. Bastava o vigia mudar o seletor de data para
amanhã e clicar: a criança saía hoje com autorização de outro dia, pela interface normal, sem
POST forjado. O e-mail ao responsável ainda dizia "hoje às 12:00", e nada na tela ou na
auditoria registrava a divergência.
"""
import pytest

from app.core.tempo import hoje


def _saida(**extra):
    linha = {'data_saida': hoje(), 'horario': '12:00', 'ra': '2024001',
             'status': 'pendente', 'aluno_id_legado': None, 'aluno_nome_legado': None}
    linha.update(extra)
    return linha


@pytest.fixture
def portaria(cliente):
    with cliente.session_transaction() as s:
        s['user_id'] = 9
        s['role'] = 'vigia'
        s['username'] = 'vigia_teste'
    return cliente


# ---------------------------------------------------------------- a data entra no UPDATE

def test_a_data_de_hoje_faz_parte_do_update(portaria, banco):
    """Sem isso a condição não é atômica — um SELECT antes do UPDATE abriria janela."""
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=1)
    banco.responder("FROM saidas s LEFT JOIN alunos", [_saida()])

    portaria.post("/concluir_saida/5")

    (sql, params), = banco.sql_com("UPDATE saidas SET status = 'concluida'")
    assert "data_saida = %s" in sql
    assert hoje() in params


def test_saida_de_data_futura_nao_e_liberada(portaria, banco):
    """O cenário do diagnóstico: seletor de data em amanhã, clique em Liberar."""
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=0)
    banco.responder("SELECT status, data_saida FROM saidas",
                    [{'status': 'pendente', 'data_saida': '2099-01-01'}])

    r = portaria.post("/concluir_saida/5", follow_redirects=True)

    assert "2099-01-01" in r.get_data(as_text=True)
    assert "não pode ser liberada hoje" in r.get_data(as_text=True)


def test_saida_de_data_passada_nao_e_liberada(portaria, banco):
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=0)
    banco.responder("SELECT status, data_saida FROM saidas",
                    [{'status': 'pendente', 'data_saida': '2020-03-01'}])

    r = portaria.post("/concluir_saida/5", follow_redirects=True)

    assert "não pode ser liberada hoje" in r.get_data(as_text=True)


def test_recusa_por_data_fica_na_auditoria(portaria, banco):
    """Uma tentativa de liberar criança em dia errado é exatamente o que precisa ficar
    registrado — antes não aparecia nem na tela nem na trilha."""
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=0)
    banco.responder("SELECT status, data_saida FROM saidas",
                    [{'status': 'pendente', 'data_saida': '2099-01-01'}])

    portaria.post("/concluir_saida/5")

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "LIBERAÇÃO RECUSADA (data)"
    assert "2099-01-01" in detalhes


def test_recusa_por_data_nao_notifica_ninguem(portaria, banco, correio):
    """A saída não aconteceu; mandar "seu filho saiu" seria pior que o próprio bug."""
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=0)
    banco.responder("SELECT status, data_saida FROM saidas",
                    [{'status': 'pendente', 'data_saida': '2099-01-01'}])

    portaria.post("/concluir_saida/5")

    assert correio.enviados == []


# ---------------------------------------------------------------- o que não pode mudar

def test_saida_de_hoje_continua_sendo_liberada(portaria, banco, correio):
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=1)
    banco.responder("FROM saidas s LEFT JOIN alunos", [_saida()])

    r = portaria.post("/concluir_saida/5", follow_redirects=True)

    assert "Saída autorizada!" in r.get_data(as_text=True)
    assert correio.destinatarios() == ["pai@teste.com", "mae@teste.com"]


def test_saida_ja_autorizada_ainda_diz_que_ja_estava_autorizada(portaria, banco):
    """A recusa por data não pode engolir a mensagem do duplo-clique, que é outra coisa."""
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=0)
    banco.responder("SELECT status, data_saida FROM saidas",
                    [{'status': 'concluida', 'data_saida': hoje()}])

    r = portaria.post("/concluir_saida/5", follow_redirects=True)

    assert "já havia sido autorizada" in r.get_data(as_text=True)


PENDENTE_NA_TELA = {
    'id': 5, 'ra': '2024001', 'turma': 'A', 'horario': '12:00', 'motivo': 'Consulta',
    'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho', 'acompanhante': None,
    'documento_path': None, 'status': 'pendente', 'tem_documento': False,
    'aluno_legado': None, 'serie_legado': None, 'turma_legado': None, 'foto_path_legado': None,
}


def test_a_lista_nao_desenha_o_botao_liberar_fora_do_dia(portaria, banco):
    """A checagem que vale é a de concluir_saida; esta evita oferecer na tela uma ação que o
    servidor vai recusar — o botão é o caminho pelo qual o defeito era alcançado."""
    banco.responder("FROM saidas s LEFT JOIN alunos", [PENDENTE_NA_TELA])

    corpo = portaria.get("/saidas?data=2099-01-01").get_data(as_text=True)

    assert "LIBERAR SAÍDA" not in corpo
    assert "Só pode ser liberada em 2099-01-01" in corpo


def test_a_lista_de_hoje_continua_com_o_botao(portaria, banco):
    banco.responder("FROM saidas s LEFT JOIN alunos", [PENDENTE_NA_TELA])

    corpo = portaria.get("/saidas?data=" + hoje()).get_data(as_text=True)

    assert "LIBERAR SAÍDA" in corpo
