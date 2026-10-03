# Integração escolar por consultas PostgreSQL — contrato para o TI

O portal **não usa API da TOTVS**. A integração oficial usa duas consultas SQL de
leitura fornecidas e aprovadas pelo TI, executadas em uma conexão PostgreSQL
separada da base operacional do SecureEdu. Nenhuma consulta real ou credencial
da escola foi recebida ou instalada até esta data.

## O que o TI precisa entregar

1. Usuário PostgreSQL com permissão **somente SELECT** nas visões necessárias,
   sem permissão de escrita, DDL ou acesso a outras bases. Restringir origem de
   rede e fornecer CA/TLS para `verify-full`.
2. `responsavel.sql`: recebe `%(email)s`; retorna **zero ou uma** linha com
   `external_parent_id` (identificador estável e único), `email` e `active`
   (BOOLEAN verdadeiro apenas para responsável autorizado). E-mail duplicado ou
   divergente é rejeitado; um responsável sem filhos pode retornar linha válida.
3. `filhos.sql`: recebe `%(external_parent_id)s`; retorna a **lista completa**
   de filhos ativos e autorizados, com `external_student_id` (identificador
   estável e único), `student_name`, `class_name` e `grade`. Lista vazia remove
   os vínculos desse responsável no portal. Não incluir outros dados pessoais.
4. Informar se o mesmo aluno aparece para múltiplos responsáveis; isso é
   permitido, desde que `external_student_id` seja igual em todos os resultados.
5. Validar casos de teste: responsável ativo/inativo, sem filhos, com múltiplos
   filhos, vínculo revogado, e-mail alterado, falha de banco e IDs duplicados.

Os modelos sem tabelas reais estão em `s2e-api/school_sql/*.sql.example`.
Guardar as consultas aprovadas como `responsavel.sql` e `filhos.sql` no
`SCHOOL_SQL_QUERY_DIR` privado do servidor; **não** colocar credenciais em Git.
Configurar `SCHOOL_DIRECTORY_MODE=sql`, `SCHOOL_SQL_DATABASE_URL` e
`SCHOOL_SQL_SSLMODE=verify-full`. O aplicativo força transação de leitura,
parâmetros vinculados e tempo-limite. A permissão de SELECT no banco é a
barreira principal: o código não substitui a revisão das consultas pelo TI.

## Fluxo já preparado

Após senha local e aprovação da conta pela escola, o portal consulta o
responsável e os filhos, atualiza os vínculos locais em transação e envia o
segundo fator por e-mail. A autorização escolar é novamente verificada em
cada acesso autenticado; falha de consulta bloqueia o acesso, sem reutilizar
vínculos antigos. Dados de aluno já sincronizado são atualizados por ID
externo, preservando foto, horários, histórico e solicitações locais. Vínculos
que não vierem na lista completa são removidos, mas aluno e histórico não são
apagados. Antes de enviar um aviso de liberação a um responsável já cadastrado,
o vínculo também é reconfirmado; falha da consulta não impede a liberação física,
mas impede o envio daquele aviso. Responsáveis que nunca se cadastraram no
portal não recebem o aviso por esse fluxo. A rotina de demonstração
(`SCHOOL_DIRECTORY_MODE=demo`) só funciona
em `localhost` com `APP_ENV=development/test` e e-mails fictícios autorizados.

## Antes de ativar

- Fazer backup e ensaio das migrações em cópia/homologação.
- Mapear manualmente `alunos.school_external_id` nos alunos locais já existentes.
  Não conciliar por nome: isso pode duplicar alunos ou associar o filho errado.
- Validar as consultas com dados fictícios e depois com amostras autorizadas
  pelo TI, conferindo especialmente se `filhos.sql` é exaustiva.
- Definir tratamento para indisponibilidade do PostgreSQL escolar e política de
  atualização de cadastro e e-mail. O login local e o 2FA continuam sendo do
  portal; a consulta escolar não entrega senha.
- Só liberar produção após revisão das pendências em `SECURITY_REVIEW.md` e
  aprovação operacional da infraestrutura pelo TI.
