"""Unit tests for CI/CD collectors."""

import pytest
from unittest.mock import patch, MagicMock
from src.collectors.jenkins_collector import JenkinsCollector
from src.collectors.gitlab_collector import GitLabCollector
from src.exporters.prometheus_exporter import MetricsRegistry


@pytest.fixture
def metrics():
    return MetricsRegistry()


@pytest.fixture
def jenkins(metrics):
    return JenkinsCollector("http://jenkins:8080", "admin", "token", metrics)


@pytest.fixture
def gitlab(metrics):
    return GitLabCollector("https://gitlab.example.com", "token", metrics)


class TestJenkinsCollector:
    def test_classify_failure_test_error(self, jenkins):
        log = "Tests run: 45, Failures: 3, Errors: 0"
        assert jenkins.classify_failure(log) == "test_failure"

    def test_classify_failure_docker(self, jenkins):
        log = "docker: Error response from daemon: pull access denied"
        assert jenkins.classify_failure(log) == "docker_error"

    def test_classify_failure_timeout(self, jenkins):
        log = "Build timed out after 30 minutes"
        assert jenkins.classify_failure(log) == "timeout"

    def test_classify_failure_dependency(self, jenkins):
        log = "Could not resolve dependency: com.example:lib:1.0"
        assert jenkins.classify_failure(log) == "dependency_error"

    def test_classify_failure_infra(self, jenkins):
        log = "java.lang.OutOfMemoryError: Java heap space"
        assert jenkins.classify_failure(log) == "infra_error"

    def test_classify_failure_terraform(self, jenkins):
        log = "Error: resource aws_instance.web already exists in state"
        assert jenkins.classify_failure(log) == "terraform_error"

    def test_classify_failure_unknown(self, jenkins):
        log = "Something completely unexpected happened"
        assert jenkins.classify_failure(log) == "unknown"

    @patch.object(JenkinsCollector, "_api_get")
    def test_get_jobs(self, mock_api, jenkins):
        mock_api.return_value = {
            "jobs": [
                {"name": "build-app", "url": "http://jenkins/job/build-app", "color": "blue"},
                {"name": "deploy-prod", "url": "http://jenkins/job/deploy-prod", "color": "red"},
            ]
        }
        jobs = jenkins.get_jobs()
        assert len(jobs) == 2
        assert jobs[0]["name"] == "build-app"


class TestGitLabCollector:
    def test_classify_failure_test(self, gitlab):
        log = "FAILED tests/test_auth.py::TestLogin - AssertionError"
        assert gitlab.classify_failure(log) == "test_failure"

    def test_classify_failure_lint(self, gitlab):
        log = "flake8: 12 errors found"
        assert gitlab.classify_failure(log) == "lint_error"

    def test_classify_failure_runner(self, gitlab):
        log = "This job is stuck because there are no matching runner"
        assert gitlab.classify_failure(log) == "runner_error"

    def test_classify_failure_docker(self, gitlab):
        log = "manifest unknown: manifest unknown"
        assert gitlab.classify_failure(log) == "docker_error"


class TestMetricsRegistry:
    def test_update_build_total(self, metrics):
        metrics.update_build_total("jenkins", 100)
        val = metrics.build_total.labels(source="jenkins")._value.get()
        assert val == 100

    def test_success_rate_calculation(self, metrics):
        metrics.update_build_total("jenkins", 100)
        metrics.update_build_failures("jenkins", 10)
        rate = metrics.build_success_rate.labels(source="jenkins")._value.get()
        assert rate == 90.0

    def test_success_rate_zero_builds(self, metrics):
        metrics.update_build_total("jenkins", 0)
        metrics.update_build_failures("jenkins", 0)
        # Should not divide by zero
        rate = metrics.build_success_rate.labels(source="jenkins")._value.get()
        assert rate == 0
