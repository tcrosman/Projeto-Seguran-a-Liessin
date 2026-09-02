# -*- coding: utf-8 -*-
"""Quando o envio de e-mail falhava, `enviar_email_async` imprimia no log o conteúdo que o
usuário deveria ter recebido: o código de 2FA e os links de redefinição de senha. A intenção
documentada era recuperar isso em desenvolvimento, mas não havia distinção entre desenvolvimento
e produção — e falha de SMTP não é exótica (senha de app do Gmail vencida, cota diária estourada).

Bastava isso para o log da hospedagem passar a conter um segundo fator válido por 10 minutos, ao
lado do e-mail a que pertence, e links de troca de senha prontos para uso.
"""
import logging

import pytest

from app.core import mailer


@pytest.fixture
def envio_falhando(monkeypatch):
    """SMTP sempre falha — é o único caminho em que o fallback é impresso."""
    monkeypatch.setattr(mailer, 'enviar_email', lambda *a, **k: False)


class _Coletor(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.registros = []

    def emit(self, record):
        self.registros.append(self.format(record))

    @property
    def text(self):
        return "\n".join(self.registros)


@pytest.fixture
def log():
    """Handler preso ao logger 's2e', e não o caplog.

    logging_config define `propagate = False` nesse logger, então nada chega ao root — e o
    caplog, que escuta no root, devolveria texto vazio. As asserções de "o segredo NÃO aparece"
    passariam sem provar nada.
    """
    coletor = _Coletor()
    logger = logging.getLogger('s2e')
    logger.addHandler(coletor)
    try:
        yield coletor
    finally:
        logger.removeHandler(coletor)


def _enviar_sincrono(destinatario, fallback):
    """Executa a tarefa sem passar pelo executor, para o teste não depender de thread."""
    enviados = []
    mailer._EXECUTOR.submit = lambda tarefa: enviados.append(tarefa())
    mailer.enviar_email_async(destinatario, "assunto", "<p>corpo</p>", fallback_log=fallback)


@pytest.fixture(autouse=True)
def _restaura_executor():
    original = mailer._EXECUTOR.submit
    yield
    mailer._EXECUTOR.submit = original


# ---------------------------------------------------------------- o defeito em si

def test_codigo_2fa_nao_vai_para_o_log_por_padrao(envio_falhando, log, monkeypatch):
    monkeypatch.delenv('MAILER_FALLBACK_LOG', raising=False)

    _enviar_sincrono("mae@teste.com", "[2FA FALLBACK] Token para mae@teste.com: 428913")

    texto = log.text
    assert "428913" not in texto


def test_link_de_redefinicao_nao_vai_para_o_log_por_padrao(envio_falhando, log, monkeypatch):
    monkeypatch.delenv('MAILER_FALLBACK_LOG', raising=False)

    _enviar_sincrono("admin@escola.br",
                     "[FALLBACK] Use este link: https://s2e/resetar_senha/tok-secreto-123")

    assert "tok-secreto-123" not in log.text


def test_a_falha_continua_visivel_mesmo_sem_o_segredo(envio_falhando, log, monkeypatch):
    """Esconder o segredo não pode esconder o problema: quem opera precisa saber que o
    responsável não recebeu o código."""
    monkeypatch.delenv('MAILER_FALLBACK_LOG', raising=False)

    _enviar_sincrono("mae@teste.com", "[2FA FALLBACK] Token para mae@teste.com: 428913")

    assert "mae@teste.com" in log.text
    assert "falhou" in log.text
    # E diz como recuperar em desenvolvimento, sem obrigar ninguém a caçar no código.
    assert "MAILER_FALLBACK_LOG" in log.text


@pytest.mark.parametrize("valor", ['false', 'False', '', 'sim', '0', 'no'])
def test_qualquer_valor_que_nao_seja_true_mantem_o_segredo_fora(envio_falhando, log,
                                                                monkeypatch, valor):
    """Erra para o lado seguro: só o 'true' explícito libera."""
    monkeypatch.setenv('MAILER_FALLBACK_LOG', valor)

    _enviar_sincrono("mae@teste.com", "[2FA FALLBACK] Token: 428913")

    assert "428913" not in log.text


# ---------------------------------------------------------------- desenvolvimento continua servido

def test_com_a_variavel_ligada_o_desenvolvedor_recupera_o_codigo(envio_falhando, log, monkeypatch):
    monkeypatch.setenv('MAILER_FALLBACK_LOG', 'true')

    _enviar_sincrono("mae@teste.com", "[2FA FALLBACK] Token para mae@teste.com: 428913")

    assert "428913" in log.text


def test_a_variavel_e_lida_a_cada_envio(envio_falhando, log, monkeypatch):
    """Lida no import, mudar a variável exigiria reiniciar o processo — e o teste acima passaria
    por acidente conforme a ordem de execução."""
    monkeypatch.setenv('MAILER_FALLBACK_LOG', 'true')
    assert mailer._fallback_em_log_autorizado() is True

    monkeypatch.setenv('MAILER_FALLBACK_LOG', 'false')
    assert mailer._fallback_em_log_autorizado() is False


# ---------------------------------------------------------------- sem regressão

def test_envio_bem_sucedido_nao_registra_fallback(log, monkeypatch):
    monkeypatch.setattr(mailer, 'enviar_email', lambda *a, **k: True)
    monkeypatch.setenv('MAILER_FALLBACK_LOG', 'true')

    _enviar_sincrono("mae@teste.com", "[2FA FALLBACK] Token: 428913")

    assert "428913" not in log.text


def test_envio_sem_fallback_declarado_nao_muda_de_comportamento(envio_falhando, log):
    _enviar_sincrono("mae@teste.com", None)

    assert "NÃO foi registrado" not in log.text
