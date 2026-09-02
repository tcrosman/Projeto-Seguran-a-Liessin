# -*- coding: utf-8 -*-
"""O anexo de uma saída (atestado, autorização) passou por três defeitos em sequência:

1. reprovado, era descartado em silêncio e a saída era registrada sem ele, com a tela dizendo
   "Saída registrada!";
2. a validação olhava só a extensão do nome, embora `validar_upload_documento` — que confere os
   magic bytes — já existisse sem ser chamada por ninguém;
3. o arquivo ia para o disco, que na hospedagem é efêmero: sumia a cada deploy enquanto
   `saidas.documento_path` continuava apontando para ele.

Agora o anexo é validado por conteúdo, recusa o registro inteiro quando inválido, e é gravado na
tabela `saidas_documentos`, na mesma transação da saída — dura exatamente o que dura o registro.
"""
import io

import pytest

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40

FORMULARIO = {
    'ra': '2024001', 'data_saida': '2026-09-10', 'horario': '13:00',
    'motivo': 'Consulta', 'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho',
}


@pytest.fixture
def portaria(sessao_admin, banco):
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 0}])
    return sessao_admin


def _enviar(cliente, conteudo=None, nome=None, **extra):
    dados = dict(FORMULARIO, **extra)
    if conteudo is not None:
        dados['documento'] = (io.BytesIO(conteudo), nome)
    return cliente.post("/registrar_saida", data=dados, content_type='multipart/form-data')


def _saida_criada(banco):
    return banco.sql_com("INSERT INTO saidas (")


def _anexo_gravado(banco):
    return banco.sql_com("INSERT INTO saidas_documentos")


# ---------------------------------------------------------------- anexo inválido barra tudo

def test_anexo_recusado_nao_registra_a_saida_em_silencio(portaria, banco):
    r = _enviar(portaria, b'qualquer coisa', 'atestado.docx')

    assert r.status_code == 200
    assert _saida_criada(banco) == []
    assert _anexo_gravado(banco) == []


def test_anexo_recusado_explica_o_motivo_e_avisa_que_nada_foi_registrado(portaria):
    corpo = _enviar(portaria, b'qualquer coisa', 'atestado.docx').get_data(as_text=True)

    assert "Documento não anexado" in corpo
    assert "não foi registrada" in corpo


def test_extensao_permitida_com_conteudo_falso_e_recusada(portaria, banco):
    """Renomear um arquivo para .pdf passava pela validação antiga, que só olhava o nome."""
    r = _enviar(portaria, b'isto aqui nao e um PDF', 'atestado.pdf')

    assert "Documento não anexado" in r.get_data(as_text=True)
    assert _saida_criada(banco) == []


def test_imagem_com_conteudo_falso_e_recusada(portaria, banco):
    _enviar(portaria, b'nao sou png', 'foto.png')

    assert _saida_criada(banco) == []


def test_arquivo_acima_do_teto_e_recusado(portaria, banco):
    """O anexo ocupa espaço no banco agora; o teto próprio é bem menor que o da request."""
    from app.api.web import DOCUMENTO_MAX_BYTES
    grande = PDF + b'\x00' * (DOCUMENTO_MAX_BYTES + 1)

    r = _enviar(portaria, grande, 'atestado.pdf')

    assert "excede o limite" in r.get_data(as_text=True)
    assert _saida_criada(banco) == []


def test_anexo_recusado_nao_gera_auditoria(portaria, banco):
    _enviar(portaria, b'qualquer coisa', 'atestado.docx')

    assert banco.acoes_auditadas() == []


# ---------------------------------------------------------------- gravação no banco

def test_pdf_valido_vai_para_o_banco_com_bytes_tipo_e_nome(portaria, banco):
    r = _enviar(portaria, PDF, 'atestado.pdf')

    assert r.status_code == 302
    (_sql, params), = _anexo_gravado(banco)
    saida_id, nome, tipo, dados = params
    assert nome == 'atestado.pdf'
    assert tipo == 'application/pdf'
    assert dados == PDF


