# Deploy do SecureEdu em VPS

Runbook completo: da máquina recém-contratada até o sistema no ar, e o que fazer no
dia a dia depois disso.

**Arquitetura**

```
Internet ──HTTPS(443)──> nginx (TLS Let's Encrypt, rate limit grosso)
                            │  proxy_pass + X-Forwarded-For / X-Forwarded-Proto
                            ▼
                    gunicorn 127.0.0.1:8002  (systemd, 2 workers x 4 threads)
                            │
                            ├──> PostgreSQL local  (dados do S2E)
                            └──> banco da escola   (somente leitura)
```

**Caminhos**

| O que | Onde |
|---|---|
| Repositório | `/opt/secureedu` |
| Aplicação (cwd do serviço) | `/opt/secureedu/S2E_postgreSQL/s2e-api` |
| Virtualenv | `.../s2e-api/.venv` |
| Configuração | `.../s2e-api/.env` (modo 600) |
| Log da aplicação | `.../s2e-api/logs/system.log` |
| Log do serviço | `journalctl -u secureedu` |
| Backups | `/var/backups/secureedu` |

---

## Parte 1 — Instalação do zero

### 1.1 Sistema base

```bash
ssh root@<IP-DO-VPS>
apt update && apt full-upgrade -y
reboot
```

Reconecte e crie o usuário da aplicação. A aplicação **nunca** roda como root.

```bash
adduser --disabled-password --gecos "" secureedu
adduser --gecos "" deploy && usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh && cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh
```

### 1.2 SSH

> **Abra uma segunda sessão SSH como `deploy` e confirme que ela funciona ANTES de
> recarregar o sshd.** Se a chave não estiver certa, este passo tranca você para fora
> do servidor, e a única saída é o console de recuperação do painel da Hostinger.

Em `/etc/ssh/sshd_config`:

```
PermitRootLogin no
PasswordAuthentication no
```

```bash
sshd -t && systemctl reload ssh
```

### 1.3 Firewall e proteção contra força bruta

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
```

A porta **8002 não é aberta**: o gunicorn escuta só em `127.0.0.1`. Isso importa mais
do que parece — a aplicação roda com `TRUST_PROXY=true`, então ela confia no
`X-Forwarded-For`. Se o gunicorn fosse alcançável direto, qualquer pessoa poderia
escolher qual IP o rate limit iria contabilizar e teria tentativas de senha infinitas.

```bash
apt install -y fail2ban unattended-upgrades
systemctl enable --now fail2ban
dpkg-reconfigure -plow unattended-upgrades
```

### 1.4 Pacotes

```bash
apt install -y python3 python3-venv python3-pip git nginx postgresql \
               certbot python3-certbot-nginx curl
```

Não é preciso instalar `libmagic`: o `python-magic` não está no `requirements.txt` e a
validação de upload usa magic bytes escritos à mão (`app/core/validators.py:205`).

### 1.5 Código

```bash
mkdir -p /opt/secureedu && chown secureedu:secureedu /opt/secureedu
sudo -u secureedu git clone <URL-DO-REPOSITORIO> /opt/secureedu
cd /opt/secureedu/S2E_postgreSQL/s2e-api
sudo -u secureedu python3 -m venv .venv
sudo -u secureedu .venv/bin/pip install --upgrade pip
sudo -u secureedu .venv/bin/pip install -r requirements.txt
```

> Use o `requirements.txt` **do `s2e-api/`**. O `S2E_postgreSQL/requirements.txt` é
> legado, tem BOM UTF-8 e pina Flask 2.3.3, que conflita com o `flask>=3.0` que o
> código exige.

### 1.6 Banco de dados

```bash
sudo bash deploy/postgres-setup.sh
```

Cria o banco e o usuário, garante TLS e prova com uma conexão real que
`sslmode=require` funciona. **O TLS não é opcional**: `app/core/database.py:53` fixa
`sslmode='require'` no código e ignora a query string da `DATABASE_URL`. Sem TLS no
servidor, a aplicação não conecta.

### 1.7 Configuração

```bash
sudo -u secureedu cp deploy/env.producao.example .env
sudo -u secureedu chmod 600 .env
sudo -u secureedu python3 -c "import os; print(os.urandom(32).hex())"   # SECRET_KEY
sudo -u secureedu nano .env
```

Preencher: `SECRET_KEY`, `DATABASE_URL`, `BASE_URL`, os quatro `SMTP_*`,
`INITIAL_ADMIN_PASSWORD` e `INITIAL_ADMIN_EMAIL`.

> **Comentário no fim da linha quebra o `.env`.** O arquivo é lido pelo python-dotenv
> *e* pelo `EnvironmentFile=` do systemd, e o systemd não remove comentário depois do
> valor: `FORCE_HTTPS=true  # ligar` vira o valor literal `true  # ligar`. Comentários
> só em linha própria.

