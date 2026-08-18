"""
PipelineHealth — CI/CD Observability Dashboard
Main Flask API serving metrics and dashboard endpoints.
"""

import os
import logging
from flask import Flask, jsonify, request
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from collectors.jenkins_collector import JenkinsCollector
from collectors.gitlab_collector import GitLabCollector
from exporters.prometheus_exporter import MetricsRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Configuration ---
JENKINS_URL = os.getenv("JENKINS_URL", "http://localhost:8080")
JENKINS_USER = os.getenv("JENKINS_USER", "admin")
JENKINS_TOKEN = os.getenv("JENKINS_TOKEN", "")
GITLAB_URL = os.getenv("GITLAB_URL", "https://gitlab.example.com")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")
COLLECTION_INTERVAL = int(os.getenv("COLLECTION_INTERVAL", "60"))

# --- Initialize collectors ---
metrics = MetricsRegistry()
jenkins = JenkinsCollector(JENKINS_URL, JENKINS_USER, JENKINS_TOKEN, metrics)
gitlab = GitLabCollector(GITLAB_URL, GITLAB_TOKEN, metrics)


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "version": "1.2.0"})


@app.route("/metrics")
def prometheus_metrics():
    """Prometheus scrape endpoint."""
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.route("/api/v1/summary")
def pipeline_summary():
    """Aggregated pipeline health summary."""
    try:
        jenkins_data = jenkins.get_summary()
        gitlab_data = gitlab.get_summary()

        total_builds = jenkins_data["total_builds"] + gitlab_data["total_pipelines"]
        total_failures = jenkins_data["failed_builds"] + gitlab_data["failed_pipelines"]
        success_rate = (
            ((total_builds - total_failures) / total_builds * 100)
            if total_builds > 0 else 0
        )

        return jsonify({
            "total_builds": total_builds,
            "total_failures": total_failures,
            "success_rate": round(success_rate, 2),
            "avg_build_duration_sec": round(
                (jenkins_data["avg_duration"] + gitlab_data["avg_duration"]) / 2, 1
            ),
            "top_failure_causes": _aggregate_failure_causes(
                jenkins_data.get("failure_causes", {}),
                gitlab_data.get("failure_causes", {})
            ),
            "sources": {
                "jenkins": jenkins_data,
                "gitlab": gitlab_data
            }
        })
    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/builds")
def recent_builds():
    """Recent build/pipeline history with filters."""
    source = request.args.get("source", "all")
    status = request.args.get("status", "all")
    limit = int(request.args.get("limit", "50"))

    builds = []
    if source in ("all", "jenkins"):
        builds.extend(jenkins.get_recent_builds(limit))
    if source in ("all", "gitlab"):
        builds.extend(gitlab.get_recent_pipelines(limit))

    if status != "all":
        builds = [b for b in builds if b["status"] == status]

    builds.sort(key=lambda x: x["timestamp"], reverse=True)
    return jsonify(builds[:limit])


@app.route("/api/v1/trends")
def build_trends():
    """Build success/failure trends over time."""
    days = int(request.args.get("days", "30"))
    granularity = request.args.get("granularity", "daily")  # daily, hourly

    jenkins_trends = jenkins.get_trends(days, granularity)
    gitlab_trends = gitlab.get_trends(days, granularity)

    merged = _merge_trends(jenkins_trends, gitlab_trends)
    return jsonify(merged)


@app.route("/api/v1/failure-analysis")
def failure_analysis():
    """Analyze recurring failure patterns."""
    days = int(request.args.get("days", "14"))

    jenkins_failures = jenkins.analyze_failures(days)
    gitlab_failures = gitlab.analyze_failures(days)

    return jsonify({
        "period_days": days,
        "jenkins": jenkins_failures,
        "gitlab": gitlab_failures,
        "top_recurring": _find_recurring_failures(
            jenkins_failures, gitlab_failures
        )
    })


def _aggregate_failure_causes(*cause_dicts):
    """Merge failure cause dictionaries and return top 5."""
    combined = {}
    for d in cause_dicts:
        for cause, count in d.items():
            combined[cause] = combined.get(cause, 0) + count
    sorted_causes = sorted(combined.items(), key=lambda x: x[1], reverse=True)
    return [{"cause": c, "count": n} for c, n in sorted_causes[:5]]


def _merge_trends(jenkins_trends, gitlab_trends):
    """Merge time-series trends from both sources."""
    merged = {}
    for entry in jenkins_trends + gitlab_trends:
        key = entry["date"]
        if key not in merged:
            merged[key] = {"date": key, "success": 0, "failure": 0, "total": 0}
        merged[key]["success"] += entry.get("success", 0)
        merged[key]["failure"] += entry.get("failure", 0)
        merged[key]["total"] += entry.get("total", 0)
    return sorted(merged.values(), key=lambda x: x["date"])


def _find_recurring_failures(jenkins_f, gitlab_f):
    """Identify failure patterns appearing in both systems."""
    all_patterns = {}
    for f in jenkins_f.get("patterns", []) + gitlab_f.get("patterns", []):
        key = f["pattern"]
        if key not in all_patterns:
            all_patterns[key] = {"pattern": key, "occurrences": 0, "jobs": []}
        all_patterns[key]["occurrences"] += f["count"]
        all_patterns[key]["jobs"].extend(f.get("jobs", []))

    recurring = [p for p in all_patterns.values() if p["occurrences"] >= 3]
    return sorted(recurring, key=lambda x: x["occurrences"], reverse=True)[:10]


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"Starting PipelineHealth on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
