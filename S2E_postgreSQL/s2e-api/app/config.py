import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Configurações centralizadas da aplicação"""
    
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'troque-esta-chave-em-producao')
    
    # Uploads
    UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'storage')
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
    
    # Email (opcional)
    SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 465))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    
    # Sistema
    BASE_URL = os.getenv('BASE_URL', 'http://localhost:8002')
    SESSION_TIMEOUT = int(os.getenv('SESSION_TIMEOUT', 3600))
    
    # Séries oficiais (mesmo do original)
    SERIES = [
        'Maternal 1', 'Maternal 2',
        'Pré 1', 'Pré 2',
        '1º ano EF', '2º ano EF', '3º ano EF', '4º ano EF', '5º ano EF',
        '6º ano EF', '7º ano EF', '8º ano EF', '9º ano EF',
        '1º ano EM', '2º ano EM', '3º ano EM',
    ]
    
    # Mapeamento de variações de série (mesmo do original)
    NORMALIZE_SERIE = {
        'maternal 1': 'Maternal 1', 'maternal i': 'Maternal 1', 'mat 1': 'Maternal 1',
        'maternal 2': 'Maternal 2', 'maternal ii': 'Maternal 2', 'mat 2': 'Maternal 2',
        # Berçário era o nome anterior desta etapa: continua mapeado para não deixar dado antigo
        # (ou vindo do banco da escola) cair num grupo à parte na tela de alunos.
        'bercario 1': 'Maternal 1', 'berçario 1': 'Maternal 1', 'berçário 1': 'Maternal 1',
        'bercario 2': 'Maternal 2', 'berçario 2': 'Maternal 2', 'berçário 2': 'Maternal 2',
        'pre 1': 'Pré 1', 'pré 1': 'Pré 1', 'pre i': 'Pré 1', 'pré i': 'Pré 1',
        'pre 2': 'Pré 2', 'pré 2': 'Pré 2', 'pre ii': 'Pré 2', 'pré ii': 'Pré 2',
        '1 ano ef': '1º ano EF', '1º ano ef': '1º ano EF', '1° ano ef': '1º ano EF',
        '2 ano ef': '2º ano EF', '2º ano ef': '2º ano EF', '2° ano ef': '2º ano EF',
        '3 ano ef': '3º ano EF', '3º ano ef': '3º ano EF', '3° ano ef': '3º ano EF',
        '4 ano ef': '4º ano EF', '4º ano ef': '4º ano EF', '4° ano ef': '4º ano EF',
        '5 ano ef': '5º ano EF', '5º ano ef': '5º ano EF', '5° ano ef': '5º ano EF',
        '6 ano ef': '6º ano EF', '6º ano ef': '6º ano EF', '6° ano ef': '6º ano EF',
        '7 ano ef': '7º ano EF', '7º ano ef': '7º ano EF', '7° ano ef': '7º ano EF',
        '8 ano ef': '8º ano EF', '8º ano ef': '8º ano EF', '8° ano ef': '8º ano EF',
        '9 ano ef': '9º ano EF', '9º ano ef': '9º ano EF', '9° ano ef': '9º ano EF',
        '1 em': '1º ano EM', '1º em': '1º ano EM', '1° em': '1º ano EM', '1 ano em': '1º ano EM',
        '2 em': '2º ano EM', '2º em': '2º ano EM', '2° em': '2º ano EM', '2 ano em': '2º ano EM',
        '3 em': '3º ano EM', '3º em': '3º ano EM', '3° em': '3º ano EM', '3 ano em': '3º ano EM',
        '1 ano ensino fundamental': '1º ano EF', '2 ano ensino fundamental': '2º ano EF',
        '3 ano ensino fundamental': '3º ano EF', '4 ano ensino fundamental': '4º ano EF',
        '5 ano ensino fundamental': '5º ano EF', '6 anno ensino fundamental': '6º ano EF',
        '7 ano ensino fundamental': '7º ano EF', '8 ano ensino fundamental': '8º ano EF',
        '9 ano ensino fundamental': '9º ano EF',
        '1 ano ensino medio': '1º ano EM', '2 ano ensino medio': '2º ano EM', '3 ano ensino medio': '3º ano EM',
        '1 ano ensino médio': '1º ano EM', '2 ano ensino médio': '2º ano EM', '3 ano ensino médio': '3º ano EM',
        # Berçário: numeral romano (nome anterior do Maternal)
        'bercario i': 'Maternal 1', 'bercario ii': 'Maternal 2',
        # EF 1-5: forma curta "X ef" e por extenso
        '1 ef': '1º ano EF', 'primeiro ano ef': '1º ano EF',
        '2 ef': '2º ano EF', 'segundo ano ef': '2º ano EF',
        '3 ef': '3º ano EF', 'terceiro ano ef': '3º ano EF',
        '4 ef': '4º ano EF', '4 ano': '4º ano EF', 'quarto ano': '4º ano EF', 'quarto ano ef': '4º ano EF',
        '5 ef': '5º ano EF', '5 ano': '5º ano EF', 'quinto ano': '5º ano EF', 'quinto ano ef': '5º ano EF',
        # EF 6-9: sem sufixo "ef", símbolo "º" removido pelo normalizador antes do lookup
        '6 ano': '6º ano EF', '6 ef': '6º ano EF', 'sexto ano': '6º ano EF',
        '7 ano': '7º ano EF', '7 ef': '7º ano EF', 'setimo ano': '7º ano EF',
        '8 ano': '8º ano EF', '8 ef': '8º ano EF', 'oitavo ano': '8º ano EF',
        '9 ano': '9º ano EF', '9 ef': '9º ano EF', 'nono ano': '9º ano EF',
        # "1 ano", "2 ano" e "3 ano" sem sufixo são do FUNDAMENTAL — é assim que a escola nomeia
        # as séries (1 a 9 no fundamental; o médio vem sempre com "EM"). Antes caíam no médio, o
        # que colocaria uma criança de 6 anos no grupo do 1º ano do ensino médio na tela.
        '1 ano': '1º ano EF', 'primeiro ano': '1º ano EF',
        '2 ano': '2º ano EF', 'segundo ano': '2º ano EF',
        '3 ano': '3º ano EF', 'terceiro ano': '3º ano EF',
        # "Xª série" e "X série em" seguem sendo do médio: é a nomenclatura antiga do EM.
        '1 serie': '1º ano EM', '1 serie em': '1º ano EM',
        '2 serie': '2º ano EM',
        '3 serie': '3º ano EM',
    }