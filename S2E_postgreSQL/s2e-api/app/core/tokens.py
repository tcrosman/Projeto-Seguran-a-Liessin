"""Derivação de tokens persistidos, sem guardar o valor enviado por e-mail."""

import hashlib
import hmac
import os


def digest_token(token):
    return hmac.new(os.environ['SECRET_KEY'].encode('utf-8'),
                    token.encode('utf-8'), hashlib.sha256).hexdigest()
