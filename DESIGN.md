# Metered API Billing Design

## Overview

This project is a local metering and billing system for a SaaS API. The product emits usage events, the backend stores them as raw facts, a worker rolls them into hourly usage windows, and monthly invoice generation prices those windows against a tiered plan. The repo runs with Docker Compose: Postgres, a Django/DRF API, a React/Vite frontend, and a lightweight worker.

The design favors correctness and debuggability over premature distributed infrastructure. Billing accuracy is contractual, so raw usage events remain the source of truth. Derived state is recomputable where possible, and money-moving changes go through explicit ops actions with audit logs.

## Data Model

`Customer` owns API keys, usage, invoices, and credits. `ApiKey` stores only a prefix and an HMAC-SHA256 hash; the raw key is returned once when created. `UsageEvent` stores the incoming event: globally unique `request_id`, customer, API key, endpoint, units, event timestamp, and ingestion timestamp. `UsageWindow` stores one aggregate row per customer, API key, and hour.

`PricePlan` stores the tier configuration. `Invoice` stores a customer billing period and status. `InvoiceLineItem` stores priced usage tiers, credits, and manual override metadata. `Credit` stores customer-level or invoice-linked credits. `AuditLog` records money-moving ops actions. `WebhookEvent` records payment processor deliveries. `JobRun` records background job locks and status.

The important constraints are in the database, not just comments. `UsageEvent.request_id` is unique. `UsageWindow(customer, api_key, window_start)` is protected by `unique_usage_window_customer_api_key_hour`. `Invoice(customer, period_start, period_end)` is protected by `unique_invoice_customer_period`. `Credit(customer, idempotency_key)` is protected by `unique_credit_customer_idempotency_key`. `WebhookEvent.provider_event_id` and `JobRun.lock_key` are also unique.

Indexes match the queries the app runs: usage events by customer/date and API key/date, windows by customer/window and API key/window, invoices by customer/period, credits by customer/created time, plus unique lookups for webhooks and jobs. Money is stored as integer cents (`amount_cents`) and integer micros (`unit_price_micros`), never floats.

At 10x, I would partition `UsageEvent` monthly by timestamp and add partition-local timestamp indexes or BRIN indexes. At 100x, I would keep Postgres as the billing ledger but move raw event ingest behind a queue and bulk insert path, then copy closed-period raw events to S3, ClickHouse, or a warehouse for long-term analytics. If one Postgres writer became the bottleneck, I would hash partition by customer.

## Idempotency And Concurrency

Event ingestion relies on the unique `request_id` constraint. The view inserts each event inside a transaction and catches `IntegrityError`; a replay increments `duplicate_count` instead of creating another billable row. A concurrency test fires the same request ten times at once and proves only one row is created.

Aggregation is idempotent because it recomputes each bucket from raw events and uses `update_or_create` for the hourly window. It never blindly adds to an existing total. Running aggregation twice produces the same window totals.

Invoice generation is idempotent through the unique customer/period invoice and recomputation from `UsageWindow`. For mutable invoices, usage line items are rebuilt from the current window totals. Paid invoices are skipped. Invoices with manual overrides are not rewritten; the total is recomputed from existing lines instead.

Credits use `Credit(customer, idempotency_key)` as the dedupe key. Invoice-linked credits lock the invoice with `select_for_update`, create one negative line item, and recompute the invoice total. A duplicate request returns the existing credit and does not apply a second negative line item.

Payment webhooks verify an HMAC over the raw request body and store the provider event id. If the same delivery arrives three times, the first delivery is processed and the later deliveries return as replays. If the invoice is already paid, the handler records no second financial effect.

Job overlap is handled by `JobRun.start`, which locks the row with `select_for_update`. If another run has the same lock key and is still running, the command exits cleanly.

## Aggregation Pipeline

The pipeline is:

`UsageEvent` -> `UsageWindow` -> `InvoiceLineItem`.

