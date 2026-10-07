# SecureEdu — preparação do teste do diretor

Atualizado em 06/10/2026 (horário de São Paulo). Branch: `codex/director-demo`.

## Feito nesta etapa

- Interface e manual alinhados: Básico aprova/rejeita solicitações, mas não vê ações de liberar ou editar saída; Avançado (`admin`) pode registrar, aprovar/rejeitar e liberar; Segurança da portaria (internamente `vigia`) só libera saídas já aprovadas e pendentes do dia.
- Preparado `s2e-api/prepare_director_staff.py`: cria apenas os três nomes abaixo, exige e-mails distintos, modo de demonstração remota, confirmação interativa e ausência de conflitos; não altera contas `homolog`. Gera senhas independentes e as mostra uma única vez. **Ainda não foi executado no VPS.**
- Testes de interface por perfil e de criação segura de contas fictícias adicionados. Foram executados 20 testes locais: 19 passaram, 1 exigia PostgreSQL descartável local e foi pulado.
- Relatório `SECURITY_REVIEW.md` atualizado com o estado da demonstração, limites das verificações e pendências de produção.
- Nenhuma mudança desta branch foi implantada no VPS. As contas `homolog` existentes não foram alteradas.

## Acessos exclusivos de Patrick

| Acesso | Nome proposto | Estado |
| --- | --- | --- |
| Básico | `teste_patrick_basico` | Não criado; falta e-mail próprio e acesso operacional ao VPS. |
| Avançado | `teste_patrick_avancado` | Não criado; falta e-mail próprio e acesso operacional ao VPS. |
| Segurança da portaria | `teste_patrick_seguranca` | Não criado; confirmar se Patrick precisa deste perfil e indicar e-mail próprio. |
| Responsável | `patrick.moreno@liessin.com.br` | Conta e aluno fictício preparados anteriormente; senha e fluxo 2FA não verificados nesta etapa. |

Senhas não foram geradas nem publicadas no repositório. As contas novas devem ter senhas aleatórias diferentes, entregues ao responsável pelo teste por canal privado e trocadas ou revogadas ao término da demonstração.

Patrick também deve executar o fluxo de **responsável/pai** com `patrick.moreno@liessin.com.br`: login, código 2FA enviado a esse e-mail, visualização do aluno fictício e envio de uma solicitação. Esse acesso não deve ser confundido com as três contas de equipe.

## Sequência necessária antes de entregar o teste

1. Confirmar os e-mails das contas de equipe e se Patrick precisa testar também a Segurança da portaria. Usar uma conta distinta por perfil; não reutilizar contas `homolog` nem seus segredos.
2. Restabelecer acesso operacional ao VPS, confirmar a versão em execução e fazer backup verificável. As tentativas de SSH de 06/10/2026 expiraram; o terminal web antigo da Hostinger também expirou e requer novo login do proprietário no hPanel. A página inicial da equipe e a dos pais responderam HTTP 200 por HTTPS e `/healthz` respondeu 204, mas isso não confirma o estado das contas nem o fluxo completo.
3. Implantar a branch de demonstração com janela monitorada, preservar `.env` e banco, verificar o login e `/healthz`. A branch não é um ambiente isolado; voltar o código a `a4613ac` não desfaz alterações no banco ou contas.
4. Criar somente as contas fictícias aprovadas com o preparador já implantado e validar seus perfis, e-mails, login e recuperação. Não registrar senhas em Git, relatórios ou logs. Confirmar a conta de responsável e o código 2FA no endereço de Patrick.
5. Ensaiar com contas fictícias: responsável solicita; Básico ou Avançado aprova; Segurança ou Avançado libera. Testar também os bloqueios: Básico não registra/libera, Segurança não registra/aprova e responsável não vê outros alunos. O dono do projeto pode repetir o fluxo com suas contas `homolog` sem compartilhar essas senhas.
6. Entregar ao diretor apenas os nomes de acesso e senhas temporárias por canal privado. Registrar aceite ou problemas do fluxo. Não é necessário fornecer consultas escolares oficiais para este teste.

## Revisão pelo chefe de TI/CI

**Adiada por solicitação do proprietário até confirmação posterior.** O código está atualmente em repositório público do GitHub. Quando a revisão for autorizada, comentários públicos não devem conter dados sensíveis; detalhes internos de infraestrutura, credenciais, dados reais e consultas escolares não devem ser adicionados ao GitHub público. A aprovação de segurança e a conexão PostgreSQL somente leitura continuam pendentes, mas não bloqueiam o teste fictício do diretor.
