"""
GitLab CI data collector.
Polls GitLab API for pipeline data, parses results, and exports metrics.
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Optional
import requests

logger = logging.getLogger(__name__)


class GitLabCollector:
    """Collects pipeline metrics from GitLab REST API (v4)."""

    FAILURE_PATTERNS = {
        "test_failure": re.compile(r"(FAILED|AssertionError|failures?:.*[1-9])", re.I),
        "lint_error": re.compile(r"(eslint|flake8|rubocop|pylint).*(error|warning)", re.I),
        "docker_error": re.compile(r"(docker.*(error|failed|denied)|manifest unknown)", re.I),
        "dependency_error": re.compile(r"(Could not resolve|ModuleNotFoundError|npm ERR)", re.I),
        "timeout": re.compile(r"(job.*exceeded.*timeout|execution took longer)", re.I),
        "runner_error": re.compile(r"(runner.*not available|stuck|no matching runner)", re.I),
        "auth_error": re.compile(r"(401|403|denied|registry login failed)", re.I),
    }

    def __init__(self, base_url: str, token: str, metrics_registry):
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v4"
        self.metrics = metrics_registry
        self.session = requests.Session()
        self.session.headers.update({
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
        })

    def _api_get(self, path: str, params: Optional[dict] = None) -> list | dict:
        """Make authenticated GET request to GitLab API."""
        url = f"{self.api_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"GitLab API error: {url} — {e}")
            return [] if "s?" not in path else {}

    def get_projects(self) -> list:
        """Get all accessible projects."""
        return self._api_get("/projects", {
            "membership": True,
            "per_page": 100,
            "order_by": "last_activity_at"
        })

    def get_pipelines(self, project_id: int, per_page: int = 50,
                      updated_after: Optional[str] = None) -> list:
        """Get pipelines for a project."""
        params = {"per_page": per_page, "order_by": "updated_at", "sort": "desc"}
        if updated_after:
            params["updated_after"] = updated_after
        return self._api_get(f"/projects/{project_id}/pipelines", params)

    def get_pipeline_jobs(self, project_id: int, pipeline_id: int) -> list:
        """Get jobs within a specific pipeline."""
        return self._api_get(f"/projects/{project_id}/pipelines/{pipeline_id}/jobs")

    def get_job_log(self, project_id: int, job_id: int) -> str:
        """Get job trace/log for failure analysis."""
        url = f"{self.api_url}/projects/{project_id}/jobs/{job_id}/trace"
        try:
            resp = self.session.get(url, timeout=30)
            return resp.text[-5000:]  # last 5KB
        except requests.RequestException:
            return ""

    def classify_failure(self, log_text: str) -> str:
        """Classify failure cause from job log."""
        for cause, pattern in self.FAILURE_PATTERNS.items():
            if pattern.search(log_text):
                return cause
        return "unknown"

    def get_summary(self) -> dict:
        """Get aggregated pipeline summary across all projects."""
        projects = self.get_projects()
        total, failed, durations = 0, 0, []
        failure_causes = {}

        for proj in projects[:20]:  # limit to most active 20
            pipelines = self.get_pipelines(proj["id"], per_page=20)

            for pipeline in pipelines:
                total += 1
                if pipeline.get("duration"):
                    durations.append(pipeline["duration"])

                if pipeline.get("status") == "failed":
                    failed += 1
                    jobs = self.get_pipeline_jobs(proj["id"], pipeline["id"])
                    for job in jobs:
                        if job.get("status") == "failed":
                            log = self.get_job_log(proj["id"], job["id"])
                            cause = self.classify_failure(log)
                            failure_causes[cause] = failure_causes.get(cause, 0) + 1
                            break  # first failed job is usually root cause

        avg_duration = sum(durations) / len(durations) if durations else 0

        self.metrics.update_build_total("gitlab", total)
        self.metrics.update_build_failures("gitlab", failed)
        self.metrics.update_avg_duration("gitlab", avg_duration)

        return {
            "total_pipelines": total,
            "failed_pipelines": failed,
            "avg_duration": round(avg_duration, 1),
            "failure_causes": failure_causes,
            "project_count": len(projects),
        }

    def get_recent_pipelines(self, limit: int = 50) -> list:
        """Get recent pipelines across all projects."""
        pipelines = []
        for proj in self.get_projects()[:10]:
            for p in self.get_pipelines(proj["id"], per_page=limit):
                pipelines.append({
                    "source": "gitlab",
                    "job": f"{proj['name']}/{p.get('ref', 'main')}",
                    "number": p["id"],
                    "status": p.get("status", "unknown"),
                    "duration_sec": p.get("duration", 0),
                    "timestamp": p.get("updated_at", ""),
                })
        pipelines.sort(key=lambda x: x["timestamp"], reverse=True)
        return pipelines[:limit]

    def get_trends(self, days: int = 30, granularity: str = "daily") -> list:
        """Get pipeline success/failure trends."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        date_fmt = "%Y-%m-%d" if granularity == "daily" else "%Y-%m-%dT%H:00"
        buckets = {}

        for proj in self.get_projects()[:10]:
            for p in self.get_pipelines(proj["id"], per_page=200, updated_after=cutoff):
                ts_str = p.get("updated_at", "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                key = ts.strftime(date_fmt)

                if key not in buckets:
                    buckets[key] = {"date": key, "success": 0, "failure": 0, "total": 0}
                buckets[key]["total"] += 1
                if p.get("status") == "success":
                    buckets[key]["success"] += 1
                elif p.get("status") == "failed":
                    buckets[key]["failure"] += 1

        return sorted(buckets.values(), key=lambda x: x["date"])

    def analyze_failures(self, days: int = 14) -> dict:
        """Analyze failure patterns over a time period."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        patterns = {}

        for proj in self.get_projects()[:10]:
            pipelines = self.get_pipelines(
                proj["id"], per_page=100, updated_after=cutoff
            )
            failed = [p for p in pipelines if p.get("status") == "failed"]

            for p in failed:
                jobs = self.get_pipeline_jobs(proj["id"], p["id"])
                for job in jobs:
                    if job.get("status") != "failed":
                        continue
                    log = self.get_job_log(proj["id"], job["id"])
                    cause = self.classify_failure(log)

                    if cause not in patterns:
                        patterns[cause] = {"pattern": cause, "count": 0, "jobs": []}
                    patterns[cause]["count"] += 1
                    job_key = f"{proj['name']}/{job.get('name', 'unknown')}"
                    if job_key not in patterns[cause]["jobs"]:
                        patterns[cause]["jobs"].append(job_key)
                    break

        return {
            "total_failures_analyzed": sum(p["count"] for p in patterns.values()),
            "patterns": sorted(patterns.values(), key=lambda x: x["count"], reverse=True)
        }
