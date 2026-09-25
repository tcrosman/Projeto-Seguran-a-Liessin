# Plano de homologação — diretor e TI

Este plano separa validação técnica, validação do fluxo escolar e preparação para uso real.
A homologação não autoriza o uso de dados reais nem substitui a revisão da TI da escola.

## 1. Gate técnico interno

- integrar as correções de segurança na branch de homologação;
- executar a suíte automatizada completa sem falhas;
- verificar autenticação, CSRF, rate limit, permissões e isolamento de sessões;
- confirmar que códigos 2FA e tokens de redefinição não aparecem nos logs;
- validar uploads, auditoria, backup, restauração e rollback;
- verificar HTTPS, cookies seguros, serviços e portas expostas;
- registrar a versão exata que será entregue aos revisores.

Critério de saída: testes verdes, backup recente e nenhuma vulnerabilidade crítica conhecida.

## 2. Homologação funcional interna

Perfis usados: administrador, básico, vigia e responsável de homologação.

- testar login, logout e recuperação de senha;
- testar 2FA do responsável com endereço controlado;
- criar, aprovar, cancelar e concluir solicitações;
- verificar restrições de cada papel;
- validar anexos PDF/JPG e rejeição de arquivo inválido;
- conferir textos, mensagens de erro, navegação e uso em celular;
- registrar evidências e defeitos sem incluir senhas ou dados de menores.

Critério de saída: fluxos essenciais concluídos pelos quatro perfis.

## 3. Entrega ao chefe de TI

- fornecer acesso ao repositório e à branch de homologação;
- entregar arquitetura, modelo de ameaças, runbook e inventário de dependências;
- documentar variáveis de ambiente sem valores secretos;
- demonstrar separação entre aplicação, banco escolar e banco operacional;
- fornecer roteiro de revisão de autenticação, autorização, uploads, logs e backups;
- receber e classificar achados por severidade antes de implantar correções.

Critério de saída: nenhum achado crítico ou alto pendente sem mitigação formal.

## 4. Entrega ao diretor

- criar credenciais exclusivas e temporárias para cada papel;
- fornecer roteiro curto orientado ao processo escolar;
- demonstrar o portal dos responsáveis e o portal dos colaboradores;
- coletar diferenças do fluxo real da escola e decisões de interface;
- não enviar mensagens a pessoas não autorizadas durante a demonstração.

Critério de saída: fluxo aprovado ou lista objetiva de ajustes funcionais.

## 5. Antes do uso real

- integrar o diretório real da escola com acesso somente leitura;
- substituir contas e dados provisórios;
- definir remetente institucional e responsabilidade pelos custos;
- configurar monitoramento e backup externo;
- trocar todas as credenciais temporárias;
- repetir testes, restauração e revisão de segurança;
- registrar aceite operacional, responsável técnico e procedimento de incidente.

