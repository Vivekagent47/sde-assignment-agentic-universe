"""HTTP client for the simulator. Retries transport errors and 5xx on idempotent calls only."""
from __future__ import annotations

import time

import httpx


class SimError(Exception):
    def __init__(self, status: int, body):
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body if isinstance(body, dict) else {"error": str(body)}

    @property
    def code(self) -> str:
        return self.body.get("error", "")


class Sim:
    def __init__(self, url: str):
        self.c = httpx.Client(base_url=url, timeout=600)

    def _req(self, method: str, path: str, retry: bool = True, **kw):
        for attempt in range(10):
            try:
                r = self.c.request(method, path, **kw)
            except httpx.TransportError:
                if not retry:
                    raise
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code >= 500 and retry:
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code >= 400:
                try:
                    body = r.json()
                except ValueError:
                    body = r.text
                raise SimError(r.status_code, body)
            return r
        raise RuntimeError(f"simulator unreachable: {method} {path}")

    def get(self, path: str, **params):
        return self._req("GET", path, params=params or None).json()

    def text(self, path: str) -> str:
        return self._req("GET", path).text

    # POST/PUT with an idempotency key (proposal_id) are safe to retry: a replay returns 409 duplicate.
    def post(self, path: str, body: dict, retry: bool = True):
        return self._req("POST", path, retry=retry, json=body).json()

    def put(self, path: str, body: dict):
        return self._req("PUT", path, json=body).json()

    def advance(self, minutes: int, from_minute: int) -> dict:
        """advance is not idempotent: on a transport failure, re-read state instead of re-posting."""
        try:
            return self._req("POST", "/sim/advance", retry=False, json={"minutes": minutes}).json()
        except (httpx.TransportError, SimError) as e:
            if isinstance(e, SimError) and e.status < 500:
                raise
            for _ in range(10):
                try:
                    st = self.get("/sim/state")
                    break
                except Exception:
                    time.sleep(5)
            else:
                raise
            if st["sim_minute"] > from_minute:
                return st  # it went through; the caller ingests by cursor anyway
            return self.advance(minutes, from_minute)