> Se a senha do Postgres tiver `@ : / ? # %`, ela precisa vir **percent-encoded** na
> `DATABASE_URL` — `database.py` separa os campos com `urlparse()`.

### 1.8 Base provisória de alunos (Etapa A)

```bash
cd /opt/secureedu/S2E_postgreSQL
sudo -u secureedu ../s2e-api/.venv/bin/python escola_provisoria/seed.py
```

### 1.9 Schema e primeiro administrador

```bash
cd /opt/secureedu/S2E_postgreSQL/s2e-api
sudo -u secureedu bash deploy/primeiro-setup.sh
```

Este é o passo que mais derruba um primeiro deploy se for pulado. As tabelas base
(`alunos`, `saidas`, `usuarios`) só nascem dentro de `reset_database()`, mas a
`migrate_database()` do boot já as assume existentes. Num banco vazio o serviço não
sobe, com `relation "alunos" does not exist`. O script confere que o banco está mesmo
vazio antes de agir, então não há risco de rodá-lo por engano num banco com dados.

### 1.10 Serviço

```bash
install -d -o secureedu -g secureedu -m 755 /opt/secureedu/S2E_postgreSQL/s2e-api/logs
install -d -o secureedu -g secureedu -m 755 /opt/secureedu/S2E_postgreSQL/s2e-api/storage
install -d -o secureedu -g secureedu -m 700 /var/backups/secureedu

cp deploy/secureedu.service /etc/systemd/system/
cp deploy/sudoers-secureedu /etc/sudoers.d/secureedu && chmod 440 /etc/sudoers.d/secureedu
visudo -c
systemctl daemon-reload
systemctl enable --now secureedu
systemctl status secureedu

# O X-Forwarded-Proto é necessário: batendo direto no gunicorn, por http, e com
# FORCE_HTTPS=true, o Talisman responde 302 para https. O cabeçalho simula o que o
# nginx vai enviar. Esperado aqui: HTTP/1.1 200 OK.
curl -I -H 'X-Forwarded-Proto: https' http://127.0.0.1:8002/
```

### 1.11 nginx e HTTPS

