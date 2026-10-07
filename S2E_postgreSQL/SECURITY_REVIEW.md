# SecureEdu — revisão técnica para TI/CI

Atualizado em 07/10/2026 (horário de São Paulo). Escopo: código da branch `codex/director-demo`, testes locais e verificações externas limitadas do portal. Esta é uma avaliação para o chefe de TI/CI, **não** uma certificação de segurança nem aprovação para dados escolares reais.

### Estado verificado para esta entrega

- A branch de demonstração `codex/director-demo` está publicada no GitHub. Ela **não foi implantada** no VPS. O último commit de produção confirmado anteriormente foi `a4613ac` (03/10/2026); a versão em execução precisa ser reconfirmada no VPS antes de qualquer deploy ou assinatura de revisão.
- Em 06/10/2026, `https://portalsecureedu.com/` respondeu HTTP 200 por HTTPS, com cabeçalhos HSTS, `X-Frame-Options: DENY` e cookie de sessão `Secure`, `HttpOnly` e `SameSite=Lax`. Isso comprova apenas a resposta dessa rota, não o funcionamento de login, e-mail, banco, backup ou restauração.
- A tentativa de SSH nesta atualização expirou sem conexão. Nenhuma alteração de contas ou configuração foi feita no VPS nesta revisão.
- O repositório `tcrosman/Projeto-Seguran-a-Liessin` responde publicamente sem autenticação. Não inserir senhas, `.env`, dados reais de alunos, logs sensíveis ou relatórios internos de infraestrutura no GitHub público. A busca atual por padrões comuns de chaves e URLs com senha em arquivos rastreados não encontrou resultado; **o histórico completo ainda requer varredura própria**.
- O modo de demonstração remota é permitido apenas com HTTPS, configuração explícita, e-mail na lista autorizada e contas/alunos marcados como fictícios. Não é integração com a base escolar. A conta de responsável de Patrick e o aluno fictício foram preparados anteriormente; o login completo com senha e 2FA ainda precisa de ensaio real.

Atualização após esclarecimento da escola: **não haverá API da TOTVS**. A fonte
oficial será um PostgreSQL somente leitura, com duas consultas aprovadas pelo TI.
O adaptador, as migrações e a sincronização dos filhos foram preparados;
detalhes e contrato em [SCHOOL_SQL_HANDOFF.md](SCHOOL_SQL_HANDOFF.md).
Nenhuma consulta real, credencial ou conexão com o banco escolar foi recebida.

## Resultado e organização

- Foram preservadas as URLs e telas ativas. As regras de autenticação, autorização, relógio da escola e validação ficaram em módulos pequenos em `app/core` e `app/api/middleware.py`.
- `setup_db.py` passou a aplicar somente migrações aditivas. O antigo `reset_database()` e seus comandos `DROP TABLE` foram removidos. `create_admin.py` exige senha fornecida pelo operador e não a imprime.
- `teste_banco.py` agora só verifica disponibilidade; deixou de listar usuários e erros de conexão no terminal.
- Dois templates sem referência (`help/manual.html` e `pais/vincular_filhos.html`), um decorador de log e um validador antigo sem uso foram removidos após busca de chamadas. A dependência `python-magic`, usada apenas por esse validador, saiu da lista raiz. A API REST não registrada foi mantida para referência, mas sua ativação agora falha explicitamente até revisão de autenticação e CSRF.
- O código de repositórios/serviços antigos foi mantido por compatibilidade potencial; ele não é chamado pelas rotas atuais e não deve ser ativado sem revisão.
- A rota `GET /healthz` retorna apenas 204 quando o PostgreSQL responde e 503 quando não responde; não expõe configuração nem dados.

## Achados e correções

