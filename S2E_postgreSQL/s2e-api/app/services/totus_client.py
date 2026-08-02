import os
import requests


class TotusClient:
    """Valida responsável via TOTVS REST API. Retorna apenas true/false — nenhum dado sensível trafega."""

    def __init__(self):
        self._url = os.getenv('TOTUS_API_URL', '').rstrip('/')
        self._token = os.getenv('TOTUS_API_TOKEN', '')

    def validar_responsavel(self, email: str) -> bool:
        """Retorna True se o email pertence a um responsável reconhecido pelo TOTVS."""
        if not self._url or not self._token:
            raise RuntimeError("TOTUS_API_URL e TOTUS_API_TOKEN devem estar configurados no .env")
        try:
            resp = requests.post(
                f"{self._url}/api/v1/validacao/responsavel",
                json={"email": email},
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            return bool(resp.json().get("autorizado", False))
        except Exception as e:
            print(f"[TOTUS] Erro ao validar responsável: {e}")
            return False

    def validar_vinculo(self, email: str, aluno_id: int) -> bool:
        """Retorna True se o TOTVS confirma que o responsável é vinculado ao aluno."""
        if not self._url or not self._token:
            raise RuntimeError("TOTUS_API_URL e TOTUS_API_TOKEN devem estar configurados no .env")
        try:
            resp = requests.post(
                f"{self._url}/api/v1/validacao/vinculo",
                json={"email": email, "aluno_id": aluno_id},
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            return bool(resp.json().get("autorizado", False))
        except Exception as e:
            print(f"[TOTUS] Erro ao validar vínculo: {e}")
            return False


class TotusClientMock(TotusClient):
    """Mock para desenvolvimento/testes sem acesso ao TOTVS real (TOTUS_MOCK=true)."""

    # Adicione emails de teste aqui para o ambiente de desenvolvimento
    EMAILS_VALIDOS = {
        "pai@teste.com",
        "mae@teste.com",
        "responsavel@teste.com",
        "theocrosman@gmail.com",
    }

    def validar_responsavel(self, email: str) -> bool:
        return email.strip().lower() in self.EMAILS_VALIDOS

    def validar_vinculo(self, email: str, aluno_id: int) -> bool:
        # No mock, qualquer responsável válido pode vincular qualquer aluno.
        # Na API real, o TOTVS valida a relação específica pai-aluno.
        return email.strip().lower() in self.EMAILS_VALIDOS


def get_totus_client() -> TotusClient:
    """Retorna mock ou cliente real com base na variável TOTUS_MOCK (padrão: true em dev)."""
    if os.getenv('TOTUS_MOCK', 'true').lower() == 'true':
        return TotusClientMock()
    return TotusClient()
