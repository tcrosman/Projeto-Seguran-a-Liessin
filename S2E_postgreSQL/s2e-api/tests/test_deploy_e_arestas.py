# -*- coding: utf-8 -*-
"""Arestas de configuração e de migração que não apareciam em teste funcional nenhum:

* `requirements.txt` declarava pandas, openpyxl e requests, que o projeto nunca importa, e não
  tinha teto de versão em linha alguma — o build da hospedagem instalava o que estivesse
  publicado no dia;
* `run.py` fixava `host="0.0.0.0"` e `debug=True`, expondo o console do Werkzeug para a rede;
* `list_of_exits.html` testava `saida.foto_path`, chave que o resolvedor deixou de produzir na
  migração para RA — como indefinido é falso no Jinja, a foto nunca aparecia naquela seção;
* o histórico herdava o `LIMIT 20` da busca da portaria, perdendo alunos em silêncio.
"""
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ler(*caminho):
    with open(os.path.join(RAIZ, *caminho), encoding='utf-8') as f:
        return f.read()


def _requisitos():
    """(nome, especificador) de cada linha de requisito, ignorando comentários."""
    linhas = [l.strip() for l in _ler('requirements.txt').splitlines()]
    itens = []
    for l in linhas:
        if not l or l.startswith('#') or l.startswith('-r'):
            continue
        nome = re.split(r'[><=!]', l, 1)[0].strip()
        itens.append((nome, l[len(nome):]))
    return itens


# ---------------------------------------------------------------- dependências

def test_toda_dependencia_declarada_e_realmente_importada():
    """pandas puxa numpy: dezenas de megabytes de build e de memória num plano free, sem uso."""
    codigo = ""
    for pasta, _sub, arquivos in os.walk(os.path.join(RAIZ, 'app')):
        for a in arquivos:
            if a.endswith('.py'):
                codigo += _ler(os.path.join(pasta, a))
    # nomes de import diferem do nome no PyPI em alguns casos
    modulo = {'psycopg2-binary': 'psycopg2', 'python-dotenv': 'dotenv',
              'flask-wtf': 'flask_wtf', 'flask-talisman': 'flask_talisman'}

    nao_usadas = []
    for nome, _spec in _requisitos():
        if nome == 'gunicorn':
            continue  # não é importado: é o servidor declarado no Procfile
        alvo = modulo.get(nome, nome)
        if not re.search(rf'^\s*(import {alvo}|from {alvo}[. ])', codigo, re.M):
            nao_usadas.append(nome)

    assert nao_usadas == []


def test_toda_dependencia_tem_teto_de_versao():
    """Sem teto, um redeploy meses depois pode trazer uma versão maior e quebrar o que estava
    de pé, sem ninguém ter mudado uma linha de código."""
    sem_teto = [nome for nome, spec in _requisitos() if '<' not in spec]

    assert sem_teto == []


def test_dependencias_tambem_tem_piso():
    sem_piso = [nome for nome, spec in _requisitos() if '>=' not in spec]

    assert sem_piso == []


# ---------------------------------------------------------------- servidor de desenvolvimento

def test_run_py_nao_fixa_debug_nem_escuta_em_todas_as_interfaces():
    # Sem os comentários: eles descrevem o comportamento antigo e casariam com o próprio assert.
    codigo = "\n".join(l for l in _ler('run.py').splitlines()
                       if not l.strip().startswith('#'))

    assert 'debug=True' not in codigo
    assert '0.0.0.0' not in codigo
    # host e debug passam a vir do ambiente, com padrão fechado
    assert "os.getenv('DEV_HOST', '127.0.0.1')" in codigo
    assert "os.getenv('FLASK_DEBUG', 'false')" in codigo


def test_producao_continua_subindo_por_gunicorn():
    assert 'gunicorn' in _ler('Procfile')
    assert 'gunicorn' in _ler('render.yaml')