def test_imagem_valida_e_aceita_com_o_tipo_certo(portaria, banco):
    _enviar(portaria, PNG, 'autorizacao.png')

    (_sql, params), = _anexo_gravado(banco)
    assert params[2] == 'image/png'
    assert params[3] == PNG


def test_o_anexo_fica_amarrado_a_saida_recem_criada(portaria, banco):
    """O id vem do RETURNING do INSERT da saída: sem isso o anexo ficaria solto."""
    _enviar(portaria, PDF, 'atestado.pdf')

    (_sql, params), = _anexo_gravado(banco)
    assert params[0] == 1  # id devolvido pelo RETURNING


def test_saida_e_anexo_entram_na_mesma_transacao(portaria, banco):
    """Um `with get_db()` só: ou os dois existem, ou nenhum. Era essa a origem do arquivo órfão."""
    _enviar(portaria, PDF, 'atestado.pdf')

    assert banco.profundidade_maxima == 1
    assert len(_saida_criada(banco)) == 1 and len(_anexo_gravado(banco)) == 1


def test_saida_sem_anexo_continua_funcionando(portaria, banco):
    r = _enviar(portaria)

    assert r.status_code == 302
    assert len(_saida_criada(banco)) == 1
    assert _anexo_gravado(banco) == []


def test_campo_de_arquivo_vazio_nao_conta_como_anexo(portaria, banco):
    """O navegador manda o campo mesmo sem escolher arquivo — nome vazio não pode virar erro."""
    r = _enviar(portaria, b'', '')

    assert r.status_code == 302
    assert _anexo_gravado(banco) == []


def test_nome_de_arquivo_com_travessia_de_caminho_e_neutralizado(portaria, banco):
    """O nome ainda vai para o Content-Disposition na hora de servir, então continua sanitizado."""
    _enviar(portaria, PDF, '../../../etc/atestado.pdf')

    (_sql, params), = _anexo_gravado(banco)
    assert '/' not in params[1] and '..' not in params[1].replace('.pdf', '')


def test_saida_duplicada_nao_grava_anexo(portaria, banco):
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 1}])

    r = _enviar(portaria, PDF, 'atestado.pdf')

    assert "já tem uma saída pendente" in r.get_data(as_text=True)
    assert _saida_criada(banco) == [] and _anexo_gravado(banco) == []


def test_formulario_incompleto_nao_grava_anexo(portaria, banco):
    r = _enviar(portaria, PDF, 'atestado.pdf', motivo='')

    assert r.status_code == 200
    assert _anexo_gravado(banco) == []


# ---------------------------------------------------------------- servir de volta

def _doc(banco, **extra):
    linha = {'nome': 'atestado.pdf', 'tipo': 'application/pdf', 'dados': PDF}
    linha.update(extra)
    banco.responder("FROM saidas_documentos WHERE saida_id", [linha])


def test_documento_e_servido_com_o_tipo_e_o_nome_gravados(sessao_admin, banco):
    _doc(banco)

    r = sessao_admin.get("/saidas/3/documento")

    assert r.status_code == 200
    assert r.data == PDF
    assert r.headers['Content-Type'] == 'application/pdf'
    assert 'atestado.pdf' in r.headers['Content-Disposition']


def test_saida_sem_documento_devolve_404(sessao_admin, banco):
    banco.responder("FROM saidas_documentos WHERE saida_id", [])

    assert sessao_admin.get("/saidas/3/documento").status_code == 404


def test_o_porteiro_consegue_abrir_o_documento(cliente, banco):
    """É ele quem confere o atestado no portão — a rota não pode ficar só para o admin."""
    _doc(banco)
    with cliente.session_transaction() as s:
        s['user_id'] = 6
        s['role'] = 'vigia'
        s['username'] = 'porteiro'

    assert cliente.get("/saidas/3/documento").status_code == 200


def test_documento_exige_sessao(cliente, banco):
    _doc(banco)

    r = cliente.get("/saidas/3/documento")

    assert r.status_code == 302
    assert banco.sql_com("FROM saidas_documentos") == []
