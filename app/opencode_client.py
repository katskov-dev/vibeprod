"""Тонкий HTTP-клиент к opencode serve.

Воркеры публикуют порт на хост, и брокер видит его по-разному:
- брокер на хосте или в host-сети compose (Linux): 127.0.0.1:<port>;
- Docker Desktop / bridge-сеть: host.docker.internal:<port>.
Клиент перебирает адреса по порядку и запоминает рабочий.
"""
import time

import httpx

USERNAME = "opencode"


def worker_urls(port):
    """Кандидаты адресов воркера для host-порта, опубликованного docker-демоном."""
    return [f"http://127.0.0.1:{port}", f"http://host.docker.internal:{port}"]


def wait_healthy(url, token, timeout=120):
    """Ждёт /global/health на любом из адресов (url — строка или список).

    Возвращает рабочий URL или None.
    """
    urls = url if isinstance(url, (list, tuple)) else [url]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for u in urls:
            try:
                r = httpx.get(
                    f"{u}/global/health",
                    auth=(USERNAME, token),
                    timeout=3,
                    trust_env=False,
                )
                if r.status_code == 200:
                    return u
            except Exception:
                pass
        time.sleep(0.5)
    return None


class OpencodeClient:
    def __init__(self, base_url, token):
        urls = base_url if isinstance(base_url, (list, tuple)) else [base_url]
        self.urls = [u.rstrip("/") for u in urls]
        self.auth = (USERNAME, token)
        self._c = httpx.Client(
            auth=self.auth,
            timeout=httpx.Timeout(60.0, connect=5.0),
            trust_env=False,
        )
        self._prefer = 0

    def close(self):
        self._c.close()

    def _req(self, method, path, **kwargs):
        """Запрос с фолбэком по адресам; удачный адрес запоминается."""
        errors = []
        for i in range(len(self.urls)):
            u = self.urls[(self._prefer + i) % len(self.urls)]
            try:
                r = self._c.request(method, f"{u}{path}", **kwargs)
                if r.status_code < 500:
                    self._prefer = (self._prefer + i) % len(self.urls)
                    return r
                errors.append(f"{u} → HTTP {r.status_code}")
            except httpx.HTTPError as exc:
                errors.append(f"{u} → {exc}")
        raise httpx.ConnectError(f"воркер недоступен ни по одному адресу: {errors}")

    def health(self):
        r = self._req("GET", "/global/health")
        r.raise_for_status()
        return r.json()

    def create_session(self, title):
        r = self._req("POST", "/session", json={"title": title or "Session"})
        r.raise_for_status()
        return r.json()

    def get_session(self, sid):
        return self._req("GET", f"/session/{sid}")

    def prompt_async(self, sid, text, agent=None):
        body = {"parts": [{"type": "text", "text": text}]}
        if agent:
            body["agent"] = agent
        r = self._req("POST", f"/session/{sid}/prompt_async", json=body)
        r.raise_for_status()
        return True

    def abort(self, sid):
        r = self._req("POST", f"/session/{sid}/abort")
        r.raise_for_status()
        return True

    def messages(self, sid):
        r = self._req("GET", f"/session/{sid}/message")
        r.raise_for_status()
        return r.json()

    def config_providers(self):
        r = self._req("GET", "/config/providers")
        r.raise_for_status()
        return r.json()

    def delete_session(self, sid):
        r = self._req("DELETE", f"/session/{sid}")
        r.raise_for_status()
        return True

    def respond_permission(self, sid, permission_id, response="always"):
        """Отвечает на запрос разрешения воркера.

        Форк opencode (anomalyco) принимает «once»/«always»/«reject»:
        сначала пробуем новый маршрут /api/session/{sid}/permission/{id}/reply
        (body {reply}), при 404 — старый /session/{sid}/permissions/{id}
        (body {response}), как в старых версиях opencode.
        """
        r = self._req(
            "POST",
            f"/api/session/{sid}/permission/{permission_id}/reply",
            json={"reply": response, "message": ""},
        )
        if r.status_code == 404:
            r = self._req(
                "POST",
                f"/session/{sid}/permissions/{permission_id}",
                json={"response": response},
            )
        return r.status_code < 400

    def questions(self):
        r = self._req("GET", "/question")
        r.raise_for_status()
        return r.json()

    def reply_question(self, request_id, answers):
        r = self._req("POST", f"/question/{request_id}/reply", json={"answers": answers})
        if r.status_code >= 400:
            raise RuntimeError(f"не удалось ответить на вопрос (HTTP {r.status_code})")
        return r.json()

    def reject_question(self, request_id):
        r = self._req("POST", f"/question/{request_id}/reject")
        if r.status_code >= 400:
            raise RuntimeError(f"не удалось отклонить вопрос (HTTP {r.status_code})")
        return r.json()
