from flask import render_template, request, redirect, session, flash, send_from_directory, jsonify, make_response
from markupsafe import escape
from app.api.middleware import login_required, admin_required
from app.core.database import get_db
from app.core.audit_logger import log_operacao
from app.core.cache import TTLCache
from app.core import rate_limit
from app.core.passwords import senha_confere
from app.core.validators import (escapar_like, horario_valido, data_valida,
                                 normalizar_serie, ordem_da_serie)
from app.core.tempo import hoje, agora_utc
from app.services.school_sql_directory import (get_school_sql_directory,
                                               aluno_vinculado_ao_responsavel)
from app.config import Config
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
import os
from app.core.logging_config import obter
from psycopg2.errors import UniqueViolation

_log = obter()


_cache_diretorio = TTLCache(ttl_seconds=300)

# Tamanho máximo do anexo de uma saída. Separado do MAX_CONTENT_LENGTH da request porque o
# documento é gravado no banco (ver saidas_documentos em app/core/database.py).
DOCUMENTO_MAX_BYTES = int(os.getenv('DOCUMENTO_MAX_BYTES', 5 * 1024 * 1024))

# Quantos alunos o nome digitado no histórico pode resolver. Casado com o LIMIT 200 da consulta
# de saídas logo abaixo: não adianta resolver mais RAs do que cabe na tela de resultados.
RAS_NO_HISTORICO = 200

# Quantos alunos a tela de alunos mostra de uma vez. A busca é o caminho para chegar a quem não
# aparece na primeira leva — o diretório da escola não tem paginação.
ALUNOS_POR_PAGINA = 200

# Piso e teto do autocomplete de /registrar_saida.
#
# Sem o piso, `?q=a` devolvia uma fatia alfabética do cadastro: `?q=a`, `?q=e`, `?q=202400`... e
# em poucas dezenas de requisições sai RA, nome, turma e série de toda a escola. Três caracteres
# ainda respondem a qualquer nome que alguém digite de verdade, e não servem para varredura.
#
# O teto é baixo porque este resultado é um autocomplete: ninguém rola além dos primeiros nomes,
# e cada linha a mais é cadastro de aluno saindo do banco da escola sem necessidade. Quem
# precisa de lista longa usa /alunos, que é outra tela e outro controle de acesso.
MIN_CARACTERES_BUSCA = 3
RESULTADOS_AUTOCOMPLETE = 10


def _buscar_alunos_com_cache(ras):
    """Resolve ra -> dados do aluno via school_sql_directory, com cache curto (5 min) e fallback
    para o último dado conhecido se a consulta externa estiver indisponível no momento."""
    if not ras:
        return {}
    faltando = [ra for ra in set(ras) if _cache_diretorio.get(ra) is None]
    if faltando:
        try:
            frescos = get_school_sql_directory().get_students_by_ras(faltando)
            for ra, info in frescos.items():
                _cache_diretorio.set(ra, info)
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Consulta indisponível, usando cache: {e}")
    resultado = {}
    for ra in set(ras):
        info = _cache_diretorio.get(ra) or _cache_diretorio.get_stale(ra)
        if info:
            resultado[ra] = info
    return resultado


def _nome_e_emails_para_saida(ra):
    """Resolve nome do aluno e e-mails dos responsáveis via school_sql_directory para notificar
    após liberar uma saída, sem propagar falha — usada por /concluir_saida, onde a liberação
    já foi persistida no banco e não pode ser desfeita por uma falha na consulta externa."""
    try:
        info = get_school_sql_directory().get_student(ra)
        emails = get_school_sql_directory().get_guardian_emails_for_ra(ra)
    except Exception as e:
        _log.warning(f"[SCHOOL_SQL] Erro ao concluir saída: {e}")
        info, emails = None, []
    nome_aluno = info['nome'] if info else f"RA {ra}"
    return nome_aluno, emails


# Por que a aprovação pode ser recusada. Mesma lista de motivos do portal dos pais
# (app/api/pais.py), com o texto endereçado a quem está do lado da escola.
_RECUSA_APROVACAO = {
    'conta_inativa': ("Solicitação não aprovada: a conta do responsável não está ativa "
                      "(pendente ou bloqueada). Regularize o cadastro antes de aprovar."),
    'sem_vinculo': ("Solicitação NÃO aprovada: quem pediu não consta mais como responsável por "
                    "este aluno no cadastro da escola. Confirme com a secretaria antes de "
                    "liberar a saída."),
    'indisponivel': ("Solicitação não aprovada: o cadastro da escola está indisponível e o "
                     "vínculo do responsável com o aluno não pôde ser confirmado. Tente de novo "
                     "em alguns minutos."),
}


def _revalidar_vinculo_da_solicitacao(sol_row):
    """Reconfere o direito de quem pediu, no momento da aprovação. Devolve None quando está tudo
    certo, ou a chave do motivo da recusa em `_RECUSA_APROVACAO`.

    Só o fluxo por RA é reconferível aqui: as linhas legadas (sem RA) não têm identidade que o
    banco da escola reconheça, e para elas o portão que resta é o status da conta, verificado
    logo acima.
    """
    if sol_row['responsavel_status'] != 'aprovado':
        return 'conta_inativa'
    if not sol_row['ra']:
        return None
    estado = aluno_vinculado_ao_responsavel(
        get_school_sql_directory(), sol_row['responsavel_email'], sol_row['ra'])
    return None if estado == 'ok' else estado


def _resolver_solicitacoes(rows):
    """Enriquece linhas de `solicitacoes_saida` com nome/turma/série do aluno, para as telas do admin.

    Linhas novas (com ra): resolvidas ao vivo no banco da escola — são a maioria, já que o portal
    dos pais só grava `ra`. Linhas antigas (sem ra): usam os campos do LEFT JOIN legado com `alunos`.
    """
    rows = [dict(r) for r in rows]
    diretorio = _buscar_alunos_com_cache([r['ra'] for r in rows if r.get('ra')])

    for r in rows:
        if r.get('ra'):
            info = diretorio.get(r['ra'])
            r['aluno_nome'] = info['nome'] if info else f"RA {r['ra']}"
            r['serie'] = (info or {}).get('serie')
            r['turma'] = (info or {}).get('turma')
        else:
            r['aluno_nome'] = r.get('nome_legado')
            r['serie'] = r.get('serie_legado')
            r['turma'] = r.get('turma_legado')
    return rows


def _resolver_dados_saidas(rows):
    """Enriquece linhas de `saidas` com nome/foto/turma/série do aluno, prontas para o template.

    Linhas novas (com ra): resolvidas ao vivo via school_sql_directory — nada disso é persistido,
    só usado para renderizar a página atual.
    Linhas antigas (pré-migração RA, sem ra): usam os campos já trazidos pelo LEFT JOIN legado
    com a tabela `alunos`, até serem migradas na Fase 5 do redesenho.
    """
    resultado = []
    rows = [dict(r) for r in rows]
    ras = [r['ra'] for r in rows if r.get('ra')]
    diretorio = _buscar_alunos_com_cache(ras)

    for r in rows:
        if r.get('ra'):
            info = diretorio.get(r['ra'])
            r['aluno'] = info['nome'] if info else f"RA {r['ra']}"
            r['foto_src'] = (info or {}).get('foto_url')
            r['serie'] = (info or {}).get('serie')
            r['turma'] = r.get('turma') or (info or {}).get('turma')
        else:
            r['aluno'] = r.get('aluno_legado')
            r['foto_src'] = f"/uploads/{r['foto_path_legado']}" if r.get('foto_path_legado') else None
            r['serie'] = r.get('serie_legado')
            r['turma'] = r.get('turma') or r.get('turma_legado')
        resultado.append(r)
    return resultado


