"""
Jenkins CI data collector.
Polls Jenkins API for build data, parses results, and exports metrics.
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Optional
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class JenkinsCollector:
    """Collects build metrics from Jenkins REST API."""

    FAILURE_PATTERNS = {
        "compilation_error": re.compile(r"(BUILD FAILURE|Compilation failure|compile error)", re.I),
        "test_failure": re.compile(r"(Tests? (run:.*Failures?:|FAILED)|pytest.*FAILED)", re.I),
        "dependency_error": re.compile(r"(Could not resolve|dependency .* not found|npm ERR!)", re.I),
        "timeout": re.compile(r"(timed? ?out|deadline exceeded|Build timed out)", re.I),
        "docker_error": re.compile(r"(docker.*(error|failed|denied)|pull access denied)", re.I),
        "infra_error": re.compile(r"(agent.*(offline|disconnected)|java\.lang\.OutOfMemoryError)", re.I),
        "auth_error": re.compile(r"(401|403|authentication failed|access denied|permission)", re.I),
        "terraform_error": re.compile(r"(Error:.*terraform|resource .* already exists)", re.I),
    }

    def __init__(self, base_url: str, username: str, token: str, metrics_registry):
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(username, token) if token else None
        self.metrics = metrics_registry
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"Accept": "application/json"})

    def _api_get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make authenticated GET request to Jenkins API."""
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Jenkins API error: {url} — {e}")
            return {}

    def get_jobs(self) -> list:
        """Get all Jenkins jobs."""
        data = self._api_get("/api/json", {"tree": "jobs[name,url,color]"})
        return data.get("jobs", [])

    def get_build_details(self, job_name: str, build_number: int) -> dict:
        """Get detailed information for a specific build."""
        path = f"/job/{job_name}/{build_number}/api/json"
        return self._api_get(path)

    def get_console_output(self, job_name: str, build_number: int) -> str:
        """Get console output for failure analysis."""
        url = f"{self.base_url}/job/{job_name}/{build_number}/consoleText"
        try:
            resp = self.session.get(url, timeout=30)
            return resp.text[-5000:]  # last 5KB — where errors usually are
        except requests.RequestException:
            return ""

    def classify_failure(self, console_text: str) -> str:
        """Classify failure cause from console output using regex patterns."""
        for cause, pattern in self.FAILURE_PATTERNS.items():
            if pattern.search(console_text):
                return cause
        return "unknown"

    def get_summary(self) -> dict:
        """Get aggregated build summary across all jobs."""
        jobs = self.get_jobs()
        total, failed, durations = 0, 0, []

        failure_causes = {}

        for job in jobs:
            job_name = job["name"]
            job_data = self._api_get(
                f"/job/{job_name}/api/json",
                {"tree": "builds[number,result,duration,timestamp]{0,20}"}
            )

            for build in job_data.get("builds", []):
                total += 1
                if build.get("duration"):
                    durations.append(build["duration"] / 1000)  # ms → sec

                if build.get("result") in ("FAILURE", "UNSTABLE"):
                    failed += 1
                    console = self.get_console_output(job_name, build["number"])
                    cause = self.classify_failure(console)
                    failure_causes[cause] = failure_causes.get(cause, 0) + 1

        avg_duration = sum(durations) / len(durations) if durations else 0

        # Update Prometheus metrics
        self.metrics.update_build_total("jenkins", total)
        self.metrics.update_build_failures("jenkins", failed)
        self.metrics.update_avg_duration("jenkins", avg_duration)

        return {
            "total_builds": total,
            "failed_builds": failed,
            "avg_duration": round(avg_duration, 1),
            "failure_causes": failure_causes,
            "job_count": len(jobs),
        }

    def get_recent_builds(self, limit: int = 50) -> list:
        """Get recent builds across all jobs."""
        builds = []
        for job in self.get_jobs():
            job_name = job["name"]
            data = self._api_get(
                f"/job/{job_name}/api/json",
                {"tree": f"builds[number,result,duration,timestamp]{{0,{limit}}}"}
            )
            for b in data.get("builds", []):
                builds.append({
                    "source": "jenkins",
                    "job": job_name,
                    "number": b["number"],
                    "status": (b.get("result") or "RUNNING").lower(),
                    "duration_sec": round(b.get("duration", 0) / 1000, 1),
                    "timestamp": datetime.fromtimestamp(
                        b["timestamp"] / 1000
                    ).isoformat(),
                })
        builds.sort(key=lambda x: x["timestamp"], reverse=True)
        return builds[:limit]

    def get_trends(self, days: int = 30, granularity: str = "daily") -> list:
        """Get build success/failure trends."""
        cutoff = datetime.now() - timedelta(days=days)
        date_fmt = "%Y-%m-%d" if granularity == "daily" else "%Y-%m-%dT%H:00"
        buckets = {}

        for job in self.get_jobs():
            data = self._api_get(
                f"/job/{job['name']}/api/json",
                {"tree": "builds[result,timestamp]{0,500}"}
            )
            for b in data.get("builds", []):
                ts = datetime.fromtimestamp(b["timestamp"] / 1000)
                if ts < cutoff:
                    continue
                key = ts.strftime(date_fmt)
                if key not in buckets:
                    buckets[key] = {"date": key, "success": 0, "failure": 0, "total": 0}
                buckets[key]["total"] += 1
                if b.get("result") == "SUCCESS":
                    buckets[key]["success"] += 1
                elif b.get("result") in ("FAILURE", "UNSTABLE"):
                    buckets[key]["failure"] += 1

        return sorted(buckets.values(), key=lambda x: x["date"])

    def analyze_failures(self, days: int = 14) -> dict:
        """Analyze failure patterns over a time period."""
        cutoff = datetime.now() - timedelta(days=days)
        patterns = {}

        for job in self.get_jobs():
            data = self._api_get(
                f"/job/{job['name']}/api/json",
                {"tree": "builds[number,result,timestamp]{0,200}"}
            )
            for b in data.get("builds", []):
                ts = datetime.fromtimestamp(b["timestamp"] / 1000)
                if ts < cutoff:
                    continue
                if b.get("result") not in ("FAILURE", "UNSTABLE"):
                    continue

                console = self.get_console_output(job["name"], b["number"])
                cause = self.classify_failure(console)

                if cause not in patterns:
                    patterns[cause] = {"pattern": cause, "count": 0, "jobs": []}
                patterns[cause]["count"] += 1
                if job["name"] not in patterns[cause]["jobs"]:
                    patterns[cause]["jobs"].append(job["name"])

        return {
            "total_failures_analyzed": sum(p["count"] for p in patterns.values()),
            "patterns": sorted(patterns.values(), key=lambda x: x["count"], reverse=True)
        }
