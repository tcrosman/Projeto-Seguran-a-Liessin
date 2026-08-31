"""Configuração central de logs.

Substitui os `print()` espalhados pelo código e o `open('logs/system.log', 'a')` do
audit_logger. Três problemas com o que havia antes:

  * arquivo sem rotação — `logs/system.log` crescia sem limite;
  * escrita concorrente — com dois workers do gunicorn abrindo o mesmo arquivo em modo append,
    linhas longas podem se intercalar;
  * `print()` não tem nível nem timestamp, e some do histórico dependendo de como o processo é
    iniciado.

Dois loggers, com finalidades diferentes:

  `s2e`       — operação da aplicação (falhas de e-mail, banco da escola indisponível,
                manutenção). Vai para stdout, que é onde o Render coleta.
  `s2e.audit` — trilha de auditoria (login, saída registrada/liberada). Vai para
                `logs/system.log` com rotação, além de stdout.

Sobre LGPD: a trilha de auditoria guarda quem fez o quê, e por isso registra usuário e IP. É
dado pessoal com prazo de vida — `LOG_BACKUPS` define quantos arquivos rotacionados ficam
guardados; ajuste conforme a política de retenção acordada com a instituição.
"""
import logging
import logging.handlers
import os
import sys

_PASTA = os.getenv('LOG_FOLDER', 'logs')
_ARQUIVO_AUDITORIA = os.path.join(_PASTA, 'system.log')
_MAX_BYTES = int(os.getenv('LOG_MAX_BYTES', 5 * 1024 * 1024))
_BACKUPS = int(os.getenv('LOG_BACKUPS', 5))
_NIVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

_configurado = False


def configurar():
    """Idempotente: pode ser chamada por cada create_app() sem duplicar handlers."""
    global _configurado
    if _configurado:
        return
    _configurado = True

    formato = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S')

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formato)

    app_log = logging.getLogger('s2e')
    app_log.setLevel(_NIVEL)
    app_log.addHandler(console)
    # Não repassa para o root: evita a mensagem sair duas vezes se algo mais configurar o root.
    app_log.propagate = False

    auditoria = logging.getLogger('s2e.audit')
    auditoria.setLevel(logging.INFO)
    auditoria.propagate = False  # já herdaria o console de 's2e'; queremos controlar aqui
    auditoria.addHandler(console)
    try:
        os.makedirs(_PASTA, exist_ok=True)
        arquivo = logging.handlers.RotatingFileHandler(
            _ARQUIVO_AUDITORIA, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding='utf-8'
        )
        arquivo.setFormatter(formato)
        auditoria.addHandler(arquivo)
    except OSError as e:
        # Disco somente-leitura (alguns planos de hospedagem): a auditoria segue no stdout.
        app_log.warning("Sem arquivo de auditoria (%s); registrando apenas no stdout.", e)


def obter(nome='s2e'):
    """Devolve um logger já configurado."""
    configurar()
    return logging.getLogger(nome)
