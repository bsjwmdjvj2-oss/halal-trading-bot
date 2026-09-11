"""PythonAnywhere account API client -- currently only Always-on Task
restart, used by the /restart Telegram command (see halal_bot.telegram.bot)
so a `git pull` deploy can be picked up without going to the PythonAnywhere
dashboard. A long-running Always-on Task process doesn't reload its own
already-imported Python modules on its own; it has to actually be restarted.

STATUS: implemented against PythonAnywhere's documented API shape
(https://www.pythonanywhere.com/api/v0/, /user/<user>/always_on_tasks/) but
NOT verified against a real account/token from here -- there's no way to
test this without real PythonAnywhere credentials, which this session
doesn't have. Treat /restart as untested until you've tried it once and
confirmed the reply matches what actually happened on the Tasks page.
"""
from __future__ import annotations

from dataclasses import dataclass

from halal_bot.config import CONFIG


class PythonAnywhereNotConfiguredError(RuntimeError):
    pass


class PythonAnywhereAPIError(RuntimeError):
    pass


@dataclass
class AlwaysOnTask:
    id: int
    command: str
    description: str
    enabled: bool


class PythonAnywhereClient:
    def __init__(self):
        cfg = CONFIG.pythonanywhere
        if not cfg.api_token or not cfg.username:
            raise PythonAnywhereNotConfiguredError(
                "PYTHONANYWHERE_API_TOKEN / PYTHONANYWHERE_USERNAME not set -- fill in "
                ".env before using PythonAnywhereClient. Token from "
                "https://www.pythonanywhere.com/account/#api_token"
            )
        import httpx

        self._username = cfg.username
        self._http = httpx.Client(
            base_url=cfg.base_url,
            headers={"Authorization": f"Token {cfg.api_token}"},
            timeout=30.0,
        )

    def list_always_on_tasks(self) -> list[AlwaysOnTask]:
        resp = self._http.get(f"/api/v0/user/{self._username}/always_on_tasks/")
        resp.raise_for_status()
        return [
            AlwaysOnTask(
                id=t["id"], command=t.get("command", ""),
                description=t.get("description", ""), enabled=t.get("enabled", True),
            )
            for t in resp.json()
        ]

    def restart_task(self, task_id: int) -> None:
        resp = self._http.post(f"/api/v0/user/{self._username}/always_on_tasks/{task_id}/restart/")
        resp.raise_for_status()

    def restart_task_matching(self, command_substring: str) -> AlwaysOnTask:
        """Finds the single Always-on Task whose command contains
        command_substring and restarts it -- avoids hardcoding a numeric
        task id that could change if the task is ever deleted/recreated on
        the PythonAnywhere dashboard. Raises if zero or more than one task
        matches, rather than guessing which one was meant."""
        matches = [t for t in self.list_always_on_tasks() if command_substring in t.command]
        if not matches:
            raise PythonAnywhereAPIError(
                f"No Always-on Task found with {command_substring!r} in its command."
            )
        if len(matches) > 1:
            raise PythonAnywhereAPIError(
                f"Multiple Always-on Tasks match {command_substring!r}: "
                f"{[t.id for t in matches]} -- ambiguous, restart by id instead."
            )
        self.restart_task(matches[0].id)
        return matches[0]
