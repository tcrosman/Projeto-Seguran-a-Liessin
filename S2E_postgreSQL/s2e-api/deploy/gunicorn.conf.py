"""Configuração do gunicorn em produção (VPS).

Substitui os parâmetros soltos do Procfile, que continua existindo para o Render.
O systemd sobe o processo com:  gunicorn -c deploy/gunicorn.conf.py run:app
"""
import os

# 127.0.0.1, e não 0.0.0.0 como no Procfile do Render.
#
# Aqui o nginx é o único que precisa alcançar o gunicorn, e a app roda com
# TRUST_PROXY=true — ou seja, ProxyFix reescreve request.remote_addr com o que vier
# em X-Forwarded-For. Se o gunicorn escutasse em todas as interfaces, qualquer um que
# alcançasse a porta 8002 diretamente poderia mandar o cabeçalho que quisesse e
# escolher o IP que o rate limit de app/core/rate_limit.py vai contabilizar — ou seja,
# um IP novo a cada tentativa de senha. O ufw também bloqueia a 8002, mas as duas
# proteções são baratas e independentes.
bind = f"127.0.0.1:{os.getenv('PORT', '8002')}"

# Dois workers = dois processos, cada um com seu pool de conexões (app/core/database.py
# monta o pool depois do fork, de propósito). O total contra o Postgres é
# DB_POOL_MAX x workers = 5 x 2 = 10 conexões.
workers = int(os.getenv('GUNICORN_WORKERS', '2'))

# Threads por worker: as rotas fazem I/O de banco e o mailer já joga o SMTP num
# ThreadPoolExecutor separado, então algumas threads melhoram a concorrência sem o
# custo de mais um processo. O pool do banco é ThreadedConnectionPool e o semáforo de
# database.py faz a request excedente esperar em vez de estourar.
threads = int(os.getenv('GUNICORN_THREADS', '4'))

# Explícito de propósito. O gunicorn troca 'sync' por 'gthread' sozinho quando
# threads > 1, mas o `--print-config` continua exibindo 'sync' — o que faz parecer que
# as threads foram ignoradas. Declarar aqui deixa a configuração igual ao que roda.
worker_class = 'gthread'

# Mesmo valor do Procfile. Precisa ser maior que DB_POOL_TIMEOUT_SEG (10s) para a
# request desistir da conexão e renderizar errors/db_unavailable.html antes de o
# gunicorn matar o worker.
timeout = 120
graceful_timeout = 30

# Fecha conexões keep-alive ociosas com o nginx um pouco depois do keepalive_timeout
# dele (65s), para o fechamento partir sempre do nginx.
keepalive = 75

# Recicla o worker periodicamente: qualquer vazamento lento de memória some no
# reinício, sem downtime (o gunicorn substitui um worker por vez). O jitter evita que
# os dois reciclem na mesma request.
max_requests = 1000
max_requests_jitter = 100

# Log no stdout/stderr para o journald recolher (journalctl -u secureedu).
# O log de auditoria da aplicação é outro e continua indo para logs/system.log e para
# a tabela `auditoria` — ver app/core/audit_logger.py.
accesslog = '-'
errorlog = '-'
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')

# %({x-forwarded-for}i)s no lugar de %(h)s: com o nginx à frente, %(h)s seria sempre
# 127.0.0.1 e o log de acesso não serviria para nada.
access_log_format = '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'

# Nome que aparece em `ps` e no journald, em vez de "gunicorn: master".
proc_name = 'secureedu'