Raw events are immutable billing facts. Windows are derived hourly summaries. Invoice usage line items are derived from windows and the customer price plan. The worker runs aggregation every 60 seconds for the last 48 hours, which keeps recent late and out-of-order events moving into the right hourly buckets.

The Billing Inspector compares raw event totals, window totals, and invoice line item units for a selected customer and period. That gives ops a direct path to debug drift without opening the database.

## Billing And Invoice Immutability

The default plan prices the first 10,000 units at zero, the next 90,000 units at 1,000 micros per unit, and units above 100,000 at 500 micros per unit. `micros_to_cents` uses integer math.

Invoices can be regenerated while still mutable. Once paid, an invoice is treated as closed and is not rewritten by generation. Manual overrides mark the line with `overridden_at`, `override_reason`, and `overridden_by`; generation preserves those rows. Invoice-linked credits are represented as negative line items and the invoice total is floored at zero. Customer-level credits are tracked but not automatically applied to future invoices yet.

## Late-Arriving Events And Reconciliation

Before invoice issuance, late events are handled by rerunning aggregation and invoice generation. The raw event timestamp controls which hourly window receives the event, so out-of-order delivery is fine.

After invoice issuance, especially after payment, the system does not silently mutate the invoice. The current implementation flags late events in Billing Inspector when an event timestamp falls inside the invoice period but `ingested_at` is after `invoice.issued_at`. In production I would create an adjustment invoice or credit memo in the next cycle, linked back to the original invoice and recorded in the audit log.

## Security And Tenant Isolation

Customer endpoints use API key authentication. `ApiKeyAuthentication` hashes the presented key and loads a non-revoked key. It sets `request.customer` and `request.api_key`. Customer-facing reads use `CustomerScopedMixin`, so invoice and usage querysets are filtered by the authenticated customer before IDs are resolved.

API keys are generated once and stored as HMAC-SHA256 using `API_KEY_HASH_SECRET`. Only a short prefix is stored for display/search. Plaintext keys are not retrievable.

Ops endpoints require `X-Ops-Token`. This is intentionally simple demo auth; production would use real internal identity, roles, and per-action authorization. The webhook endpoint verifies an HMAC signature over `request.body`, not parsed JSON, so the exact delivered bytes are signed.

`AuditLog` rows are append-only in normal code paths. The model rejects updates and deletes, and Django admin exposes the table as read-only.

## Ops Workflows And Audit Trail

Ops can list customers, inspect customer usage and invoices, see anomaly signals, issue credits, override line items, and open Billing Inspector. The UI requires a reason and confirmation for credits and overrides.

`AuditLog` captures actor, action, target type/id, before/after JSON, reason, and timestamp. Billing Inspector shows recent jobs and audit rows so support can answer “why is this invoice wrong?” from the app.

## Background Jobs And Job Locking

The `worker` service runs `run_worker`. Each tick runs `aggregate_usage` for the last 48 hours. On the first day of the month, it runs `generate_invoices` for the previous month.

The scheduler itself is thin. The commands own their locks through `JobRun`, so multiple workers or retries do not double-apply billing work.

## Observability

For production I would alert on failed or stale `JobRun` rows, aggregation lag beyond the expected 60-second cadence, webhook signature failures or replay spikes, Billing Inspector mismatches between raw events/windows/invoices, unusual credit or override volume, and invoice generation skips for paid invoices. The local version exposes the core data for this through `JobRun`, `WebhookEvent`, `AuditLog`, and Billing Inspector instead of adding a full metrics stack.

## Scaling Path

The target is 5,000 active customers, 200 events/sec sustained, 2,000/sec peak, and about 500M events/month. Two hundred events/sec is about 17.3M events/day. The current design is enough for the take-home and a small product, but not for unbounded raw event growth.

The first thing to break is raw event ingest and table size. I would partition `UsageEvent` by month, bulk insert batches, add connection pooling, and put a queue in front of ingestion. The second thing to break is aggregation scan cost. I would aggregate by partition and watermark, then move hot aggregation to a stream processor if needed. The third thing to break is debugging over raw events. I would keep Postgres as the billing ledger and copy raw events to an analytical store for investigation.

