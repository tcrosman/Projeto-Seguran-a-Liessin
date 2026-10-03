import unicodedata
from datetime import datetime
from app.core.clock import school_now
from PIL import Image, UnidentifiedImageError
from app.config import Config


def validar_agendamento(data, horario):
    """Valida no servidor a data e a hora informadas no navegador."""
    try:
        saida = datetime.strptime(f'{data} {horario}', '%Y-%m-%d %H:%M')
        if saida.strftime('%Y-%m-%d %H:%M') != f'{data} {horario}':
            raise ValueError
    except (ValueError, TypeError):
        return "Data ou horário inválido."
    if saida <= school_now():
        return "Escolha uma data e um horário futuros."
    return None


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
    try:
        file_stream.seek(0)
        with Image.open(file_stream) as image:
            if image.width * image.height > 20_000_000:
                return False
            image.verify()
            return image.format in {'JPEG', 'PNG', 'GIF'}
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return False
    finally:
        file_stream.seek(posicao)


def is_valid_pdf(file_stream):
    """
    Verifica se o arquivo é realmente um PDF válido.
    PDFs começam com %PDF (bytes: 25 50 44 46)
    """
    posicao = file_stream.tell()
    file_stream.seek(0)
    
    # Cabeçalho e marcador final: um cabeçalho isolado não basta.
    cabecalho = file_stream.read(4)
    file_stream.seek(0, 2)
    size = file_stream.tell()
    file_stream.seek(max(0, size - 2048))
    tail = file_stream.read()
    
    # Restaura posição
    file_stream.seek(posicao)
    
    # PDF deve começar com %PDF
    return cabecalho == b'%PDF' and b'%%EOF' in tail


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
    
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in {'jpg', 'jpeg', 'png', 'gif'}:
        return False, "Envie uma imagem JPEG, PNG ou GIF"
    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(0)
    if size == 0 or size > 5 * 1024 * 1024:
        return False, "A imagem deve ter até 5 MB"
    if not is_valid_image(file):
        return False, "Arquivo não é uma imagem válida (pode estar corrompido ou ser falso)"
    with Image.open(file.stream) as image:
        expected = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'gif': 'GIF'}[ext]
        if image.format != expected:
            file.stream.seek(0)
            return False, "O conteúdo não corresponde à extensão da imagem"
    file.stream.seek(0)
    
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
    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(0)
    if size == 0 or size > 10 * 1024 * 1024:
        return False, "O documento deve ter até 10 MB"
    
    if ext == 'pdf':
        if not is_valid_pdf(file):
            return False, "Arquivo não é um PDF válido"
    elif ext in ['jpg', 'jpeg', 'png', 'gif']:
        return validar_upload_imagem(file)
    
    return True, "OK"
