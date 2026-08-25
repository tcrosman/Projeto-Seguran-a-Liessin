# Banco provisório da escola

Banco SQLite local que faz o papel do **sistema de cadastro da escola** enquanto a instituição não
define o banco real. O S2E não cadastra mais alunos: ele só lê daqui (nome, RA, turma, série) e
confirma quem é responsável.

O `schema.sql` é, na prática, a **especificação do mínimo que a escola precisa expor** para o
sistema funcionar.

## Criar o banco

```bash
cd S2E_postgreSQL
python escola_provisoria/seed.py           # cria escola_provisoria/escola.db
python escola_provisoria/seed.py --force   # recria do zero
```

O arquivo `.db` não vai para o Git (`*.db` está no `.gitignore`).

## Apontar o sistema para ele

No `s2e-api/.env`:

```
SCHOOL_SQL_MOCK=false
SCHOOL_SQL_ENGINE=sqlite
SCHOOL_SQL_DATABASE=../escola_provisoria/escola.db
```

Com `SCHOOL_SQL_MOCK=true` (padrão) o sistema ignora este banco e usa os 3 alunos fixos em Python.

## O que tem dentro

Três tabelas: `alunos` (ra, nome, turma, serie, foto_url, ativo), `responsaveis` (email, nome,
ativo) e `vinculos` (ra, email) — a relação é muitos-para-muitos: um responsável pode ter vários
filhos e um aluno pode ter vários responsáveis.

`ativo = 0` significa aluno desligado ou responsável sem acesso — o sistema precisa conseguir
**negar**, não só permitir.

### Dados de exemplo (13 alunos, 10 responsáveis)

Os três primeiros (`2024001` Ana Beatriz Souza, `2024002` Carlos Eduardo Lima, `2024003` Theo
Crosman) são os mesmos do mock antigo, para os testes manuais já conhecidos continuarem valendo.

Casos-limite propositais, para testar o que costuma quebrar:

| Caso | Onde |
|---|---|
| Aluno com **dois** responsáveis | `2024001` (pai@ e mae@) |
| Aluno **sem nenhum** responsável | `2024012` Isabela Martins — liberar a saída dela não pode quebrar por não ter quem notificar |
| Responsável com **dois filhos** | `claudia.almeida@teste.com` — o painel dos pais deve listar os dois |
| Responsável **sem vínculo ativo** | `sem.vinculo@teste.com` — deve ser barrado no cadastro e no login |
| Responsável **inativo** | `bloqueado@teste.com` — idem |
| Responsável só de aluno desligado | `pai.desligado@teste.com` — perdeu o vínculo, perde o acesso |
| Aluno **desligado** | `2024013` Gabriel Torres — não pode aparecer na busca da portaria |
| Nome em CAIXA ALTA / com acento | `JÚLIA MENDES`, `Beatriz Camões`, `Mariana Gonçalves` — buscar "julia" tem que achar "JÚLIA" |

As séries usam exatamente os nomes de `Config.SERIES` (`s2e-api/app/config.py`); as turmas são
A, B, C e D. Todas as fotos são `NULL` — as telas mostram as iniciais do nome quando não há foto.

## Editar os dados

Para mudar os dados de exemplo, edite as listas `ALUNOS`, `RESPONSAVEIS` e `VINCULOS` no
`seed.py` e rode com `--force`. Para mexer em registros pontuais, qualquer cliente SQLite
(DB Browser for SQLite, DBeaver, ou o módulo `sqlite3` do Python) abre o arquivo direto.

## Quando a escola definir o banco real

Em `s2e-api/app/services/school_sql_directory.py`: acrescente o engine em `_conectar()` e ajuste
nomes de tabela/coluna se os da escola forem outros. As queries já usam um placeholder neutro
(`?` no SQLite, `%s` no psycopg2), então não precisam ser reescritas. Atenção à busca sem acento:
no SQLite é uma função registrada na conexão; no Postgres, `unaccent(lower(...))` exige a extensão
`unaccent` habilitada.
