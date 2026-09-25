# Plano de homologação — diretor e TI

Este plano separa validação técnica, validação do fluxo escolar e preparação para uso real.
A homologação não autoriza o uso de dados reais nem substitui a revisão da TI da escola.

Estado em 25/09/2026: `[x]` concluído na branch de homologação, `[ ]` pendente e
`[H]` depende de validação humana ou do ambiente do VPS.

## 1. Gate técnico interno

- [x] integrar as correções de segurança na branch `codex/homologacao-diretor-ti`;
- [x] executar a suíte automatizada completa: 435 testes aprovados;
- [x] cobrir por testes autenticação, CSRF, rate limit, permissões e isolamento de sessões;
- [x] cobrir por testes a ausência de 2FA e tokens de redefinição nos logs por padrão;
- [x] validar no código uploads, auditoria, migrações concorrentes e rollback operacional;
- [H] repetir no VPS a restauração e conferir o backup imediatamente anterior ao deploy;
- [H] verificar HTTPS, cookies, serviços, portas e logs na versão candidata implantada;
- [ ] marcar a versão candidata com commit/tag imutável após o gate interno.

Critério de saída: testes verdes, backup recente e nenhuma vulnerabilidade crítica conhecida.

## 2. Homologação funcional interna

Perfis usados: administrador, básico, vigia e responsável de homologação.

- [x] normalizar identificadores com espaços externos sem modificar senhas;
- [x] priorizar o portal dos responsáveis e manter portal próprio para colaboradores;
- [H] testar login, logout e recuperação de senha com as contas de homologação;
- [H] testar 2FA do responsável somente com endereço controlado;
- [H] criar, aprovar, cancelar e concluir solicitações;
- [H] verificar restrições de administrador, básico, vigia e responsável;
- [H] validar anexos PDF/JPG e rejeição de arquivo inválido;
- [H] conferir textos, erros, navegação e celular;
- [ ] registrar evidências e defeitos sem senhas ou dados de menores.

Critério de saída: fluxos essenciais concluídos pelos quatro perfis.

## 3. Entrega ao chefe de TI

- [x] fornecer acesso ao repositório e à branch de homologação;
- [x] entregar arquitetura, runbook e variáveis de ambiente sem segredos;
- [x] preparar modelo de ameaças, inventário direto e roteiro em `REVISAO_SEGURANCA_TI.md`;
- demonstrar separação entre aplicação, banco escolar e banco operacional;
- fornecer roteiro de revisão de autenticação, autorização, uploads, logs e backups;
- receber e classificar achados por severidade antes de implantar correções.

Critério de saída: nenhum achado crítico ou alto pendente sem mitigação formal.

## 4. Entrega ao diretor

- [x] criar credenciais temporárias para administrador, básico, vigia e responsável;
- fornecer roteiro curto orientado ao processo escolar;
- [x] separar o portal dos responsáveis do portal dos colaboradores;
- coletar diferenças do fluxo real da escola e decisões de interface;
- não enviar mensagens a pessoas não autorizadas durante a demonstração.

Critério de saída: fluxo aprovado ou lista objetiva de ajustes funcionais.

## Fundação para outras escolas incluída antes da homologação

- [x] decisão por um código-base e uma instalação isolada por instituição;
- [x] nome, identificador e contato institucional configuráveis sem editar código;
- [x] contexto público de templates testado para não expor segredos;
- [x] arquitetura e regra de customização documentadas;
- [ ] tornar logotipo, cores e textos legais configuráveis;
- [ ] extrair variações de aprovação/horários/documentos para políticas;
- [ ] trocar verificações rígidas de papel por capacidades quando novos papéis surgirem;
- [ ] implementar adaptador para o banco/engine real definido pela escola.

Os quatro últimos itens não bloqueiam a homologação desta primeira escola. Devem preceder a
segunda implantação ou entrar junto da primeira necessidade real correspondente.

## 5. Antes do uso real

- integrar o diretório real da escola com acesso somente leitura;
- substituir contas e dados provisórios;
- definir remetente institucional e responsabilidade pelos custos;
- configurar monitoramento e backup externo;
- trocar todas as credenciais temporárias;
- repetir testes, restauração e revisão de segurança;
- registrar aceite operacional, responsável técnico e procedimento de incidente.