| Prioridade | Achado | Situação |
| --- | --- | --- |
| Crítico | Script de inicialização apagava tabelas; outro script criava administrador com senha fixa conhecida. | Corrigido no código. A senha antiga pode constar do histórico Git: verificar se a conta foi usada e trocar sua senha antes de qualquer implantação. |
| Crítico | A chave de exemplo do `.env.example` era longa o suficiente para passar pela checagem mínima, embora fosse previsível. | Placeholders comuns agora são recusados na inicialização. Conferir que a chave instalada é aleatória e distinta da de outros ambientes. |
| Alto | O carregamento de `.env` no módulo de e-mail podia sobrescrever variáveis do ambiente de implantação após a chave de sessão já ter sido configurada. | Removida a precedência do arquivo sobre variáveis já definidas pelo processo. |
| Alto | Limites de login por memória eram independentes entre workers; tentativas de 2FA dependiam do cookie. | Contadores atômicos no PostgreSQL. Quando o proxy aparece como loopback, o limite é por conta para evitar bloqueio global da escola. Configurar IP real somente por proxy confiável. |
| Alto | Código 2FA e tokens novos de recuperação ficavam em texto no banco; uso simultâneo podia reutilizar token. | Novos valores persistidos como HMAC; consumo e redefinição protegidos por atualização/bloqueio transacional. Códigos antigos de 2FA deixam de funcionar e exigem novo login; links de recuperação antigos continuam válidos até expirar. |
| Alto | Sessões continuavam válidas após redefinição de senha, bloqueio ou remoção de conta. | Versão de autenticação e estado/perfil atuais verificados a cada requisição protegida. Sessões antigas serão encerradas após a migração. |
| Alto | A portaria podia consultar mais dados e uma URL direta podia escapar da lista de bloqueios por perfil. | Permissão da portaria por lista fechada; lista de saídas e uploads limitada aos pendentes do dia; liberação exige POST e só aceita data atual. |
| Alto | Saída e solicitação eram associadas apenas por aluno/data; uma edição podia excluir saída de outra origem. | Coluna opcional `saidas.solicitacao_id`, índice único e alterações por ID para novas aprovações. Registros antigos sem vínculo foram preservados; edição de solicitação aprovada antiga sem saída identificável é bloqueada até conciliação. |
| Alto | Aprovação, edição e liberação simultâneas podiam gerar duplicação ou reabrir saída já liberada. | Bloqueios de linha e de aluno nos fluxos ativos. Ainda falta uma restrição única de pendência no banco após saneamento de dados existentes. |
| Alto | Erro de importação de planilha podia renderizar conteúdo informado pelo usuário como HTML. | Removido `safe`; mensagens escapadas e erros internos ocultos. |
| Alto | Uploads confiavam em assinaturas curtas e extensões; fotos em lote não eram verificadas. | Decodificação de imagens por Pillow, limite por arquivo, extensão compatível, PDF com cabeçalho e marcador final, nomes aleatórios, acesso autenticado. PDF ainda requer antivírus/inspeção de conteúdo se a política da escola exigir. |
| Alto | A antiga validação por API não sincronizava filhos e podia aceitar valores não booleanos. | A integração REST foi removida. Consultas PostgreSQL de leitura usam parâmetros e contrato estrito; o portal sincroniza filhos por ID externo, revoga vínculos ausentes e bloqueia acesso quando a fonte falha. A demonstração remota exige controles explícitos para contas fictícias. |
| Médio | O perfil básico via o botão de liberação, embora o servidor negasse a ação. O manual básico também dizia que ele podia liberar saídas. | Corrigidos na branch `codex/director-demo`: botão de liberar só para admin e segurança da portaria, botão de editar só para admin e manual alinhado à matriz de permissões. Ainda não implantado. |
| Médio | Uma consulta de histórico podia buscar campos de saúde sem precisar deles; logs aceitavam quebras de linha. | Consulta minimizada; linhas de auditoria normalizadas; logs de SMTP não exibem destinatários, tokens ou detalhes de exceção. |
| Médio | Função de retenção não chamada montava intervalo SQL por interpolação. | Intervalo parametrizado e validado. Não foi ativada exclusão automática de histórico. |
| Médio | Os manuais prometiam remoção automática após 30 dias, mas não havia agendamento. | Texto corrigido. `maintenance.py` permite agendar a passagem para `nao_realizada` e limpar contadores antigos; não remove histórico escolar. |

## Pendências antes de produção

