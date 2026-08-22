from flask import Flask, render_template, request, redirect, session, flash, send_from_directory, jsonify
from app.api.middleware import login_required, admin_required
from app.core.database import get_db, expirar_saidas_nao_liberadas
from app.core.audit_logger import log_operacao
from app.core.cache import TTLCache
from app.services.school_directory import get_school_directory
from app.config import Config
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta
from collections import defaultdict
import os
import re


_cache_diretorio = TTLCache(ttl_seconds=300)


def _buscar_alunos_com_cache(ras):
    """Resolve ra -> dados do aluno via school_directory, com cache curto (5 min) e fallback
    para o último dado conhecido se a consulta externa estiver indisponível no momento."""
    if not ras:
        return {}
    faltando = [ra for ra in set(ras) if _cache_diretorio.get(ra) is None]
    if faltando:
        try:
            frescos = get_school_directory().get_students_by_ras(faltando)
            for ra, info in frescos.items():
                _cache_diretorio.set(ra, info)
        except Exception as e:
            print(f"[SCHOOL_DIRECTORY] Consulta indisponível, usando cache: {e}")
    resultado = {}
    for ra in set(ras):
        info = _cache_diretorio.get(ra) or _cache_diretorio.get_stale(ra)
        if info:
            resultado[ra] = info
    return resultado


