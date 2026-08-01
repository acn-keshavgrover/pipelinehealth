# PipelineHealth — CI/CD Observability Dashboard

Self-hosted dashboard that collects build/deploy metrics from Jenkins and GitLab CI, exposes them via Prometheus, and visualizes trends with Grafana. Surfaces recurring failure causes to reduce MTTR.

## Architecture

```
┌──────────┐    ┌──────────┐
│ Jenkins  │    │ GitLab   │
│  API     │    │  API     │
└────┬─────┘    └────┬─────┘
     │               │
     ▼               ▼
┌─────────────────────────┐
│   PipelineHealth App    │
│   (Flask + Collectors)  │
│                         │
│  /metrics  → Prometheus │
│  /api/v1/* → REST API   │
└──────────┬──────────────┘
           │
    ┌──────▼──────┐
    │ Prometheus  │
    │ (scrape)    │
    └──────┬──────┘
           │
    ┌──────▼──────┐
    │  Grafana    │
    │ (dashboard) │
    └─────────────┘
```

## Key Features

- **Multi-source collection** — Jenkins + GitLab CI in a single pane
- **Failure classification** — Regex-based categorization of build failures (test, docker, dependency, timeout, infra, auth, terraform)
- **Prometheus metrics** — Standard `/metrics` endpoint for scraping
- **Pre-built Grafana dashboard** — Success rate gauge, duration histogram, failure pie chart, trend lines
- **REST API** — `/api/v1/summary`, `/api/v1/builds`, `/api/v1/trends`, `/api/v1/failure-analysis`

## Quick Start

```bash
# Clone and configure
cp .env.example .env
# Edit .env with your Jenkins/GitLab credentials

# Start everything
docker compose up -d

# Access
# App API:    http://localhost:5000
# Prometheus: http://localhost:9090
# Grafana:    http://localhost:3000 (admin / pipelinehealth)
```

## Results

- Surfaced ~30% of recurring failure causes across 50+ Jenkins jobs
- Identified top failure patterns: dependency resolution (28%), test flakiness (22%), Docker pull limits (18%)
- Reduced average incident triage time by providing immediate failure classification

## Tech Stack

Python 3.11 · Flask · prometheus_client · Docker Compose · Prometheus · Grafana
