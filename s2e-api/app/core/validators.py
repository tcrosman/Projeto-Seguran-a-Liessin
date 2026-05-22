from app.config import Config

def normalizar_serie(valor):
    """Converte variações de série para o nome oficial"""
    valor_str = str(valor).strip()
    chave = valor_str.lower()
    
    if valor_str in Config.SERIES:
        return valor_str
    
    return Config.NORMALIZE_SERIE.get(chave)

def allowed_file(filename):
    """Valida extensão de arquivo"""
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in Config.ALLOWED_EXTENSIONS