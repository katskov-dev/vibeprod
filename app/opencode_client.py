"""Тонкий HTTP-клиент к opencode serve."""
import time

import httpx

USERNAME = "opencode"


def wait_healthy(url, token, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(
                f"{url}/global/health",
                auth=(USERNAME, token),
                timeout=3,
                trust_env=False,
            )
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


class OpencodeClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.auth = (USERNAME, token)
        self._c = httpx.Client(
            auth=self.auth,
            timeout=httpx.Timeout(60.0, connect=5.0),
            trust_env=False,
        )

    def close(self):
        self._c.close()

    def health(self):
        r = self._c.get(f"{self.base_url}/global/health")
        r.raise_for_status()
        return r.json()

    def create_session(self, title):
        r = self._c.post(f"{self.base_url}/session", json={"title": title or "Session"})
        r.raise_for_status()
        return r.json()

    def get_session(self, sid):
        return self._c.get(f"{self.base_url}/session/{sid}")

    def prompt_async(self, sid, text, agent=None):
        body = {"parts": [{"type": "text", "text": text}]}
        if agent:
            body["agent"] = agent
        r = self._c.post(f"{self.base_url}/session/{sid}/prompt_async", json=body)
        r.raise_for_status()
        return True

    def abort(self, sid):
        r = self._c.post(f"{self.base_url}/session/{sid}/abort")
        r.raise_for_status()
        return True

    def messages(self, sid):
        r = self._c.get(f"{self.base_url}/session/{sid}/message")
        r.raise_for_status()
        return r.json()

    def config_providers(self):
        r = self._c.get(f"{self.base_url}/config/providers")
        r.raise_for_status()
        return r.json()

    def delete_session(self, sid):
        r = self._c.delete(f"{self.base_url}/session/{sid}")
        r.raise_for_status()
        return True

    def respond_permission(self, sid, permission_id, response="allow"):
        r = self._c.post(
            f"{self.base_url}/session/{sid}/permissions/{permission_id}",
            json={"response": response},
        )
        return r.status_code < 400

    def questions(self):
        """Список ожидающих вопросов (все сессии воркера)."""
        r = self._c.get(f"{self.base_url}/question")
        r.raise_for_status()
        return r.json()

    def reply_question(self, request_id, answers):
        """answers — список ответов в порядке вопросов, каждый — список выбранных label."""
        r = self._c.post(
            f"{self.base_url}/question/{request_id}/reply",
            json={"answers": answers},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"не удалось ответить на вопрос (HTTP {r.status_code})")
        return r.json()

    def reject_question(self, request_id):
        r = self._c.post(f"{self.base_url}/question/{request_id}/reject")
        if r.status_code >= 400:
            raise RuntimeError(f"не удалось отклонить вопрос (HTTP {r.status_code})")
        return r.json()
