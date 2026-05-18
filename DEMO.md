# Metered Billing Demo Walkthrough

This walkthrough demonstrates the full billing lifecycle: 
usage ingestion → hourly aggregation → invoice generation → customer visibility → ops corrections → auditability and replay safety.

## 1. Start The App

```bash
docker compose up --build
```

In a second terminal:

```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_demo_data --reset
```

The seed command prints demo customers and raw `sk_test_...` API keys. Copy one key.

Open the frontend:

```text
http://localhost:5173
```

The Django backend API runs at:

```text
http://localhost:8000
```

The backend does not serve a homepage at `/`, so `http://localhost:8000/` may show Django's 404 page. That is expected. Use:

```text
http://localhost:8000/admin/
http://localhost:8000/v1/usage
http://localhost:8000/v1/invoices
http://localhost:8000/ops/customers
```

The seeded `sk_test_...` keys are printed once and may change after each reset. Use the latest key printed by the seed command.

## 2. Customer Dashboard

1. Open `Customer Dashboard`.
2. Paste one printed `sk_test_...` key.
3. Click `Save`.
4. Confirm the page shows:
   - current-period usage cards
   - daily usage chart
   - recent usage windows
   - invoice list
5. Click `View details` on an invoice.
6. Confirm invoice line items, unit prices, status, and total render.

## 3. Ops Console

1. Open `Ops Console`.
2. Enter ops token:

```text
dev-ops-token
```

3. Click `Save`. After saving the token, the customer list should load.
4. Select `Anomaly Systems` to see the anomaly flag.
5. In `Billing Inspector`, keep the default current-month range or choose a seeded month.
6. Click `Load Billing Inspector`.
7. Confirm the inspector shows:
   - raw events -> usage windows -> invoice line items
   - warnings when totals drift
   - recent jobs
   - recent audit logs when money-moving actions exist

## 4. Money-Moving Ops Actions

With a customer selected:

1. Customer-level credit:
   - amount: `500`
   - idempotency key: `demo-ui-credit-1`
   - leave invoice ID blank
   - reason: `Demo customer-level goodwill credit`
   - submit and confirm

2. Invoice-linked credit:
   - copy an invoice ID from the selected customer invoice table
   - amount: `500`
   - idempotency key: `demo-ui-invoice-credit-1`
   - reason: `Demo invoice-linked goodwill credit`
   - submit and confirm
   - confirm a negative credit line item appears after refresh

3. Line-item override:
   - expand an invoice
   - click `Use for override` next to a line item
   - change amount cents
   - add reason: `Demo usage dispute correction`
   - submit and confirm
   - reload Billing Inspector and confirm audit logs reflect the change

## 5. Correctness Demos

Set a key from seed output:

```bash
API_KEY='paste_printed_sk_test_key_here'
```

Duplicate event replay: run the same request twice.

```bash
curl -X POST http://localhost:8000/v1/events \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"events":[{"request_id":"demo_replay_req_1","endpoint":"/v1/chat","units":42,"timestamp":"2026-05-16T18:00:00Z"}]}'

curl -X POST http://localhost:8000/v1/events \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"events":[{"request_id":"demo_replay_req_1","endpoint":"/v1/chat","units":42,"timestamp":"2026-05-16T18:00:00Z"}]}'
```

Expected: first response has `accepted_count: 1`; second has `duplicate_count: 1`.

Duplicate credit replay:

1. In Ops Console, issue a credit with idempotency key `demo-credit-replay`.
2. Submit the same form again with the same idempotency key.
3. Expected: no second `Credit` row or second negative invoice line item is created.

Payment webhook signature verification and replay safety are covered by automated tests:

```bash
docker compose exec backend python manage.py test billing.tests.PaymentWebhookTests
```

## 6. Test Commands

```bash
docker compose exec backend python manage.py test
docker compose exec frontend npm run build
```

Expected: backend tests pass and frontend build succeeds.
