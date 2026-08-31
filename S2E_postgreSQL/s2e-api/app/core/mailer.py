import smtplib
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from pathlib import Path
from dotenv import load_dotenv

# Garante que o .env da pasta s2e-api/ seja carregado independente de onde o servidor é iniciado
_env_path = Path(__file__).resolve().parents[2] / '.env'
load_dotenv(dotenv_path=_env_path, override=True)

_PLACEHOLDERS = {'seu_email@gmail.com', 'sua_senha_de_app', '', 'seu_email', 'sua_senha', 'cole-aqui-sua-senha-de-app'}

_SMTP_TIMEOUT_SEG = int(os.getenv('SMTP_TIMEOUT_SEG', 20))
from app.core.logging_config import obter

_log = obter()

def enviar_email(destinatario, assunto, corpo_html):
    """Envia email via SMTP usando as configurações do arquivo .env"""
    if not destinatario:
        _log.warning("[MAILER] Destinatário não informado")
        return False

    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 465))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_password = os.getenv('SMTP_PASSWORD', '')

    if smtp_user in _PLACEHOLDERS or smtp_password in _PLACEHOLDERS:
        _log.warning("[MAILER] ERRO: Credenciais SMTP não configuradas no .env "
                     "(ainda contém valores de exemplo). Configure SMTP_USER e SMTP_PASSWORD.")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From'] = smtp_user
        msg['To'] = destinatario
        msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

        # Sem timeout explícito o socket herda None (espera infinita): um servidor SMTP que
        # aceita a conexão e não responde prenderia a thread para sempre — e o ThreadPoolExecutor
        # dá join nas suas threads no encerramento, travando o shutdown do worker.
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=_SMTP_TIMEOUT_SEG) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)

        _log.info(f"[MAILER] Email enviado com sucesso para {destinatario}")
        return True

    except smtplib.SMTPAuthenticationError:
        _log.warning("[MAILER] ERRO de autenticação: usuário ou senha incorretos. "
                     "Verifique SMTP_USER e SMTP_PASSWORD no .env. "
                     "Para Gmail, use uma Senha de App, não a senha normal da conta.")
        return False
    except smtplib.SMTPException as e:
        _log.warning(f"[MAILER] ERRO SMTP: {e}")
        return False
    except Exception as e:
        _log.warning(f"[MAILER] ERRO inesperado ao enviar e-mail: {e}")
        return False


# Uma conexão SMTP leva segundos (handshake TLS + login + envio). Feita dentro da request, ela
# bloqueia a resposta do login 2FA, da liberação de saída (que envia para cada responsável) e do
# autocadastro (que avisa cada admin). Poucas threads bastam: o volume é baixo e o gargalo é a
# espera de rede, não CPU.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix='mailer')


def enviar_email_async(destinatario, assunto, corpo_html, fallback_log=None):
    """Enfileira o envio e retorna na hora, sem bloquear a request.

    `fallback_log` é impresso se o envio falhar — usado onde o e-mail carrega algo que o usuário
    precisa (código de 2FA, link de redefinição) e o log é a única forma de recuperá-lo em dev.
    Não devolve status: quem chama não pode esperar pelo resultado.
    """
    def _tarefa():
        try:
            if not enviar_email(destinatario, assunto, corpo_html) and fallback_log:
                _log.warning(fallback_log)
        except Exception as e:
            _log.warning(f"[MAILER] ERRO no envio assíncrono para {destinatario}: {e}")

    _EXECUTOR.submit(_tarefa)
