from app.repositories.base_repositories import BaseRepository
from app.core.database import get_db
from typing import List, Dict
from datetime import datetime

class LogRepository(BaseRepository):
    """Repositório para logs de auditoria"""
    
    def __init__(self):
        super().__init__('logs_alunos')
    
    def create(self, aluno_id: int, usuario_id: int, acao: str, 
               dados_antigos: str = None, dados_novos: str = None) -> int:
        """Registra um log de auditoria"""
        with get_db() as conn:
            row = conn.execute("""
                INSERT INTO logs_alunos (aluno_id, usuario_id, acao, dados_antigos, dados_novos, data_hora)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                aluno_id, usuario_id, acao, dados_antigos, dados_novos,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )).fetchone()
            return row['id']

    def get_by_student(self, aluno_id: int, limit: int = 100) -> List[Dict]:
        """Retorna logs de um aluno específico"""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM logs_alunos
                WHERE aluno_id = %s
                ORDER BY data_hora DESC
                LIMIT %s
            """, (aluno_id, limit)).fetchall()
            return [dict(row) for row in rows]