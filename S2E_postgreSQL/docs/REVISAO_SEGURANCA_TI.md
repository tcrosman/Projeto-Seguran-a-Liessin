# Pacote de revisão de segurança — SecureEdu

Documento para a TI revisar a versão de homologação sem receber segredos, dados reais ou acesso
administrativo desnecessário. Branch: `codex/homologacao-diretor-ti`. A versão candidata final
deve ser identificada pelo hash entregue junto às credenciais temporárias.

## Escopo e arquitetura

- Flask/Gunicorn em `127.0.0.1:8002`, atrás de nginx e HTTPS;
- PostgreSQL local para contas, solicitações, anexos, auditoria, tokens e rate limit;
- diretório escolar separado e somente leitura (SQLite provisório na homologação);
- quatro perfis de teste: administrador, básico, vigia e responsável;
- SMTP temporário para 2FA e recuperação, sem credencial no repositório.

Os limites de confiança são: navegador ↔ nginx; nginx ↔ aplicação; aplicação ↔ banco operacional;
aplicação ↔ diretório escolar; aplicação ↔ SMTP; operador ↔ VPS/GitHub/backups.

## Dependências diretas

| Componente | Faixa declarada | Finalidade |
|---|---:|---|
| Flask | `>=3.0,<4` | aplicação web e sessões |
| python-dotenv | `>=1.0,<2` | configuração local |
| psycopg2-binary | `>=2.9,<3` | PostgreSQL |
| gunicorn | `>=21.0,<24` | servidor da aplicação |
| Flask-WTF | `>=1.2,<2` | proteção CSRF |
| Flask-Talisman | `>=1.1,<2` | cookies e cabeçalhos do navegador |

As versões instaladas no candidato devem ser anexadas pela TI com `pip freeze`, sem variáveis de
ambiente. Os tetos evitam atualização automática para uma versão principal nova, mas não
substituem verificação periódica de vulnerabilidades.

## Ameaças prioritárias e controles

| Ameaça | Controle a revisar |
|---|---|
| acesso de um papel a função administrativa | middleware de autorização e testes do papel vigia |
| acesso de responsável a aluno sem vínculo | vínculo revalidado no diretório escolar e escopo por responsável |
| força bruta de senha ou 2FA | limites separados por IP e conta, persistidos no banco |
| enumeração de responsáveis | resposta pública uniforme e limitação do autocadastro |
| CSRF | token em operações com mudança de estado e logout somente por POST |
| sessão forjada ou roubada | chave forte, cookie HttpOnly/Secure/SameSite e expiração |
| IP forjado | `TRUST_PROXY` apenas atrás do nginx e Gunicorn somente em localhost |
| upload malicioso | limite, extensão, assinatura/magic bytes e autorização para leitura |
| injeção SQL | parâmetros nas consultas; revisar identificadores e ordenações compostos |
| código 2FA ou reset em log | fallback desligado em produção e testes de regressão |
| HTML em e-mail | escape de dados vindos de formulário |
| corrida em aprovação/saída/migração | unicidade, tratamento de colisão e advisory lock |
| perda ou vazamento de dados | permissões mínimas, backup, restauração e cópia externa |
| mistura entre futuras escolas | instalação, banco, segredos, domínio, logs e backup isolados |

## Roteiro de revisão da TI

1. Confirmar que o hash recebido pertence à branch e que a árvore de trabalho está limpa.
2. Executar a suíte completa em ambiente sem acesso ao banco real.
3. Revisar `app/api/middleware.py`, login/2FA/reset, consultas e escopo de documentos.
4. Testar uploads com arquivos válidos, arquivo renomeado e arquivo acima do limite.
5. Confirmar que `.env`, dumps, bancos, logs e chaves não estão no Git.
6. Inspecionar CSP, HSTS, cookies, CSRF e resposta a métodos HTTP inesperados.
7. Testar autorização negativa digitando URLs de outro papel, não só escondendo botões.
8. Verificar que a internet alcança somente SSH, HTTP e HTTPS.
9. Conferir dono/modo do `.env`, diretórios graváveis e isolamento do serviço systemd.
10. Inspecionar logs após 2FA e reset; não pode haver código, token, senha ou corpo sensível.
11. Restaurar o backup mais recente em banco descartável e comparar tabelas essenciais.
12. Classificar achados como crítico, alto, médio ou baixo, com evidência e reprodução.

## Critérios de bloqueio

Não entregar para uso real se houver: acesso cruzado entre responsáveis/alunos; elevação de papel;
bypass de 2FA; segredo no Git ou log; upload sem autorização; banco ou Gunicorn exposto; backup
sem restauração; HTTPS/cookie seguro incorreto; ou achado crítico/alto sem mitigação aceita.

## Evidências permitidas

Capturas podem mostrar telas e mensagens, mas devem ocultar e-mails, nomes, RA, documentos, tokens,
cookies e credenciais. Logs devem ser recortados e saneados. Nunca anexar `.env`, dump ou chave SSH.
