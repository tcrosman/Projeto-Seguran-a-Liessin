# Decisão arquitetural — expansão para outras escolas

## Decisão

O SecureEdu terá um único código-base e, inicialmente, uma instalação isolada por escola.
Cada instituição terá domínio, banco, segredos, diretório escolar, remetente, logs e backups
próprios. Não serão criadas cópias independentes do repositório por cliente.

## Motivos

- reduz o risco de vazamento entre instituições;
- permite restaurar ou atualizar uma escola sem afetar as demais;
- mantém correções de segurança em um único produto;
- acomoda diferenças de fluxo por configuração e adaptadores;
- evita antecipar a complexidade de um banco SaaS multi-tenant.

## Fundação a manter no código

1. **Perfil institucional configurável:** identificador, nome, domínio, logotipo, cores,
   fuso, contatos, remetente e textos legais.
2. **Políticas de fluxo:** aprovação, horários, documentos, cancelamento, confirmação e
   notificações devem ser configuráveis ou implementadas como políticas selecionáveis.
3. **Diretório escolar por adaptador:** SQLite de homologação, PostgreSQL, SQL Server/TOTVS,
   Oracle ou API devem cumprir a mesma interface.
4. **Permissões:** novas capacidades devem preferir permissões explícitas a verificações
   rígidas e espalhadas de nomes de papel.
5. **Isolamento operacional:** uma `SECRET_KEY`, um banco, um conjunto de credenciais, um
   destino de backup e um domínio por instituição.
6. **Implantação repetível:** provisionamento, migração, healthcheck e rollback precisam ser
   automatizados e receber a configuração da instituição sem editar o código.

## O que fica fora desta homologação

Não será criado agora um banco compartilhado com `instituicao_id`. Essa mudança exigiria
particionamento de todas as tabelas, consultas, tokens, auditorias, anexos, índices e tarefas de
manutenção, além de testes específicos contra vazamento entre tenants. A decisão será reavaliada
após duas ou três implantações reais, quando as variações de fluxo estiverem documentadas.

## Regra para customizações

Uma necessidade de escola deve entrar, nesta ordem, como configuração, política de fluxo ou
adaptador. Alteração exclusiva no núcleo é o último recurso e deve continuar coberta por testes
que valham para todas as instituições.

