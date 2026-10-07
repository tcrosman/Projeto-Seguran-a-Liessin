# SecureEdu — preparação do teste do diretor

Atualizado em 06/10/2026 (horário de São Paulo). Branch: `codex/director-demo`.

## Feito nesta etapa

- Interface e manual alinhados: Básico aprova/rejeita solicitações, mas não vê ações de liberar ou editar saída; Avançado (`admin`) pode registrar, aprovar/rejeitar e liberar; Segurança da portaria (internamente `vigia`) só libera saídas já aprovadas e pendentes do dia.
- Preparado `s2e-api/prepare_director_staff.py`: cria apenas os três nomes abaixo, sem e-mail de equipe; exige que a conta de pai de Patrick e todos os filhos vinculados sejam fictícios, modo de demonstração remota, confirmação interativa e ausência de conflitos; não altera contas `homolog` nem a senha do pai. Gera senhas independentes e as mostra uma única vez. **Ainda não foi executado no VPS.**
- Testes de interface por perfil e de criação segura de contas fictícias adicionados. Foram executados 21 testes locais: 20 passaram, 1 exigia PostgreSQL descartável local e foi pulado.
- Relatório `SECURITY_REVIEW.md` atualizado com o estado da demonstração, limites das verificações e pendências de produção.
- Nenhuma mudança desta branch foi implantada no VPS. As contas `homolog` existentes não foram alteradas.

## Acessos exclusivos de Patrick

| Acesso | Nome proposto | Estado |
| --- | --- | --- |
| Básico | `teste_patrick_basico` | Não criado; temporário, sem e-mail de recuperação. |
| Avançado | `teste_patrick_avancado` | Não criado; temporário, sem e-mail de recuperação. |
| Segurança da portaria | `teste_patrick_seguranca` | Não criado; temporário, sem e-mail de recuperação. |
| Responsável | `patrick.moreno@liessin.com.br` | Conta e aluno fictício preparados anteriormente; senha e fluxo 2FA não verificados nesta etapa. |

Senhas não foram geradas nem publicadas no repositório. As contas novas terão senhas aleatórias diferentes e não terão recuperação automática por e-mail. Se uma senha se perder, o administrador deverá recriar ou redefinir a conta; ao terminar o teste, essas contas deverão ser revogadas. Entregar as senhas ao responsável pelo teste por canal privado.

Patrick também deve executar o fluxo de **responsável/pai** com `patrick.moreno@liessin.com.br`: login, código 2FA enviado a esse e-mail, visualização do aluno fictício e envio de uma solicitação. Esse acesso não deve ser confundido com as três contas de equipe.

## Sequência necessária antes de entregar o teste

1. Usar uma conta distinta por perfil para Patrick, mas reservar o único e-mail `patrick.moreno@liessin.com.br` para a conta de pai e o 2FA. Não reutilizar contas `homolog` nem seus segredos.
2. Confirmar serviço e fazer backup verificável no VPS. As tentativas de SSH de 06/10/2026 expiraram, mas o terminal web root foi aberto em outra janela do Chrome e confirmou que `DEPLOYED_COMMIT` ainda é `a4613ac6df266940c845d49b30bb76931ff1b2e3`. A página inicial da equipe e a dos pais responderam HTTP 200 por HTTPS e `/healthz` respondeu 204; isso não confirma o estado das contas nem o fluxo completo.
3. Implantar a branch de demonstração com janela monitorada, preservar `.env` e banco, verificar o login e `/healthz`. A branch não é um ambiente isolado; voltar o código a `a4613ac` não desfaz alterações no banco ou contas.
4. Criar somente as contas fictícias com o preparador já implantado e validar seus perfis e login. Essas três contas não terão recuperação por e-mail; não registrar senhas em Git, relatórios ou logs. Confirmar a conta de pai, sua senha conhecida por Patrick ou redefinição via portal, e o código 2FA no endereço dele.
5. Ensaiar com contas fictícias: responsável solicita; Básico ou Avançado aprova; Segurança ou Avançado libera. Testar também os bloqueios: Básico não registra/libera, Segurança não registra/aprova e responsável não vê outros alunos. O dono do projeto pode repetir o fluxo com suas contas `homolog` sem compartilhar essas senhas.
6. Entregar ao diretor apenas os nomes de acesso e senhas temporárias por canal privado. Registrar aceite ou problemas do fluxo. Não é necessário fornecer consultas escolares oficiais para este teste.

## Revisão pelo chefe de TI/CI

**Adiada por solicitação do proprietário até confirmação posterior.** O código está atualmente em repositório público do GitHub. Quando a revisão for autorizada, comentários públicos não devem conter dados sensíveis; detalhes internos de infraestrutura, credenciais, dados reais e consultas escolares não devem ser adicionados ao GitHub público. A aprovação de segurança e a conexão PostgreSQL somente leitura continuam pendentes, mas não bloqueiam o teste fictício do diretor.
