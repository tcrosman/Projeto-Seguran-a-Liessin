from app.repositories.base_repositories import BaseRepository
from app.core.database import get_db
from typing import List, Dict, Optional

class StudentRepository(BaseRepository):
    """Repositório para alunos"""
    
    def __init__(self):
        super().__init__('alunos')
    
    def get_all_grouped(self) -> List[Dict]:
        """Retorna todos os alunos ordenados por série e turma"""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT id, nome, turma, serie, foto_path 
                FROM alunos 
                ORDER BY 
                    CASE serie 
                        WHEN 'Berçário 1' THEN 1
                        WHEN 'Berçário 2' THEN 2
                        WHEN 'Pré 1' THEN 3
                        WHEN 'Pré 2' THEN 4
                        WHEN '1º ano EF' THEN 5
                        WHEN '2º ano EF' THEN 6
                        WHEN '3º ano EF' THEN 7
                        WHEN '4º ano EF' THEN 8
                        WHEN '5º ano EF' THEN 9
                        WHEN '6º ano EF' THEN 10
                        WHEN '7º ano EF' THEN 11
                        WHEN '8º ano EF' THEN 12
                        WHEN '9º ano EF' THEN 13
                        WHEN '1º ano EM' THEN 14
                        WHEN '2º ano EM' THEN 15
                        WHEN '3º ano EM' THEN 16
                        ELSE 99
                    END, turma, nome
            """).fetchall()
            return [dict(row) for row in rows]
    
    def search_by_name(self, search: str) -> List[Dict]:
        """Busca alunos por nome"""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, nome, turma, serie, foto_path FROM alunos WHERE nome LIKE ?",
                (f'%{search}%',)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def create(self, data: Dict) -> int:
        """Cria um novo aluno"""
        with get_db() as conn:
            cursor = conn.execute("""
                INSERT INTO alunos (
                    nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex,
                    responsaveis, foto_path, telefone, email_responsavel, data_nascimento, 
                    alergias, observacoes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('nome'), data.get('turma'), data.get('serie'),
                data.get('saida_seg'), data.get('saida_ter'), data.get('saida_qua'),
                data.get('saida_qui'), data.get('saida_sex'), data.get('responsaveis'),
                data.get('foto_path'), data.get('telefone'), data.get('email_responsavel'),
                data.get('data_nascimento'), data.get('alergias'), data.get('observacoes')
            ))
            return cursor.lastrowid
    
    def update(self, id: int, data: Dict) -> bool:
        """Atualiza um aluno"""
        with get_db() as conn:
            cursor = conn.execute("""
                UPDATE alunos SET 
                    nome=?, turma=?, serie=?, saida_seg=?, saida_ter=?, saida_qua=?, 
                    saida_qui=?, saida_sex=?, responsaveis=?, foto_path=?, telefone=?,
                    email_responsavel=?, data_nascimento=?, alergias=?, observacoes=?
                WHERE id=?
            """, (
                data.get('nome'), data.get('turma'), data.get('serie'),
                data.get('saida_seg'), data.get('saida_ter'), data.get('saida_qua'),
                data.get('saida_qui'), data.get('saida_sex'), data.get('responsaveis'),
                data.get('foto_path'), data.get('telefone'), data.get('email_responsavel'),
                data.get('data_nascimento'), data.get('alergias'), data.get('observacoes'),
                id
            ))
            return cursor.rowcount > 0
    
    def has_pending_departures(self, student_id: int) -> bool:
        """Verifica se aluno tem saídas pendentes"""
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE aluno = ? AND status = 'pendente'",
                (student_id,)
            ).fetchone()
            return row['total'] > 0 if row else False