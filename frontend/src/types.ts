export type UsageWindow = {
  window_start: string;
  window_end: string;
  api_key_id: string;
  total_units: number;
  event_count: number;
};

export type UsageResponse = {
  results: UsageWindow[];
  page: number;
  page_size: number;
  total: number;
};

export type Invoice = {
  id: string;
  period_start: string;
  period_end: string;
  status: "draft" | "issued" | "paid" | "void";
  total_cents: number;
  issued_at: string | null;
  paid_at: string | null;
  created_at: string;
  updated_at: string;
};

export type InvoiceLineItem = {
  id: string;
  description: string;
  units: number;
  unit_price_micros: number;
  amount_cents: number;
  metadata: Record<string, unknown>;
};

export type InvoiceDetail = Invoice & {
  line_items: InvoiceLineItem[];
};

export type OpsInvoice = Invoice & {
  line_items?: InvoiceLineItem[];
};

export type Credit = {
  id: string;
  invoice_id: string | null;
  amount_cents: number;
  reason: string;
  created_by: string;
  idempotency_key: string;
  created_at: string;
};

export type OpsCustomerSummary = {
  id: string;
  name: string;
  email: string;
  created_at: string;
  current_month_usage_total: number;
  invoice_count: number;
  anomaly?: boolean;
};

export type OpsCustomerDetail = {
  id: string;
  name: string;
  email: string;
  created_at: string;
  recent_usage_windows: UsageWindow[];
  invoices: OpsInvoice[];
  credits: Credit[];
  anomaly: boolean;
};