Monthly invoice generation is smaller: 5,000 customers means 5,000 invoices per month, which is not the hard part compared with ingest.

## Failure Modes

Raw events grow too quickly. The fix is partitioning, retention policy, and warehouse archival after billing close.

The worker stops or falls behind. `JobRun` status makes stale jobs visible, and aggregation/invoice generation can be rerun because they are idempotent.

A customer disputes an invoice. Billing Inspector compares raw events, windows, invoice line items, credits, overrides, jobs, and audit logs. Ops can issue an audited credit or line-item override instead of editing data by hand.

## Threat Model

A hostile customer may guess another invoice ID or API key ID. Customer reads are scoped through the authenticated customer, and the event payload cannot choose `customer_id`; the backend always uses the customer from the API key.

A hostile internal user may issue improper credits or override invoices. In this demo they need the ops token, must provide a reason, and every action leaves before/after audit data. The remaining production gap is real RBAC and approval workflows for large credits.

A compromised webhook source may replay old deliveries or send modified bodies. Invalid signatures are rejected. Replayed provider event IDs are ignored. Already-paid invoices do not get a second paid effect.

## Trade-Offs

I chose Django/DRF/Postgres over FastAPI or Node because the ORM, transactions, admin, and company-stack alignment matter for a billing system. The rejected option would have been a lighter API framework, but it would have moved more transaction and admin safety into custom code.

I chose a DB-backed worker over Celery/Redis to keep local setup simple while still showing locked jobs. Celery would be a good production step, but it adds a broker and more moving parts before the core billing behavior is proven.

I kept raw events instead of aggregate-only storage because billing disputes need recomputation. Aggregate-only storage would be cheaper, but it would make late-event reconciliation and invoice debugging much weaker. I kept ingestion synchronous for clarity; the scale path is queue plus bulk insert.

## What I Did Not Build

I did not build real login/RBAC, a real payment processor, a full adjustment invoice engine, a distributed queue, a production metrics stack, or a warehouse pipeline. Those are the next steps after the core ledger behavior is correct.

## Correctness Proof

| Failure scenario | Protection | Where enforced | Test |
|---|---|---|---|
| duplicate `request_id` | unique DB constraint, catch `IntegrityError` | `UsageEvent.request_id`, `EventIngestionView` | `test_duplicate_request_id_does_not_create_two_rows` |
| concurrent duplicate event | same unique constraint under concurrent API calls | DB + `EventIngestionView` transaction | `test_concurrent_duplicate_request_id_creates_one_event` |
| aggregator rerun | recompute from raw events, `update_or_create` window | `aggregate_usage` | `test_running_aggregation_twice_does_not_double_count` |
| invoice generation rerun | unique customer/period invoice, rebuild usage lines | `generate_invoice_for_customer` | `test_running_invoice_generation_twice_does_not_double_bill` |
| paid invoice regeneration | skip paid invoices | `generate_invoice_for_customer` | `test_paid_invoice_is_not_changed_by_generate_invoices` |
| duplicate credit idempotency key | unique customer/idempotency key | `Credit` constraint, `issue_credit` | `test_duplicate_credit_idempotency_key_does_not_double_credit` |
| invoice-linked credit replay | same idempotency key, invoice lock | `issue_credit`, `select_for_update` | `test_duplicate_invoice_linked_credit_does_not_double_apply` |
| webhook replay | unique provider event ID | `WebhookEvent.provider_event_id` | `test_same_provider_event_id_replay_does_not_double_apply` |
| cross-tenant invoice guess | customer-scoped queryset | `CustomerScopedMixin`, invoice views | `test_customer_cannot_retrieve_another_customers_invoice` |
| API key leakage | HMAC hash only, prefix stored | `hash_api_key`, `ApiKey.create_key` | `test_api_key_hash_is_hmac_not_raw_or_plain_sha256` |
| audit tampering | append-only model, read-only admin | `AuditLog.save/delete`, `AuditLogAdmin` | audit action tests plus implementation guard |
