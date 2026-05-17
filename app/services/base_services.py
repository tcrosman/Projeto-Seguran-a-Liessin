from typing import Optional, Dict, Any, List
from app.core.exceptions import ValidationError, NotFoundError

class BaseService:
    """Classe base para todos os serviços"""
    
    def __init__(self, repository):
        self.repository = repository
    
    def get_all(self) -> List[Dict]:
        """Retorna todos os registros"""
        return self.repository.get_all()
    
    def get_by_id(self, id: int) -> Optional[Dict]:
        """Retorna um registro por ID"""
        result = self.repository.get_by_id(id)
        if not result:
            raise NotFoundError(f"Registro com ID {id} não encontrado")
        return result
    
    def delete(self, id: int) -> bool:
        """Deleta um registro"""
        if not self.repository.delete(id):
            raise NotFoundError(f"Registro com ID {id} não encontrado")
        return True