```bash
mkdir -p /etc/nginx/snippets
cp deploy/nginx-secureedu-proxy.conf /etc/nginx/snippets/secureedu-proxy.conf
sed 's/SEU_DOMINIO/portal.suaescola.com.br/g' deploy/nginx-secureedu.conf \
    > /etc/nginx/sites-available/secureedu
ln -sf /etc/nginx/sites-available/secureedu /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

O bloco `443` ainda não tem certificado — o certbot escreve isso:

```bash
certbot --nginx -d portal.suaescola.com.br
certbot renew --dry-run
```

O DNS já precisa estar apontando para o IP do VPS, senão a validação falha.

---

## Parte 2 — Backup

```bash
cp deploy/secureedu-backup.service deploy/secureedu-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now secureedu-backup.timer
systemctl start secureedu-backup.service      # roda uma vez agora
journalctl -u secureedu-backup -n 30
```

**Por que isso é obrigatório:** os anexos das saídas (atestados, autorizações) são
gravados na coluna `BYTEA` da tabela `saidas_documentos`, **dentro do Postgres**, não
em arquivo. A trilha de auditoria da LGPD também vive numa tabela. Perder o banco é
perder documentos e auditoria junto — o disco do VPS não guarda cópia de nada disso.

### Testar a restauração

Backup nunca restaurado não é backup. Faça isto uma vez, agora, e repita a cada
semestre:

```bash
sudo -u postgres createdb teste_restore
gzip -dc /var/backups/secureedu/secureedu_<data>.sql.gz | sudo -u postgres psql teste_restore
sudo -u postgres psql teste_restore -c "SELECT count(*) FROM usuarios; SELECT count(*) FROM saidas;"
sudo -u postgres dropdb teste_restore
```

### Cópia fora do servidor

Backup no mesmo disco não protege contra perder o servidor. Configure ao menos uma
cópia semanal para outra máquina — pelo painel da Hostinger (snapshot) ou um `rsync`
para um computador da escola.

---

## Parte 3 — Operação

### Comandos do dia a dia

| Para | Comando |
|---|---|
| Atualizar para a última versão | `sudo -u secureedu bash deploy/deploy.sh` |
| Ver logs ao vivo | `journalctl -u secureedu -f` |
| Ver erros da aplicação | `tail -f logs/system.log` |
| Reiniciar | `sudo systemctl restart secureedu` |
| Recarregar sem derrubar | `sudo systemctl reload secureedu` |
| Estado | `systemctl status secureedu` |
| Backup agora | `sudo systemctl start secureedu-backup.service` |
| Próximo backup | `systemctl list-timers secureedu-backup` |

### Trocar uma variável do `.env`

```bash
sudo -u secureedu nano /opt/secureedu/S2E_postgreSQL/s2e-api/.env
sudo systemctl restart secureedu
```

`reload` (SIGHUP) não relê o `EnvironmentFile` — para mudança de configuração é
`restart` mesmo.

### Reverter uma versão ruim

O `deploy.sh` já reverte sozinho se o healthcheck falhar. Manualmente:

```bash
cd /opt/secureedu
sudo -u secureedu git log --oneline -10
sudo -u secureedu git reset --hard <commit-bom>
sudo -u secureedu s2e-api/.venv/bin/pip install -r S2E_postgreSQL/s2e-api/requirements.txt
sudo systemctl restart secureedu
```

Isso reverte o **código**. As migrações deste projeto são só aditivas, então a versão
anterior convive com colunas a mais. Para desfazer dados, é restaurar o backup.

---

## Parte 4 — Etapa B: trocar para o banco real da escola

Hoje o sistema roda com a base provisória em SQLite. Quando a TI da escola liberar o
acesso ao banco real:

1. **Testar a rota até lá**, de dentro do VPS:
   `psql "postgresql://<user>@<host>:<porta>/<base>" -c "select 1"`.
   Se travar, é firewall do lado deles — o IP do VPS precisa estar liberado.

2. **Conferir o schema** contra `escola_provisoria/schema.sql`, que é a especificação
   do que a escola precisa expor. Nomes de tabela ou coluna diferentes exigem ajuste
   nas queries de `app/services/school_sql_directory.py`.

3. **Se o engine não for PostgreSQL nem SQLite** — o TOTVS costuma ficar em SQL Server
   ou Oracle — é preciso acrescentar o driver (`pymssql`, `oracledb`) ao
   `requirements.txt` e um ramo em `_conectar()`
   (`app/services/school_sql_directory.py:65`). As queries em si não mudam: o código já
   troca o placeholder de parâmetro conforme o engine.

4. **Se for PostgreSQL**, habilitar a extensão `unaccent` no banco da escola:
   `CREATE EXTENSION IF NOT EXISTS unaccent;`
   Sem ela, `_expr_norm()` gera `unaccent(lower(coluna))` e **toda busca por nome de
   aluno falha**.

5. Trocar as variáveis `SCHOOL_SQL_*` no `.env` (o bloco "ETAPA B" já está lá,
   comentado) e `sudo systemctl restart secureedu`.

6. Em `deploy/secureedu.service`, remover a linha
   `ReadWritePaths=.../escola_provisoria` — não é mais necessária.

7. Refazer a validação com um aluno real: busca na portaria, ficha, e um cadastro de
   responsável no portal dos pais.

---

## Parte 5 — Validação depois de subir

Não basta o site responder 200. Confira cada item:

1. `https://<dominio>` abre com cadeado válido; `http://` redireciona para `https://`.
2. Login da equipe funciona; sair pelo botão encerra a sessão.
3. **Portal dos pais:** autocadastro → o código 2FA chega **no e-mail real** → login
   completa. É o que prova que o SMTP funciona de ponta a ponta.
