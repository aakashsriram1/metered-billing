import { Fragment, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, FileText, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchJson, opsFetchJson } from "./api";
import type {
  BillingInspectorResponse,
  Invoice,
  InvoiceDetail,
  InvoiceLineItem,
  OpsCustomerDetail,
  OpsCustomerSummary,
  OpsInvoice,
  UsageResponse,
  UsageWindow,
} from "./types";

const API_KEY_STORAGE_KEY = "metered_billing_customer_api_key";
const OPS_TOKEN_STORAGE_KEY = "metered_billing_ops_token";

type LoadState = "idle" | "loading" | "loaded" | "error";
type Tab = "customer" | "ops";

function currentMonthRange() {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1, 0, 0, 0));
  return {
    start: start.toISOString(),
    end: now.toISOString(),
  };
}

function currentMonthDateRange() {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}

function formatDate(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function money(cents: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

function unitPrice(micros: number) {
  if (micros === 0) return "$0.0000";
  return `$${(micros / 1_000_000).toFixed(4)}`;
}

function shortId(id: string) {
  return `${id.slice(0, 8)}...`;
}

async function copyText(value: string) {
  await navigator.clipboard?.writeText(value);
}

function groupUsageByDay(windows: UsageWindow[]) {
  const totals = new Map<string, number>();
  for (const window of windows) {
    const day = new Date(window.window_start).toISOString().slice(0, 10);
    totals.set(day, (totals.get(day) || 0) + window.total_units);
  }
  return Array.from(totals.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, units]) => ({
      day: new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(new Date(day)),
      units,
    }));
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("customer");

  return (
    <main className="app-shell">
      <nav className="top-tabs" aria-label="Dashboard sections">
        <button className={activeTab === "customer" ? "tab active" : "tab"} onClick={() => setActiveTab("customer")}>
          Customer Dashboard
        </button>
        <button className={activeTab === "ops" ? "tab active" : "tab"} onClick={() => setActiveTab("ops")}>
          Ops Console
        </button>
      </nav>
      {activeTab === "customer" ? <CustomerDashboard /> : <OpsConsole />}
    </main>
  );
}

