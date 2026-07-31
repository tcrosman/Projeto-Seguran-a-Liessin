import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from pathlib import Path
from dotenv import load_dotenv

# Garante que o .env da pasta s2e-api/ seja carregado independente de onde o servidor é iniciado
_env_path = Path(__file__).resolve().parents[2] / '.env'
load_dotenv(dotenv_path=_env_path, override=True)

_PLACEHOLDERS = {'seu_email@gmail.com', 'sua_senha_de_app', '', 'seu_email', 'sua_senha'}

def enviar_email(destinatario, assunto, corpo_html):
    """Envia email via SMTP usando as configurações do arquivo .env"""
    if not destinatario:
        print("[MAILER] Destinatário não informado")
        return False

    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 465))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_password = os.getenv('SMTP_PASSWORD', '')

    if smtp_user in _PLACEHOLDERS or smtp_password in _PLACEHOLDERS:
        print("[MAILER] ERRO: Credenciais SMTP não configuradas no .env "
              "(ainda contém valores de exemplo). Configure SMTP_USER e SMTP_PASSWORD.")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From'] = smtp_user
        msg['To'] = destinatario
        msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

        with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)

        print(f"[MAILER] Email enviado com sucesso para {destinatario}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("[MAILER] ERRO de autenticação: usuário ou senha incorretos. "
              "Verifique SMTP_USER e SMTP_PASSWORD no .env. "
              "Para Gmail, use uma Senha de App, não a senha normal da conta.")
        return False
    except smtplib.SMTPException as e:
        print(f"[MAILER] ERRO SMTP: {e}")
        return False
    except Exception as e:
        print(f"[MAILER] ERRO inesperado ao enviar e-mail: {e}")
        return False