def test_config_do_flask_recebe_base_url_do_ambiente(monkeypatch):
    """Os links de reset consultam app.config, não os.getenv diretamente."""
    monkeypatch.setenv('BASE_URL', 'https://secureedu.escola.br')
    # Config é avaliada no import; recarregar prova que o valor do ambiente chega ao Flask.
    import importlib
    import app.config as config_mod
    importlib.reload(config_mod)

    from flask import Flask
    aplicacao = Flask(__name__)
    aplicacao.config.from_object(config_mod.Config)

    assert aplicacao.config['BASE_URL'] == 'https://secureedu.escola.br'


def test_setup_db_nao_apaga_banco_por_padrao():
    codigo = _ler('setup_db.py')

    assert "if args.reset:" in codigo
    assert "CONFIRM_RESET_DATABASE" in codigo
    assert "generate_password_hash('admin123'" not in codigo


def test_falha_de_migracao_nao_e_engolida():
    codigo = _ler('app', '__init__.py')
    bloco = codigo[codigo.index('# Migração do schema'):codigo.index('# Limpeza/expiração periódica')]

    assert 'except Exception' not in bloco


# ---------------------------------------------------------------- templates sem chave morta

def test_nenhum_template_de_saidas_usa_chave_que_o_resolvedor_nao_produz():
    """Varredura, e não um assert pontual: o mesmo tipo de resíduo pode reaparecer na próxima
    migração de campo."""
    produzidas = {
        'aluno', 'foto_src', 'serie', 'turma', 'ra', 'id', 'horario', 'motivo',
        'responsavel_escola', 'tipo_saida', 'acompanhante', 'documento_path', 'tem_documento',
        'status', 'data_saida', 'liberado_em', 'aluno_legado', 'foto_path_legado',
        'serie_legado', 'turma_legado',
    }
    sobras = {}
    for arquivo in ('list_of_exits.html', 'history_of_departures.html', 'edit_exits.html'):
        conteudo = _ler('app', 'templates', 'departures', arquivo)
        usadas = set(re.findall(r'\b(?:saida|r)\.([a-z_]+)', conteudo))
        if usadas - produzidas:
            sobras[arquivo] = sorted(usadas - produzidas)

    assert sobras == {}


def test_as_tres_secoes_da_lista_usam_a_mesma_chave_de_foto():
    conteudo = _ler('app', 'templates', 'departures', 'list_of_exits.html')

    assert 'saida.foto_path' not in conteudo
    assert conteudo.count('saida.foto_src') >= 3


# ---------------------------------------------------------------- limite da busca

def test_historico_pede_um_limite_maior_que_o_da_portaria(sessao_admin, banco):
    from unittest.mock import patch
    from tests.apoio import DiretorioFalso
    from app.api.web import RAS_NO_HISTORICO
    from app.services.school_sql_directory import SchoolSqlDirectoryClient

    escola = DiretorioFalso({})
    banco.responder("FROM saidas s", [])

    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        sessao_admin.get("/historico?nome=Silva")

    assert escola.limites == [RAS_NO_HISTORICO]
    assert RAS_NO_HISTORICO > SchoolSqlDirectoryClient.LIMITE_BUSCA_PADRAO


def test_a_portaria_continua_com_o_limite_curto(sessao_admin, banco):
    """O autocomplete não deve trazer a escola inteira a cada tecla.

    O limite passou a ser explícito na rota (RESULTADOS_AUTOCOMPLETE), e não mais o padrão do
    client: ele é o teto de quanto cadastro sai por requisição, e por isso precisa estar onde a
    decisão é tomada — ver A7.
    """
    from unittest.mock import patch
    from tests.apoio import DiretorioFalso
    from app.api.web import RESULTADOS_AUTOCOMPLETE
    from app.services.school_sql_directory import SchoolSqlDirectoryClient

    escola = DiretorioFalso({})
    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        sessao_admin.get("/portaria/buscar_aluno?q=Silva")

    assert escola.limites == [RESULTADOS_AUTOCOMPLETE]
    assert RESULTADOS_AUTOCOMPLETE <= SchoolSqlDirectoryClient.LIMITE_BUSCA_PADRAO


def test_o_mock_respeita_o_limite_como_o_client_real():
    """Dublê e original precisam concordar, senão o teste do histórico não prova nada."""
    from app.services.school_sql_directory import SchoolSqlDirectoryMock

    assert len(SchoolSqlDirectoryMock().search_students("a", limite=1)) <= 1
