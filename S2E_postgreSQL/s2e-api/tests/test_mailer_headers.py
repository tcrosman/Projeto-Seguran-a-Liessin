from app.core import mailer


class _SMTPFake:
    ultima_mensagem = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def login(self, usuario, senha):
        assert usuario == "emailapikey"
        assert senha == "segredo-de-teste"

    def send_message(self, mensagem):
        self.__class__.ultima_mensagem = mensagem


def test_autenticacao_remetente_e_resposta_sao_independentes(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.zeptomail.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "emailapikey")
    monkeypatch.setenv("SMTP_PASSWORD", "segredo-de-teste")
    monkeypatch.setenv("SMTP_FROM", "notificacoes@portalsecureedu.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "SecureEdu")
    monkeypatch.setenv("SMTP_REPLY_TO", "suporte@portalsecureedu.com")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _SMTPFake)

    assert mailer.enviar_email("destino@exemplo.com", "Teste", "<p>Olá</p>") is True

    mensagem = _SMTPFake.ultima_mensagem
    assert mensagem["From"] == "SecureEdu <notificacoes@portalsecureedu.com>"
    assert mensagem["Reply-To"] == "suporte@portalsecureedu.com"
    assert mensagem["To"] == "destino@exemplo.com"


def test_configuracao_antiga_continua_usando_usuario_como_remetente(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "legado@exemplo.com")
    monkeypatch.setenv("SMTP_PASSWORD", "segredo-de-teste")
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_REPLY_TO", raising=False)
    monkeypatch.setenv("SMTP_FROM_NAME", "")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _SMTPFake)
    monkeypatch.setattr(_SMTPFake, "login", lambda self, usuario, senha: None)

    assert mailer.enviar_email("destino@exemplo.com", "Teste", "<p>Olá</p>") is True
    assert _SMTPFake.ultima_mensagem["From"] == "legado@exemplo.com"
    assert _SMTPFake.ultima_mensagem["Reply-To"] is None
