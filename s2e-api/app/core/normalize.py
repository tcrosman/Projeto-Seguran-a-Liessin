import unicodedata

def normalizar_nome_para_foto(nome):
    """Remove acentos e padroniza nome para match com arquivo de foto"""
    if not nome:
        return ""
    nfkd = unicodedata.normalize('NFKD', nome)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return sem_acento.lower().strip()