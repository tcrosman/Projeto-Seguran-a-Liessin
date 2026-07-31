# LGPD Art. 46 (💻 App obligation) — REST API: not currently registered in __init__.py.
# Do NOT call register_rest_routes(app) without first reviewing all auth requirements below.
# All endpoints require @login_required. Write endpoints require @admin_required.

from flask import request, jsonify, session
from app.api.middleware import login_required, admin_required
from app.core.database import get_db
from app.core.validators import normalizar_serie
from app.config import Config
from werkzeug.security import check_password_hash
from datetime import datetime
import secrets
import json

def register_rest_routes(app):
    """Registra todas as rotas da API REST — ver aviso no topo do arquivo antes de ativar."""

    # ==================== AUTENTICAÇÃO API ====================
    @app.route("/api/v1/login", methods=["POST"])
    def api_login():
        """Autentica via JSON e retorna confirmação de sessão"""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON obrigatório'}), 400
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return jsonify({'error': 'Usuário e senha obrigatórios'}), 400

        with get_db() as conn:
            user = conn.execute(
                "SELECT id, role, username, password FROM usuarios WHERE username=%s",
                (username,)
            ).fetchone()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['username'] = user['username']
            return jsonify({
                'success': True,
                'user_id': user['id'],
                'role': user['role'],
                'username': user['username']
            })

        return jsonify({'error': 'Credenciais inválidas'}), 401

    # ==================== ALUNOS ====================
    @app.route("/api/v1/students", methods=["GET"])
    @login_required
    def api_get_students():
        """Lista todos os alunos — requer login, apenas admin vê dados sensíveis"""
        is_admin = session.get('role') == 'admin'
        with get_db() as conn:
            if is_admin:
                rows = conn.execute("""
                    SELECT id, nome, turma, serie, responsaveis, telefone,
                           email_responsavel, data_nascimento, alergias, observacoes
                    FROM alunos ORDER BY serie, turma, nome
                """).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, nome, turma, serie FROM alunos ORDER BY serie, turma, nome"
                ).fetchall()
            return jsonify([dict(row) for row in rows])

    @app.route("/api/v1/students/<int:student_id>", methods=["GET"])
    @login_required
    def api_get_student(student_id):
        """Busca aluno por ID — dados sensíveis apenas para admin"""
        is_admin = session.get('role') == 'admin'
        with get_db() as conn:
            if is_admin:
                row = conn.execute("""
                    SELECT id, nome, turma, serie, responsaveis, telefone,
                           email_responsavel, data_nascimento, alergias, observacoes
                    FROM alunos WHERE id = %s
                """, (student_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, nome, turma, serie FROM alunos WHERE id = %s",
                    (student_id,)
                ).fetchone()

            if not row:
                return jsonify({'error': 'Aluno não encontrado'}), 404
            return jsonify(dict(row))

    @app.route("/api/v1/students/search", methods=["GET"])
    @login_required
    def api_search_students():
        """Busca alunos por nome"""
        nome = request.args.get('nome', '')
        if not nome:
            return jsonify({'error': 'Parâmetro "nome" obrigatório'}), 400

        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, nome, turma, serie FROM alunos WHERE nome ILIKE %s LIMIT 50",
                (f'%{nome}%',)
            ).fetchall()
            return jsonify([dict(row) for row in rows])

    @app.route("/api/v1/students", methods=["POST"])
    @admin_required
    def api_create_student():
        """Cadastra novo aluno — requer admin"""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON obrigatório'}), 400

        required = ['nome', 'turma', 'serie']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'Campo {field} obrigatório'}), 400

        serie_normalizada = normalizar_serie(data['serie'])
        if not serie_normalizada:
            return jsonify({'error': f"Série '{data['serie']}' não reconhecida"}), 400

        with get_db() as conn:
            student_id = conn.execute("""
                INSERT INTO alunos (nome, turma, serie, responsaveis, telefone,
                                   email_responsavel, data_nascimento, alergias, observacoes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                data['nome'], data['turma'], serie_normalizada,
                data.get('responsaveis', ''), data.get('telefone', ''),
                data.get('email_responsavel', ''), data.get('data_nascimento', ''),
                data.get('alergias', ''), data.get('observacoes', '')
            )).fetchone()['id']

        return jsonify({'success': True, 'id': student_id}), 201

    # ==================== SAÍDAS ====================
    @app.route("/api/v1/departures", methods=["POST"])
    @login_required
    def api_register_departure():
        """Registra uma nova saída — requer login"""
        if session.get('role') == 'vigia':
            return jsonify({'error': 'Porteiros não podem registrar saídas'}), 403

        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON obrigatório'}), 400

        required = ['aluno_id', 'horario', 'motivo', 'responsavel_escola', 'tipo_saida']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'Campo {field} obrigatório'}), 400

        aluno_id = data['aluno_id']
        data_saida = data.get('data_saida', datetime.now().strftime("%Y-%m-%d"))

        with get_db() as conn:
            aluno = conn.execute("SELECT id FROM alunos WHERE id = %s", (aluno_id,)).fetchone()
            if not aluno:
                return jsonify({'error': 'Aluno não encontrado'}), 404

            pendente = conn.execute("""
                SELECT COUNT(*) as total FROM saidas
                WHERE aluno = %s AND data_saida = %s AND status = 'pendente'
            """, (aluno_id, data_saida)).fetchone()

            if pendente['total'] > 0:
                return jsonify({'error': 'Aluno já tem saída pendente para esta data'}), 409

            departure_id = conn.execute("""
                INSERT INTO saidas (aluno, data_saida, horario, motivo, responsavel_escola,
                                   tipo_saida, acompanhante, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pendente') RETURNING id
            """, (
                aluno_id, data_saida, data['horario'], data['motivo'],
                data['responsavel_escola'], data['tipo_saida'],
                data.get('acompanhante')
            )).fetchone()['id']

        return jsonify({'success': True, 'id': departure_id}), 201

    @app.route("/api/v1/departures", methods=["GET"])
    @login_required
    def api_get_departures():
        """Lista saídas com filtros — motivo ocultado para vigia"""
        data_param = request.args.get('data', datetime.now().strftime("%Y-%m-%d"))
        status = request.args.get('status', '')
        is_vigia = session.get('role') == 'vigia'

        if is_vigia:
            query = """
                SELECT s.id, a.nome, s.horario, s.tipo_saida, s.acompanhante, s.status
                FROM saidas s JOIN alunos a ON s.aluno = a.id
                WHERE s.data_saida = %s
            """
        else:
            query = """
                SELECT s.id, a.nome, s.horario, s.motivo, s.responsavel_escola,
                       s.tipo_saida, s.acompanhante, s.status
                FROM saidas s JOIN alunos a ON s.aluno = a.id
                WHERE s.data_saida = %s
            """
        params = [data_param]

        if status:
            query += " AND s.status = %s"
            params.append(status)

        query += " ORDER BY s.horario ASC"

        with get_db() as conn:
            rows = conn.execute(query, params).fetchall()
            return jsonify([dict(row) for row in rows])

    @app.route("/api/v1/departures/<int:departure_id>/complete", methods=["PUT"])
    @login_required
    def api_complete_departure(departure_id):
        """Autoriza uma saída — requer login, não disponível para vigia"""
        if session.get('role') == 'vigia':
            return jsonify({'error': 'Porteiros não podem autorizar saídas'}), 403

        with get_db() as conn:
            info = conn.execute("""
                SELECT s.aluno, s.data_saida, s.horario, s.motivo, s.responsavel_escola,
                       a.email_responsavel, a.nome
                FROM saidas s JOIN alunos a ON s.aluno = a.id
                WHERE s.id = %s AND s.status = 'pendente'
            """, (departure_id,)).fetchone()

            if not info:
                return jsonify({'error': 'Saída não encontrada ou já autorizada'}), 404

            conn.execute(
                "UPDATE saidas SET status = 'concluida' WHERE id = %s",
                (departure_id,)
            )

        return jsonify({'success': True, 'message': 'Saída autorizada'})

    # ==================== HISTÓRICO ====================
    @app.route("/api/v1/students/<int:student_id>/history", methods=["GET"])
    @login_required
    def api_student_history(student_id):
        """Histórico de saídas de um aluno — requer login"""
        if session.get('role') == 'vigia':
            return jsonify({'error': 'Acesso negado'}), 403

        with get_db() as conn:
            aluno = conn.execute("SELECT nome FROM alunos WHERE id = %s", (student_id,)).fetchone()
            if not aluno:
                return jsonify({'error': 'Aluno não encontrado'}), 404

            rows = conn.execute("""
                SELECT data_saida, horario, motivo, responsavel_escola, tipo_saida,
                       acompanhante, status
                FROM saidas
                WHERE aluno = %s AND status = 'concluida'
                ORDER BY data_saida DESC, horario DESC
            """, (student_id,)).fetchall()

            return jsonify({
                'student_id': student_id,
                'student_name': aluno['nome'],
                'history': [dict(row) for row in rows]
            })

    # ==================== HEALTH CHECK ====================
    @app.route("/api/v1/health", methods=["GET"])
    def api_health():
        """Verifica se a API está funcionando — público (sem dados pessoais)"""
        try:
            with get_db() as conn:
                conn.execute("SELECT 1")
            return jsonify({
                'status': 'ok',
                'timestamp': datetime.now().isoformat(),
                'version': '1.0.0'
            })
        except Exception as e:
            return jsonify({'status': 'error', 'error': str(e)}), 500

    # ==================== ESTATÍSTICAS ====================
    @app.route("/api/v1/stats", methods=["GET"])
    @login_required
    def api_stats():
        """Estatísticas gerais — requer login"""
        hoje = datetime.now().strftime("%Y-%m-%d")
        with get_db() as conn:
            total_alunos = conn.execute("SELECT COUNT(*) as total FROM alunos").fetchone()['total']
            total_saidas = conn.execute("SELECT COUNT(*) as total FROM saidas").fetchone()['total']
            saidas_hoje = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE data_saida = %s", (hoje,)
            ).fetchone()['total']

            saidas_por_mes = conn.execute("""
                SELECT LEFT(data_saida, 7) as mes, COUNT(*) as total
                FROM saidas GROUP BY mes ORDER BY mes DESC LIMIT 6
            """).fetchall()

        return jsonify({
            'total_students': total_alunos,
            'total_departures': total_saidas,
            'departures_today': saidas_hoje,
            'departures_by_month': [dict(row) for row in saidas_por_mes]
        })

    # REST API uses session auth, not HTML forms — exempt all endpoints from form CSRF.
    # Form-based CSRF still protects the web UI; this only exempts the /api/v1/ views.
    csrf_protect = app.extensions.get('csrf_protect')
    if csrf_protect:
        for endpoint in [
            api_login, api_get_students, api_get_student, api_search_students,
            api_create_student, api_register_departure, api_get_departures,
            api_complete_departure, api_student_history, api_health, api_stats,
        ]:
            csrf_protect.exempt(endpoint)

    return app