4. "Esqueci minha senha" → o link no e-mail aponta para `https://<dominio>/...` e
   **não** para `localhost:8002`. Se apontar para localhost, a `BASE_URL` está errada.
5. Registrar uma saída com anexo **PDF** e outra com **JPG**; reabrir os documentos.
   Testar também um `.txt` renomeado para `.pdf` — tem que ser recusado (a validação é
   por magic bytes, não por extensão).
6. Errar a senha 6 vezes seguidas → bloqueio. Depois:
   `sudo -u postgres psql secureedu -c "SELECT chave FROM rate_limit_falhas ORDER BY id DESC LIMIT 5;"`
   O IP registrado tem que ser o **seu**, não `127.0.0.1`. Se aparecer `127.0.0.1`, o
   `X-Forwarded-For` não está chegando e o rate limit por IP está trancando a escola
   inteira de uma vez.
7. `grep -iE '[0-9]{6}|resetar_senha' logs/system.log` → **não pode** aparecer código
   2FA nem link de redefinição. Se aparecer, `MAILER_FALLBACK_LOG` está em `true`.
8. Entrar com um usuário de papel `vigia` e tentar `/admin/responsaveis` → tem que
   negar.
9. `sudo systemctl restart secureedu` → as sessões caem (esperado), mas o rate limit e
   os códigos 2FA sobrevivem, porque ficam no banco e não em memória.
10. `sudo reboot` → tudo volta sozinho.

---

## Diagnóstico

**O serviço não sobe.**
`journalctl -u secureedu -n 50`. Causas mais comuns, todas com mensagem explícita
porque a aplicação falha rápido de propósito:

| Mensagem | Causa |
|---|---|
| `SECRET_KEY inválida` | chave com menos de 32 caracteres ou valor de exemplo |
| `relation "alunos" does not exist` | banco vazio — falta rodar `deploy/primeiro-setup.sh` |
| `SCHOOL_SQL_MOCK não definida` | variável ausente no `.env` |
| `DATABASE_URL não configurada` | idem, ou o `EnvironmentFile` não foi lido |
| `SSL connection is required` | TLS desligado no Postgres — rode `deploy/postgres-setup.sh` |

**502 no navegador.** O nginx está de pé e o gunicorn não:
`systemctl status secureedu` e `curl -I http://127.0.0.1:8002/`.

**Loop de redirecionamento.** Falta o `X-Forwarded-Proto`: confira se o
`/etc/nginx/snippets/secureedu-proxy.conf` está instalado e incluído nos três
`location`.

**Rate limit trancando todo mundo de uma vez.** O `X-Forwarded-For` não está chegando —
mesma causa, mesma verificação. Para destravar agora:
`sudo -u postgres psql secureedu -c "DELETE FROM rate_limit_falhas;"`

**E-mail não chega.** `grep -i smtp logs/system.log`. Verifique se `SMTP_PASSWORD` é a
senha de app de 16 caracteres (não a senha da conta) e se a verificação em duas etapas
está ligada no Google. **Não** ligue `MAILER_FALLBACK_LOG` para contornar em produção.

**Disco cheio.** Quase sempre são os backups: `du -sh /var/backups/secureedu`. Ajuste
`BACKUP_RETENCAO_DIAS` no `secureedu-backup.service`.
