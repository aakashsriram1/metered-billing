# Metered API Billing

A local metering and billing system for a SaaS API. It ingests idempotent usage events, aggregates them into hourly windows, generates tiered monthly invoices, handles mock payment webhooks, and provides customer and ops frontends.

## Stack

- Backend: Django, Django REST Framework
- Database: Postgres
- Frontend: React, Vite, TypeScript
- Charts/UI helpers: Recharts, lucide-react
- Local runtime: Docker Compose
- Background jobs: Django management command worker with DB-backed `JobRun` locks

## Local Setup

Start all services:

```bash
docker compose up --build
```

This starts:

- `db`: Postgres on `localhost:5432`
- `backend`: Django API on `localhost:8000`
- `frontend`: Vite app on `localhost:5173`
- `worker`: lightweight scheduler for aggregation and invoice generation

In a second terminal, run migrations:

```bash
docker compose exec backend python manage.py migrate
```

Seed demo data:

```bash
docker compose exec backend python manage.py seed_demo_data --reset
```

The seed command prints demo customers and raw `sk_test_...` API keys. Copy one key for the Customer Dashboard.

## Frontend

Open:

```text
http://localhost:5173
```

Customer Dashboard:

- paste a printed `sk_test_...` API key
- view usage cards, usage chart, invoice list, and invoice detail

Ops Console:

- use ops token `dev-ops-token`
- view customers, anomalies, usage, invoices, credits
- issue customer-level or invoice-linked credits
- override invoice line items with an audit trail
- use Billing Inspector to debug raw events, windows, invoices, jobs, and audit logs

## Backend API Summary

Customer-facing endpoints require:

```text
Authorization: Bearer <raw_api_key>
```

Endpoints:

- `POST /v1/events`
- `GET /v1/usage`
- `GET /v1/invoices`
- `GET /v1/invoices/{id}`

Ops endpoints require:

```text
X-Ops-Token: dev-ops-token
```

Endpoints:

- `GET /ops/customers`
- `GET /ops/customers/{id}`
- `GET /ops/customers/{id}/billing-inspector`
- `POST /ops/customers/{id}/credits`
- `PATCH /ops/invoices/{invoice_id}/line-items/{line_item_id}`

Webhook endpoint:

- `POST /webhooks/payments`

## Background Jobs

The `worker` service runs:

- `aggregate_usage` every 60 seconds for the last 48 hours
- `generate_invoices` on the first day of the month for the previous month

Both jobs use DB-backed `JobRun` locks so overlapping runs skip safely.

Manual commands:

```bash
docker compose exec backend python manage.py aggregate_usage
docker compose exec backend python manage.py generate_invoices --period-start 2026-05-01T00:00:00Z --period-end 2026-06-01T00:00:00Z
docker compose exec backend python manage.py run_worker --once
```

## Tests

Backend:

```bash
docker compose exec backend python manage.py test
```

Frontend:

```bash
docker compose exec frontend npm run build
```

## Environment Variables

See `.env.example` for the full list:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `PAYMENT_WEBHOOK_SECRET`
- `API_KEY_HASH_SECRET`
- `OPS_TOKEN`

The values in `docker-compose.yml` are local demo secrets only. Do not use them in production.

## More Docs

- `DEMO.md`: step-by-step evaluator walkthrough
- `DESIGN.md`: architecture, correctness, scaling, and trade-offs
