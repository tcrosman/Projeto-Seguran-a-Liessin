# Secure-Edu

Controle de saída antecipada de alunos. Flask + PostgreSQL (Supabase), hospedado no Render.

O sistema decide se uma criança pode sair da escola e com quem. Trate as decisões de
implantação abaixo com esse peso — várias delas não têm como ser verificadas por teste
automatizado, e nenhuma aparece na interface quando está errada.

## Checklist de implantação (uma escola nova)

1. **Plano do Render: pago, nunca `free`.** O free hiberna após 15 min sem tráfego e leva ~50s
   para acordar. A primeira requisição do dia é a portaria abrindo a tela às 14h55, com os pais
   já no portão. O `preDeployCommand` (migrações como passo de release) também só existe em
   plano pago. Ver `s2e-api/render.yaml`.
2. **`SCHOOL_SQL_MOCK=false`** e as demais `SCHOOL_SQL_*` preenchidas no painel. Com `true` o
   sistema serve três alunos de teste como se fossem o cadastro da escola. O app se recusa a
   subir se a variável estiver ausente, e conferre o banco da escola no boot.
3. **`TRUST_PROXY`**: `true` só quando há proxy conhecido na frente (é o caso do Render). Fora
   disso, `false` — senão o cliente escolhe o próprio IP pelo cabeçalho `X-Forwarded-For`, o que
   derruba todo limite por IP e contamina o IP gravado na auditoria.
4. **`SECRET_KEY`** aleatória, mínimo 32 caracteres. O app não sobe sem ela.
5. **`MAILER_FALLBACK_LOG=false`** em produção. Com `true`, código de 2FA e link de redefinição
   de senha vão para o log da hospedagem quando o SMTP falha.
6. **SMTP configurado.** Sem ele não há 2FA no portal dos responsáveis nem aviso de saída
   liberada. Conta comum do Gmail tem limite diário (~500 mensagens).
7. **Fuso**: `TIMEZONE=America/Sao_Paulo`. Servidor e Postgres rodam em UTC; portaria e pais
   usam o relógio local.

## Rodar em desenvolvimento

    cd S2E_postgreSQL/s2e-api
    cp .env.example .env      # preencha DATABASE_URL, SECRET_KEY e SCHOOL_SQL_MOCK
    pip install -r requirements.txt
    python ../run.py

## Testes

    cd S2E_postgreSQL/s2e-api
    python -m pytest tests -q

Os testes usam dublês (`tests/apoio.py`) e não encostam em banco real.
