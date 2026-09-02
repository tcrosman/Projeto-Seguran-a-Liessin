# -*- coding: utf-8 -*-
"""/registrar_saida valida campo a campo antes de gravar; /editar_saida validava só horário e
tipo. `motivo` e `responsavel_escola` iam direto do formulário para o UPDATE, e `saidas.motivo` é
NOT NULL no banco: um POST sem o campo gravava NULL e derrubava a request com IntegrityError.
O `required` do formulário é do navegador e não protege nada disso.
"""
import pytest

SAIDA_PENDENTE = {
    'id': 3, 'ra': '2024001', 'turma': 'A', 'horario': '12:00', 'motivo': 'Dentista',
    'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho', 'acompanhante': None,
    'status': 'pendente', 'documento_path': None, 'aluno_legado': None,
    'foto_path_legado': None, 'serie_legado': None, 'turma_legado': None,
}

COMPLETO = {
    'horario': '15:30', 'motivo': 'Consulta médica', 'responsavel_escola': 'Secretaria',
    'tipo_saida': 'sozinho',
}


@pytest.fixture
def com_saida(sessao_admin, banco):
    banco.responder("FROM saidas s", [SAIDA_PENDENTE])
    return sessao_admin


def _sem(campo):
    dados = dict(COMPLETO)
    dados.pop(campo)
    return dados


# ---------------------------------------------------------------- o defeito em si

@pytest.mark.parametrize("campo", ['motivo', 'responsavel_escola', 'horario', 'tipo_saida'])
def test_campo_ausente_nao_chega_ao_update(com_saida, banco, campo):
    """Nenhum campo obrigatório pode virar NULL no banco — `motivo` chegava lá e estourava."""
    r = com_saida.post("/editar_saida/3", data=_sem(campo))

    assert r.status_code == 200
    assert banco.sql_com("UPDATE saidas SET horario") == []


@pytest.mark.parametrize("campo", ['motivo', 'responsavel_escola'])
def test_campo_so_com_espacos_e_recusado(com_saida, banco, campo):
    """" " passa por um `if not motivo` ingênuo e vira um motivo em branco na tela da portaria."""
    dados = dict(COMPLETO)
    dados[campo] = '   '

    com_saida.post("/editar_saida/3", data=dados)

    assert banco.sql_com("UPDATE saidas SET horario") == []


def test_mensagem_de_erro_e_mostrada_ao_usuario(com_saida):
    r = com_saida.post("/editar_saida/3", data=_sem('motivo'))

    assert "Todos os campos são obrigatórios" in r.get_data(as_text=True)


# ---------------------------------------------------------------- sem regressão

def test_edicao_valida_continua_gravando(com_saida, banco):
    r = com_saida.post("/editar_saida/3", data=COMPLETO)

    assert r.status_code == 302
    _sql, params = banco.sql_com("UPDATE saidas SET horario")[0]
    assert params[0] == '15:30'
    assert params[1] == 'Consulta médica'


def test_valor_gravado_vem_sem_espaco_nas_pontas(com_saida, banco):
    dados = dict(COMPLETO, motivo='  Consulta médica  ')

    com_saida.post("/editar_saida/3", data=dados)

    _sql, params = banco.sql_com("UPDATE saidas SET horario")[0]
    assert params[1] == 'Consulta médica'


def test_horario_fora_do_formato_continua_recusado(com_saida, banco):
    """Validação que já existia — não pode ter sido engolida pela nova."""
    r = com_saida.post("/editar_saida/3", data=dict(COMPLETO, horario='9:30'))

    assert "Horário inválido" in r.get_data(as_text=True)
    assert banco.sql_com("UPDATE saidas SET horario") == []


def test_tipo_de_saida_invalido_continua_recusado(com_saida, banco):
    r = com_saida.post("/editar_saida/3", data=dict(COMPLETO, tipo_saida='qualquer_coisa'))

    assert "Tipo de saída inválido" in r.get_data(as_text=True)
    assert banco.sql_com("UPDATE saidas SET horario") == []


def test_acompanhante_so_e_gravado_quando_a_saida_e_acompanhada(com_saida, banco):
    com_saida.post("/editar_saida/3", data=dict(COMPLETO, tipo_saida='sozinho',
                                                acompanhante='Alguém'))

    _sql, params = banco.sql_com("UPDATE saidas SET horario")[0]
    assert params[4] is None


def test_saida_ja_concluida_nao_e_editavel(sessao_admin, banco):
    """A consulta filtra status='pendente'; sem linha, a rota tem que sair antes do UPDATE."""
    banco.responder("FROM saidas s", [])

    r = sessao_admin.post("/editar_saida/3", data=COMPLETO)

    assert r.status_code == 302
    assert banco.sql_com("UPDATE saidas SET horario") == []


def test_porteiro_continua_sem_editar(cliente, banco):
    """O bloqueio vem do decorador (403), antes do corpo da rota — o `if role == 'vigia'` que
    existe lá dentro é inalcançável."""
    banco.responder("FROM saidas s", [SAIDA_PENDENTE])
    with cliente.session_transaction() as s:
        s['user_id'] = 6
        s['role'] = 'vigia'
        s['username'] = 'porteiro'

    r = cliente.post("/editar_saida/3", data=COMPLETO)

    assert r.status_code == 403
    assert banco.sql_com("UPDATE saidas SET horario") == []


def test_edicao_recusada_nao_gera_auditoria(com_saida, banco):
    """Sem alteração, sem registro na trilha."""
    com_saida.post("/editar_saida/3", data=_sem('motivo'))

    assert banco.acoes_auditadas() == []
