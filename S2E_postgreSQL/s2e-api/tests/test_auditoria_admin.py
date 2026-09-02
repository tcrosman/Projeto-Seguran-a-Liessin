# -*- coding: utf-8 -*-
"""Nenhuma decisão administrativa era registrada: aprovar/rejeitar saída, aprovar/bloquear
responsável, criar/apagar usuário e editar saída não deixavam rastro nenhum. A pergunta "quem
autorizou a saída daquele aluno?" não tinha resposta 30 dias depois, quando a manutenção apaga a
solicitação — e o arquivo de log some a cada deploy, porque o disco do Render é efêmero.

Estes testes cobrem as três coisas que o conserto precisa garantir ao mesmo tempo:
registrar a decisão, gravar no banco (que sobrevive ao restart), e não estragar nada no caminho.
"""
import pytest


def _solicitacao(**extra):
    linha = {
        'id': 7, 'aluno_id': None, 'ra': '2024001', 'data_solicitada': '2026-09-10',
        'horario_solicitado': '13:00', 'motivo': 'Consulta médica', 'tipo_saida': 'sozinho',
        'acompanhante': None, 'status': 'aguardando', 'turma': 'A',
        'responsavel_nome': 'Maria Souza', 'responsavel_email': 'mae@teste.com',
        'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
    }
    linha.update(extra)
    return linha


# ---------------------------------------------------------------- decisões sobre saídas

def test_aprovar_solicitacao_registra_quem_aprovou(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])

    r = sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert r.status_code == 302
    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "APROVOU SOLICITAÇÃO"
    # O registro precisa identificar a solicitação E o aluno: só o id não responde à pergunta
    # "quem saiu?" depois que a manutenção apagar a linha da solicitação.
    assert "7" in detalhes and "2024001" in detalhes and "2026-09-10" in detalhes


def test_aprovar_registra_o_usuario_e_o_ip_da_sessao(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])

    sessao_admin.post("/admin/solicitacoes/7/aprovar", environ_base={'REMOTE_ADDR': '10.1.2.3'})

    _sql, params = banco.sql_com("INSERT INTO auditoria")[0]
    assert params[0] == 'admin_teste'
    assert params[3] == '10.1.2.3'


def test_aprovar_audita_tambem_quando_ja_existia_saida_pendente(sessao_admin, banco):
    """Caminho de saída antecipado da rota: a aprovação aconteceu, mas nenhuma saída nova é
    criada. Auditar só no fim deixaria justamente esta decisão sem registro."""
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [{'status': 'pendente'}])

    sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.acoes_auditadas() == ["APROVOU SOLICITAÇÃO"]
    assert banco.sql_com("INSERT INTO saidas (") == []


def test_solicitacao_ja_revisada_nao_gera_auditoria(sessao_admin, banco):
    """Sem decisão, sem registro — a trilha não pode encher de eventos que não aconteceram."""
    banco.responder("FROM solicitacoes_saida ss", [])

    sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.acoes_auditadas() == []


def test_corrida_de_aprovacao_perdida_nao_gera_auditoria(sessao_admin, banco):
    """Duas aprovações simultâneas: a segunda casa 0 linhas no UPDATE e para. Auditar aí
    registraria duas aprovações para uma decisão só."""
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=0)

    sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.acoes_auditadas() == []


def test_rejeitar_solicitacao_registra_a_decisao(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])

    r = sessao_admin.post("/admin/solicitacoes/7/rejeitar")

    assert r.status_code == 302
    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "REJEITOU SOLICITAÇÃO"
    assert "2024001" in detalhes


def test_editar_saida_registra_o_que_mudou(sessao_admin, banco):
    banco.responder("FROM saidas s", [{
        'id': 3, 'ra': '2024001', 'turma': 'A', 'horario': '12:00', 'motivo': 'Dentista',
        'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho', 'acompanhante': None,
        'status': 'pendente', 'documento_path': None, 'aluno_legado': None,
        'foto_path_legado': None, 'serie_legado': None, 'turma_legado': None,
    }])

    r = sessao_admin.post("/editar_saida/3", data={
        'horario': '15:30', 'motivo': 'Dentista', 'responsavel_escola': 'Secretaria',
        'tipo_saida': 'acompanhado', 'acompanhante': 'Maria Souza',
    })

    assert r.status_code == 302
    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "EDITOU SAÍDA"
    assert "15:30" in detalhes and "acompanhado" in detalhes


# ---------------------------------------------------------------- decisões sobre contas

def test_aprovar_responsavel_registra_quem_liberou_o_acesso(sessao_admin, banco):
    banco.responder("SELECT nome, email FROM responsaveis", [
        {'nome': 'Maria Souza', 'email': 'mae@teste.com'}
    ])

    sessao_admin.post("/admin/responsaveis/4/aprovar")

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "APROVOU RESPONSÁVEL"
    assert "mae@teste.com" in detalhes and "4" in detalhes