1. **Conciliação de dados antigos (alto).** Inspecionar solicitações aprovadas sem `solicitacao_id`; associar somente quando a correspondência for inequívoca, com backup e revisão humana. Não inferir vínculo apenas de aluno/data. Conferir duplicações existentes antes de criar restrição única para saída pendente por aluno/data.
2. **Consultas escolares (alto).** O TI ainda precisa fornecer as duas consultas PostgreSQL, usuário de SELECT mínimo, acesso de rede e CA/TLS. Conferir com dados autorizados se a lista de filhos é exaustiva e se os IDs externos são estáveis. Mapear alunos locais existentes por ID antes de ativar para evitar duplicatas; não vincular por nome. O modo `off` bloqueia o portal de pais. O modo `demo` remoto exige HTTPS, permissão explícita, lista de e-mails e marcação individual de contas/alunos fictícios; não substitui acesso de pais reais.
3. **Notificações (alto).** O aviso de liberação é enviado depois do commit. Falha de SMTP não desfaz a liberação e não há fila de reenvio. Projetar caixa de saída transacional e monitoramento antes de prometer entrega garantida.
4. **Transporte do banco (alto).** A conexão usa TLS com `sslmode=require`, mas a identidade do servidor não é verificada por esse modo. Testar `DATABASE_SSLMODE=verify-full` e certificado CA fornecido pelo provedor em homologação antes da troca. Nunca usar `disable` fora de teste local.
5. **Runtime e dependências (alto).** `runtime.txt` fixa Python 3.9.18, versão sem suporte desde 31/10/2025. Existem duas listas de dependências com critérios diferentes e a lista de `s2e-api` usa limites mínimos. Migrar para Python suportado, unificar e fixar versões após teste de compatibilidade e varredura de dependências.
6. **Implantação (alto).** Não há configuração de Nginx ou serviço da VPS no repositório; `Procfile` e `render.yaml` não comprovam a implantação atual. Confirmar Gunicorn acessível apenas via proxy, HTTPS, redirecionamento, HSTS, cookies, tamanho de upload, permissões de `storage/`, backups e logs. Ligar o monitor de saúde a `/healthz`. URLs de redefinição contêm token: remover ou mascarar essas rotas nos logs de acesso do proxy.
7. **Retenção e manutenção (médio).** A passagem para `nao_realizada` ainda ocorre em leituras, para preservar o comportamento atual. Agendar `maintenance.py` após meia-noite no horário de São Paulo e, só após observar sua execução, retirar essas atualizações das rotas GET. Definir com a escola quais tabelas e arquivos devem ser eliminados após 30 dias, aprovar backup/restauração e implementar exclusão supervisionada.
8. **Cabeçalhos e sessão (médio).** A política CSP ainda permite scripts/estilos inline porque os templates dependem disso. Planejar remoção gradual. Revisar configuração de proxy confiável antes de usar cabeçalhos `X-Forwarded-*` para rate limiting ou HTTPS.
9. **Dados e concorrência (médio).** Várias tabelas antigas usam IDs sem chaves estrangeiras e datas como texto. Planejar restrições após saneamento, com migração testada em cópia dos dados. O teste de concorrência entre processos deve ser repetido em homologação com carga real.
10. **Código dormente (médio).** `app/services/auth.py` ainda possui fluxo antigo de token em texto, e `app/services/notifications.py` permite webhook arbitrário. Nenhum dos dois é chamado pelas rotas atuais. Remover ou revisar antes de qualquer reutilização; não registrá-los como novos endpoints sem testes.

## Verificações realizadas

- Sintaxe de todos os arquivos Python e `git diff --check`: sem erros.
- Inventário estático: 31 formulários HTML; nenhum POST sem campo CSRF. A proteção global também rejeitou POST sem token no teste.
- Em 06/10/2026, foram executados 21 testes locais: 20 passaram e 1 foi pulado por exigir PostgreSQL descartável local. Incluem login/logout de três perfis de funcionários, acesso direto a URLs, CSRF, IDOR, 2FA, upload inválido, horário, restrição da demonstração, validação das consultas escolares, a verificação dos botões por perfil e a preparação controlada de contas fictícias.
- Duas execuções consecutivas das migrações passaram em PostgreSQL 16 descartável.
- Quatro testes com PostgreSQL descartável passaram: os fluxos anteriores, a nova consulta escolar com sincronização/revogação e o preparo controlado de uma família fictícia para demonstração. A rota `/healthz` retornou 204 com banco disponível e 503 em falha simulada.
- `.env` não está rastreado no estado atual. A busca por padrões comuns de chaves/URLs com senha em arquivos rastreados não encontrou correspondências; isso não substitui uma varredura de segredos em todo o histórico remoto, especialmente porque o repositório está público.
- Nenhum teste usou Supabase. O contêiner de teste foi removido após a execução.

## Orientações para revisão do chefe de TI/CI

1. Revisar no GitHub a branch `codex/director-demo` e compará-la com `a4613ac`. O código é público; comentários de revisão em uma solicitação de mudança podem ser públicos também. Não colocar dados escolares ou detalhes de infraestrutura nesses comentários.
2. Fazer backup verificável e ensaio de restauração; aplicar as migrações somente em cópia/homologação antes da produção. Conferir especialmente `auth_version`, `auth_attempts` e `saidas.solicitacao_id`.
3. Conciliar registros legados e identificar se a antiga senha fixa de administrador foi usada. Se foi, alterar credenciais e revisar acessos anteriores.
4. Executar os testes isolados com `PYTHON_DOTENV_DISABLED=1` e sem URL de produção. O teste PostgreSQL exige `APP_ENV=test` e `SECUREEDU_TEST_DATABASE_URL` apontando especificamente para `secureedu_test` em loopback.
5. Antes de uso escolar real, validar as consultas PostgreSQL e os IDs externos, o envio/reenvio de e-mail, certificados TLS, proxy/Nginx e a política de retenção com a equipe responsável. **As consultas oficiais não são requisito para o teste fictício do diretor.**
6. Para a revisão desta demonstração, verificar no VPS a versão implantada, as permissões de cada conta fictícia, backup recente e restauração possível. Executar o fluxo completo com contas fictícias sem usar alunos reais.

Fontes para as recomendações de infraestrutura: [ciclo de suporte do Python](https://devguide.python.org/versions/), [Flask com Gunicorn e proxy](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/), [validação TLS em Requests](https://requests.readthedocs.io/en/stable/user/advanced/).
