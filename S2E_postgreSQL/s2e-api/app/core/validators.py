import unicodedata
from app.config import Config


def _chave_normalizada(valor):
    """Remove acentos, símbolos ordinais (º ª °) e normaliza espaços."""
    # Ordinais primeiro: NFKD converteria º→o e ª→a antes da remoção
    sem_ordinal = str(valor).replace('º', '').replace('ª', '').replace('°', '')
    nfkd = unicodedata.normalize('NFKD', sem_ordinal)
    sem_acento = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ' '.join(sem_acento.lower().split())


def normalizar_serie(valor):
    """Converte variações de série para o nome oficial"""
    valor_str = str(valor).strip()

    if valor_str in Config.SERIES:
        return valor_str

    return Config.NORMALIZE_SERIE.get(_chave_normalizada(valor_str))


def allowed_file(filename):
    """Valida extensão de arquivo (primeiro filtro)"""
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in Config.ALLOWED_EXTENSIONS


def is_valid_image(file_stream):
    """
    Verifica se o arquivo é realmente uma imagem válida via magic bytes.
    Retorna True se for imagem, False caso contrário.
    """
    posicao = file_stream.tell()
    file_stream.seek(0)
    header = file_stream.read(12)
    file_stream.seek(posicao)

    return (
        header[:3] == b'\xff\xd8\xff' or           # JPEG
        header[:8] == b'\x89PNG\r\n\x1a\n' or      # PNG
        header[:6] in (b'GIF87a', b'GIF89a')        # GIF
    )


def is_valid_pdf(file_stream):
    """
    Verifica se o arquivo é realmente um PDF válido.
    PDFs começam com %PDF (bytes: 25 50 44 46)
    """
    posicao = file_stream.tell()
    file_stream.seek(0)
    
    # Lê os primeiros 4 bytes
    cabecalho = file_stream.read(4)
    
    # Restaura posição
    file_stream.seek(posicao)
    
    # PDF deve começar com %PDF
    return cabecalho == b'%PDF'


def validar_tipo_arquivo(file_stream, extensoes_permitidas, mimes_permitidos):
    """
    Verifica se o arquivo é realmente do tipo que diz ser.
    
    Args:
        file_stream: objeto do arquivo (request.files['...'])
        extensoes_permitidas: tuple de extensões ('jpg', 'png', 'pdf')
        mimes_permitidos: tuple de MIME types ('image/jpeg', 'image/png', 'application/pdf')
    
    Returns:
        (bool, str) - (é válido, mensagem de erro)
    """
    nome = file_stream.filename
    if not nome or '.' not in nome:
        return False, "Nome de arquivo inválido"
    
    # 1. Verifica extensão
    ext = nome.rsplit('.', 1)[1].lower()
    if ext not in extensoes_permitidas:
        return False, f"Extensão .{ext} não permitida"
    
    # 2. Verifica MIME type real (lê o conteúdo)
    file_stream.seek(0)
    try:
        import magic
        mime = magic.from_buffer(file_stream.read(1024), mime=True)
        file_stream.seek(0)
        
        if mime not in mimes_permitidos:
            return False, f"Tipo de arquivo real '{mime}' não corresponde à extensão .{ext}"
        
        return True, "OK"
    except Exception as e:
        # Se magic falhar, pelo menos verifica extensão
        file_stream.seek(0)
        return True, "OK (validação básica)"


def validar_upload_imagem(file):
    """
    Valida completamente um upload de imagem:
    - Extensão permitida
    - Conteúdo é realmente uma imagem
    - Tamanho razoável (já configurado no Flask)
    """
    if not file or file.filename == '':
        return False, "Nenhum arquivo selecionado"
    
    if not allowed_file(file.filename):
        return False, f"Tipo de arquivo não permitido. Use: {', '.join(Config.ALLOWED_EXTENSIONS)}"
    
    if not is_valid_image(file):
        return False, "Arquivo não é uma imagem válida (pode estar corrompido ou ser falso)"
    
    return True, "OK"


def validar_upload_documento(file):
    """
    Valida completamente um upload de documento:
    - Extensão permitida
    - Se for PDF, valida conteúdo
    - Se for imagem, valida como imagem
    """
    if not file or file.filename == '':
        return False, "Nenhum arquivo selecionado"
    
    if not allowed_file(file.filename):
        return False, f"Tipo de arquivo não permitido. Use: {', '.join(Config.ALLOWED_EXTENSIONS)}"
    
    ext = file.filename.rsplit('.', 1)[1].lower()
    
    if ext == 'pdf':
        if not is_valid_pdf(file):
            return False, "Arquivo não é um PDF válido"
    elif ext in ['jpg', 'jpeg', 'png', 'gif']:
        if not is_valid_image(file):
            return False, "Arquivo não é uma imagem válida"
    
    return True, "OK"