function CustomerDashboard() {
  const [apiKeyInput, setApiKeyInput] = useState(() => localStorage.getItem(API_KEY_STORAGE_KEY) || "");
  const [apiKey, setApiKey] = useState("");
  const [usage, setUsage] = useState<UsageWindow[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<string | null>(null);
  const [invoiceDetail, setInvoiceDetail] = useState<InvoiceDetail | null>(null);
  const [dashboardState, setDashboardState] = useState<LoadState>("idle");
  const [detailState, setDetailState] = useState<LoadState>("idle");
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");

  async function loadDashboard(activeKey = apiKey) {
    if (!activeKey) {
      setDashboardState("idle");
      setUsage([]);
      setInvoices([]);
      setInvoiceDetail(null);
      return;
    }

    const { start, end } = currentMonthRange();
    setDashboardState("loading");
    setError("");
    try {
      const [usageResponse, invoiceResponse] = await Promise.all([
        fetchJson<UsageResponse>(
          `/v1/usage?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&page_size=200`,
          activeKey,
        ),
        fetchJson<{ results: Invoice[] }>("/v1/invoices", activeKey),
      ]);
      setUsage(usageResponse.results);
      setInvoices(invoiceResponse.results);
      setDashboardState("loaded");
    } catch (err) {
      setDashboardState("error");
      setError(err instanceof Error ? err.message : "Unable to load dashboard data.");
    }
  }

  async function loadInvoiceDetail(invoiceId: string) {
    if (!apiKey) return;
    setSelectedInvoiceId(invoiceId);
    setDetailState("loading");
    setDetailError("");
    try {
      const detail = await fetchJson<InvoiceDetail>(`/v1/invoices/${invoiceId}`, apiKey);
      setInvoiceDetail(detail);
      setDetailState("loaded");
    } catch (err) {
      setInvoiceDetail(null);
      setDetailState("error");
      setDetailError(err instanceof Error ? err.message : "Unable to load invoice detail.");
    }
  }

  function saveApiKey() {
    const trimmed = apiKeyInput.trim();
    if (!trimmed) return;
    localStorage.setItem(API_KEY_STORAGE_KEY, trimmed);
    setApiKey(trimmed);
    loadDashboard(trimmed);
  }

  function clearApiKey() {
    localStorage.removeItem(API_KEY_STORAGE_KEY);
    setApiKey("");
    setApiKeyInput("");
    setUsage([]);
    setInvoices([]);
    setSelectedInvoiceId(null);
    setInvoiceDetail(null);
    setDashboardState("idle");
    setError("");
    setDetailError("");
  }

  const usageTotals = useMemo(
    () => ({
      totalUnits: usage.reduce((sum, window) => sum + window.total_units, 0),
      eventCount: usage.reduce((sum, window) => sum + window.event_count, 0),
      windowCount: usage.length,
    }),
    [usage],
  );

  const chartData = useMemo(() => groupUsageByDay(usage), [usage]);

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Customer Dashboard</h1>
          <p>API usage and invoices</p>
        </div>
        <TokenPanel
          icon={<KeyRound size={18} />}
          token={apiKeyInput}
          saved={Boolean(apiKey)}
          connectedLabel="Connected"
          missingLabel="Missing API key"
          placeholder="Paste seeded sk_test key"
          ariaLabel="Customer API key"
          onChange={setApiKeyInput}
          onSave={saveApiKey}
          onClear={clearApiKey}
        />
      </header>

      {!apiKey && (
        <EmptyState
          title="Paste a seeded API key"
          message="Run seed_demo_data, copy one sk_test key, and save it here to load the dashboard."
        />
      )}

      {apiKey && (
        <>
          <div className="toolbar">
            <button className="secondary" onClick={() => loadDashboard()} disabled={dashboardState === "loading"}>
              <RefreshCw size={16} />
              Refresh
            </button>
          </div>

          {dashboardState === "loading" && <PanelMessage message="Loading customer billing data..." />}
          {dashboardState === "error" && <ErrorState message={error} />}

          {dashboardState === "loaded" && (
            <>
              <section className="metric-grid">
                <MetricCard label="Total units this period" value={usageTotals.totalUnits.toLocaleString()} />
                <MetricCard label="Events this period" value={usageTotals.eventCount.toLocaleString()} />
                <MetricCard label="Usage windows" value={usageTotals.windowCount.toLocaleString()} />
                <MetricCard label="Current-period estimate" value="Usage only" muted />
              </section>

              <section className="dashboard-grid">
                <UsageChartPanel chartData={chartData} />
                <UsageWindowsPanel usage={usage} />
                <InvoiceListPanel invoices={invoices} onSelect={loadInvoiceDetail} />
                <InvoiceDetailPanel
                  selectedInvoiceId={selectedInvoiceId}
                  invoiceDetail={invoiceDetail}
                  detailState={detailState}
                  detailError={detailError}
                />
              </section>
            </>
          )}
        </>
      )}
    </>
  );
}

