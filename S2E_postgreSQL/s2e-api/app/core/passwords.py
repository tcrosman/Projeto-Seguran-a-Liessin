"""Política de senha e verificação com tempo constante entre conta existente e inexistente."""
import re

from werkzeug.security import check_password_hash, generate_password_hash

TAMANHO_MINIMO = 8

# Hash descartável, gerado uma vez no import. Serve só para gastar o mesmo tempo de CPU quando a
# conta não existe: sem ele, o pbkdf2 não roda nesse caso e a resposta volta bem mais rápido,
# permitindo descobrir quais usuários/e-mails existem apenas cronometrando o login.
_HASH_DUMMY = generate_password_hash('senha-inexistente-para-timing', method='pbkdf2:sha256')


def senha_confere(hash_armazenado, senha_informada):
    """Compara a senha gastando o mesmo tempo exista ou não a conta.

    `hash_armazenado` é None (ou vazio) quando a conta não foi encontrada.
    """
    if not hash_armazenado:
        check_password_hash(_HASH_DUMMY, senha_informada)
        return False
    return check_password_hash(hash_armazenado, senha_informada)


def verificar_forca(senha):
    """Devolve a mensagem de erro se a senha não atende à política, ou None se atende.

    Fonte única da política. Antes, funcionários passavam por maiúscula + número + caractere
    especial, enquanto o portal dos responsáveis exigia apenas 8 caracteres — e é justamente a
    conta do responsável que dá acesso a dados de menores de idade.
    """
    if len(senha) < TAMANHO_MINIMO:
        return f'Senha deve ter no mínimo {TAMANHO_MINIMO} caracteres'
    if not re.search(r'[A-Z]', senha):
        return 'Senha deve conter ao menos uma letra maiúscula'
    if not re.search(r'[0-9]', senha):
        return 'Senha deve conter ao menos um número'
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{};:\'",.<>/?\\|`~]', senha):
        return 'Senha deve conter ao menos um caractere especial (@, !, #, etc.)'
    return None
