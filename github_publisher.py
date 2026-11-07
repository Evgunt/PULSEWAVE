"""Publish PULSEWAVE results to a GitHub repository using the GitHub Contents API."""
from __future__ import annotations

import base64
import keyring
import keyring.errors
import httpx

SERVICE_NAME = "PULSEWAVE-GitHub"
API_ROOT = "https://api.github.com"


class GitHubPublishError(RuntimeError):
    """Raised when GitHub authentication or publication fails."""


def _windows_credential_backend():
    backend = keyring.get_keyring()
    # Do not silently fall back to plaintext or an insecure keyring backend.
    if not backend.__class__.__module__.startswith("keyring.backends.Windows"):
        raise GitHubPublishError(
            "Не найдено защищённое хранилище учётных данных Windows. "
            "Установи/включи стандартный Windows Credential Manager."
        )
    return backend


def save_github_token(token: str) -> None:
    if not token or "\n" in token:
        raise GitHubPublishError("GitHub token пустой или имеет неверный формат.")
    _windows_credential_backend()
    keyring.set_password(SERVICE_NAME, "personal_access_token", token)


def load_github_token() -> str | None:
    try:
        _windows_credential_backend()
        return keyring.get_password(SERVICE_NAME, "personal_access_token")
    except Exception:
        return None


def delete_github_token() -> None:
    try:
        _windows_credential_backend()
        keyring.delete_password(SERVICE_NAME, "personal_access_token")
    except keyring.errors.PasswordDeleteError:
        pass


class GitHubPublisher:
    def __init__(self, owner: str, repo: str, branch: str, token: str):
        self.owner = owner.strip()
        self.repo = repo.strip()
        self.branch = branch.strip() or "main"
        self.token = token
        if not all((self.owner, self.repo, self.token)):
            raise GitHubPublishError("Не указаны владелец, репозиторий или токен GitHub.")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PULSEWAVE-Desktop",
        }

    def _request(self, method: str, url: str, **kwargs):
        try:
            response = httpx.request(
                method, url, headers=self.headers, timeout=25.0, **kwargs
            )
        except httpx.HTTPError as exc:
            raise GitHubPublishError(f"Сетевая ошибка GitHub: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("message", response.text)
            except Exception:
                message = response.text
            raise GitHubPublishError(
                f"GitHub API вернул {response.status_code}: {message}"
            )
        return response

    @property
    def repo_url(self):
        return f"{API_ROOT}/repos/{self.owner}/{self.repo}"

    def test_connection(self):
        response = self._request("GET", self.repo_url)
        data = response.json()
        if data.get("permissions") and not data["permissions"].get("push", False):
            raise GitHubPublishError("У токена нет права записи в этот репозиторий.")
        return data

    def upload_text(self, path: str, content: str, commit_message: str):
        encoded_path = "/".join(
            __import__("urllib.parse", fromlist=["quote"]).quote(part, safe="")
            for part in path.split("/")
        )
        url = f"{self.repo_url}/contents/{encoded_path}"
        existing = self._request(
            "GET", url, params={"ref": self.branch}
        ).json() if self._file_exists(url) else None
        payload = {
            "message": commit_message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if existing and existing.get("sha"):
            payload["sha"] = existing["sha"]
        response = self._request("PUT", url, json=payload).json()
        return response.get("content", {}).get("html_url") or response.get("commit", {}).get("html_url") or url

    def _file_exists(self, url: str) -> bool:
        try:
            self._request("GET", url, params={"ref": self.branch})
            return True
        except GitHubPublishError as exc:
            if "404" in str(exc):
                return False
            raise
