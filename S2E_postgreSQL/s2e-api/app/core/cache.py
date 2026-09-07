import time
from collections import OrderedDict
from threading import Lock


class TTLCache:
    """Cache em memória de processo com expiração por tempo.

    Não persiste em disco nem sobrevive a um restart do processo — usado para
    dados vindos de consultas externas (ex: app/services/school_sql_directory.py)
    que não devem ser gravados localmente, só reduzir chamadas repetidas.

    Três limites, e cada um existe por um motivo diferente:

      `ttl_seconds`   até quando o valor é considerado atual (get).
      `stale_seconds` até quando ele ainda serve de último recurso, quando a fonte externa não
                      responde (get_stale). Passado esse prazo a entrada não vale mais para
                      nada: mostrar o nome e a turma de um aluno lidos há um dia é pior do que
                      dizer que o cadastro está indisponível — e são dados pessoais de menores
                      guardados sem necessidade.
      `maximo`        quantas entradas cabem. Sem teto, `_store` crescia com um item por RA
                      consultado e nunca devolvia memória, num plano de hospedagem onde ela é o
                      recurso mais apertado. O descarte é LRU: sai quem não é usado há mais
                      tempo, que numa escola é o aluno que ninguém procurou hoje.
    """

    def __init__(self, ttl_seconds: int = 300, stale_seconds: int = 3600, maximo: int = 2000):
        self._ttl = ttl_seconds
        self._stale = stale_seconds
        self._maximo = maximo
        # OrderedDict, e não dict: é a ordem de uso que decide quem sai quando o teto estoura.
        self._store = OrderedDict()
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
            value, expires_at, _gravado_em = entry
            if time.time() > expires_at:
                return None
            self._store.move_to_end(key)
            return value

    def get_stale(self, key):
        """Último valor conhecido, mesmo expirado — só para quando a fonte externa está fora do ar.

        Quem chama precisa ter confirmado que a consulta FALHOU. Usar isto no caminho de sucesso
        foi o defeito A10: quando o diretório respondia mas já não trazia aquele RA (aluno
        desligado, transferido, vínculo revogado), `set()` nunca era chamado e este método
        devolvia o valor antigo indefinidamente — nome, turma e e-mails dos responsáveis de um
        aluno que a escola já desligou.

        Devolve None depois de `stale_seconds`: um valor velho demais deixa de ser fallback e
        vira desinformação.
        """
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            value, _expires_at, gravado_em = entry
            if time.time() - gravado_em > self._stale:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key, value):
        agora = time.time()
        with self._lock:
            self._store[key] = (value, agora + self._ttl, agora)
            self._store.move_to_end(key)
            while len(self._store) > self._maximo:
                self._store.popitem(last=False)

    def descartar(self, key):
        """Remove uma entrada. Para quando se sabe que o valor não vale mais — não há por que
        esperar o TTL."""
        with self._lock:
            self._store.pop(key, None)

    def limpar(self):
        with self._lock:
            self._store.clear()

    def __len__(self):
        with self._lock:
            return len(self._store)
