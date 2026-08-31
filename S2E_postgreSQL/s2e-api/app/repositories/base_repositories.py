from app.core.database import get_db
from typing import List, Dict, Optional

class BaseRepository:
    """Classe base para todos os repositórios"""
    
    def __init__(self, table_name: str):
        self.table_name = table_name
    
    def get_all(self) -> List[Dict]:
        """Retorna todos os registros"""
        with get_db() as conn:
            rows = conn.execute(f"SELECT * FROM {self.table_name}").fetchall()
            return [dict(row) for row in rows]
    
    def get_by_id(self, id: int) -> Optional[Dict]:
        """Retorna um registro por ID"""
        with get_db() as conn:
            row = conn.execute(
                f"SELECT * FROM {self.table_name} WHERE id = %s", (id,)
            ).fetchone()
            return dict(row) if row else None
    
    def delete(self, id: int) -> bool:
        """Deleta um registro por ID"""
        with get_db() as conn:
            cursor = conn.execute(
                f"DELETE FROM {self.table_name} WHERE id = %s", (id,)
            )
            return cursor.rowcount > 0
    
    def count(self) -> int:
        """Retorna o total de registros"""
        with get_db() as conn:
            row = conn.execute(f"SELECT COUNT(*) as total FROM {self.table_name}").fetchone()
            return row['total'] if row else 0