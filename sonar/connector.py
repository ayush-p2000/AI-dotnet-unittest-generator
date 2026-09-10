import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from sonar.models import SonarIssue, SonarProject

load_dotenv()


class SonarConnectionError(Exception):
    """Raised when communication with SonarQube fails."""
    pass


class SonarAuthenticationError(SonarConnectionError):
    """Raised when authentication with SonarQube fails."""
    pass


class SonarConnector:
    """
    Robust, dependency-free HTTP client connecting to SonarQube Server or SonarCloud Web API.
    """

    def __init__(
        self,
        host_url: Optional[str] = None,
        token: Optional[str] = None,
        organization: Optional[str] = None,
        timeout_seconds: int = 20,
    ):
        raw_url = host_url or os.getenv("SONAR_HOST_URL") or "http://localhost:9000"
        self.host_url = raw_url.rstrip("/")
        self.token = token or os.getenv("SONAR_TOKEN") or ""
        self.organization = organization or os.getenv("SONAR_ORGANIZATION")
        self.timeout = timeout_seconds

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "SonarComplexityResolver/1.0",
        }
        if self.token:
            # SonarQube HTTP Basic Auth uses token as the username with empty password
            auth_str = f"{self.token}:"
            encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded_auth}"
        return headers

    def _request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        method: str = "GET",
        data: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Executes an HTTP request to the SonarQube Web API.
        """
        clean_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        query_str = f"?{urllib.parse.urlencode(params)}" if params else ""
        full_url = f"{self.host_url}{clean_endpoint}{query_str}"

        headers = self._build_headers()
        req = urllib.request.Request(
            url=full_url,
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset)
                if "application/json" in content_type or body.strip().startswith(("{", "[")):
                    return json.loads(body)
                return {"raw_text": body}

        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            err_msg = body
            try:
                err_json = json.loads(body)
                if "errors" in err_json:
                    err_msg = ", ".join(e.get("msg", str(e)) for e in err_json["errors"])
                elif "message" in err_json:
                    err_msg = err_json["message"]
            except Exception:
                pass

            if err.code in (401, 403):
                raise SonarAuthenticationError(
                    f"Authentication failed (HTTP {err.code}): {err_msg} at {full_url}"
                ) from err
            raise SonarConnectionError(
                f"SonarQube API returned HTTP {err.code}: {err_msg} at {full_url}"
            ) from err

        except urllib.error.URLError as err:
            raise SonarConnectionError(
                f"Failed to connect to SonarQube at {self.host_url}: {err.reason}"
            ) from err

    def validate_connection(self) -> Dict[str, Any]:
        """
        Validates connection and token validity against SonarQube.
        """
        result = {
            "connected": False,
            "authenticated": False,
            "server_version": None,
            "status": "UNKNOWN",
            "details": {},
        }

        # 1. Check system status (does not require auth)
        try:
            status_resp = self._request("/api/system/status")
            result["connected"] = True
            result["server_version"] = status_resp.get("version")
            result["status"] = status_resp.get("status", "UP")
        except SonarConnectionError as e:
            result["details"]["system_status_error"] = str(e)

        # 2. Check token validation if token provided
        if self.token:
            try:
                auth_resp = self._request("/api/authentication/validate")
                result["connected"] = True
                result["authenticated"] = auth_resp.get("valid", False)
            except SonarConnectionError as e:
                result["details"]["auth_error"] = str(e)
        else:
            result["authenticated"] = False
            result["details"]["auth_warning"] = "No token provided; unauthenticated mode."

        return result

    def get_projects(self, query: Optional[str] = None) -> List[SonarProject]:
        """
        Lists available projects from SonarQube.
        """
        params: Dict[str, Any] = {"ps": 100}
        if query:
            params["q"] = query
        if self.organization:
            params["organization"] = self.organization

        resp = self._request("/api/projects/search", params=params)
        components = resp.get("components", [])
        return [SonarProject.from_dict(c) for c in components]

    def get_cognitive_complexity_issues(
        self,
        project_key: str,
        rule: str = "csharpsquid:S3776",
        branch: Optional[str] = None,
        pull_request: Optional[str] = None,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> List[SonarIssue]:
        """
        Retrieves all unresolved Cognitive Complexity issues (S3776) for a given project.
        Handles multi-page traversal automatically.
        """
        all_issues: List[SonarIssue] = []
        page = 1

        while page <= max_pages:
            params: Dict[str, Any] = {
                "componentKeys": project_key,
                "rules": rule,
                "resolved": "false",
                "ps": page_size,
                "p": page,
                "additionalFields": "_all",
            }
            if self.organization:
                params["organization"] = self.organization
            if branch:
                params["branch"] = branch
            if pull_request:
                params["pullRequest"] = pull_request

            # Fallback for SonarQube versions that require projectKeys instead of componentKeys
            try:
                resp = self._request("/api/issues/search", params=params)
            except SonarConnectionError as err:
                if "componentKeys" in str(err) or "Unknown parameter" in str(err):
                    del params["componentKeys"]
                    params["projects"] = project_key
                    resp = self._request("/api/issues/search", params=params)
                else:
                    raise

            issues_raw = resp.get("issues", [])
            for item in issues_raw:
                all_issues.append(SonarIssue.from_dict(item))

            paging = resp.get("paging", {})
            total = paging.get("total", len(all_issues))
            page_size_actual = paging.get("pageSize", page_size)

            if page * page_size_actual >= total or not issues_raw:
                break

            page += 1

        return all_issues

    def get_issue_by_key(self, issue_key: str) -> Optional[SonarIssue]:
        """
        Retrieves a single issue by its unique key, with full flow details.
        """
        params = {"issues": issue_key, "additionalFields": "_all"}
        resp = self._request("/api/issues/search", params=params)
        issues_raw = resp.get("issues", [])
        if issues_raw:
            return SonarIssue.from_dict(issues_raw[0])
        return None

    def get_project_quality_gate(self, project_key: str, branch: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves the Quality Gate status of the project.
        """
        params = {"projectKey": project_key}
        if branch:
            params["branch"] = branch
        return self._request("/api/qualitygates/project_status", params=params)
