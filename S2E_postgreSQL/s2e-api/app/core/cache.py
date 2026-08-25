import time
from threading import Lock


class TTLCache:
    """Cache em memória de processo com expiração por tempo.

    Não persiste em disco nem sobrevive a um restart do processo — usado para
    dados vindos de consultas externas (ex: app/services/school_sql_directory.py)
    que não devem ser gravados localmente, só reduzir chamadas repetidas.
    """

    def __init__(self, ttl_seconds: int = 300):
        self._ttl = ttl_seconds
        self._store = {}
        self._lock = Lock()

    def get(self, key):
        """Retorna o valor em cache, ou None se ausente/expirado.

        Não apaga a entrada expirada — get_stale() precisa dela para o fallback.
        set() é quem substitui/limpa uma entrada antiga.
        """
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            value, expires_at = entry
            if time.time() > expires_at:
                return None
            return value

    def get_stale(self, key):
        """Retorna o último valor conhecido mesmo expirado — fallback para quando a fonte externa está fora do ar."""
        with self._lock:
            entry = self._store.get(key)
            return entry[0] if entry else None

    def set(self, key, value):
        with self._lock:
            self._store[key] = (value, time.time() + self._ttl)
