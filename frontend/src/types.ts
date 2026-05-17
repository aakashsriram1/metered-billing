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
