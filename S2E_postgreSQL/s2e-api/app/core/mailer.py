import smtplib
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from pathlib import Path
from dotenv import load_dotenv

# Garante que o .env da pasta s2e-api/ seja carregado independente de onde o servidor é iniciado.
#
# Sem `override`: com ele, este import — que acontece num ponto qualquer da construção do app —
# reescrevia TODAS as variáveis do processo a partir do arquivo, fazendo o .env ganhar das
# variáveis definidas na hospedagem. O caminho explícito acima já resolve o problema que o
# override tentava resolver (achar o .env certo), e a precedência correta é a inversa: quem
# define no ambiente manda, o arquivo só preenche o que falta.
_env_path = Path(__file__).resolve().parents[2] / '.env'
load_dotenv(dotenv_path=_env_path)

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


def _fallback_em_log_autorizado():
    """Lido a cada chamada, e não no import, para o valor poder ser conferido em teste."""
    return os.getenv('MAILER_FALLBACK_LOG', 'false').strip().lower() == 'true'


def enviar_email_async(destinatario, assunto, corpo_html, fallback_log=None):
    """Enfileira o envio e retorna na hora, sem bloquear a request.

    `fallback_log` carrega o conteúdo que o usuário deveria ter recebido — código de 2FA, link de
    redefinição de senha — para recuperá-lo em desenvolvimento quando o SMTP não funciona.

    Ele só é impresso se MAILER_FALLBACK_LOG=true. Antes era impresso sempre, sem distinguir
    desenvolvimento de produção: bastava o SMTP falhar (senha de app do Gmail vencida, cota
    diária estourada) para o log passar a conter um segundo fator válido por 10 minutos, ao lado
    do e-mail a que ele pertence, e links de redefinição de senha prontos para uso — visíveis a
    qualquer pessoa com acesso ao painel de logs da hospedagem.

    Com a variável desligada, a falha continua registrada; o que não vai para o log é o segredo.
    Não devolve status: quem chama não pode esperar pelo resultado.
    """
    def _tarefa():
        try:
            if not enviar_email(destinatario, assunto, corpo_html) and fallback_log:
                if _fallback_em_log_autorizado():
                    _log.warning(fallback_log)
                else:
                    _log.warning(
                        "[MAILER] Envio para %s falhou e o conteúdo (código ou link) NÃO foi "
                        "registrado no log, por ser material sensível. Em desenvolvimento, use "
                        "MAILER_FALLBACK_LOG=true para recuperá-lo.", destinatario)
        except Exception as e:
            _log.warning(f"[MAILER] ERRO no envio assíncrono para {destinatario}: {e}")

    _EXECUTOR.submit(_tarefa)