def _resolver_dados_saidas(rows):
    """Enriquece linhas de `saidas` com nome/foto/turma/série do aluno, prontas para o template.

    Linhas novas (com ra): resolvidas ao vivo via school_directory — nada disso é persistido,
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
    
    # ==================== RATE LIMIT ====================
    # Bloqueia após 5 senhas erradas; libera automaticamente após 5 minutos.
    _falhas_por_ip   = defaultdict(list)  # ip -> lista de timestamps de falhas
    _bloqueios_por_ip = {}                # ip -> datetime de liberação

    _MAX_FALHAS   = 5
    _JANELA_MIN   = 15   # janela de tempo para contar falhas (minutos)
    _BLOQUEIO_MIN = 5    # duração do bloqueio após atingir o limite (minutos)

    def is_rate_limited(ip):
        """Retorna True se o IP está bloqueado por excesso de senhas erradas."""
        agora = datetime.now()
        if ip in _bloqueios_por_ip:
            if agora < _bloqueios_por_ip[ip]:
                return True
            del _bloqueios_por_ip[ip]
            _falhas_por_ip.pop(ip, None)
        return False

    def registrar_falha(ip):
        """Conta falha de login; bloqueia por 5 min após 5 tentativas erradas."""
        agora = datetime.now()
        _falhas_por_ip[ip] = [t for t in _falhas_por_ip[ip] if agora - t < timedelta(minutes=_JANELA_MIN)]
        _falhas_por_ip[ip].append(agora)
        if len(_falhas_por_ip[ip]) >= _MAX_FALHAS:
            _bloqueios_por_ip[ip] = agora + timedelta(minutes=_BLOQUEIO_MIN)
            _falhas_por_ip.pop(ip, None)
    
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
        
        if is_rate_limited(ip):
            return render_template("auth/login.html", erro="Muitas tentativas de login. Tente novamente em 5 minutos.")

        if request.method == "POST":
            with get_db() as conn:
                user = conn.execute(
                    "SELECT id, role, username, password FROM usuarios WHERE username=%s",
                    (request.form["u"],)
                ).fetchone()

            if user and check_password_hash(user['password'], request.form["s"]):
                # Login OK — zera contadores deste IP
                _falhas_por_ip.pop(ip, None)
                _bloqueios_por_ip.pop(ip, None)
                log_operacao(user['username'], "LOGIN_SUCESSO", f"role={user['role']}", ip=ip)
                session['user_id'] = user['id']
                session['role'] = user['role']
                session['username'] = user['username']
                return redirect("/inicio")
            else:
                registrar_falha(ip)
                tentativa_usuario = request.form.get("u", "")
                log_operacao(tentativa_usuario or "desconhecido", "LOGIN_FALHA", "senha incorreta ou usuário inexistente", ip=ip)
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
    
    @app.route("/logout")
    def logout():
        log_operacao(session.get('username', 'desconhecido'), "LOGOUT", "", ip=request.remote_addr)
        session.clear()
        return redirect("/")
    
    @app.route("/esqueci_senha", methods=["GET", "POST"])
    def esqueci_senha():
        from app.core.mailer import enviar_email
        mensagem = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            link_para_enviar = None

            # Bloco de DB isolado: gera e persiste o token antes de qualquer envio
            with get_db() as conn:
                user = conn.execute("SELECT id FROM usuarios WHERE LOWER(email) = %s", (email,)).fetchone()
                if user:
                    import secrets
                    token = secrets.token_urlsafe(32)
                    expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
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
                enviado = enviar_email(email, "Redefinição de senha — SecureEdu", corpo)
                if not enviado:
                    print(f"[FALLBACK] Email não enviado. Use este link manualmente: {link_para_enviar}")

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
                if datetime.now() <= expires_at:
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
    
    # ==================== ALUNOS ====================
    @app.route("/cadastro_aluno")
    @login_required
    def cadastro_aluno():
        busca = request.args.get("busca")
        _ordem_serie = """
            CASE serie
                WHEN 'Berçário 1' THEN 1  WHEN 'Berçário 2' THEN 2
                WHEN 'Pré 1'      THEN 3  WHEN 'Pré 2'      THEN 4
                WHEN '1º ano EF'  THEN 5  WHEN '2º ano EF'  THEN 6
                WHEN '3º ano EF'  THEN 7  WHEN '4º ano EF'  THEN 8
                WHEN '5º ano EF'  THEN 9  WHEN '6º ano EF'  THEN 10
                WHEN '7º ano EF'  THEN 11 WHEN '8º ano EF'  THEN 12
                WHEN '9º ano EF'  THEN 13
                WHEN '1º ano EM'  THEN 14 WHEN '2º ano EM'  THEN 15
                WHEN '3º ano EM'  THEN 16 ELSE 99
            END, turma, nome
        """
        with get_db() as conn:
            if busca:
                rows = conn.execute(
                    f"SELECT id, nome, turma, serie, foto_path FROM alunos WHERE nome ILIKE %s ORDER BY {_ordem_serie}",
                    (f'%{busca}%',)
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT id, nome, turma, serie, foto_path FROM alunos ORDER BY {_ordem_serie}"
                ).fetchall()

        from itertools import groupby
        alunos = [dict(row) for row in rows]
        grupos = [
            {'serie': serie, 'turma': turma, 'alunos': list(membros)}
            for (serie, turma), membros in groupby(alunos, key=lambda a: (a['serie'], a['turma']))
        ]
        return render_template("students/list_of_students.html", grupos=grupos, busca=busca, series=Config.SERIES)
    
    @app.route("/editar_aluno/<int:id_aluno>", methods=["GET", "POST"])
    @admin_required
    def editar_aluno(id_aluno):
        log_operacao(session.get('username'), "ACESSO_DADOS_SAUDE", f"editou aluno ID={id_aluno}", ip=request.remote_addr)
        from app.core.validators import normalizar_serie

        with get_db() as conn:
            aluno = conn.execute("SELECT * FROM alunos WHERE id = %s", (id_aluno,)).fetchone()
            if not aluno:
                flash("Aluno não encontrado", "error")
                return redirect("/cadastro_aluno")

            if request.method == "POST":
                nome = request.form.get("nome", "").strip()
                turma = request.form.get("turma", "").strip()
                serie = request.form.get("serie", "").strip()

                if not nome or not turma or not serie:
                    flash("Preencher nome, turma e série", "error")
                else:
                    serie_norm = normalizar_serie(serie) or serie
                    conn.execute("""
                        UPDATE alunos SET
                            nome=%s, turma=%s, serie=%s, saida_seg=%s, saida_ter=%s, saida_qua=%s, saida_qui=%s, saida_sex=%s,
                            responsaveis=%s, telefone=%s, data_nascimento=%s, alergias=%s, observacoes=%s
                        WHERE id=%s
                    """, (
                        nome, turma, serie_norm,
                        request.form.get("saida_seg", ""),
                        request.form.get("saida_ter", ""),
                        request.form.get("saida_qua", ""),
                        request.form.get("saida_qui", ""),
                        request.form.get("saida_sex", ""),
                        request.form.get("responsaveis", ""),
                        request.form.get("telefone", ""),
                        request.form.get("data_nascimento", ""),
                        request.form.get("alergias", ""),
                        request.form.get("observacoes", ""),
                        id_aluno
                    ))
                    flash("Aluno atualizado!", "success")
                    return redirect(f"/historico_aluno/{id_aluno}")
            
            return render_template("students/edit_students.html", aluno=dict(aluno), series=Config.SERIES)
    
    @app.route("/deletar_aluno/<int:id_aluno>")
    @admin_required
    def deletar_aluno(id_aluno):
        with get_db() as conn:
            pendentes = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE aluno = %s AND status = 'pendente'",
                (id_aluno,)
            ).fetchone()['total']

            if pendentes > 0:
                flash("Não é possível remover aluno com saídas pendentes", "error")
                return redirect("/cadastro_aluno")

            conn.execute("DELETE FROM alunos WHERE id = %s", (id_aluno,))
        
        log_operacao(session.get('username'), "EXCLUIU ALUNO", f"ID: {id_aluno}")
        flash("Aluno removido!", "success")
        return redirect("/cadastro_aluno")
    
    @app.route("/historico_aluno/<int:id_aluno>")
    @login_required
    def historico_aluno(id_aluno):
        log_operacao(session.get('username'), "ACESSO_DADOS_SAUDE", f"visualizou histórico aluno ID={id_aluno}", ip=request.remote_addr)
        with get_db() as conn:
            aluno = conn.execute("SELECT * FROM alunos WHERE id = %s", (id_aluno,)).fetchone()
            if not aluno:
                flash("Aluno não encontrado", "error")
                return redirect("/cadastro_aluno")

            expirar_saidas_nao_liberadas(conn)
            historico = conn.execute("""
                SELECT data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, status
                FROM saidas
                WHERE aluno = %s
                ORDER BY data_saida DESC, horario DESC
            """, (id_aluno,)).fetchall()
        
        return render_template("students/history_students.html", aluno=dict(aluno), historico=[dict(h) for h in historico])
    
    @app.route("/historico", methods=["GET"])
    @login_required
    def historico_geral():
        nome = request.args.get("nome", "").strip()
        resultados = []
        
        if nome:
            with get_db() as conn:
                expirar_saidas_nao_liberadas(conn)
                rows = conn.execute("""
                    SELECT a.id, a.nome, a.turma, a.serie, a.foto_path,
                           s.data_saida, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status
                    FROM alunos a
                    LEFT JOIN saidas s ON a.id = s.aluno
                    WHERE a.nome ILIKE %s
                    ORDER BY s.data_saida DESC, s.horario DESC
                """, (f'%{nome}%',)).fetchall()
                resultados = [dict(row) for row in rows]
        
        return render_template("departures/history_of_departures.html", resultados=resultados, nome=nome)
    
    # ==================== SAÍDAS ====================
    @app.route("/portaria/buscar_aluno")
    @login_required
    def portaria_buscar_aluno():
        """Busca alunos por nome/RA parcial para o formulário de registrar saída.
        Dado vem direto da consulta externa — nada aqui é persistido no S2E."""
        query = request.args.get("q", "")
        resultados = get_school_directory().search_students(query)
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
            data_saida = request.form.get("data_saida", datetime.now().strftime("%Y-%m-%d"))
            horario = request.form.get("horario")
            motivo = request.form.get("motivo")
            responsavel_escola = request.form.get("responsavel_escola")
            tipo_saida = request.form.get("tipo_saida")
            acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None

            if not ra or not horario or not motivo or not responsavel_escola or not tipo_saida:
                flash("Todos os campos são obrigatórios, incluindo a seleção do aluno na busca!", "error")
            else:
                aluno = get_school_directory().get_student(ra)
                if not aluno:
                    flash("Aluno não encontrado para o RA informado. Busque novamente pelo nome ou RA.", "error")
                else:
                    documento_path = None
                    doc = request.files.get('documento')
                    if doc and doc.filename != '':
                        from werkzeug.utils import secure_filename
                        from app.core.validators import allowed_file
                        if allowed_file(doc.filename):
                            filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{doc.filename}")
                            upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                            os.makedirs(os.path.join(upload_folder, 'documents'), exist_ok=True)
                            doc.save(os.path.join(upload_folder, 'documents', filename))
                            documento_path = os.path.join('documents', filename)

                    with get_db() as conn:
                        pendente = conn.execute(
                            "SELECT COUNT(*) as total FROM saidas WHERE ra = %s AND data_saida = %s AND status = 'pendente'",
                            (ra, data_saida)
                        ).fetchone()['total']

                        if pendente > 0:
                            flash("Este aluno já tem uma saída pendente para hoje!", "error")
                        else:
                            conn.execute("""
                                INSERT INTO saidas (ra, turma, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, documento_path, status)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pendente')
                            """, (ra, aluno.get('turma'), data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, documento_path))
                            log_operacao(session.get('username'), "REGISTROU SAÍDA", f"RA: {ra}")
                            flash("Saída registrada!", "success")
                            return redirect("/saidas")

        return render_template("departures/register.html",
                               ra_selecionado=ra_pre_selecionado,
                               today=datetime.now().strftime("%Y-%m-%d"))
    
    @app.route("/saidas")
    @login_required
    def lista_saidas():
        data_selecionada = request.args.get("data", datetime.now().strftime("%Y-%m-%d"))
        busca = request.args.get("busca", "").strip()

        with get_db() as conn:
            # Limpar saídas antigas
            conn.execute("DELETE FROM saidas WHERE status = 'concluida' AND data_saida < TO_CHAR(CURRENT_DATE - INTERVAL '30 days', 'YYYY-MM-DD')")
            # Marca como não realizadas as saídas aprovadas cujo dia já passou sem liberação
            expirar_saidas_nao_liberadas(conn)

            rows = conn.execute("""
                SELECT s.id, s.ra, s.turma, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida,
                       s.acompanhante, s.documento_path, s.status,
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

            saida = _resolver_dados_saidas([saida_row])[0]

            if request.method == "POST":
                horario = request.form.get("horario")
                motivo = request.form.get("motivo")
                responsavel_escola = request.form.get("responsavel_escola")
                tipo_saida = request.form.get("tipo_saida")
                acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None

                conn.execute("""
                    UPDATE saidas SET horario=%s, motivo=%s, responsavel_escola=%s, tipo_saida=%s, acompanhante=%s
                    WHERE id=%s
                """, (horario, motivo, responsavel_escola, tipo_saida, acompanhante, id_saida))
                flash("Saída atualizada!", "success")
                return redirect("/saidas")
            
            return render_template("departures/edit_exits.html", saida=saida)

    @app.route("/concluir_saida/<int:id_saida>")
    @login_required
    def concluir_saida(id_saida):
        with get_db() as conn:
            conn.execute(
                "UPDATE saidas SET status = 'concluida', usuario_autorizou = %s, liberado_em = NOW() WHERE id = %s",
                (session['user_id'], id_saida)
            )

            saida = conn.execute("""
                SELECT s.data_saida, s.horario, s.ra, a.id AS aluno_id_legado, a.nome AS aluno_nome_legado
                FROM saidas s
                LEFT JOIN alunos a ON a.id = s.aluno
                WHERE s.id = %s
            """, (id_saida,)).fetchone()

            if saida and saida['ra']:
                info = get_school_directory().get_student(saida['ra'])
                nome_aluno = info['nome'] if info else f"RA {saida['ra']}"
                emails = get_school_directory().get_guardian_emails_for_ra(saida['ra'])
            elif saida:
                nome_aluno = saida['aluno_nome_legado']
                responsaveis = conn.execute("""
                    SELECT r.email
                    FROM vinculos_pais_alunos v
                    JOIN responsaveis r ON r.id = v.responsavel_id
                    WHERE v.aluno_id = %s
                """, (saida['aluno_id_legado'],)).fetchall()
                emails = [r['email'] for r in responsaveis]
            else:
                nome_aluno, emails = None, []

        if saida:
            from app.core.mailer import enviar_email
            for email in emails:
                enviar_email(
                    email,
                    "Saída liberada — SecureEdu",
                    f"""<p>Olá! A saída de <strong>{nome_aluno}</strong> foi
                    <strong style="color:#16a34a;">liberada pela segurança da escola</strong>
                    hoje às <strong>{saida['horario']}</strong>.</p>"""
                )

        log_operacao(session.get('username'), "CONCLUIU SAÍDA", f"ID Saída: {id_saida}")
        flash("Saída autorizada!", "success")
        return redirect("/saidas")
    
    # ==================== ADMIN ====================

    @app.route("/cadastro_massa", methods=["GET", "POST"])
    @admin_required
    def cadastro_massa():
        from app.core.audit_logger import log_operacao
        import pandas as pd
        from werkzeug.utils import secure_filename
        import os
        from app.core.validators import normalizar_serie
        from pathlib import Path

        mensagem_erro = ""
        horarios_padrao = {}

        # Carregar horários padrão para o frontend
        with get_db() as conn:
            rows = conn.execute("SELECT serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao").fetchall()
            for row in rows:
                horarios_padrao[row['serie']] = {
                    'seg': row['saida_seg'] or '',
                    'ter': row['saida_ter'] or '',
                    'qua': row['saida_qua'] or '',
                    'qui': row['saida_qui'] or '',
                    'sex': row['saida_sex'] or ''
                }

        if request.method == "POST":
            # Verificar se é upload de Excel ou cadastro individual
            if "arquivo_excel" in request.files and request.files["arquivo_excel"].filename != "":
                # Importação Excel
                arquivo = request.files["arquivo_excel"]
                try:
                    df = pd.read_excel(arquivo)
                    df.columns = df.columns.str.lower().str.strip()
                    
                    erros = []
                    rows = []
                    for i, linha in df.iterrows():
                        serie_raw = str(linha["serie"]).strip()
                        serie = normalizar_serie(serie_raw)
                        if not serie:
                            erros.append(f"Linha {i + 2}: série não reconhecida → \"{serie_raw}\"")
                        else:
                            rows.append((
                                str(linha["nome"]).strip(),
                                str(linha["turma"]).strip(),
                                serie,
                                str(linha.get("saida_seg", "")).strip(),
                                str(linha.get("saida_ter", "")).strip(),
                                str(linha.get("saida_qua", "")).strip(),
                                str(linha.get("saida_qui", "")).strip(),
                                str(linha.get("saida_sex", "")).strip(),
                                str(linha.get("responsaveis", "")).strip()
                            ))
                    
                    if erros:
                        series_str = ", ".join(Config.SERIES)
                        mensagem_erro = "Série não reconhecida nas seguintes linhas:<br>" + "<br>".join(erros) + f"<br><br><strong>Valores aceitos:</strong> {series_str}"
                    else:
                        # Processar fotos
                        foto_map = {}
                        fotos_files = request.files.getlist('fotos')
                        for foto_file in fotos_files:
                            if not foto_file or foto_file.filename == '':
                                continue
                            nome_arquivo = Path(foto_file.filename).name
                            ext = nome_arquivo.rsplit('.', 1)[-1].lower() if '.' in nome_arquivo else ''
                            if ext not in {'png', 'jpg', 'jpeg'}:
                                continue
                            from app.core.normalize import normalizar_nome_para_foto
                            nome_sem_ext = Path(nome_arquivo).stem
                            nome_normalizado = normalizar_nome_para_foto(nome_sem_ext)
                            foto_map[nome_normalizado] = foto_file.read()
                        
                        upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                        from app.core.normalize import normalizar_nome_para_foto
                        
                        with get_db() as conn:
                            alunos_inseridos = 0
                            for row in rows:
                                aluno_id = conn.execute("""
                                    INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                                """, row).fetchone()['id']
                                nome_aluno = row[0]
                                nome_normalizado = normalizar_nome_para_foto(nome_aluno)
                                if nome_normalizado in foto_map:
                                    filename = secure_filename(f"{aluno_id}_{nome_aluno}.jpg")
                                    os.makedirs(os.path.join(upload_folder, 'photos'), exist_ok=True)
                                    with open(os.path.join(upload_folder, 'photos', filename), 'wb') as f:
                                        f.write(foto_map[nome_normalizado])
                                    foto_path = os.path.join('photos', filename)
                                    conn.execute("UPDATE alunos SET foto_path = %s WHERE id = %s", (foto_path, aluno_id))
                                alunos_inseridos += 1
                        
                        log_operacao(session.get('username'), "IMPORTOU EXCEL", f"{alunos_inseridos} alunos")
                        flash(f"{alunos_inseridos} alunos importados!", "success")
                        return redirect("/cadastro_aluno")
                        
                except Exception as e:
                    mensagem_erro = f"Erro ao processar o arquivo: {str(e)}"
            
            else:
                # Cadastro individual
                nome = request.form.get("nome", "").strip()
                turma = request.form.get("turma", "").strip()
                serie = request.form.get("serie", "").strip()
                responsaveis = request.form.get("responsaveis", "").strip()
                
                if not nome or not turma or not serie:
                    mensagem_erro = "Preencher nome, turma e série é obrigatório!"
                else:
                    # Buscar horários padrão
                    with get_db() as conn:
                        padrao = conn.execute(
                            "SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = %s",
                            (serie,)
                        ).fetchone()

                    foto_path = None
                    if 'foto' in request.files:
                        file = request.files['foto']
                        if file and file.filename != '':
                            from app.core.validators import allowed_file
                            if allowed_file(file.filename):
                                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                                upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                                os.makedirs(os.path.join(upload_folder, 'photos'), exist_ok=True)
                                file.save(os.path.join(upload_folder, 'photos', filename))
                                foto_path = os.path.join('photos', filename)

                    with get_db() as conn:
                        conn.execute("""
                            INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            nome, turma, serie,
                            request.form.get("saida_seg") or (padrao['saida_seg'] if padrao else ''),
                            request.form.get("saida_ter") or (padrao['saida_ter'] if padrao else ''),
                            request.form.get("saida_qua") or (padrao['saida_qua'] if padrao else ''),
                            request.form.get("saida_qui") or (padrao['saida_qui'] if padrao else ''),
                            request.form.get("saida_sex") or (padrao['saida_sex'] if padrao else ''),
                            responsaveis, foto_path
                        ))
                    log_operacao(session.get('username'), "CADASTROU ALUNO", f"Nome: {nome}")
                    flash(f"Aluno {nome} cadastrado com sucesso!", "success")
                    return redirect("/cadastro_aluno")
        
        return render_template("students/bulk.html", erro=mensagem_erro, series=Config.SERIES, horarios_padrao=horarios_padrao)

    @app.route("/configuracoes")
    @admin_required
    def configuracoes():
        hoje = datetime.now().strftime("%Y-%m-%d")
        with get_db() as conn:
            total_alunos = conn.execute("SELECT COUNT(*) as total FROM alunos").fetchone()['total']
            total_usuarios = conn.execute("SELECT COUNT(*) as total FROM usuarios").fetchone()['total']
            total_saidas = conn.execute("SELECT COUNT(*) as total FROM saidas").fetchone()['total']
            saidas_hoje = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE data_saida = %s", (hoje,)
            ).fetchone()['total']

        return render_template("admin/settings.html",
                               total_alunos=total_alunos,
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
                    flash(f"Usuário {validated['username']} criado!", "success")
                except Exception as e:
                    flash(f"Erro: {e}", "error")
            return redirect("/novo")
        
        usuarios = repo.get_all_without_passwords()
        return render_template("admin/users.html", usuarios=usuarios)
    
    @app.route("/deletar_usuario/<int:id_usuario>")
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
        flash("Usuário deletado!", "success")
        return redirect("/novo")
    
    @app.route("/configurar_horarios", methods=["GET", "POST"])
    @admin_required
    def configurar_horarios():
        if request.method == "POST":
            with get_db() as conn:
                for serie in Config.SERIES:
                    seg = request.form.get(f"seg_{serie}", "")
                    ter = request.form.get(f"ter_{serie}", "")
                    qua = request.form.get(f"qua_{serie}", "")
                    qui = request.form.get(f"qui_{serie}", "")
                    sex = request.form.get(f"sex_{serie}", "")
                    conn.execute("""
                        INSERT INTO horarios_padrao
                            (serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (serie) DO UPDATE SET
                            saida_seg = EXCLUDED.saida_seg,
                            saida_ter = EXCLUDED.saida_ter,
                            saida_qua = EXCLUDED.saida_qua,
                            saida_qui = EXCLUDED.saida_qui,
                            saida_sex = EXCLUDED.saida_sex
                    """, (serie, seg, ter, qua, qui, sex))
            flash("Horários salvos!", "success")
            return redirect("/configurar_horarios")
        
        with get_db() as conn:
            rows = conn.execute("SELECT serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao").fetchall()
        
        horarios = {}
        for row in rows:
            horarios[row['serie']] = {
                'seg': row['saida_seg'] or '',
                'ter': row['saida_ter'] or '',
                'qua': row['saida_qua'] or '',
                'qui': row['saida_qui'] or '',
                'sex': row['saida_sex'] or ''
            }
        
        return render_template("admin/schedules.html", series=Config.SERIES, horarios=horarios)
    
    # ==================== ARQUIVOS ESTÁTICOS E UPLOADS ====================
    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        # Aceita tanto sessão de funcionário quanto de responsável
        if 'user_id' not in session and 'pai_id' not in session:
            return redirect('/')
        upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
        return send_from_directory(upload_folder, filename)
    
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
                flash(f"Conta de {resp['nome']} aprovada.", "success")
        return redirect("/admin/responsaveis")

    @app.route("/admin/responsaveis/<int:resp_id>/bloquear", methods=["POST"])
    @admin_required
    def admin_bloquear_responsavel(resp_id):
        with get_db() as conn:
            resp = conn.execute("SELECT nome FROM responsaveis WHERE id = %s", (resp_id,)).fetchone()
            if resp:
                conn.execute("UPDATE responsaveis SET status = 'bloqueado' WHERE id = %s", (resp_id,))
                flash(f"Conta de {resp['nome']} bloqueada.", "success")
        return redirect("/admin/responsaveis")

    # ==================== ADMIN: SOLICITAÇÕES DE SAÍDA ====================

    @app.route("/admin/solicitacoes")
    @admin_required
    def admin_solicitacoes():
        with get_db() as conn:
            conn.execute("""
                DELETE FROM solicitacoes_saida
                WHERE status IN ('aprovado', 'rejeitado')
                  AND criado_em < NOW() - INTERVAL '30 days'
            """)
            expirar_saidas_nao_liberadas(conn)
            rows = conn.execute("""
                SELECT ss.id, ss.data_solicitada, ss.horario_solicitado, ss.motivo, ss.status,
                       ss.tipo_saida, ss.acompanhante,
                       a.nome AS aluno_nome, a.turma, a.serie,
                       r.nome AS responsavel_nome, r.email AS responsavel_email,
                       s.status AS saida_status
                FROM solicitacoes_saida ss
                JOIN alunos a ON a.id = ss.aluno_id
                JOIN responsaveis r ON r.id = ss.responsavel_id
                LEFT JOIN saidas s ON s.aluno = ss.aluno_id AND s.data_saida = ss.data_solicitada
                ORDER BY ss.criado_em DESC
                LIMIT 100
            """).fetchall()
        aguardando = [r for r in rows if r['status'] == 'aguardando']
        historico  = [r for r in rows if r['status'] != 'aguardando']
        return render_template("admin/solicitacoes.html", aguardando=aguardando, historico=historico)

    @app.route("/admin/solicitacoes/<int:sol_id>/aprovar", methods=["POST"])
    @admin_required
    def admin_aprovar_solicitacao(sol_id):
        with get_db() as conn:
            sol = conn.execute("""
                SELECT ss.aluno_id, ss.data_solicitada, ss.horario_solicitado, ss.motivo,
                       ss.tipo_saida, ss.acompanhante,
                       r.nome AS responsavel_nome, r.email AS responsavel_email, a.nome AS aluno_nome
                FROM solicitacoes_saida ss
                JOIN responsaveis r ON r.id = ss.responsavel_id
                JOIN alunos a ON a.id = ss.aluno_id
                WHERE ss.id = %s AND ss.status = 'aguardando'
            """, (sol_id,)).fetchone()

            if not sol:
                flash("Solicitação não encontrada ou já revisada.", "error")
                return redirect("/admin/solicitacoes")

            # Aprova a solicitação
            conn.execute(
                "UPDATE solicitacoes_saida SET status = 'aprovado', revisado_por = %s, revisado_em = NOW() WHERE id = %s",
                (session['user_id'], sol_id)
            )
            # Cria a saída real na tabela saidas para a portaria ver
            conn.execute("""
                INSERT INTO saidas (aluno, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pendente')
            """, (
                sol['aluno_id'],
                sol['data_solicitada'],
                sol['horario_solicitado'] or '',
                sol['motivo'] or 'Solicitado pelo responsável',
                session.get('username', 'admin'),
                sol['tipo_saida'] or 'acompanhado',
                sol['acompanhante'] or sol['responsavel_nome'],
            ))

        flash(f"Saída de {sol['aluno_nome']} aprovada e registrada.", "success")
        return redirect("/admin/solicitacoes")

    @app.route("/admin/solicitacoes/<int:sol_id>/rejeitar", methods=["POST"])
    @admin_required
    def admin_rejeitar_solicitacao(sol_id):
        with get_db() as conn:
            sol = conn.execute("""
                SELECT ss.id, r.email AS responsavel_email, a.nome AS aluno_nome
                FROM solicitacoes_saida ss
                JOIN responsaveis r ON r.id = ss.responsavel_id
                JOIN alunos a ON a.id = ss.aluno_id
                WHERE ss.id = %s AND ss.status = 'aguardando'
            """, (sol_id,)).fetchone()

            if not sol:
                flash("Solicitação não encontrada ou já revisada.", "error")
                return redirect("/admin/solicitacoes")

            conn.execute(
                "UPDATE solicitacoes_saida SET status = 'rejeitado', revisado_por = %s, revisado_em = NOW() WHERE id = %s",
                (session['user_id'], sol_id)
            )

        from app.core.mailer import enviar_email
        enviar_email(
            sol['responsavel_email'],
            "Solicitação de saída — SecureEdu",
            f"""<p>A solicitação de saída de <strong>{sol['aluno_nome']}</strong>
            foi <strong style="color:#dc2626;">rejeitada</strong> pela escola.
            Entre em contato com a secretaria para mais informações.</p>"""
        )
        flash(f"Solicitação rejeitada.", "success")
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