def register_routes(app):
    """Registra todas as rotas web"""
    
    # ==================== ARQUIVOS ESTÁTICOS ====================
    @app.route('/static/css/style.css')
    def serve_css():
        static_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        return send_from_directory(os.path.join(static_folder, 'css'), 'style.css')
    
    @app.route('/static/images/<path:filename>')
    def serve_images(filename):
        static_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        return send_from_directory(os.path.join(static_folder, 'images'), filename)
    
    # ==================== AUTENTICAÇÃO ====================
    @app.route("/", methods=["GET", "POST"])
    def login():
        ip = request.remote_addr

        if request.method == "POST":
            usuario_informado = request.form.get("u", "").strip()

            # Verificado só no POST: "/" é a landing page do app e o limite agora custa uma
            # consulta ao banco — não vale pagá-la em toda visita anônima.
            # Limite estreito por conta (protege quem está sendo atacado) e largo por IP (rede de
            # segurança contra varredura). Ver app/core/rate_limit.py para o porquê da diferença.
            if rate_limit.esta_bloqueado(rate_limit.LOGIN_IP, ip) or (
                usuario_informado and rate_limit.esta_bloqueado(rate_limit.LOGIN_CONTA, usuario_informado)
            ):
                return render_template("auth/login.html", erro="Muitas tentativas de login. Tente novamente em 5 minutos.")

            with get_db() as conn:
                # Mesmo valor usado na chave do rate limit: buscar pelo não-normalizado deixaria
                # " admin " contando falhas em "admin" mas nunca casando com o usuário.
                user = conn.execute(
                    "SELECT id, role, username, password FROM usuarios WHERE username=%s",
                    (usuario_informado,)
                ).fetchone()

            if senha_confere(user['password'] if user else None, request.form.get("s", "")):
                rate_limit.limpar(rate_limit.LOGIN_CONTA, user['username'])
                rate_limit.limpar(rate_limit.LOGIN_IP, ip)
                log_operacao(user['username'], "LOGIN_SUCESSO", f"role={user['role']}", ip=ip)
                session['user_id'] = user['id']
                session['role'] = user['role']
                session['username'] = user['username']
                return redirect("/inicio")
            else:
                rate_limit.registrar_falha(rate_limit.LOGIN_IP, ip)
                if usuario_informado:
                    rate_limit.registrar_falha(rate_limit.LOGIN_CONTA, usuario_informado)
                log_operacao(usuario_informado or "desconhecido", "LOGIN_FALHA", "senha incorreta ou usuário inexistente", ip=ip)
                return render_template("auth/login.html", erro="Usuário ou senha incorretos.")

        return render_template("auth/login.html")
    
    @app.route("/inicio")
    @login_required
    def inicio():
        if session.get('role') == 'vigia':
            return redirect("/saidas")
        solicitacoes_pendentes = 0
        responsaveis_pendentes = 0
        if session.get('role') == 'admin':
            with get_db() as conn:
                r = conn.execute("SELECT COUNT(*) AS c FROM solicitacoes_saida WHERE status = 'aguardando'").fetchone()
                solicitacoes_pendentes = r['c'] if r else 0
                r2 = conn.execute("SELECT COUNT(*) AS c FROM responsaveis WHERE status = 'pendente'").fetchone()
                responsaveis_pendentes = r2['c'] if r2 else 0
        return render_template("dashboard/home.html",
                               solicitacoes_pendentes=solicitacoes_pendentes,
                               responsaveis_pendentes=responsaveis_pendentes)
    
    # POST apenas: com GET, um <img src="/logout"> em qualquer página derrubava a sessão do
    # usuário. O formulário no sidebar carrega o token CSRF.
    @app.route("/logout", methods=["POST"])
    def logout():
        log_operacao(session.get('username', 'desconhecido'), "LOGOUT", "", ip=request.remote_addr)
        session.clear()
        return redirect("/")
    
    @app.route("/esqueci_senha", methods=["GET", "POST"])
    def esqueci_senha():
        from app.core.mailer import enviar_email_async
        mensagem = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            link_para_enviar = None
            ip = request.remote_addr

            # Rota pública que dispara e-mail: sem limite, um script enche a caixa de qualquer
            # funcionário e queima a reputação do SMTP da escola. A tentativa é contada sempre,
            # e não só quando "falha" — aqui o próprio pedido é o que custa caro.
            if rate_limit.esta_bloqueado(rate_limit.RESET_IP, ip) or (
                email and rate_limit.esta_bloqueado(rate_limit.RESET_CONTA, email)
            ):
                return render_template(
                    "auth/forgot.html",
                    mensagem="Muitas solicitações de redefinição. Tente novamente em 15 minutos."
                )
            rate_limit.registrar_tentativa(rate_limit.RESET_IP, ip)
            if email:
                rate_limit.registrar_tentativa(rate_limit.RESET_CONTA, email)

            # Bloco de DB isolado: gera e persiste o token antes de qualquer envio
            with get_db() as conn:
                user = conn.execute("SELECT id FROM usuarios WHERE LOWER(email) = %s", (email,)).fetchone()
                if user:
                    import secrets
                    token = secrets.token_urlsafe(32)
                    expires_at = (agora_utc() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("DELETE FROM reset_tokens WHERE user_id = %s", (user['id'],))
                    conn.execute("INSERT INTO reset_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
                                 (user['id'], token, expires_at))
                    base_url = app.config.get('BASE_URL', 'http://localhost:8002').rstrip('/')
                    link_para_enviar = f"{base_url}/resetar_senha/{token}"

            # Envio de email fora do bloco de DB: falha no SMTP não faz rollback do token
            if link_para_enviar:
                corpo = f"""
                <div style="font-family:sans-serif; max-width:480px; margin:0 auto; padding:32px 24px;">
                  <h2 style="color:#111827; margin-bottom:8px;">Redefinição de senha</h2>
                  <p style="color:#6b7280; margin-bottom:24px;">
                    Recebemos uma solicitação para redefinir a senha da sua conta no <strong>SecureEdu</strong>.
                    Clique no botão abaixo para criar uma nova senha. O link é válido por <strong>1 hora</strong>.
                  </p>
                  <a href="{link_para_enviar}" style="display:inline-block; background:#2563eb; color:#fff;
                     padding:12px 28px; border-radius:8px; text-decoration:none; font-weight:600; font-size:15px;">
                    Redefinir minha senha
                  </a>
                  <p style="color:#9ca3af; font-size:12px; margin-top:24px;">
                    Se você não solicitou isso, ignore este email. Sua senha não será alterada.
                  </p>
                </div>
                """
                enviar_email_async(
                    email, "Redefinição de senha — SecureEdu", corpo,
                    fallback_log=f"[FALLBACK] Email não enviado. Use este link manualmente: {link_para_enviar}",
                )

            mensagem = "Se este email estiver cadastrado, você receberá um link em breve."
        return render_template("auth/forgot.html", mensagem=mensagem)
    
    @app.route("/resetar_senha/<token>", methods=["GET", "POST"])
    def resetar_senha(token):
        from datetime import datetime
        erro = ""
        user_id = None
        
        with get_db() as conn:
            registro = conn.execute(
                "SELECT user_id, expires_at FROM reset_tokens WHERE token = %s", (token,)
            ).fetchone()

            if registro:
                expires_at = datetime.strptime(registro['expires_at'], "%Y-%m-%d %H:%M:%S")
                if agora_utc() <= expires_at:
                    user_id = registro['user_id']
                else:
                    conn.execute("DELETE FROM reset_tokens WHERE token = %s", (token,))
                    erro = "Link expirado. Solicite um novo."
            else:
                erro = "Link inválido."

        if request.method == "POST" and user_id:
            nova = request.form.get("senha", "")
            confirma = request.form.get("confirma", "")
            from app.schemas.user_schema import UserSchema as _US
            _err = _US._check_password_strength(nova)
            if _err:
                erro = _err + '.'
            elif nova != confirma:
                erro = "As senhas não coincidem."
            else:
                with get_db() as conn:
                    conn.execute("UPDATE usuarios SET password = %s WHERE id = %s", (generate_password_hash(nova, method='pbkdf2:sha256'), user_id))
                    conn.execute("DELETE FROM reset_tokens WHERE token = %s", (token,))
                return redirect("/?resetado=1")
        
        return render_template("auth/reset.html", erro=erro, token=token if user_id else None)
    
    @app.route("/historico", methods=["GET"])
    @login_required
    def historico_geral():
        """Histórico de saídas por nome de aluno, nas duas origens de dado.

        A consulta antiga partia de `alunos LEFT JOIN saidas ON a.id = s.aluno` e por isso só
        enxergava o mundo legado: toda saída criada pelo fluxo atual grava `ra` e deixa `aluno`
        NULL, então nenhuma delas aparecia aqui — sem erro, só resultado vazio. Enquanto o
        diretório roda em mock isso passa despercebido; com o banco da instituição plugado, a
        tela inteira ficaria em branco.

        Agora a busca parte das saídas e casa por RA (nome resolvido ao vivo no banco da escola)
        ou pelo nome na tabela `alunos` (linhas anteriores à migração).
        """
        nome = request.args.get("nome", "").strip()
        resultados = []

        if nome:
            # Nome só existe no banco da escola: é lá que ele vira uma lista de RAs. Falha na
            # consulta externa não pode zerar a tela — o histórico legado ainda é pesquisável.
            try:
                # Limite maior que o padrão da portaria: aqui o nome vira a lista de RAs que
                # filtra as saídas, e cortar em 20 faria sumir, sem aviso, o histórico dos demais
                # alunos que casam com um sobrenome comum.
                ras = [a["ra"] for a in
                       get_school_sql_directory().search_students(nome, limite=RAS_NO_HISTORICO)]
            except Exception as e:
                _log.warning(f"[SCHOOL_SQL] Busca de aluno no histórico indisponível: {e}")
                ras = []

            with get_db() as conn:
                rows = conn.execute("""
                    SELECT s.id, s.ra, s.turma, s.data_saida, s.horario, s.motivo,
                           s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status,
                           a.nome AS aluno_legado, a.serie AS serie_legado,
                           a.turma AS turma_legado, a.foto_path AS foto_path_legado
                    FROM saidas s
                    LEFT JOIN alunos a ON a.id = s.aluno
                    WHERE s.ra = ANY(%s::text[])
                       OR a.nome ILIKE %s ESCAPE '\\'
                    ORDER BY s.data_saida DESC, s.horario DESC
                    LIMIT 200
                """, (ras, f'%{escapar_like(nome)}%')).fetchall()

            # Mesmo resolvedor de /saidas: além do nome, traz foto e série do banco da escola,
            # que a consulta antiga só conseguia para os alunos legados.
            resultados = _resolver_dados_saidas(rows)

        return render_template("departures/history_of_departures.html", resultados=resultados, nome=nome)
    
    # ==================== ALUNOS ====================

    @app.route("/alunos")
    @login_required
    def lista_alunos():
        """Lista de alunos da escola, com busca — porta de entrada para o histórico individual.

        A tela existia quando os alunos eram cadastrados aqui e saiu junto com aquele cadastro.
        O dado agora vem do banco da instituição, mas a necessidade continua: a secretaria
        precisa olhar um aluno específico, e não a lista de todas as saídas do dia misturadas.
        """
        busca = request.args.get("busca", "").strip()
        filtro_serie = request.args.get("serie", "").strip()
        filtro_turma = request.args.get("turma", "").strip()

        try:
            diretorio = get_school_sql_directory()
            alunos = (diretorio.search_students(busca, limite=ALUNOS_POR_PAGINA) if busca
                      else diretorio.list_students(limite=ALUNOS_POR_PAGINA))
            indisponivel = False
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Lista de alunos indisponível: {e}")
            alunos, indisponivel = [], True

        # A lista vem cortada no limite; sem avisar, o usuário conclui que a escola tem
        # exatamente este número de alunos. Medido antes dos filtros, que são aplicados aqui.
        truncada = len(alunos) >= ALUNOS_POR_PAGINA

        # A série vem como texto do banco da instituição, que é mantido por outra equipe:
        # "1 ano", "1º ano EF" e "1 ANO" precisam cair no mesmo grupo. normalizar_serie devolve
        # None para o que não reconhece — nesse caso o valor original vira o nome do grupo, e o
        # aluno aparece na tela em vez de sumir.
        for a in alunos:
            a['serie_grupo'] = (normalizar_serie(a.get('serie')) or
                                (a.get('serie') or '').strip() or 'Sem série')
            a['turma_grupo'] = (a.get('turma') or '').strip() or '—'

        # Opções dos filtros saem do conjunto completo, antes de filtrar: senão, escolher uma
        # série esvaziaria a lista de séries e não haveria como voltar.
        series_disponiveis = sorted({a['serie_grupo'] for a in alunos}, key=ordem_da_serie)
        turmas_disponiveis = sorted({a['turma_grupo'] for a in alunos})

        if filtro_serie:
            alunos = [a for a in alunos if a['serie_grupo'] == filtro_serie]
        if filtro_turma:
            alunos = [a for a in alunos if a['turma_grupo'] == filtro_turma]

        # Um grupo por série, na ordem pedagógica; dentro dele, por turma e depois por nome.
        grupos = []
        for serie in sorted({a['serie_grupo'] for a in alunos}, key=ordem_da_serie):
            do_grupo = sorted((a for a in alunos if a['serie_grupo'] == serie),
                              key=lambda a: (a['turma_grupo'], (a.get('nome') or '').lower()))
            grupos.append({
                'serie': serie,
                'alunos': do_grupo,
                'turmas': sorted({a['turma_grupo'] for a in do_grupo}),
            })

        return render_template("students/list_of_students.html",
                               grupos=grupos,
                               total=len(alunos),
                               busca=busca,
                               filtro_serie=filtro_serie,
                               filtro_turma=filtro_turma,
                               series_disponiveis=series_disponiveis,
                               turmas_disponiveis=turmas_disponiveis,
                               indisponivel=indisponivel,
                               truncada=truncada)

    @app.route("/alunos/<ra>")
    @login_required
    def historico_do_aluno(ra):
        """Ficha de um aluno: dados vindos da escola e todas as saídas dele."""
        try:
            aluno = get_school_sql_directory().get_student(ra)
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao abrir a ficha do aluno: {e}")
            aluno = None

        if not aluno:
            flash("Aluno não encontrado no cadastro da escola.", "error")
            return redirect("/alunos")

        with get_db() as conn:
            linhas = conn.execute("""
                SELECT s.id, s.ra, s.turma, s.data_saida, s.horario, s.motivo,
                       s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status,
                       s.liberado_em, s.documento_path,
                       EXISTS (SELECT 1 FROM saidas_documentos d WHERE d.saida_id = s.id) AS tem_documento,
                       a.nome AS aluno_legado, a.serie AS serie_legado,
                       a.turma AS turma_legado, a.foto_path AS foto_path_legado
                FROM saidas s
                LEFT JOIN alunos a ON a.id = s.aluno
                -- Duas origens. As saídas do fluxo atual casam pelo RA. As anteriores à migração
                -- não têm RA e só podem ser reconhecidas pelo nome na tabela legada — igualdade
                -- exata, não ILIKE: aqui a lista é de UM aluno, e um homônimo não pode entrar.
                WHERE s.ra = %s
                   OR (s.ra IS NULL AND a.nome = %s)
                ORDER BY s.data_saida DESC, s.horario DESC
                LIMIT 500
            """, (ra, aluno['nome'])).fetchall()

        saidas = _resolver_dados_saidas(linhas)
        resumo = {
            'total': len(saidas),
            'concluidas': sum(1 for s in saidas if s['status'] == 'concluida'),
            'pendentes': sum(1 for s in saidas if s['status'] == 'pendente'),
            'nao_realizadas': sum(1 for s in saidas if s['status'] == 'nao_realizada'),
        }
        return render_template("students/student_history.html",
                               aluno=aluno, saidas=saidas, resumo=resumo)

    # ==================== SAÍDAS ====================
    @app.route("/portaria/buscar_aluno")
    @login_required
    def portaria_buscar_aluno():
        """Busca alunos por nome/RA parcial para o formulário de registrar saída.
        Dado vem direto da consulta externa — nada aqui é persistido no S2E."""
        query = request.args.get("q", "").strip()
        # Piso antes de qualquer consulta: além de fechar a enumeração, evita a varredura da
        # tabela de alunos (LIKE '%...%' sobre nome normalizado) a cada tecla digitada.
        if len(query) < MIN_CARACTERES_BUSCA:
            return jsonify([])
        try:
            resultados = get_school_sql_directory().search_students(
                query, limite=RESULTADOS_AUTOCOMPLETE)
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar aluno na portaria: {e}")
            resultados = []
        return jsonify([
            {"ra": a["ra"], "nome": a["nome"], "turma": a.get("turma"), "serie": a.get("serie")}
            for a in resultados
        ])

    @app.route("/registrar_saida", methods=["GET", "POST"])
    @login_required
    def registrar_saida():
        ra_pre_selecionado = request.args.get("ra")

        if request.method == "POST":
            ra = request.form.get("ra", "").strip()
            data_saida = request.form.get("data_saida", "").strip() or hoje()
            horario = request.form.get("horario", "").strip()
            motivo = request.form.get("motivo")
            responsavel_escola = request.form.get("responsavel_escola")
            tipo_saida = request.form.get("tipo_saida")
            acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None

            if not ra or not horario or not motivo or not responsavel_escola or not tipo_saida:
                flash("Todos os campos são obrigatórios, incluindo a seleção do aluno na busca!", "error")
            elif not horario_valido(horario):
                flash("Horário inválido. Use o formato HH:MM.", "error")
            elif not data_valida(data_saida):
                flash("Data inválida. Use o formato AAAA-MM-DD.", "error")
            elif tipo_saida not in ('sozinho', 'acompanhado'):
                flash("Tipo de saída inválido.", "error")
            else:
                try:
                    aluno = get_school_sql_directory().get_student(ra)
                except Exception as e:
                    _log.warning(f"[SCHOOL_SQL] Erro ao registrar saída: {e}")
                    aluno = None
                if not aluno:
                    flash("Aluno não encontrado para o RA informado. Busque novamente pelo nome ou RA.", "error")
                else:
                    from werkzeug.utils import secure_filename
                    from app.core.validators import validar_upload_documento, mime_do_arquivo

                    doc = request.files.get('documento')
                    tem_anexo = bool(doc and doc.filename)

                    # Antes, um anexo reprovado era só ignorado: a saída era gravada sem ele e a
                    # tela dizia "Saída registrada!". Quem anexou o atestado ia embora achando que
                    # o documento estava lá. Agora o envio inteiro é recusado, com o motivo.
                    #
                    # E a validação passou a ser a de conteúdo (validar_upload_documento confere os
                    # magic bytes do PDF e das imagens), não só a extensão do nome do arquivo —
                    # essa função já existia no projeto e não era chamada por ninguém.
                    if tem_anexo:
                        anexo_ok, erro_anexo = validar_upload_documento(doc)
                        # Teto próprio, bem abaixo do MAX_CONTENT_LENGTH (16 MB) que vale para a
                        # request inteira: o anexo agora ocupa espaço no banco, e o plano free do
                        # Supabase tem 500 MB. Uma foto de atestado cabe folgada em 5 MB.
                        if anexo_ok:
                            doc.seek(0, os.SEEK_END)
                            tamanho = doc.tell()
                            doc.seek(0)
                            if tamanho > DOCUMENTO_MAX_BYTES:
                                anexo_ok = False
                                erro_anexo = (f"arquivo de {tamanho // (1024*1024)} MB excede o "
                                              f"limite de {DOCUMENTO_MAX_BYTES // (1024*1024)} MB")
                        if not anexo_ok:
                            flash(f"Documento não anexado: {erro_anexo}. A saída não foi registrada.", "error")
                            return render_template("departures/register.html",
                                                   ra_selecionado=ra_pre_selecionado, today=hoje())

                    # O COUNT continua aqui porque é ele que dá a mensagem boa no caso comum
                    # (o aluno já tem saída pendente e o usuário precisa saber disso). O que ele
                    # NÃO faz é garantir a unicidade: entre o SELECT e o INSERT, em READ
                    # COMMITTED, cabe outra transação inteira. Quem garante é o índice único
                    # parcial uq_saidas_pendente_por_dia — ver app/core/migrations.py.
                    #
                    # O except fica FORA do `with`: a UniqueViolation aborta a transação, e
                    # qualquer comando emitido depois dela, ainda dentro do bloco, falharia
                    # também. Aqui a transação já terminou em rollback quando chegamos.
                    duplicada = False
                    try:
                        with get_db() as conn:
                            pendente = conn.execute(
                                "SELECT COUNT(*) as total FROM saidas WHERE ra = %s AND data_saida = %s AND status = 'pendente'",
                                (ra, data_saida)
                            ).fetchone()['total']

                            if pendente > 0:
                                duplicada = True
                            else:
                                id_saida = conn.execute("""
                                    INSERT INTO saidas (ra, turma, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, status)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pendente')
                                    RETURNING id
                                """, (ra, aluno.get('turma'), data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante)).fetchone()['id']

                                # O anexo entra na mesma transação da saída: ou os dois existem, ou
                                # nenhum. Antes ele era escrito em disco antes do INSERT — ficava
                                # órfão quando o registro era barrado, e sumia de vez a cada deploy,
                                # porque o disco da hospedagem é efêmero.
                                if tem_anexo:
                                    conn.execute(
                                        "INSERT INTO saidas_documentos (saida_id, nome, tipo, dados) VALUES (%s, %s, %s, %s)",
                                        (id_saida, secure_filename(doc.filename),
                                         mime_do_arquivo(doc.filename), doc.read())
                                    )
                                log_operacao(session.get('username'), "REGISTROU SAÍDA",
                                             f"RA: {ra} | data: {data_saida} | horário: {horario}",
                                             ip=request.remote_addr, conn=conn)
                    except UniqueViolation:
                        # A outra transação venceu a corrida. Do ponto de vista de quem está no
                        # balcão é a mesma situação do COUNT acima, e a mensagem é a mesma.
                        _log.info("[SAIDAS] INSERT concorrente barrado pelo índice único "
                                  "(RA %s, data %s)", ra, data_saida)
                        duplicada = True

                    if not duplicada:
                        flash("Saída registrada!", "success")
                        return redirect("/saidas")
                    flash(f"Este aluno já tem uma saída pendente para {data_saida}!", "error")

        return render_template("departures/register.html",
                               ra_selecionado=ra_pre_selecionado,
                               today=hoje())
    
    @app.route("/saidas")
    @login_required
    def lista_saidas():
        data_selecionada = request.args.get("data", hoje())
        busca = request.args.get("busca", "").strip()

        # A expiração de saídas e a remoção das antigas saíram daqui: rodavam a cada
        # carregamento da página. Agora ficam em app/core/maintenance.py.
        with get_db() as conn:
            rows = conn.execute("""
                SELECT s.id, s.ra, s.turma, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida,
                       s.acompanhante, s.documento_path, s.status,
                       -- EXISTS, e não um JOIN: traria o binário do anexo para todas as linhas
                       -- do dia só para saber se há anexo.
                       EXISTS (SELECT 1 FROM saidas_documentos d WHERE d.saida_id = s.id) AS tem_documento,
                       a.nome AS aluno_legado, a.serie AS serie_legado, a.turma AS turma_legado,
                       a.foto_path AS foto_path_legado
                FROM saidas s
                LEFT JOIN alunos a ON s.aluno = a.id
                WHERE s.data_saida = %s
                ORDER BY s.horario ASC
            """, (data_selecionada,)).fetchall()

        saidas = _resolver_dados_saidas(rows)
        if busca:
            busca_lower = busca.lower()
            saidas = [s for s in saidas if busca_lower in (s['aluno'] or '').lower()]

        pendentes = [s for s in saidas if s['status'] == 'pendente']
        concluidas = [s for s in saidas if s['status'] == 'concluida']
        nao_realizadas = [s for s in saidas if s['status'] == 'nao_realizada']

        return render_template("departures/list_of_exits.html",
                               pendentes=pendentes,
                               concluidas=concluidas,
                               nao_realizadas=nao_realizadas,
                               data_selecionada=data_selecionada,
                               # Só no dia da saída o botão de liberar faz sentido. A checagem
                               # que vale é a de concluir_saida; esta evita oferecer na tela uma
                               # ação que o servidor vai recusar.
                               hoje_local=hoje(),
                               busca=busca)
    
    @app.route("/editar_saida/<int:id_saida>", methods=["GET", "POST"])
    @login_required
    def editar_saida(id_saida):
        if session.get('role') == 'vigia':
            flash("Acesso negado", "error")
            return redirect("/saidas")

        with get_db() as conn:
            saida_row = conn.execute("""
                SELECT s.*, a.nome AS aluno_legado
                FROM saidas s
                LEFT JOIN alunos a ON s.aluno = a.id
                WHERE s.id = %s AND s.status = 'pendente'
            """, (id_saida,)).fetchone()

        if not saida_row:
            flash("Saída não encontrada ou já autorizada", "error")
            return redirect("/saidas")

        # Fora da transação: _resolver_dados_saidas consulta o banco da escola, que é outro
        # sistema e pode estar lento. Ler a saída e gravá-la em transações separadas é seguro
        # porque o UPDATE mantém o filtro `status='pendente'` — é ele, e não a duração da
        # transação, que impede editar uma saída já autorizada nesse intervalo.
        saida = _resolver_dados_saidas([saida_row])[0]

        if request.method == "POST":
            horario = request.form.get("horario", "").strip()
            # .strip() e obrigatoriedade como em /registrar_saida: sem isso, `motivo` chegava
            # como None num UPDATE de coluna NOT NULL — IntegrityError e erro 500. O
            # `required` do formulário é só do navegador e não vale para um POST montado à
            # mão, nem para um envio com o JavaScript desligado.
            motivo = (request.form.get("motivo") or "").strip()
            responsavel_escola = (request.form.get("responsavel_escola") or "").strip()
            tipo_saida = request.form.get("tipo_saida")
            acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None

            if not horario or not motivo or not responsavel_escola or not tipo_saida:
                flash("Todos os campos são obrigatórios.", "error")
                return render_template("departures/edit_exits.html", saida=saida)
            if not horario_valido(horario):
                flash("Horário inválido. Use o formato HH:MM.", "error")
                return render_template("departures/edit_exits.html", saida=saida)
            if tipo_saida not in ('sozinho', 'acompanhado'):
                flash("Tipo de saída inválido.", "error")
                return render_template("departures/edit_exits.html", saida=saida)

            with get_db() as conn:
                conn.execute("""
                    UPDATE saidas SET horario=%s, motivo=%s, responsavel_escola=%s, tipo_saida=%s, acompanhante=%s
                    WHERE id=%s AND status='pendente'
                """, (horario, motivo, responsavel_escola, tipo_saida, acompanhante, id_saida))
                log_operacao(session.get('username'), "EDITOU SAÍDA",
                             f"ID Saída: {id_saida} | horário: {horario} | tipo: {tipo_saida}",
                             ip=request.remote_addr, conn=conn)
            flash("Saída atualizada!", "success")
            return redirect("/saidas")

        return render_template("departures/edit_exits.html", saida=saida)

    @app.route("/concluir_saida/<int:id_saida>", methods=["POST"])
    @login_required
    def concluir_saida(id_saida):
        with get_db() as conn:
            # Só a transição pendente -> concluída conta. Sem o filtro de status, um duplo-clique
            # (ou POST repetido) reescrevia liberado_em e reenviava o e-mail aos responsáveis.
            #
            # E só no dia da própria saída. /saidas aceita ?data= qualquer e desenha a lista
            # daquele dia; sem esta condição, mudar o seletor de data para amanhã e clicar em
            # "Liberar" entregava hoje uma criança autorizada para outro dia — pela interface
            # normal, sem POST forjado, e com o e-mail ao responsável dizendo "hoje às 12:00".
            #
            # A comparação vai dentro do UPDATE, e não num SELECT antes dele, para não abrir
            # janela entre conferir e gravar: continua sendo um único comando atômico.
            hoje_local = hoje()
            liberou = conn.execute(
                """UPDATE saidas SET status = 'concluida', usuario_autorizou = %s, liberado_em = NOW()
                   WHERE id = %s AND status = 'pendente' AND data_saida = %s""",
                (session['user_id'], id_saida, hoje_local)
            ).rowcount

            if not liberou:
                # rowcount 0 tem duas causas com desfechos muito diferentes para quem está no
                # portão: já autorizada, ou agendada para outro dia. Vale a consulta extra —
                # ela só acontece no caminho de recusa.
                atual = conn.execute(
                    "SELECT status, data_saida FROM saidas WHERE id = %s", (id_saida,)
                ).fetchone()
                if atual and atual['status'] == 'pendente':
                    log_operacao(session.get('username'), "LIBERAÇÃO RECUSADA (data)",
                                 f"ID Saída: {id_saida} | agendada para {atual['data_saida']} | "
                                 f"hoje é {hoje_local}",
                                 ip=request.remote_addr, conn=conn)
                    flash(f"Esta saída está agendada para {atual['data_saida']}, "
                          "não pode ser liberada hoje.", "error")
                else:
                    flash("Esta saída já havia sido autorizada.", "error")
                return redirect("/saidas")

            saida = conn.execute("""
                SELECT s.data_saida, s.horario, s.ra, a.id AS aluno_id_legado, a.nome AS aluno_nome_legado
                FROM saidas s
                LEFT JOIN alunos a ON a.id = s.aluno
                WHERE s.id = %s
            """, (id_saida,)).fetchone()

            # Só o que sai do banco PRÓPRIO fica aqui dentro. O caminho legado é uma consulta
            # local e não custa nada; a consulta ao banco da escola foi para fora do `with`,
            # logo abaixo.
            nome_aluno, emails = None, []
            if saida and not saida['ra']:
                nome_aluno = saida['aluno_nome_legado']
                responsaveis = conn.execute("""
                    SELECT r.email
                    FROM vinculos_pais_alunos v
                    JOIN responsaveis r ON r.id = v.responsavel_id
                    WHERE v.aluno_id = %s
                """, (saida['aluno_id_legado'],)).fetchall()
                emails = [r['email'] for r in responsaveis]

        # Fora da transação, de propósito. O UPDATE acima trava a linha da saída, e
        # _nome_e_emails_para_saida abre TRÊS conexões ao banco da escola (get_student já são
        # duas, por causa de _emails_por_ra, mais get_guardian_emails_for_ra). Com
        # SCHOOL_SQL_TIMEOUT_SEG=5 o pior caso é ~30s segurando ao mesmo tempo uma conexão do
        # pool e uma thread do worker — e basta o banco da escola estar LENTO, não fora do ar.
        # Dois seguranças clicando "liberar" juntos paravam o sistema inteiro, portaria
        # inclusive, sem chegar ao timeout do gunicorn que dispararia o restart.
        if saida and saida['ra']:
            nome_aluno, emails = _nome_e_emails_para_saida(saida['ra'])

        if saida:
            from app.core.mailer import enviar_email_async
            # nome_aluno vem do banco da escola e horario do formulário da portaria: ambos vão
            # escapados, para não injetarem HTML no e-mail que chega ao responsável.
            corpo = (f"""<p>Olá! A saída de <strong>{escape(nome_aluno)}</strong> foi
                    <strong style="color:#16a34a;">liberada pela segurança da escola</strong>
                    hoje às <strong>{escape(saida['horario'])}</strong>.</p>""")
            for email in emails:
                enviar_email_async(email, "Saída liberada — SecureEdu", corpo)

        log_operacao(session.get('username'), "CONCLUIU SAÍDA", f"ID Saída: {id_saida}")
        flash("Saída autorizada!", "success")
        return redirect("/saidas")
    
    # ==================== ADMIN ====================

    @app.route("/configuracoes")
    @admin_required
    def configuracoes():
        hoje_local = hoje()
        with get_db() as conn:
            total_usuarios = conn.execute("SELECT COUNT(*) as total FROM usuarios").fetchone()['total']
            total_saidas = conn.execute("SELECT COUNT(*) as total FROM saidas").fetchone()['total']
            saidas_hoje = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE data_saida = %s", (hoje_local,)
            ).fetchone()['total']

        return render_template("admin/settings.html",
                               total_usuarios=total_usuarios,
                               total_saidas=total_saidas,
                               saidas_hoje=saidas_hoje)

    @app.route("/admin/backup")
    @admin_required
    def admin_backup():
        flash("Backups são gerenciados automaticamente pelo Supabase. Acesse o painel do Supabase para exportar os dados.", "success")
        return redirect("/configuracoes")
    
    @app.route("/novo", methods=["GET", "POST"])
    @admin_required
    def gerenciar_usuarios():
        from app.repositories.user_repo import UserRepository
        repo = UserRepository()
        
        if request.method == "POST":
            from app.schemas.user_schema import UserSchema
            data = {
                'username': request.form.get("u", "").strip(),
                'password': request.form.get("s", ""),
                'role':     request.form.get("r", "basico"),
                'email':    request.form.get("e", "").strip().lower(),
            }
            ok, err, validated = UserSchema.validate(data)
            if not ok:
                flash(err, "error")
            else:
                try:
                    repo.create(validated)
                    log_operacao(session.get('username'), "CRIOU USUÁRIO",
                                 f"{validated['username']} | perfil: {validated['role']}",
                                 ip=request.remote_addr)
                    flash(f"Usuário {validated['username']} criado!", "success")
                except Exception as e:
                    flash(f"Erro: {e}", "error")
            return redirect("/novo")
        
        usuarios = repo.get_all_without_passwords()
        return render_template("admin/users.html", usuarios=usuarios)
    
    @app.route("/deletar_usuario/<int:id_usuario>", methods=["POST"])
    @admin_required
    def deletar_usuario(id_usuario):
        from app.repositories.user_repo import UserRepository
        repo = UserRepository()
        
        if id_usuario == session.get('user_id'):
            flash("Você não pode deletar seu próprio usuário!", "error")
            return redirect("/novo")
        
        user = repo.get_by_id(id_usuario)
        if user and user['role'] == 'admin':
            admin_count = repo.get_admin_count_excluding(id_usuario)
            if admin_count == 0:
                flash("Não pode deletar o último administrador!", "error")
                return redirect("/novo")
        
        repo.delete(id_usuario)
        # `user` foi lido antes do delete: depois dele não há mais de onde tirar o nome.
        alvo = f"{user['username']} | perfil: {user['role']}" if user else f"ID {id_usuario}"
        log_operacao(session.get('username'), "DELETOU USUÁRIO", alvo, ip=request.remote_addr)
        flash("Usuário deletado!", "success")
        return redirect("/novo")
    
    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        # Somente funcionários. Aqui ficam documentos anexados às saídas (atestados, autorizações)
        # e as fotos legadas dos alunos, e a rota não sabe a qual aluno o arquivo pertence — com a
        # sessão de responsável liberada, qualquer pai autenticado leria o documento de qualquer
        # outro aluno. Nenhuma tela do portal dos pais aponta para /uploads: o link do anexo só
        # existe em /saidas, e a foto do filho vem de foto_url, do banco da escola.
        if 'user_id' not in session:
            return redirect('/')
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    
    @app.route("/saidas/<int:id_saida>/documento")
    @login_required
    def documento_da_saida(id_saida):
        """Devolve o anexo de uma saída, lido do banco.

        Mesmo alcance da rota /uploads: qualquer funcionário logado, porteiro incluído — é ele
        quem confere o atestado no portão. O nome do arquivo passou por secure_filename na
        gravação, e o tipo veio da extensão já confrontada com os magic bytes do conteúdo, então
        o Content-Type não é palavra do usuário.
        """
        with get_db() as conn:
            doc = conn.execute(
                "SELECT nome, tipo, dados FROM saidas_documentos WHERE saida_id = %s",
                (id_saida,)
            ).fetchone()

        if not doc:
            return "Documento não encontrado.", 404

        resposta = make_response(bytes(doc['dados']))
        resposta.headers['Content-Type'] = doc['tipo']
        # inline: a portaria abre o atestado na aba, sem baixar. filename fica para quando o
        # navegador decidir salvar.
        resposta.headers['Content-Disposition'] = f'inline; filename="{doc["nome"]}"'
        return resposta

    # ==================== ADMIN: RESPONSÁVEIS ====================

    @app.route("/admin/responsaveis")
    @admin_required
    def admin_responsaveis():
        with get_db() as conn:
            todos = conn.execute(
                "SELECT id, nome, email, status, criado_em FROM responsaveis ORDER BY criado_em DESC"
            ).fetchall()
        pendentes = [r for r in todos if r['status'] == 'pendente']
        return render_template("admin/responsaveis.html", todos=todos, pendentes=pendentes)

    @app.route("/admin/responsaveis/<int:resp_id>/aprovar", methods=["POST"])
    @admin_required
    def admin_aprovar_responsavel(resp_id):
        with get_db() as conn:
            resp = conn.execute("SELECT nome, email FROM responsaveis WHERE id = %s", (resp_id,)).fetchone()
            if resp:
                conn.execute("UPDATE responsaveis SET status = 'aprovado' WHERE id = %s", (resp_id,))
                log_operacao(session.get('username'), "APROVOU RESPONSÁVEL",
                             f"{resp['nome']} <{resp['email']}> | ID {resp_id}",
                             ip=request.remote_addr, conn=conn)
                flash(f"Conta de {resp['nome']} aprovada.", "success")
        return redirect("/admin/responsaveis")

    @app.route("/admin/responsaveis/<int:resp_id>/bloquear", methods=["POST"])
    @admin_required
    def admin_bloquear_responsavel(resp_id):
        with get_db() as conn:
            resp = conn.execute("SELECT nome FROM responsaveis WHERE id = %s", (resp_id,)).fetchone()
            if resp:
                conn.execute("UPDATE responsaveis SET status = 'bloqueado' WHERE id = %s", (resp_id,))
                log_operacao(session.get('username'), "BLOQUEOU RESPONSÁVEL",
                             f"{resp['nome']} | ID {resp_id}", ip=request.remote_addr, conn=conn)
                flash(f"Conta de {resp['nome']} bloqueada.", "success")
        return redirect("/admin/responsaveis")

    # ==================== ADMIN: SOLICITAÇÕES DE SAÍDA ====================

    @app.route("/admin/solicitacoes")
    @admin_required
    def admin_solicitacoes():
        with get_db() as conn:
            rows = conn.execute("""
                SELECT ss.id, ss.ra, ss.data_solicitada, ss.horario_solicitado, ss.motivo, ss.status,
                       ss.tipo_saida, ss.acompanhante,
                       a.nome AS nome_legado, a.turma AS turma_legado, a.serie AS serie_legado,
                       r.nome AS responsavel_nome, r.email AS responsavel_email,
                       s.status AS saida_status
                FROM solicitacoes_saida ss
                -- LEFT: não há FK em responsavel_id, então uma solicitação órfã (responsável
                -- apagado do banco) some daqui com JOIN, mas continua contando no aviso do
                -- /inicio — o admin via "1 aguardando revisão" e uma tela vazia.
                LEFT JOIN responsaveis r ON r.id = ss.responsavel_id
                LEFT JOIN alunos a ON a.id = ss.aluno_id
                -- LATERAL com LIMIT 1: duas saídas do mesmo aluno na mesma data duplicavam a
                -- solicitação na tela, e o status exibido era o de uma linha qualquer das duas.
                LEFT JOIN LATERAL (
                    SELECT s2.status
                    FROM saidas s2
                    WHERE s2.data_saida = ss.data_solicitada
                      AND ((ss.ra IS NOT NULL AND s2.ra = ss.ra)
                           OR (ss.aluno_id IS NOT NULL AND s2.aluno = ss.aluno_id))
                    ORDER BY s2.id DESC
                    LIMIT 1
                ) s ON TRUE
                -- Pendentes primeiro: com o LIMIT sobre o histórico, uma solicitação antiga
                -- ainda aguardando caía fora da janela de 100 linhas e sumia da tela.
                ORDER BY (ss.status = 'aguardando') DESC, ss.criado_em DESC
                LIMIT 100
            """).fetchall()
        rows = _resolver_solicitacoes(rows)
        aguardando = [r for r in rows if r['status'] == 'aguardando']
        historico  = [r for r in rows if r['status'] != 'aguardando']
        return render_template("admin/solicitacoes.html", aguardando=aguardando, historico=historico)

    @app.route("/admin/solicitacoes/<int:sol_id>/aprovar", methods=["POST"])
    @admin_required
    def admin_aprovar_solicitacao(sol_id):
        with get_db() as conn:
            sol_row = conn.execute("""
                SELECT ss.aluno_id, ss.ra, ss.data_solicitada, ss.horario_solicitado, ss.motivo,
                       ss.tipo_saida, ss.acompanhante, ss.status,
                       r.nome AS responsavel_nome, r.email AS responsavel_email,
                       r.status AS responsavel_status,
                       a.nome AS nome_legado, a.turma AS turma_legado, a.serie AS serie_legado
                FROM solicitacoes_saida ss
                LEFT JOIN responsaveis r ON r.id = ss.responsavel_id
                LEFT JOIN alunos a ON a.id = ss.aluno_id
                WHERE ss.id = %s AND ss.status = 'aguardando'
            """, (sol_id,)).fetchone()

        if not sol_row:
            flash("Solicitação não encontrada ou já revisada.", "error")
            return redirect("/admin/solicitacoes")

        # As duas linhas abaixo consultam o banco da escola (nome/turma do aluno e vínculo do
        # responsável) e ficam FORA de qualquer transação nossa: são até três conexões a outro
        # sistema, com 5s de timeout cada, e prender uma conexão do pool durante isso era o que
        # travava o sistema inteiro quando aquele banco ficava lento.
        sol = _resolver_solicitacoes([sol_row])[0]

        # Segundo portão. A solicitação foi criada pelo responsável, que naquele momento tinha
        # o vínculo conferido em /pais/solicitar — mas a aprovação acontece depois, às vezes
        # dias depois, e o vínculo pode ter mudado nesse intervalo (guarda, ordem judicial,
        # transferência) ou a conta pode ter sido bloqueada pela própria escola. Aprovar sem
        # reconferir transforma um pedido que deixou de ser legítimo numa saída válida na tela
        # da portaria, com o acompanhante que aquele pedido indicava.
        #
        # Fail-closed: banco da escola sem resposta recusa a aprovação. O admin pode tentar de
        # novo em minutos; entregar a criança à pessoa errada não tem segunda tentativa.
        recusa = _revalidar_vinculo_da_solicitacao(sol_row)
        if recusa:
            log_operacao(session.get('username'), "APROVAÇÃO RECUSADA",
                         f"ID Solicitação: {sol_id} | RA: {sol['ra'] or '—'} | motivo: {recusa}",
                         ip=request.remote_addr)
            flash(_RECUSA_APROVACAO[recusa], "error")
            return redirect("/admin/solicitacoes")

        with get_db() as conn:
            # Aprova a solicitação. O filtro por status é o que serializa duas aprovações
            # simultâneas: a segunda transação relê a linha já aprovada, casa 0 linhas e para
            # aqui, em vez de criar uma segunda saída para o mesmo aluno.
            aprovou = conn.execute(
                """UPDATE solicitacoes_saida SET status = 'aprovado', revisado_por = %s, revisado_em = NOW()
                   WHERE id = %s AND status = 'aguardando'""",
                (session['user_id'], sol_id)
            ).rowcount

            if not aprovou:
                flash("Solicitação não encontrada ou já revisada.", "error")
                return redirect("/admin/solicitacoes")

            # Auditada aqui, e não no fim da rota: a decisão já está persistida neste ponto, e os
            # dois caminhos seguintes (criar a saída ou encontrar uma já pendente) terminam em
            # `return` — auditar depois deixaria um deles sem registro.
            log_operacao(session.get('username'), "APROVOU SOLICITAÇÃO",
                         f"ID Solicitação: {sol_id} | aluno: {sol['aluno_nome']} | "
                         f"RA: {sol['ra'] or '—'} | data: {sol['data_solicitada']}",
                         ip=request.remote_addr, conn=conn)

            # A portaria pode ter registrado a saída deste aluno para o mesmo dia por conta
            # própria. 'concluida' entra na checagem junto com 'pendente': antes só a pendente
            # bloqueava, e aprovar uma solicitação esquecida na fila depois de o aluno já ter
            # sido liberado criava uma nova saída pendente para quem não está mais na escola —
            # a portaria passava a ver autorização de saída para um aluno que já saiu.
            #
            # 'nao_realizada' fica de fora de propósito: ela só existe em data que já passou, e
            # não descreve um aluno que saiu.
            # ORDER BY: havendo as duas, a concluída é a que precisa ser avisada.
            if sol['ra']:
                existente = conn.execute(
                    """SELECT status FROM saidas
                       WHERE ra = %s AND data_saida = %s AND status IN ('pendente', 'concluida')
                       ORDER BY (status = 'concluida') DESC LIMIT 1""",
                    (sol['ra'], sol['data_solicitada'])
                ).fetchone()
            else:
                existente = conn.execute(
                    """SELECT status FROM saidas
                       WHERE aluno = %s AND data_saida = %s AND status IN ('pendente', 'concluida')
                       ORDER BY (status = 'concluida') DESC LIMIT 1""",
                    (sol['aluno_id'], sol['data_solicitada'])
                ).fetchone()

            if existente:
                if existente['status'] == 'concluida':
                    flash(f"Solicitação de {sol['aluno_nome']} aprovada, mas ATENÇÃO: este aluno "
                          "já foi liberado nesta data. Nenhuma saída nova foi criada — a portaria "
                          "não deve liberá-lo de novo.", "error")
                else:
                    flash(f"Solicitação de {sol['aluno_nome']} aprovada. Este aluno já tinha uma saída "
                          "pendente para esta data, então nenhuma saída nova foi criada.", "success")
                return redirect("/admin/solicitacoes")

            # Cria a saída real na tabela saidas para a portaria ver — por ra no fluxo novo,
            # mantendo aluno (FK legada) para as solicitações antigas
            # solicitacao_id amarra esta saída à solicitação que a originou: é por ele que a
            # edição feita pelo responsável encontra a saída certa para remover, sem tocar nas
            # que a portaria registrou por conta própria.
            conn.execute("""
                INSERT INTO saidas (aluno, ra, turma, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, status, solicitacao_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pendente', %s)
            """, (
                sol['aluno_id'],
                sol['ra'],
                sol['turma'],
                sol['data_solicitada'],
                sol['horario_solicitado'] or '',
                sol['motivo'] or 'Solicitado pelo responsável',
                session.get('username', 'admin'),
                sol['tipo_saida'] or 'acompanhado',
                sol['acompanhante'] or sol['responsavel_nome'],
                sol_id,
            ))

        flash(f"Saída de {sol['aluno_nome']} aprovada e registrada.", "success")
        return redirect("/admin/solicitacoes")

    @app.route("/admin/solicitacoes/<int:sol_id>/rejeitar", methods=["POST"])
    @admin_required
    def admin_rejeitar_solicitacao(sol_id):
        with get_db() as conn:
            sol_row = conn.execute("""
                SELECT ss.id, ss.ra, r.email AS responsavel_email,
                       a.nome AS nome_legado, a.turma AS turma_legado, a.serie AS serie_legado
                FROM solicitacoes_saida ss
                LEFT JOIN responsaveis r ON r.id = ss.responsavel_id
                LEFT JOIN alunos a ON a.id = ss.aluno_id
                WHERE ss.id = %s AND ss.status = 'aguardando'
            """, (sol_id,)).fetchone()

        if not sol_row:
            flash("Solicitação não encontrada ou já revisada.", "error")
            return redirect("/admin/solicitacoes")

        # Fora da transação: resolve nome/turma do aluno no banco da escola. Mesma razão de
        # /concluir_saida — aquele banco é outro sistema e pode estar lento.
        sol = _resolver_solicitacoes([sol_row])[0]

        with get_db() as conn:
            conn.execute(
                "UPDATE solicitacoes_saida SET status = 'rejeitado', revisado_por = %s, revisado_em = NOW() WHERE id = %s",
                (session['user_id'], sol_id)
            )
            log_operacao(session.get('username'), "REJEITOU SOLICITAÇÃO",
                         f"ID Solicitação: {sol_id} | aluno: {sol['aluno_nome']} | "
                         f"RA: {sol['ra'] or '—'}",
                         ip=request.remote_addr, conn=conn)

        from app.core.mailer import enviar_email_async
        enviar_email_async(
            sol['responsavel_email'],
            "Solicitação de saída — SecureEdu",
            f"""<p>A solicitação de saída de <strong>{escape(sol['aluno_nome'])}</strong>
            foi <strong style="color:#dc2626;">rejeitada</strong> pela escola.
            Entre em contato com a secretaria para mais informações.</p>"""
        )
        flash("Solicitação rejeitada.", "success")
        return redirect("/admin/solicitacoes")

    # ==================== MANUAL ====================
    @app.route("/manual")
    @login_required
    def manual():
        if session.get('role') == 'admin':
            return redirect('/manual/avancado')
        return redirect('/manual/basico')

    @app.route("/manual/basico")
    @login_required
    def manual_basico():
        return render_template("help/manual_basico.html")

    @app.route("/manual/avancado")
    @login_required
    def manual_avancado():
        return render_template("help/manual_avancado.html")

    return app