def test_bloquear_responsavel_registra_a_decisao(sessao_admin, banco):
    banco.responder("SELECT nome FROM responsaveis", [{'nome': 'Maria Souza'}])

    sessao_admin.post("/admin/responsaveis/4/bloquear")

    assert banco.acoes_auditadas() == ["BLOQUEOU RESPONSÁVEL"]


def test_deletar_usuario_registra_quem_era_a_conta_apagada(sessao_admin, banco):
    """O nome precisa ser lido antes do DELETE: depois dele não há mais de onde tirar."""
    banco.responder("SELECT * FROM usuarios WHERE id", [
        {'id': 9, 'username': 'porteiro_antigo', 'role': 'vigia', 'email': 'p@escola.br'}
    ])

    sessao_admin.post("/deletar_usuario/9")

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "DELETOU USUÁRIO"
    assert "porteiro_antigo" in detalhes and "vigia" in detalhes


def test_criar_usuario_registra_o_perfil_concedido(sessao_admin, banco):
    banco.responder("INSERT INTO usuarios", [{'id': 12}])

    sessao_admin.post("/novo", data={
        'u': 'nova_secretaria', 's': 'Senha@Forte1', 'r': 'admin', 'e': 'sec@escola.br',
    })

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "CRIOU USUÁRIO"
    # O perfil é o que importa numa apuração: quem virou admin, e por decisão de quem.
    assert "nova_secretaria" in detalhes and "admin" in detalhes


# ---------------------------------------------------------------- o conserto não pode estragar nada

@pytest.mark.parametrize("rota,dados,preparar", [
    ("/admin/solicitacoes/7/aprovar", None, "solicitacao"),
    ("/admin/solicitacoes/7/rejeitar", None, "solicitacao"),
    ("/admin/responsaveis/4/aprovar", None, "responsavel"),
    ("/admin/responsaveis/4/bloquear", None, "responsavel"),
    ("/deletar_usuario/9", None, "usuario"),
])
def test_auditoria_nao_abre_uma_segunda_conexao_do_pool(sessao_admin, banco, rota, dados, preparar):
    """Auditar de dentro de um `with get_db()` sem reaproveitar a conexão retém duas ao mesmo
    tempo. Com DB_POOL_MAX=5, algumas requests simultâneas travariam o pool — e nenhum teste
    funcional pegaria isso, porque cada uma delas passa sozinha."""
    if preparar == "solicitacao":
        banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
        banco.responder("SELECT status FROM saidas", [])
    elif preparar == "responsavel":
        banco.responder("FROM responsaveis", [{'nome': 'Maria Souza', 'email': 'mae@teste.com'}])
    else:
        banco.responder("SELECT * FROM usuarios WHERE id",
                        [{'id': 9, 'username': 'x', 'role': 'basico', 'email': ''}])

    sessao_admin.post(rota, data=dados)

    assert banco.profundidade_maxima == 1, (
        f"{rota} manteve {banco.profundidade_maxima} conexões abertas ao mesmo tempo")


def test_falha_ao_gravar_auditoria_nao_derruba_a_aprovacao(sessao_admin, banco):
    """A trilha é importante, mas não pode ser um ponto único de falha: se a tabela sumir ou o
    INSERT falhar, a aprovação já persistida tem que seguir de pé, não virar erro 500."""
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])
    banco.falhar_em("INSERT INTO auditoria")

    r = sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert r.status_code == 302
    # A saída continua sendo criada — a rota não abortou no meio.
    assert banco.sql_com("INSERT INTO saidas") != []


def test_falha_na_auditoria_desfaz_so_ela_via_savepoint(sessao_admin, banco):
    """Sem SAVEPOINT, um INSERT que falha aborta a transação inteira no Postgres — e a aprovação
    já feita cairia junto, silenciosamente."""
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])
    banco.falhar_em("INSERT INTO auditoria")

    sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.sql_com("SAVEPOINT auditoria") != []
    assert banco.sql_com("ROLLBACK TO SAVEPOINT auditoria") != []


def test_savepoint_e_liberado_quando_a_auditoria_da_certo(sessao_admin, banco):
    """SAVEPOINT não liberado se acumula na transação; o caminho feliz precisa dar RELEASE."""
    banco.responder("FROM solicitacoes_saida ss", [_solicitacao()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])

    sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.sql_com("RELEASE SAVEPOINT auditoria") != []
    assert banco.sql_com("ROLLBACK TO SAVEPOINT auditoria") == []


def test_registrar_saida_continua_auditando_e_agora_sem_conexao_extra(sessao_admin, banco):
    """Esta rota já auditava, mas de dentro do `with get_db()` e sem passar a conexão."""
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 0}])

    r = sessao_admin.post("/registrar_saida", data={
        'ra': '2024001', 'data_saida': '2026-09-10', 'horario': '13:00',
        'motivo': 'Consulta', 'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho',
    })

    assert r.status_code == 302
    assert banco.acoes_auditadas() == ["REGISTROU SAÍDA"]
    assert banco.profundidade_maxima == 1
