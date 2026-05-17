import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, FileText, KeyRound, RefreshCw } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchJson } from "./api";
import type { Invoice, InvoiceDetail, UsageResponse, UsageWindow } from "./types";

const API_KEY_STORAGE_KEY = "metered_billing_customer_api_key";

type LoadState = "idle" | "loading" | "loaded" | "error";

function currentMonthRange() {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1, 0, 0, 0));
  return {
    start: start.toISOString(),
    end: now.toISOString(),
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
  const [apiKeyInput, setApiKeyInput] = useState(() => localStorage.getItem(API_KEY_STORAGE_KEY) || "");
  const [apiKey, setApiKey] = useState(() => localStorage.getItem(API_KEY_STORAGE_KEY) || "");
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

  useEffect(() => {
    if (apiKey) {
      loadDashboard(apiKey);
    }
  }, []);

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
    <main className="app-shell">
      <header className="page-header">
        <div>
          <h1>Customer Dashboard</h1>
          <p>API usage and invoices</p>
        </div>
        <section className="key-panel" aria-label="API key settings">
          <div className={apiKey ? "status connected" : "status missing"}>
            {apiKey ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
            {apiKey ? "Connected" : "Missing API key"}
          </div>
          <div className="key-row">
            <KeyRound size={18} />
            <input
              type="password"
              value={apiKeyInput}
              onChange={(event) => setApiKeyInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") saveApiKey();
              }}
              placeholder="Paste seeded sk_test key"
              aria-label="Customer API key"
            />
            <button onClick={saveApiKey}>Save API key</button>
            <button className="secondary" onClick={clearApiKey}>Clear</button>
          </div>
        </section>
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

                <Panel title="Recent Usage Windows">
                  {usage.length === 0 ? (
                    <EmptyState title="No windows" message="Try ingesting events and running aggregate_usage." compact />
                  ) : (
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
                          {usage.slice(0, 12).map((window) => (
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
                  )}
                </Panel>

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
                                <button className="link-button" onClick={() => loadInvoiceDetail(invoice.id)}>
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
              </section>
            </>
          )}
        </>
      )}
    </main>
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

function EmptyState({ title, message, compact = false }: { title: string; message: string; compact?: boolean }) {
  return (
    <section className={compact ? "empty-state compact" : "empty-state"}>
      <strong>{title}</strong>
      <p>{message}</p>
    </section>
  );
}