function OpsConsole() {
  const [opsTokenInput, setOpsTokenInput] = useState(() => localStorage.getItem(OPS_TOKEN_STORAGE_KEY) || "dev-ops-token");
  const [opsToken, setOpsToken] = useState(() => localStorage.getItem(OPS_TOKEN_STORAGE_KEY) || "");
  const [customers, setCustomers] = useState<OpsCustomerSummary[]>([]);
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [selectedOpsInvoiceId, setSelectedOpsInvoiceId] = useState<string | null>(null);
  const [customerDetail, setCustomerDetail] = useState<OpsCustomerDetail | null>(null);
  const [listState, setListState] = useState<LoadState>("idle");
  const [detailState, setDetailState] = useState<LoadState>("idle");
  const [inspectorState, setInspectorState] = useState<LoadState>("idle");
  const [inspectorData, setInspectorData] = useState<BillingInspectorResponse | null>(null);
  const [inspectorError, setInspectorError] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [inspectorRange, setInspectorRange] = useState(() => currentMonthDateRange());
  const [creditForm, setCreditForm] = useState({
    amount_cents: "500",
    reason: "",
    idempotency_key: "",
    invoice_id: "",
  });
  const [overrideForm, setOverrideForm] = useState({
    invoice_id: "",
    line_item_id: "",
    amount_cents: "",
    reason: "",
  });

  async function loadCustomers(activeToken = opsToken) {
    if (!activeToken) {
      setListState("idle");
      setCustomers([]);
      return;
    }
    setListState("loading");
    setError("");
    try {
      const response = await opsFetchJson<{ results: OpsCustomerSummary[] }>("/ops/customers", activeToken);
      setCustomers(response.results);
      setListState("loaded");
    } catch (err) {
      setListState("error");
      setError(err instanceof Error ? err.message : "Unable to load customers.");
    }
  }

  async function loadCustomerDetail(customerId: string, activeToken = opsToken) {
    setSelectedCustomerId(customerId);
    setSelectedOpsInvoiceId(null);
    setDetailState("loading");
    setError("");
    try {
      const detail = await opsFetchJson<OpsCustomerDetail>(`/ops/customers/${customerId}`, activeToken);
      setCustomerDetail(detail);
      setInspectorData(null);
      setInspectorState("idle");
      setInspectorError("");
      setDetailState("loaded");
    } catch (err) {
      setCustomerDetail(null);
      setDetailState("error");
      setError(err instanceof Error ? err.message : "Unable to load customer detail.");
    }
  }

  async function loadBillingInspector() {
    if (!customerDetail || !opsToken) return;
    setInspectorState("loading");
    setInspectorError("");
    try {
      const query = new URLSearchParams({
        period_start: inspectorRange.start,
        period_end: inspectorRange.end,
      });
      const data = await opsFetchJson<BillingInspectorResponse>(
        `/ops/customers/${customerDetail.id}/billing-inspector?${query.toString()}`,
        opsToken,
      );
      setInspectorData(data);
      setInspectorState("loaded");
    } catch (err) {
      setInspectorData(null);
      setInspectorState("error");
      setInspectorError(err instanceof Error ? err.message : "Unable to load billing inspector.");
    }
  }

  function useLineItemForOverride(invoice: OpsInvoice, lineItem: InvoiceLineItem) {
    setSelectedOpsInvoiceId(invoice.id);
    setOverrideForm({
      invoice_id: invoice.id,
      line_item_id: lineItem.id,
      amount_cents: String(lineItem.amount_cents),
      reason: "",
    });
    setMessage(`Loaded ${shortId(lineItem.id)} into the override form.`);
    setError("");
  }

  function saveOpsToken() {
    const trimmed = opsTokenInput.trim();
    if (!trimmed) return;
    localStorage.setItem(OPS_TOKEN_STORAGE_KEY, trimmed);
    setOpsToken(trimmed);
    loadCustomers(trimmed);
  }

  function clearOpsToken() {
    localStorage.removeItem(OPS_TOKEN_STORAGE_KEY);
    setOpsToken("");
    setOpsTokenInput("");
    setCustomers([]);
    setCustomerDetail(null);
    setSelectedCustomerId(null);
    setSelectedOpsInvoiceId(null);
    setListState("idle");
    setDetailState("idle");
    setInspectorState("idle");
    setInspectorData(null);
    setInspectorError("");
    setMessage("");
    setError("");
  }

  async function submitCredit() {
    if (!customerDetail || !opsToken) return;
    const amount = Number(creditForm.amount_cents);
    if (!creditForm.reason.trim()) {
      setError("Credit reason is required.");
      return;
    }
    if (!creditForm.idempotency_key.trim()) {
      setError("Credit idempotency key is required.");
      return;
    }
    if (!Number.isInteger(amount) || amount <= 0) {
      setError("Credit amount must be a positive integer in cents.");
      return;
    }
    const creditScope = creditForm.invoice_id.trim() ? "invoice-linked" : "customer-level";
    if (!window.confirm(`This creates a ${creditScope} billing credit and audit log. Continue?`)) return;

    setMessage("");
    setError("");
    try {
      await opsFetchJson(`/ops/customers/${customerDetail.id}/credits`, opsToken, {
        method: "POST",
        body: {
          amount_cents: amount,
          reason: creditForm.reason.trim(),
          idempotency_key: creditForm.idempotency_key.trim(),
          ...(creditForm.invoice_id.trim() ? { invoice_id: creditForm.invoice_id.trim() } : {}),
        },
      });
      setMessage("Credit created successfully.");
      setCreditForm({ amount_cents: "500", reason: "", idempotency_key: "", invoice_id: "" });
      await loadCustomerDetail(customerDetail.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create credit.");
    }
  }

  async function submitOverride() {
    if (!customerDetail || !opsToken) return;
    const amount = Number(overrideForm.amount_cents);
    if (!overrideForm.reason.trim()) {
      setError("Override reason is required.");
      return;
    }
    if (!overrideForm.invoice_id.trim() || !overrideForm.line_item_id.trim()) {
      setError("Invoice ID and line item ID are required.");
      return;
    }
    if (!Number.isInteger(amount)) {
      setError("Override amount must be an integer in cents.");
      return;
    }
    if (!window.confirm(`Final warning: set line item ${shortId(overrideForm.line_item_id.trim())} to ${money(amount)} and write an audit log?`)) return;

    setMessage("");
    setError("");
    try {
      await opsFetchJson(
        `/ops/invoices/${overrideForm.invoice_id.trim()}/line-items/${overrideForm.line_item_id.trim()}`,
        opsToken,
        {
          method: "PATCH",
          body: {
            amount_cents: amount,
            reason: overrideForm.reason.trim(),
          },
        },
      );
      setMessage("Line item override applied successfully.");
      setOverrideForm({ invoice_id: "", line_item_id: "", amount_cents: "", reason: "" });
      await loadCustomerDetail(customerDetail.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to override line item.");
    }
  }

  useEffect(() => {
    if (opsToken) {
      loadCustomers(opsToken);
    }
  }, []);

  const opsTokenStatus = opsToken
    ? { label: "Ops token saved", tone: "connected" as const }
    : opsTokenInput.trim()
      ? { label: "Save token to connect", tone: "missing" as const }
      : { label: "Missing ops token", tone: "missing" as const };

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Ops Console</h1>
          <p>Billing operations and customer support</p>
        </div>
        <TokenPanel
          icon={<ShieldCheck size={18} />}
          token={opsTokenInput}
          saved={Boolean(opsToken)}
          connectedLabel="Ops token saved"
          missingLabel="Missing ops token"
          statusLabel={opsTokenStatus.label}
          statusTone={opsTokenStatus.tone}
          placeholder="dev-ops-token"
          ariaLabel="Ops token"
          onChange={setOpsTokenInput}
          onSave={saveOpsToken}
          onClear={clearOpsToken}
        />
      </header>

      {!opsToken && (
        <EmptyState title="Save an ops token" message="Use dev-ops-token for the local demo environment." />
      )}

      {opsToken && (
        <>
          <div className="toolbar">
            <button className="secondary" onClick={() => loadCustomers()} disabled={listState === "loading"}>
              <RefreshCw size={16} />
              Refresh customers
            </button>
          </div>

          {message && <SuccessState message={message} />}
          {error && <ErrorState message={error} />}
          {listState === "loading" && <PanelMessage message="Loading customers..." />}

          <section className="ops-grid">
            <Panel title="Customers">
              {listState === "error" && <ErrorState message={error} />}
              {listState === "loaded" && customers.length === 0 && (
                <EmptyState title="No customers" message="Run seed_demo_data to create demo customers." compact />
              )}
              {customers.length > 0 && (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Customer</th>
                        <th>Email</th>
                        <th>Usage</th>
                        <th>Invoices</th>
                        <th>Anomaly</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {customers.map((customer) => (
                        <tr key={customer.id} className={selectedCustomerId === customer.id ? "selected-row" : ""}>
                          <td>{customer.name}</td>
                          <td>{customer.email}</td>
                          <td>{customer.current_month_usage_total.toLocaleString()}</td>
                          <td>{customer.invoice_count}</td>
                          <td>{customer.anomaly ? <span className="pill danger">anomaly</span> : "-"}</td>
                          <td>
                            <button className="link-button" onClick={() => loadCustomerDetail(customer.id)}>
                              View
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>

            <Panel title="Customer Detail">
              {!selectedCustomerId && (
                <EmptyState title="Select a customer" message="Choose a customer to inspect usage, invoices, and credits." compact />
              )}
              {detailState === "loading" && <PanelMessage message="Loading customer detail..." />}
              {detailState === "error" && <ErrorState message={error} />}
              {detailState === "loaded" && customerDetail && (
                <div className="ops-detail">
                  <div className="detail-header">
                    <div>
                      <strong>{customerDetail.name}</strong>
                      <span className="muted-text">{customerDetail.email}</span>
                    </div>
                    {customerDetail.anomaly ? <span className="pill danger">anomaly</span> : <span className="pill paid">normal</span>}
                  </div>

                  <h3>Recent usage windows</h3>
                  <MiniUsageTable usage={customerDetail.recent_usage_windows} />

                  <h3>Invoices</h3>
                  <InvoiceOpsTable
                    invoices={customerDetail.invoices}
                    selectedInvoiceId={selectedOpsInvoiceId}
                    onSelectInvoice={setSelectedOpsInvoiceId}
                    onUseLineItem={useLineItemForOverride}
                  />

                  <h3>Credits</h3>
                  <CreditsTable credits={customerDetail.credits} />

                  <h3>Billing Inspector</h3>
                  <BillingInspectorPanel
                    range={inspectorRange}
                    data={inspectorData}
                    state={inspectorState}
                    error={inspectorError}
                    onRangeChange={setInspectorRange}
                    onLoad={loadBillingInspector}
                  />
                </div>
              )}
            </Panel>

            {customerDetail && (
              <>
                <Panel title="Issue Credit">
                  <WarningText message="This creates a billing credit and audit log." />
                  <p className="form-note">
                    {creditForm.invoice_id.trim()
                      ? "Mode: invoice-linked credit. A negative invoice line item will be created."
                      : "Mode: customer-level credit. It will be tracked but not automatically applied to an invoice."}
                  </p>
                  <div className="form-grid">
                    <label>
                      Amount cents
                      <input
                        type="number"
                        min="1"
                        value={creditForm.amount_cents}
                        onChange={(event) => setCreditForm({ ...creditForm, amount_cents: event.target.value })}
                      />
                    </label>
                    <label>
                      Idempotency key
                      <input
                        value={creditForm.idempotency_key}
                        onChange={(event) => setCreditForm({ ...creditForm, idempotency_key: event.target.value })}
                        placeholder="credit_123"
                      />
                    </label>
                    <label>
                      Optional invoice ID
                      <input
                        value={creditForm.invoice_id}
                        onChange={(event) => setCreditForm({ ...creditForm, invoice_id: event.target.value })}
                        placeholder="invoice uuid"
                      />
                    </label>
                    <label className="full">
                      Reason
                      <textarea
                        value={creditForm.reason}
                        onChange={(event) => setCreditForm({ ...creditForm, reason: event.target.value })}
                        placeholder="Required reason"
                      />
                    </label>
                  </div>
                  <button className="danger-button" onClick={submitCredit}>Issue credit</button>
                </Panel>

                <Panel title="Override Line Item">
                  <WarningText message="This changes a billed invoice line item and writes an audit log." />
                  <p className="form-note">
                    Final warning before submit: this changes invoice money and records an immutable audit entry.
                  </p>
                  <div className="form-grid">
                    <label>
                      Invoice ID
                      <input
                        value={overrideForm.invoice_id}
                        onChange={(event) => setOverrideForm({ ...overrideForm, invoice_id: event.target.value })}
                        placeholder="invoice uuid"
                      />
                    </label>
                    <label>
                      Line item ID
                      <input
                        value={overrideForm.line_item_id}
                        onChange={(event) => setOverrideForm({ ...overrideForm, line_item_id: event.target.value })}
                        placeholder="line item uuid"
                      />
                    </label>
                    <label>
                      New amount cents
                      <input
                        type="number"
                        value={overrideForm.amount_cents}
                        onChange={(event) => setOverrideForm({ ...overrideForm, amount_cents: event.target.value })}
                        placeholder="2500"
                      />
                    </label>
                    <label className="full">
                      Reason
                      <textarea
                        value={overrideForm.reason}
                        onChange={(event) => setOverrideForm({ ...overrideForm, reason: event.target.value })}
                        placeholder="Required reason"
                      />
                    </label>
                  </div>
                  <button className="danger-button" onClick={submitOverride}>Override line item</button>
                </Panel>
              </>
            )}
          </section>
        </>
      )}
    </>
  );
}

function TokenPanel({
  icon,
  token,
  saved,
  connectedLabel,
  missingLabel,
  statusLabel,
  statusTone,
  placeholder,
  ariaLabel,
  onChange,
  onSave,
  onClear,
}: {
  icon: ReactNode;
  token: string;
  saved: boolean;
  connectedLabel: string;
  missingLabel: string;
  statusLabel?: string;
  statusTone?: "connected" | "missing";
  placeholder: string;
  ariaLabel: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onClear: () => void;
}) {
  const resolvedStatusLabel = statusLabel || (saved ? connectedLabel : missingLabel);
  const resolvedStatusTone = statusTone || (saved ? "connected" : "missing");

  return (
    <section className="key-panel" aria-label={ariaLabel}>
      <div className={`status ${resolvedStatusTone}`}>
        {resolvedStatusTone === "connected" ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
        {resolvedStatusLabel}
      </div>
      <div className="key-row">
        {icon}
        <input
          type="password"
          value={token}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSave();
          }}
          placeholder={placeholder}
          aria-label={ariaLabel}
        />
        <button onClick={onSave}>Save</button>
        <button className="secondary" onClick={onClear}>Clear</button>
      </div>
    </section>
  );
}

function UsageChartPanel({ chartData }: { chartData: { day: string; units: number }[] }) {
  return (
    <Panel title="Daily Usage">
      {chartData.length === 0 ? (
        <EmptyState title="No usage yet" message="No usage windows were found for the current period." compact />
      ) : (
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="day" tickLine={false} axisLine={false} />
              <YAxis tickLine={false} axisLine={false} width={70} />
              <Tooltip formatter={(value) => [Number(value).toLocaleString(), "units"]} />
              <Bar dataKey="units" fill="#2563eb" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}

function UsageWindowsPanel({ usage }: { usage: UsageWindow[] }) {
  return (
    <Panel title="Recent Usage Windows">
      {usage.length === 0 ? (
        <EmptyState title="No windows" message="Try ingesting events and running aggregate_usage." compact />
      ) : (
        <MiniUsageTable usage={usage.slice(0, 12)} />
      )}
    </Panel>
  );
}

function MiniUsageTable({ usage }: { usage: UsageWindow[] }) {
  if (usage.length === 0) {
    return <EmptyState title="No usage windows" message="No recent usage windows were returned." compact />;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Window start</th>
            <th>API key</th>
            <th>Units</th>
            <th>Events</th>
          </tr>
        </thead>
        <tbody>
          {usage.map((window) => (
            <tr key={`${window.api_key_id}-${window.window_start}`}>
              <td>{formatDateTime(window.window_start)}</td>
              <td>{shortId(window.api_key_id)}</td>
              <td>{window.total_units.toLocaleString()}</td>
              <td>{window.event_count.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InvoiceListPanel({ invoices, onSelect }: { invoices: Invoice[]; onSelect: (id: string) => void }) {
  return (
    <Panel title="Invoices">
      {invoices.length === 0 ? (
        <EmptyState title="No invoices" message="No invoices are available for this customer yet." compact />
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Period</th>
                <th>Status</th>
                <th>Total</th>
                <th>Issued</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((invoice) => (
                <tr key={invoice.id}>
                  <td>{formatDate(invoice.period_start)} - {formatDate(invoice.period_end)}</td>
                  <td><span className={`pill ${invoice.status}`}>{invoice.status}</span></td>
                  <td>{money(invoice.total_cents)}</td>
                  <td>{formatDate(invoice.issued_at)}</td>
                  <td>
                    <button className="link-button" onClick={() => onSelect(invoice.id)}>
                      View details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function InvoiceOpsTable({
  invoices,
  selectedInvoiceId,
  onSelectInvoice,
  onUseLineItem,
}: {
  invoices: OpsInvoice[];
  selectedInvoiceId: string | null;
  onSelectInvoice: (invoiceId: string | null) => void;
  onUseLineItem: (invoice: OpsInvoice, lineItem: InvoiceLineItem) => void;
}) {
  if (invoices.length === 0) {
    return <EmptyState title="No invoices" message="This customer has no invoices yet." compact />;
  }
  return (
    <div className="table-scroll">
      <table className="ops-invoice-table">
        <thead>
          <tr>
            <th>Invoice ID</th>
            <th>Period</th>
            <th>Status</th>
            <th>Total</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((invoice) => {
            const expanded = selectedInvoiceId === invoice.id;
            return (
              <Fragment key={invoice.id}>
                <tr className={expanded ? "selected-row" : ""}>
                  <td>
                    <code title={invoice.id}>{shortId(invoice.id)}</code>
                    <button className="copy-button" onClick={() => copyText(invoice.id)}>Copy</button>
                  </td>
                  <td>{formatDate(invoice.period_start)} - {formatDate(invoice.period_end)}</td>
                  <td><span className={`pill ${invoice.status}`}>{invoice.status}</span></td>
                  <td>{money(invoice.total_cents)}</td>
                  <td>
                    <button className="link-button" onClick={() => onSelectInvoice(expanded ? null : invoice.id)}>
                      {expanded ? "Hide lines" : "Show lines"}
                    </button>
                  </td>
                </tr>
                {expanded && (
                <tr className="line-items-row">
                    <td colSpan={5}>
                      <LineItemsTable invoice={invoice} onUseLineItem={onUseLineItem} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LineItemsTable({
  invoice,
  onUseLineItem,
}: {
  invoice: OpsInvoice;
  onUseLineItem: (invoice: OpsInvoice, lineItem: InvoiceLineItem) => void;
}) {
  const lineItems = invoice.line_items || [];

  if (lineItems.length === 0) {
    return <EmptyState title="No line items" message="This invoice detail did not include line items." compact />;
  }

  return (
    <div className="nested-table">
      <table>
        <thead>
          <tr>
            <th>Invoice ID</th>
            <th>Line item ID</th>
            <th>Description</th>
            <th>Units</th>
            <th>Amount</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {lineItems.map((lineItem) => (
            <tr key={lineItem.id}>
              <td>
                <code title={invoice.id}>{shortId(invoice.id)}</code>
                <button className="copy-button" onClick={() => copyText(invoice.id)}>Copy</button>
              </td>
              <td>
                <code title={lineItem.id}>{shortId(lineItem.id)}</code>
                <button className="copy-button" onClick={() => copyText(lineItem.id)}>Copy</button>
              </td>
              <td>{lineItem.description}</td>
              <td>{lineItem.units.toLocaleString()}</td>
              <td>{money(lineItem.amount_cents)}</td>
              <td>
                <button className="secondary compact-button" onClick={() => onUseLineItem(invoice, lineItem)}>
                  Use for override
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CreditsTable({ credits }: { credits: OpsCustomerDetail["credits"] }) {
  if (credits.length === 0) {
    return <EmptyState title="No credits" message="No credits have been issued for this customer." compact />;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Amount</th>
            <th>Reason</th>
            <th>Invoice</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {credits.map((credit) => (
            <tr key={credit.id}>
              <td>{money(credit.amount_cents)}</td>
              <td>{credit.reason}</td>
              <td>{credit.invoice_id ? <code>{credit.invoice_id}</code> : "Unapplied"}</td>
              <td>{formatDate(credit.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BillingInspectorPanel({
  range,
  data,
  state,
  error,
  onRangeChange,
  onLoad,
}: {
  range: { start: string; end: string };
  data: BillingInspectorResponse | null;
  state: LoadState;
  error: string;
  onRangeChange: (range: { start: string; end: string }) => void;
  onLoad: () => void;
}) {
  const invoiceLineItemCount = data?.invoices.reduce((sum, invoice) => sum + invoice.line_items.length, 0) || 0;
  const invoiceLineItemCents =
    data?.invoices.reduce(
      (invoiceSum, invoice) =>
        invoiceSum + invoice.line_items.reduce((lineSum, lineItem) => lineSum + lineItem.amount_cents, 0),
      0,
    ) || 0;
  const hasWarnings =
    Boolean(data?.warnings.raw_vs_window_mismatch) ||
    Boolean(data?.warnings.window_vs_invoice_mismatch) ||
    Boolean(data?.warnings.late_events_count);

  return (
    <div className="billing-inspector">
      <div className="inspector-controls">
        <label>
          Start
          <input
            type="date"
            value={range.start}
            onChange={(event) => onRangeChange({ ...range, start: event.target.value })}
          />
        </label>
        <label>
          End
          <input
            type="date"
            value={range.end}
            onChange={(event) => onRangeChange({ ...range, end: event.target.value })}
          />
        </label>
        <button className="secondary" onClick={onLoad} disabled={state === "loading"}>
          Load Billing Inspector
        </button>
      </div>

      {state === "loading" && <PanelMessage message="Loading billing inspector..." />}
      {state === "error" && <ErrorState message={error} />}

      {state === "loaded" && data && (
        <div className="inspector-results">
          <div className="flow-row">
            <InspectorStat title="Raw Events" primary={`${data.events.count.toLocaleString()} events`} secondary={`${data.events.total_units.toLocaleString()} units`} />
            <span className="flow-arrow">→</span>
            <InspectorStat title="Usage Windows" primary={`${data.windows.count.toLocaleString()} windows`} secondary={`${data.windows.total_units.toLocaleString()} units`} />
            <span className="flow-arrow">→</span>
            <InspectorStat title="Invoice Line Items" primary={`${invoiceLineItemCount.toLocaleString()} items`} secondary={money(invoiceLineItemCents)} />
          </div>

          {hasWarnings && (
            <div className="warning-block">
              {data.warnings.raw_vs_window_mismatch && <span className="badge red">Unit mismatch: raw events vs windows</span>}
              {data.warnings.window_vs_invoice_mismatch && <span className="badge red">Unit mismatch: windows vs invoice</span>}
              {data.warnings.late_events_count > 0 && (
                <span className="badge yellow">
                  {data.warnings.late_events_count.toLocaleString()} late events received after invoice was issued
                </span>
              )}
            </div>
          )}

          {data.credits.count > 0 && (
            <div>
              <h3>Inspector Credits</h3>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Amount</th>
                      <th>Reason</th>
                      <th>Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.credits.items.map((credit) => (
                      <tr key={credit.id}>
                        <td>{money(credit.amount_cents)}</td>
                        <td>{credit.reason}</td>
                        <td>{formatDate(credit.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {data.job_runs.length > 0 && (
            <div>
              <h3>Recent Jobs</h3>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Job</th>
                      <th>Status</th>
                      <th>Started</th>
                      <th>Finished</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.job_runs.map((jobRun) => (
                      <tr key={jobRun.id}>
                        <td>{jobRun.job_name}</td>
                        <td><span className={`pill ${jobRun.status}`}>{jobRun.status}</span></td>
                        <td>{formatDateTime(jobRun.started_at)}</td>
                        <td>{jobRun.finished_at ? formatDateTime(jobRun.finished_at) : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {data.audit_logs.length > 0 && (
            <div>
              <h3>Recent Audit Logs</h3>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Action</th>
                      <th>Actor</th>
                      <th>Timestamp</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.audit_logs.map((auditLog) => (
                      <tr key={auditLog.id}>
                        <td>{auditLog.action}</td>
                        <td>{auditLog.actor}</td>
                        <td>{formatDateTime(auditLog.timestamp)}</td>
                        <td>{auditLog.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InspectorStat({ title, primary, secondary }: { title: string; primary: string; secondary: string }) {
  return (
    <section className="inspector-stat">
      <span>{title}</span>
      <strong>{primary}</strong>
      <small>{secondary}</small>
    </section>
  );
}

function InvoiceDetailPanel({
  selectedInvoiceId,
  invoiceDetail,
  detailState,
  detailError,
}: {
  selectedInvoiceId: string | null;
  invoiceDetail: InvoiceDetail | null;
  detailState: LoadState;
  detailError: string;
}) {
  return (
    <Panel title="Invoice Detail">
      {!selectedInvoiceId && (
        <EmptyState title="Select an invoice" message="Click View details to inspect invoice line items." compact />
      )}
      {detailState === "loading" && <PanelMessage message="Loading invoice detail..." />}
      {detailState === "error" && <ErrorState message={detailError} />}
      {detailState === "loaded" && invoiceDetail && (
        <div className="invoice-detail">
          <div className="detail-header">
            <div>
              <FileText size={20} />
              <strong>{formatDate(invoiceDetail.period_start)} - {formatDate(invoiceDetail.period_end)}</strong>
            </div>
            <span className={`pill ${invoiceDetail.status}`}>{invoiceDetail.status}</span>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Description</th>
                  <th>Units</th>
                  <th>Unit price</th>
                  <th>Amount</th>
                </tr>
              </thead>
              <tbody>
                {invoiceDetail.line_items.map((line) => (
                  <tr key={line.id}>
                    <td>{line.description}</td>
                    <td>{line.units.toLocaleString()}</td>
                    <td>{unitPrice(line.unit_price_micros)}</td>
                    <td>{money(line.amount_cents)}</td>
                  </tr>
                ))}
                <tr className="total-row">
                  <td>Total</td>
                  <td></td>
                  <td></td>
                  <td>{money(invoiceDetail.total_cents)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
}

function MetricCard({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <section className={muted ? "metric-card muted-card" : "metric-card"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </section>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function PanelMessage({ message }: { message: string }) {
  return <div className="panel-message">{message}</div>;
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="error-state">
      <AlertCircle size={18} />
      <span>{message}</span>
    </div>
  );
}

function SuccessState({ message }: { message: string }) {
  return (
    <div className="success-state">
      <CheckCircle2 size={18} />
      <span>{message}</span>
    </div>
  );
}

function WarningText({ message }: { message: string }) {
  return (
    <div className="warning-text">
      <AlertCircle size={16} />
      <span>{message}</span>
    </div>
  );
}

function EmptyState({ title, message, compact = false }: { title: string; message: string; compact?: boolean }) {
  return (
    <section className={compact ? "empty-state compact" : "empty-state"}>
      <strong>{title}</strong>
      <p>{message}</p>
    </section>
  );
}
