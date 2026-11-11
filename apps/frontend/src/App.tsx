import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Bot,
  Check,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Download,
  FileText,
  Filter,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  Moon,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  ShieldHalf,
  Sparkles,
  Sun,
  Upload,
  Wallet,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  accessToken,
  api,
  clearSession,
  download,
  post,
  saveSession,
  streamRun,
  type Row,
} from "./api";
import { Button } from "./components/ui/button";

const nav = [
  ["/", "Overview", LayoutDashboard],
  ["/operations", "AI workspace", Sparkles],
  ["/transactions", "Transactions", Wallet],
  ["/risk", "Risk intelligence", ShieldHalf],
  ["/reconciliation", "Reconciliation", RefreshCw],
  ["/approvals", "Approvals", CheckCheck],
  ["/monitor", "Agent monitor", Activity],
  ["/reports", "Reports", FileText],
  ["/audit", "Audit trail", ListChecks],
] as const;
const fmt = (n: unknown) => Number(n || 0).toLocaleString("en-GB");
const money = (n: unknown, currency = "GBP") =>
  new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(n || 0));
const stamp = (s: unknown) =>
  s
    ? new Date(String(s)).toLocaleString("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "—";
const short = (s: unknown) =>
  String(s || "—").length > 26
    ? String(s).slice(0, 23) + "…"
    : String(s || "—");
const today = () => new Date().toISOString().slice(0, 10);
const dailyRequest =
  "Analyse today's transactions, identify high-risk activity, reconcile all eligible transactions against the bank statement, investigate mismatches, and prepare a report with actions requiring approval.";
function useData(path: string, enabled = true) {
  return useQuery({
    queryKey: [path],
    queryFn: () => api(path),
    enabled,
    refetchInterval: 10000,
  });
}
export function Badge({ value }: { value: unknown }) {
  const s = String(value || "");
  return (
    <span
      className={`badge ${/HIGH|FAILED|REJECTED|MISMATCH/.test(s) ? "red" : /PENDING|WAITING|MEDIUM|QUEUED/.test(s) ? "amber" : /COMPLETED|APPROVED|EXECUTED|POSTED|LOW|ready/.test(s) ? "green" : "neutral"}`}
    >
      <span />
      {s.replaceAll("_", " ")}
    </span>
  );
}
function Empty({
  text = "No records yet. Start an analysis to see findings here.",
}: {
  text?: string;
}) {
  return (
    <div className="empty">
      <ListChecks size={28} />
      <strong>Nothing to review</strong>
      <p>{text}</p>
    </div>
  );
}
function Load({ query, children }: { query: Row; children: ReactNode }) {
  if (query.isPending)
    return (
      <div className="loading">
        <RefreshCw className="spin" size={20} /> Loading your workspace…
      </div>
    );
  if (query.error)
    return (
      <div role="alert" className="error">
        {query.error.message}
        <Button variant="outline" onClick={() => query.refetch()}>
          Retry
        </Button>
      </div>
    );
  return <>{children}</>;
}
function Json({ data }: { data: unknown }) {
  return <pre className="json">{JSON.stringify(data, null, 2)}</pre>;
}
function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.querySelector<HTMLElement>("button")?.focus();
    const fn = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
      if (e.key === "Tab") {
        const controls = Array.from(
          dialog.current?.querySelectorAll<HTMLElement>(
            "button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href]",
          ) || [],
        );
        const first = controls[0],
          last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", fn);
    return () => {
      document.removeEventListener("keydown", fn);
      document.body.style.overflow = priorOverflow;
      previous?.focus();
    };
  }, []);
  return (
    <div className="modal-bg" onClick={onClose}>
      <section
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <h2>{title}</h2>
          <Button aria-label="Close dialog" variant="ghost" onClick={onClose}>
            <X size={20} />
          </Button>
        </header>
        {children}
      </section>
    </div>
  );
}
function DataTable({
  rows,
  columns,
  onRow,
}: {
  rows: Row[];
  columns: { key: string; label: string; render?: (row: Row) => ReactNode }[];
  onRow?: (row: Row) => void;
}) {
  if (!rows.length) return <Empty />;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.id || i}>
              {columns.map((c, j) => (
                <td key={c.key}>
                  {j === 0 && onRow ? (
                    <button className="text-button" onClick={() => onRow(row)}>
                      {c.render ? c.render(row) : short(row[c.key])}
                    </button>
                  ) : c.render ? (
                    c.render(row)
                  ) : (
                    short(row[c.key])
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function PageHead({
  eyebrow = "WORKSPACE",
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="heading-actions">{children}</div>
    </div>
  );
}
const authSchema = z.object({
  email: z.email("Enter a valid email"),
  password: z.string().min(8, "Use at least 8 characters"),
  name: z.string().optional(),
  tenant_name: z.string().optional(),
});
function Auth({ onLogin }: { onLogin: () => void }) {
  const [register, setRegister] = useState(false);
  const [error, setError] = useState("");
  const {
    register: field,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof authSchema>>({
    resolver: zodResolver(authSchema),
  });
  return (
    <div className="auth">
      <div className="auth-story">
        <div className="brand">
          <span className="brand-icon">
            <ShieldCheck />
          </span>
          LedgerGuard<span>AI</span>
        </div>
        <div>
          <div className="eyebrow">FINANCIAL OPERATIONS, WITH OVERSIGHT</div>
          <h1>
            Clarity in every
            <br />
            transaction.
            <br />
            <em>Control at every step.</em>
          </h1>
          <p>
            Connect your ledger, investigate the exceptions, and keep every
            decision accountable.
          </p>
          <div className="auth-pills">
            <span>
              <ShieldCheck size={16} /> Sandbox by design
            </span>
            <span>
              <CheckCheck size={16} /> Human approval
            </span>
          </div>
        </div>
        <small>LedgerGuard AI · Synthetic financial operations platform</small>
      </div>
      <div className="auth-form">
        <div className="auth-card">
          <span className="brand-icon">
            <ShieldCheck />
          </span>
          <h2>{register ? "Create your workspace" : "Welcome back"}</h2>
          <p>
            {register
              ? "Start with an isolated sandbox tenant."
              : "Sign in to your financial operations workspace."}
          </p>
          <form
            onSubmit={handleSubmit(async (data) => {
              setError("");
              try {
                const body = register
                  ? {
                      ...data,
                      name: data.name || "Analyst",
                      tenant_name: data.tenant_name || "Sandbox",
                    }
                  : { email: data.email, password: data.password };
                saveSession(
                  await post(`/auth/${register ? "register" : "login"}`, body),
                );
                onLogin();
              } catch (e) {
                setError((e as Error).message);
              }
            })}
          >
            {register && (
              <>
                <label>
                  Your name
                  <input {...field("name")} required />
                </label>
                <label>
                  Workspace name
                  <input {...field("tenant_name")} required />
                </label>
              </>
            )}
            <label>
              Email address
              <input
                type="email"
                autoComplete="username"
                {...field("email")}
                placeholder="you@company.com"
              />
            </label>
            {errors.email && <small role="alert">{errors.email.message}</small>}
            <label>
              Password
              <input
                type="password"
                autoComplete={register ? "new-password" : "current-password"}
                {...field("password")}
                placeholder="Enter your password"
              />
            </label>
            {errors.password && (
              <small role="alert">{errors.password.message}</small>
            )}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <Button disabled={isSubmitting} type="submit">
              {isSubmitting
                ? "Signing in…"
                : register
                  ? "Create account"
                  : "Sign in"}
              <ArrowRight size={16} />
            </Button>
          </form>
          <button
            className="text-button auth-switch"
            onClick={() => setRegister(!register)}
          >
            {register
              ? "Already have an account? Sign in"
              : "Create a new workspace"}
          </button>
          <div className="demo-note">
            <ShieldCheck size={18} />
            <div>
              <strong>Development sandbox</strong>
              <p>
                Demo: analyst@ledgerguard.demo
                <br />
                Password: SandboxDemo!2026
                <br />
                Use approver@ledgerguard.demo for review.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
function Overview() {
  const q = useData("/dashboard");
  const d = q.data || {};
  const navigate = useNavigate();
  const [currency, setCurrency] = useState("GBP");
  const trend = Object.entries(d.daily || {})
    .slice(-14)
    .map(([date, count]) => ({ date: date.slice(5), count }));
  const distribution = Object.entries(d.risk_distribution || {}).map(
    ([name, value]) => ({ name, value }),
  );
  return (
    <>
      <PageHead
        eyebrow="OPERATIONS / OVERVIEW"
        title="Your ledger, in focus."
        description="A clear view of financial activity, emerging risks, and what needs your attention."
      >
        <div className="date-pill">
          {new Date().toLocaleDateString("en-GB", {
            day: "numeric",
            month: "short",
            year: "numeric",
          })}
        </div>
        <Button onClick={() => navigate("/operations")}>
          <Sparkles size={16} /> New AI analysis
        </Button>
      </PageHead>
      <Load query={q}>
        <div className="review-banner">
          <div className="banner-symbol">
            <Sparkles size={24} />
          </div>
          <div>
            <span className="eyebrow">YOUR DAILY REVIEW, CONNECTED</span>
            <h2>From thousands of transactions to a clear next step.</h2>
            <p>
              Let specialized agents investigate risk and reconcile your ledger.
              You stay in control.
            </p>
          </div>
          <Button variant="outline" onClick={() => navigate("/operations")}>
            Open AI workspace <ArrowRight size={16} />
          </Button>
        </div>
        <div className="metric-grid">
          {[
            ["Transactions", fmt(d.count), "Persisted sandbox records", Wallet],
            [
              "Transaction volume",
              money(d.currencies?.[currency]?.volume, currency),
              `${currency} · gross movement`,
              ArrowUpRight,
            ],
            [
              "High-risk alerts",
              fmt(d.high_risk_alerts),
              "Indicators requiring investigation",
              ShieldHalf,
            ],
            [
              "Reconciliation rate",
              `${d.reconciliation_rate || 0}%`,
              "Latest run per account",
              CheckCheck,
            ],
          ].map(([label, value, sub, Icon]) => {
            const I = Icon as typeof Wallet;
            return (
              <div className="metric" key={String(label)}>
                <div>
                  <span>{String(label)}</span>
                  <I size={18} />
                </div>
                <strong>{String(value)}</strong>
                <small>{String(sub)}</small>
              </div>
            );
          })}
        </div>
        <div className="dashboard-grid">
          <section className="panel volume-panel">
            <div className="panel-head">
              <div>
                <h2>Transaction activity</h2>
                <p>Daily record count · synthetic dataset</p>
              </div>
              <select
                aria-label="Display currency"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                {Object.keys(d.currencies || { GBP: {} }).map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </div>
            <div className="chart">
              <ResponsiveContainer width="100%" height={255}>
                <AreaChart data={trend}>
                  <defs>
                    <linearGradient
                      id="activityFill"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop offset="0%" stopColor="#198273" stopOpacity={0.2} />
                      <stop offset="95%" stopColor="#198273" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 6"
                    vertical={false}
                    stroke="var(--border)"
                  />
                  <XAxis
                    dataKey="date"
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#8a959b", fontSize: 11 }}
                  />
                  <YAxis
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#8a959b", fontSize: 11 }}
                  />
                  <Tooltip />
                  <Area
                    isAnimationActive={false}
                    dataKey="count"
                    name="Transactions"
                    type="monotone"
                    stroke="#198273"
                    strokeWidth={2.5}
                    fill="url(#activityFill)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </section>
          <section className="panel risk-panel">
            <div className="panel-head">
              <div>
                <h2>Risk overview</h2>
                <p>Deterministic policy + anomaly signals</p>
              </div>
              <ShieldHalf size={18} />
            </div>
            {distribution.some((x) => Number(x.value) > 0) ? (
              <>
                <div className="donut">
                  <ResponsiveContainer width="100%" height={190}>
                    <PieChart>
                      <Pie
                        isAnimationActive={false}
                        data={distribution}
                        innerRadius={57}
                        outerRadius={77}
                        dataKey="value"
                        paddingAngle={5}
                        stroke="none"
                      >
                        {distribution.map((v, i) => (
                          <Cell
                            key={v.name}
                            fill={["#18816f", "#e7ae5d", "#d7786c"][i]}
                          />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                  <div>
                    <strong>
                      {fmt(
                        distribution.reduce((n, x) => n + Number(x.value), 0),
                      )}
                    </strong>
                    <small>assessments</small>
                  </div>
                </div>
                <div className="risk-legend">
                  {distribution.map((x, i) => (
                    <div key={x.name}>
                      <i
                        style={{
                          background: ["#18816f", "#e7ae5d", "#d7786c"][i],
                        }}
                      />
                      {x.name.toLowerCase()}
                      <strong>{fmt(x.value)}</strong>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <Empty text="Run your first risk analysis to populate this chart." />
            )}
          </section>
          <section className="panel attention-panel">
            <div className="panel-head">
              <div>
                <h2>Needs your attention</h2>
                <p>Keep the review moving</p>
              </div>
              <span className="count-pill">
                {fmt(
                  Number(d.pending_approvals || 0) + Number(d.active_runs || 0),
                )}
              </span>
            </div>
            <button
              className="attention-row"
              onClick={() => navigate("/approvals")}
            >
              <span className="attention-icon amber">
                <CheckCheck size={20} />
              </span>
              <span>
                <strong>Pending approvals</strong>
                <small>Human review before ledger changes</small>
              </span>
              <b>{fmt(d.pending_approvals)}</b>
              <ChevronRight size={16} />
            </button>
            <button
              className="attention-row"
              onClick={() => navigate("/monitor")}
            >
              <span className="attention-icon green">
                <Bot size={20} />
              </span>
              <span>
                <strong>Active agent workflows</strong>
                <small>Analysis and approval checkpoints</small>
              </span>
              <b>{fmt(d.active_runs)}</b>
              <ChevronRight size={16} />
            </button>
            <div className="trust-note">
              <ShieldCheck size={16} /> Every correction requires an authorized
              reviewer.
            </div>
          </section>
          <section className="panel activity-panel">
            <div className="panel-head">
              <div>
                <h2>Recent activity</h2>
                <p>A traceable record of operations</p>
              </div>
              <NavLink to="/audit" className="text-button">
                View audit trail <ArrowRight size={14} />
              </NavLink>
            </div>
            {(d.recent_activity || []).slice(0, 4).map((a: Row) => (
              <div className="activity-row" key={a.id}>
                <span className="activity-dot" />
                <div>
                  <strong>{a.action.toLowerCase().replaceAll("_", " ")}</strong>
                  <small>{short(a.entity_id)}</small>
                </div>
                <time>{stamp(a.created_at)}</time>
              </div>
            ))}
            {!d.recent_activity?.length && (
              <Empty text="Your recent financial operations will appear here." />
            )}
          </section>
        </div>
        <div className="workspace-foot">
          <span>
            <span className="live-dot" /> Sandbox environment · All financial
            actions are simulated
          </span>
          <span>Evidence first. Human governed.</span>
        </div>
      </Load>
    </>
  );
}

function Transactions() {
  const [search, setSearch] = useState("");
  const [account, setAccount] = useState("");
  const [date, setDate] = useState("");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Row | null>(null);
  const [notice, setNotice] = useState("");
  const accounts = useData("/accounts");
  const params = new URLSearchParams({
    page: String(page),
    page_size: "25",
    sort,
    ...(search ? { search } : {}),
    ...(account ? { account_id: account } : {}),
    ...(date
      ? { start_date: `${date}T00:00:00Z`, end_date: `${date}T23:59:59Z` }
      : {}),
  });
  const q = useData("/transactions?" + params);
  const client = useQueryClient();
  return (
    <>
      <PageHead
        title="Transaction explorer"
        description="Search your source records and follow the evidence behind every movement."
      >
        <Button
          variant="outline"
          onClick={() =>
            download(
              "/transactions/export?" + params,
              "transactions.csv",
            ).catch((e) => setNotice(e.message))
          }
        >
          <Download size={16} /> Export CSV
        </Button>
        <label className="button primary">
          <Upload size={16} /> Import CSV
          <input
            hidden
            type="file"
            accept=".csv"
            onChange={async (e) => {
              if (e.target.files?.[0]) {
                const form = new FormData();
                form.append("file", e.target.files[0]);
                try {
                  await api("/transactions/import", {
                    method: "POST",
                    body: form,
                  });
                  setNotice(
                    "Import queued. Records appear after validation completes.",
                  );
                  client.invalidateQueries();
                } catch (err) {
                  setNotice((err as Error).message);
                }
              }
            }}
          />
        </label>
      </PageHead>
      <section className="panel">
        <div className="filter-bar">
          <div className="search-input">
            <Search size={16} />
            <input
              aria-label="Search transactions"
              placeholder="Search reference, description or ID…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <select
            aria-label="Filter account"
            value={account}
            onChange={(e) => {
              setAccount(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All accounts</option>
            {accounts.data?.items.map((a: Row) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <input
            aria-label="Transaction date"
            type="date"
            value={date}
            onChange={(e) => {
              setDate(e.target.value);
              setPage(1);
            }}
          />
          <select
            aria-label="Sort transactions"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="amount">Largest amount</option>
          </select>
          <Filter size={16} />
        </div>
        {notice && (
          <p className="notice" role="status">
            {notice}
          </p>
        )}
        <Load query={q}>
          <DataTable
            rows={q.data?.items || []}
            onRow={setSelected}
            columns={[
              {
                key: "source_id",
                label: "Transaction",
                render: (r) => (
                  <span className="cell-id">
                    {r.source_id}
                    <small>{short(r.description)}</small>
                  </span>
                ),
              },
              { key: "reference", label: "Reference" },
              { key: "account_id", label: "Account" },
              {
                key: "booked_at",
                label: "Booked",
                render: (r) => stamp(r.booked_at),
              },
              {
                key: "amount",
                label: "Amount",
                render: (r) => (
                  <span className={Number(r.amount) > 0 ? "credit" : ""}>
                    {money(r.amount, r.currency)}
                  </span>
                ),
              },
              {
                key: "status",
                label: "Status",
                render: (r) => <Badge value={r.status} />,
              },
            ]}
          />
          <div className="pagination">
            <span>
              {fmt(q.data?.total)} records · page {page}
            </span>
            <Button
              variant="outline"
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
            >
              <ChevronLeft size={15} /> Previous
            </Button>
            <Button
              variant="outline"
              disabled={page * 25 >= (q.data?.total || 0)}
              onClick={() => setPage(page + 1)}
            >
              Next <ChevronRight size={15} />
            </Button>
          </div>
        </Load>
      </section>
      {selected && (
        <Modal title="Transaction evidence" onClose={() => setSelected(null)}>
          <Json data={selected} />
        </Modal>
      )}
    </>
  );
}

function RiskCharts() {
  const [account, setAccount] = useState("");
  const accounts = useData("/accounts");
  const metrics = useData(
    "/risk/analytics/summary" +
      (account ? "?account_id=" + encodeURIComponent(account) : ""),
  );
  const navigate = useNavigate();
  const [error, setError] = useState("");
  return (
    <>
      <div className="filter-bar" style={{ paddingLeft: 0 }}>
        <select
          aria-label="Risk account"
          value={account}
          onChange={(e) => setAccount(e.target.value)}
        >
          <option value="">All account signals</option>
          {accounts.data?.items.map((a: Row) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <Button
          variant="outline"
          disabled={!account}
          onClick={async () => {
            try {
              const run = await post("/agents/run", {
                request: `Investigate unusual transaction activity for account ${account}.`,
              });
              sessionStorage.setItem("lg_run", run.id);
              navigate("/operations");
            } catch (error) {
              setError((error as Error).message);
            }
          }}
        >
          Investigate account
        </Button>
        {error && <p className="error">{error}</p>}
      </div>
      <div className="dashboard-grid spaced" style={{ marginBottom: 22 }}>
        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Risk indicator trend</h2>
              <p>Latest assessments by source booking month</p>
            </div>
          </div>
          <div className="chart">
            <ResponsiveContainer width="100%" height={210}>
              <AreaChart data={metrics.data?.monthly || []}>
                <CartesianGrid
                  vertical={false}
                  stroke="var(--border)"
                  strokeDasharray="3 6"
                />
                <XAxis
                  dataKey="month"
                  tick={{ fontSize: 10, fill: "#899ba0" }}
                />
                <YAxis tick={{ fontSize: 10, fill: "#899ba0" }} />
                <Tooltip />
                <Area
                  isAnimationActive={false}
                  dataKey="HIGH"
                  name="High priority"
                  stroke="#d7786c"
                  fill="#d7786c22"
                  type="monotone"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Risk distribution</h2>
              <p>
                {fmt(metrics.data?.assessed_transactions)} assessed source
                transactions
              </p>
            </div>
          </div>
          <div className="chart">
            <ResponsiveContainer width="100%" height={210}>
              <PieChart>
                <Pie
                  isAnimationActive={false}
                  data={["LOW", "MEDIUM", "HIGH"].map((name) => ({
                    name,
                    value: metrics.data?.counts?.[name] || 0,
                  }))}
                  dataKey="value"
                  innerRadius={50}
                  outerRadius={75}
                  paddingAngle={3}
                >
                  {["#18816f", "#e7ae5d", "#d7786c"].map((colour) => (
                    <Cell key={colour} fill={colour} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>
    </>
  );
}

function Risk() {
  const [category, setCategory] = useState("HIGH");
  const [selected, setSelected] = useState<Row | null>(null);
  const [notice, setNotice] = useState("");
  const q = useData("/risk/assessments?category=" + category);
  const alerts = useData("/risk/alerts");
  const [date, setDate] = useState(today());
  return (
    <>
      <PageHead
        title="Risk intelligence"
        description="Investigate policy signals and statistical anomalies with source evidence."
      >
        <input
          aria-label="Risk analysis date"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
        <Button
          onClick={async () => {
            try {
              const job = await post("/risk/analyse", {
                start_date: date + "T00:00:00Z",
                end_date: date + "T23:59:59Z",
              });
              setNotice("Risk analysis queued · " + job.id);
            } catch (e) {
              setNotice((e as Error).message);
            }
          }}
        >
          <Play size={16} /> Analyse risk
        </Button>
      </PageHead>
      <div className="info-strip">
        <ShieldHalf size={18} />
        <span>
          These are risk indicators requiring investigation. Statistical
          anomalies do not establish fraud or an AML determination.
        </span>
      </div>
      <RiskCharts />
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Explainable assessments</h2>
            <p>Rules and a versioned Isolation Forest model</p>
          </div>
          <select
            aria-label="Risk category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option>HIGH</option>
            <option>MEDIUM</option>
            <option>LOW</option>
          </select>
        </div>
        <Load query={q}>
          <DataTable
            rows={q.data?.items || []}
            onRow={setSelected}
            columns={[
              { key: "transaction_id", label: "Transaction" },
              {
                key: "score",
                label: "Risk score",
                render: (r) => (
                  <div className="score">
                    <b>{r.score}</b>
                    <span>
                      <i style={{ width: r.score + "%" }} />
                    </span>
                  </div>
                ),
              },
              {
                key: "category",
                label: "Category",
                render: (r) => <Badge value={r.category} />,
              },
              {
                key: "rules",
                label: "Triggered rules",
                render: (r) => (
                  <span className="rule-chips">
                    {r.rules.map((x: string) => (
                      <span key={x}>{x.replaceAll("_", " ")}</span>
                    ))}
                  </span>
                ),
              },
              {
                key: "model_version",
                label: "Model",
                render: (r) => short(r.model_version),
              },
            ]}
          />
        </Load>
      </section>
      <section className="panel spaced">
        <div className="panel-head">
          <h2>Operational alerts</h2>
          <span className="count-pill">{fmt(alerts.data?.total)}</span>
        </div>
        <Load query={alerts}>
          <DataTable
            rows={alerts.data?.items || []}
            onRow={setSelected}
            columns={[
              { key: "id", label: "Alert" },
              { key: "assessment_id", label: "Assessment evidence" },
              {
                key: "severity",
                label: "Severity",
                render: (r) => <Badge value={r.severity} />,
              },
              {
                key: "status",
                label: "Status",
                render: (r) => <Badge value={r.status} />,
              },
            ]}
          />
        </Load>
      </section>
      {selected && (
        <Modal
          title="Risk evidence and explanation"
          onClose={() => setSelected(null)}
        >
          <Json data={selected} />
        </Modal>
      )}
    </>
  );
}

function Operations({ monitor = false }: { monitor?: boolean }) {
  const runs = useData("/agents/runs");
  const [request, setRequest] = useState(dailyRequest);
  const [runId, setRunId] = useState(sessionStorage.getItem("lg_run") || "");
  const run = useData("/agents/runs/" + runId, !!runId);
  const tools = useData("/mcp/tool-executions?run_id=" + runId, !!runId);
  const [events, setEvents] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const client = useQueryClient();
  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    const controller = new AbortController();
    streamRun(
      runId,
      (e) => {
        setEvents((prev) =>
          prev.some((x) => x.id === e.id) ? prev : [...prev, e],
        );
        client.invalidateQueries({ queryKey: ["/agents/runs/" + runId] });
      },
      controller.signal,
    ).catch((e) => {
      if (e.name !== "AbortError") setError(e.message);
    });
    return () => controller.abort();
  }, [runId, run.data?.status, client]);
  async function start() {
    setStarting(true);
    setError("");
    try {
      const r = await post("/agents/run", { request });
      setRunId(r.id);
      sessionStorage.setItem("lg_run", r.id);
      client.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }
  return (
    <>
      <PageHead
        title={monitor ? "Agent execution monitor" : "AI operations workspace"}
        description="Coordinate an evidence-based review across specialized agents and secure MCP tools."
      >
        <span className="status-pill">
          <span className="live-dot" />{" "}
          {run.data?.status?.replaceAll("_", " ") || "Ready for a request"}
        </span>
      </PageHead>
      {!monitor && (
        <section className="panel composer">
          <div className="composer-title">
            <span className="attention-icon green">
              <Sparkles size={21} />
            </span>
            <div>
              <h2>What would you like to investigate?</h2>
              <p>
                Describe the review. Financial calculations stay deterministic.
              </p>
            </div>
          </div>
          <textarea
            aria-label="Analysis request"
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            rows={3}
          />
          <div className="composer-foot">
            <span>
              <ShieldCheck size={15} /> Every proposed correction pauses for
              human approval
            </span>
            <Button disabled={starting || request.length < 5} onClick={start}>
              <ArrowUpRight size={16} />
              {starting ? "Starting…" : "Start analysis"}
            </Button>
          </div>
        </section>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="operations-grid">
        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Execution timeline</h2>
              <p>
                {runId
                  ? `Thread ${runId}`
                  : "Your agents are ready when you are"}
              </p>
            </div>
            <Bot size={20} />
          </div>
          {runId ? (
            <Load query={run}>
              {run.data?.status === "WAITING_APPROVAL" && (
                <div className="approval-interrupt">
                  <CheckCheck size={22} />
                  <div>
                    <strong>Human review required</strong>
                    <p>
                      The workflow is durably paused. An authorized second user
                      must review the proposal.
                    </p>
                    <NavLink className="text-button" to="/approvals">
                      Review approvals <ArrowRight size={14} />
                    </NavLink>
                  </div>
                </div>
              )}
              {run.data?.error && <p className="error">{run.data.error}</p>}
              <div className="timeline">
                {events.map((e) => (
                  <div className="timeline-item" key={e.id}>
                    <span
                      className={`timeline-icon ${/FAILED/.test(e.event) ? "red" : ""}`}
                    >
                      {/COMPLETED|GENERATED/.test(e.event) ? (
                        <Check size={14} />
                      ) : (
                        <Activity size={14} />
                      )}
                    </span>
                    <div>
                      <strong>
                        {e.event.toLowerCase().replaceAll("_", " ")}
                      </strong>
                      <p>
                        {e.data.agent ||
                          [e.data.server, e.data.tool]
                            .filter(Boolean)
                            .join(" / ") ||
                          short(JSON.stringify(e.data))}
                      </p>
                      <small>{stamp(e.created_at)}</small>
                    </div>
                  </div>
                ))}
              </div>
              {run.data?.status === "COMPLETED" && (
                <div className="notice">
                  Review complete.{" "}
                  <NavLink to="/reports">Open the evidence report →</NavLink>
                </div>
              )}
              {["RUNNING", "WAITING_APPROVAL", "QUEUED"].includes(
                run.data?.status,
              ) && (
                <Button
                  variant="outline"
                  onClick={() =>
                    post(`/agents/runs/${runId}/cancel`)
                      .then(() => client.invalidateQueries())
                      .catch((e) => setError(e.message))
                  }
                >
                  Cancel workflow
                </Button>
              )}
              {run.data?.status === "FAILED" && (
                <Button
                  onClick={() =>
                    post(`/agents/runs/${runId}/retry`)
                      .then(() => client.invalidateQueries())
                      .catch((e) => setError(e.message))
                  }
                >
                  Retry from checkpoint
                </Button>
              )}
            </Load>
          ) : (
            <Empty text="Start an analysis above to follow the execution timeline." />
          )}
        </section>
        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Review history</h2>
              <p>Persisted across sessions</p>
            </div>
            <Activity size={18} />
          </div>
          <Load query={runs}>
            {runs.data?.items.length ? (
              runs.data.items.map((r: Row) => (
                <button
                  className={`run-card ${runId === r.id ? "selected" : ""}`}
                  key={r.id}
                  onClick={() => {
                    setRunId(r.id);
                    sessionStorage.setItem("lg_run", r.id);
                  }}
                >
                  <div>
                    <Badge value={r.status} />
                    <small>{stamp(r.created_at)}</small>
                  </div>
                  <p>{r.request}</p>
                  <small>{r.id}</small>
                </button>
              ))
            ) : (
              <Empty />
            )}
          </Load>
        </section>
      </div>
      {runId && (
        <section className="panel spaced">
          <div className="panel-head">
            <div>
              <h2>MCP tool activity</h2>
              <p>Actual authenticated protocol invocations</p>
            </div>
            <span className="count-pill">{tools.data?.total || 0}</span>
          </div>
          <DataTable
            rows={tools.data?.items || []}
            columns={[
              { key: "server", label: "MCP server" },
              { key: "tool", label: "Tool" },
              {
                key: "duration_ms",
                label: "Duration",
                render: (r) => `${r.duration_ms} ms`,
              },
              {
                key: "status",
                label: "Status",
                render: (r) => <Badge value={r.status} />,
              },
            ]}
          />
        </section>
      )}
    </>
  );
}

function Reconciliation() {
  const jobs = useData("/reconciliation/jobs");
  const statements = useData("/bank-statements");
  const [statement, setStatement] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const [notice, setNotice] = useState("");
  const ex = useData(
    `/reconciliation/jobs/${selected?.id}/exceptions`,
    !!selected,
  );
  return (
    <>
      <PageHead
        title="Reconciliation"
        description="Match ledger records to bank statements, with every exception explained."
      >
        <select
          aria-label="Bank statement"
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
        >
          <option value="">Select bank statement</option>
          {statements.data?.items.map((s: Row) => (
            <option key={s.id} value={s.id}>
              {s.account_id} · {s.name}
            </option>
          ))}
        </select>
        <label className="button outline">
          <Upload size={16} /> Upload statement
          <input
            type="file"
            accept=".csv"
            hidden
            disabled={!statement}
            onChange={async (e) => {
              if (e.target.files?.[0]) {
                const form = new FormData();
                form.append("file", e.target.files[0]);
                try {
                  await api(`/bank-statements/${statement}/import`, {
                    method: "POST",
                    body: form,
                  });
                  setNotice("Statement import queued.");
                } catch (e) {
                  setNotice((e as Error).message);
                }
              }
            }}
          />
        </label>
        <Button
          disabled={!statement}
          onClick={async () => {
            try {
              const s = statements.data?.items.find(
                (s: Row) => s.id === statement,
              );
              await post("/reconciliation/jobs", {
                bank_statement_id: statement,
                account_id: s.account_id,
                start_date: today() + "T00:00:00Z",
                end_date: today() + "T23:59:59Z",
              });
              setNotice("Reconciliation queued.");
            } catch (e) {
              setNotice((e as Error).message);
            }
          }}
        >
          <RefreshCw size={16} /> Reconcile
        </Button>
      </PageHead>
      <div className="info-strip">
        <CheckCheck size={18} />
        Exact references, signed amounts, currencies, and date tolerances.
        Ambiguous matches remain open for investigation.
      </div>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <section className="panel">
        <div className="panel-head">
          <h2>Reconciliation runs</h2>
          <span className="count-pill">{fmt(jobs.data?.total)}</span>
        </div>
        <Load query={jobs}>
          <DataTable
            rows={jobs.data?.items || []}
            onRow={setSelected}
            columns={[
              { key: "id", label: "Job" },
              { key: "account_id", label: "Account" },
              {
                key: "status",
                label: "Status",
                render: (r) => <Badge value={r.status} />,
              },
              {
                key: "matched",
                label: "Matched ledger",
                render: (r) => fmt(r.summary.matched_ledger_records),
              },
              {
                key: "exceptions",
                label: "Exceptions",
                render: (r) => (
                  <span className="exception-count">
                    {r.summary.exception_count}
                  </span>
                ),
              },
              {
                key: "created_at",
                label: "Run date",
                render: (r) => stamp(r.created_at),
              },
            ]}
          />
        </Load>
      </section>
      {selected && (
        <Modal
          title="Reconciliation evidence"
          onClose={() => setSelected(null)}
        >
          <Json data={selected.summary} />
          <Load query={ex}>
            <DataTable
              rows={ex.data?.items || []}
              columns={[
                {
                  key: "kind",
                  label: "Classification",
                  render: (r) => <Badge value={r.kind} />,
                },
                {
                  key: "evidence",
                  label: "Evidence",
                  render: (r) => (
                    <details>
                      <summary>Inspect source records</summary>
                      <Json data={r.evidence} />
                    </details>
                  ),
                },
              ]}
            />
          </Load>
        </Modal>
      )}
    </>
  );
}

function Approvals({ user }: { user: Row }) {
  const q = useData("/approvals");
  const [selected, setSelected] = useState("");
  const detail = useData("/approvals/" + selected, !!selected);
  const [reason, setReason] = useState("");
  const [decision, setDecision] = useState("");
  const [error, setError] = useState("");
  const client = useQueryClient();
  const canDecide =
    ["approver", "admin"].includes(user.role) &&
    detail.data?.action?.maker_id !== user.id &&
    detail.data?.status === "PENDING";
  async function submit() {
    try {
      await post(`/approvals/${selected}/${decision}`, {
        reason,
        version: detail.data?.version,
      });
      setDecision("");
      setReason("");
      setSelected("");
      client.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <PageHead
        title="Approval centre"
        description="Review the evidence. Make a decision. Every sandbox correction stays accountable."
      >
        <span className="status-pill">
          <ShieldCheck size={15} /> Maker-checker enforced
        </span>
      </PageHead>
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Proposed actions</h2>
            <p>
              AI agents can request approval. Only authorized humans can grant
              it.
            </p>
          </div>
        </div>
        <Load query={q}>
          <DataTable
            rows={q.data?.items || []}
            onRow={(r) => {
              setSelected(r.id);
              setError("");
            }}
            columns={[
              { key: "id", label: "Approval request" },
              {
                key: "status",
                label: "Status",
                render: (r) => <Badge value={r.status} />,
              },
              { key: "required_approvals", label: "Required reviewers" },
              {
                key: "expires_at",
                label: "Expires",
                render: (r) => stamp(r.expires_at),
              },
              {
                key: "created_at",
                label: "Requested",
                render: (r) => stamp(r.created_at),
              },
            ]}
          />
        </Load>
      </section>
      {selected && (
        <Modal
          title="Review proposed sandbox correction"
          onClose={() => {
            setSelected("");
            setDecision("");
          }}
        >
          <Load query={detail}>
            <div className="approval-summary">
              <Badge value={detail.data?.status} />
              <h3>Void duplicate ledger entry</h3>
              <p>
                This preserves the source transaction and original entry, and
                marks only the duplicate sandbox ledger entry as inactive.
              </p>
            </div>
            <h4>Supporting evidence</h4>
            <Json data={detail.data?.action?.payload} />
            <div className="info-strip">
              <ShieldCheck size={18} />
              {detail.data?.required_approvals} independent reviewer(s) required
              · Proposal revision {detail.data?.version}
            </div>
            {detail.data?.decisions?.length > 0 && (
              <>
                <h4>Decision history</h4>
                <Json data={detail.data?.decisions} />
              </>
            )}
            {canDecide && !decision && (
              <div className="dialog-actions">
                <Button
                  variant="outline"
                  onClick={() => setDecision("request-investigation")}
                >
                  Request investigation
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => setDecision("reject")}
                >
                  Reject
                </Button>
                <Button onClick={() => setDecision("approve")}>
                  <Check size={16} /> Approve
                </Button>
              </div>
            )}
            {!canDecide && detail.data?.status === "PENDING" && (
              <p className="notice">
                An authorized second user must review this action. Sign in with
                an approver account.
              </p>
            )}
            {decision && (
              <div className="decision-box">
                <h3>Confirm {decision.replaceAll("-", " ")}</h3>
                <p>
                  Your decision is recorded with your identity and the exact
                  proposal revision.
                </p>
                <label>
                  Decision reason
                  <textarea
                    aria-label="Decision reason"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    rows={3}
                    placeholder="Explain your review of the supporting evidence…"
                  />
                </label>
                <div className="dialog-actions">
                  <Button variant="outline" onClick={() => setDecision("")}>
                    Back
                  </Button>
                  <Button disabled={reason.trim().length < 5} onClick={submit}>
                    Confirm decision
                  </Button>
                </div>
              </div>
            )}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </Load>
        </Modal>
      )}
    </>
  );
}

function Reports() {
  const q = useData("/reports");
  const [selected, setSelected] = useState<Row | null>(null);
  const [error, setError] = useState("");
  return (
    <>
      <PageHead
        title="Reports"
        description="Verified financial findings, approval decisions, and execution outcomes in one place."
      />
      {error && <p className="error">{error}</p>}
      <Load query={q}>
        <div className="report-grid">
          {q.data?.items.map((r: Row) => (
            <article key={r.id} className="panel report-card">
              <div className="report-icon">
                <FileText size={25} />
              </div>
              <span className="eyebrow">OPERATIONS REVIEW</span>
              <h2>{r.title}</h2>
              <p>{stamp(r.created_at)}</p>
              <div className="report-facts">
                <span>{fmt(r.content.transactions?.count)} transactions</span>
                <span>
                  {r.content.approvals?.length || 0} approval requests
                </span>
              </div>
              <div>
                <Button variant="outline" onClick={() => setSelected(r)}>
                  View findings
                </Button>
                <Button
                  onClick={() =>
                    download(
                      `/reports/${r.id}/pdf`,
                      `ledgerguard-${r.id}.pdf`,
                    ).catch((e) => setError(e.message))
                  }
                >
                  <Download size={15} /> PDF
                </Button>
              </div>
            </article>
          ))}
        </div>
        {!q.data?.items.length && (
          <section className="panel">
            <Empty text="Complete an AI review to generate your first operations report." />
          </section>
        )}
      </Load>
      {selected && (
        <Modal title="Operations report" onClose={() => setSelected(null)}>
          <Json data={selected.content} />
        </Modal>
      )}
    </>
  );
}
function Audit() {
  const [page, setPage] = useState(1);
  const q = useData("/audit-logs?page=" + page);
  const [selected, setSelected] = useState<Row | null>(null);
  return (
    <>
      <PageHead
        title="Audit trail"
        description="An append-only history of identities, decisions, and financial operations."
      />
      <section className="panel">
        <Load query={q}>
          <DataTable
            rows={q.data?.items || []}
            onRow={setSelected}
            columns={[
              {
                key: "action",
                label: "Action",
                render: (r) => r.action.replaceAll("_", " "),
              },
              { key: "actor_id", label: "Actor" },
              { key: "entity_id", label: "Related entity" },
              {
                key: "outcome",
                label: "Outcome",
                render: (r) => <Badge value={r.outcome} />,
              },
              {
                key: "created_at",
                label: "Timestamp",
                render: (r) => stamp(r.created_at),
              },
            ]}
          />
          <div className="pagination">
            <span>{fmt(q.data?.total)} events</span>
            <Button
              variant="outline"
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              disabled={page * 100 >= (q.data?.total || 0)}
              onClick={() => setPage(page + 1)}
            >
              Next
            </Button>
          </div>
        </Load>
      </section>
      {selected && (
        <Modal title="Audit evidence" onClose={() => setSelected(null)}>
          <Json data={selected} />
        </Modal>
      )}
    </>
  );
}
function SettingsPage({ user }: { user: Row }) {
  const q = useData("/settings");
  const health = useData("/mcp/health");
  const [edit, setEdit] = useState(false);
  const [highValue, setHighValue] = useState("10000.00");
  const [velocity, setVelocity] = useState("20");
  const [threshold, setThreshold] = useState("50000.00");
  const [expiry, setExpiry] = useState("24");
  const [notice, setNotice] = useState("");
  const client = useQueryClient();
  return (
    <>
      <PageHead
        title="Workspace settings"
        description="Inspect the policies, model configuration, and secure tool services behind your workspace."
      >
        {user.role === "admin" && (
          <Button
            onClick={() => {
              setHighValue(
                q.data?.risk_rules?.[0]?.config.high_value || "10000.00",
              );
              setVelocity(
                String(q.data?.risk_rules?.[0]?.config.velocity || 20),
              );
              setThreshold(
                q.data?.approval_policies?.[0]?.config.two_person_threshold ||
                  "50000.00",
              );
              setExpiry(
                String(
                  q.data?.approval_policies?.[0]?.config.expires_hours || 24,
                ),
              );
              setEdit(true);
            }}
          >
            Edit policies
          </Button>
        )}
      </PageHead>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <div className="settings-grid">
        <section className="panel">
          <div className="panel-head">
            <h2>Policy and model configuration</h2>
          </div>
          <Load query={q}>
            <Json data={q.data} />
            {edit && (
              <Modal
                title="Update sandbox policies"
                onClose={() => setEdit(false)}
              >
                <form
                  className="policy-form"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    try {
                      await api("/settings/risk", {
                        method: "PUT",
                        body: JSON.stringify({
                          high_value: highValue,
                          velocity: Number(velocity),
                        }),
                      });
                      await api("/settings/approvals", {
                        method: "PUT",
                        body: JSON.stringify({
                          two_person_threshold: threshold,
                          expires_hours: Number(expiry),
                        }),
                      });
                      setNotice("New policy versions saved and audited.");
                      setEdit(false);
                      client.invalidateQueries();
                    } catch (error) {
                      setNotice((error as Error).message);
                    }
                  }}
                >
                  <label>
                    High-value threshold
                    <input
                      required
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={highValue}
                      onChange={(e) => setHighValue(e.target.value)}
                    />
                  </label>
                  <label>
                    Hourly velocity threshold
                    <input
                      required
                      type="number"
                      min="2"
                      max="10000"
                      value={velocity}
                      onChange={(e) => setVelocity(e.target.value)}
                    />
                  </label>
                  <label>
                    Two-person approval threshold
                    <input
                      required
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={threshold}
                      onChange={(e) => setThreshold(e.target.value)}
                    />
                  </label>
                  <label>
                    Approval expiry in hours
                    <input
                      required
                      type="number"
                      min="1"
                      max="168"
                      value={expiry}
                      onChange={(e) => setExpiry(e.target.value)}
                    />
                  </label>
                  <p>
                    Stricter current approval thresholds are rechecked before
                    execution.
                  </p>
                  <div className="dialog-actions">
                    <Button type="submit">Save policy versions</Button>
                  </div>
                </form>
              </Modal>
            )}
          </Load>
        </section>
        <section className="panel">
          <div className="panel-head">
            <h2>MCP service health</h2>
            <ShieldCheck size={18} />
          </div>
          <Load query={health}>
            {health.data?.items.map((s: Row) => (
              <div className="service-row" key={s.server}>
                <div>
                  <strong>{s.server}</strong>
                  <small>
                    {s.tools.length} discovered tools · Streamable HTTP
                  </small>
                </div>
                <Badge value={s.status} />
              </div>
            ))}
          </Load>
          <div className="trust-note">
            Approval and execution capabilities remain outside the AI tool
            allowlist.
          </div>
        </section>
      </div>
    </>
  );
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(!!accessToken());
  const [collapsed, setCollapsed] = useState(false);
  const [dark, setDark] = useState(localStorage.getItem("lg_theme") === "dark");
  const q = useData("/auth/me", authenticated);
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  useEffect(() => {
    const fn = () => setAuthenticated(false);
    window.addEventListener("session-expired", fn);
    return () => window.removeEventListener("session-expired", fn);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("lg_theme", dark ? "dark" : "light");
  }, [dark]);
  if (!authenticated)
    return (
      <Auth
        onLogin={() => {
          setAuthenticated(true);
          client.clear();
          navigate("/");
        }}
      />
    );
  const user = q.data || {};
  return (
    <div className={`app-shell ${collapsed ? "collapsed" : ""}`}>
      <aside className="sidebar">
        <NavLink to="/" className="brand">
          <span className="brand-icon">
            <ShieldCheck size={22} />
          </span>
          <span>
            LedgerGuard <em>AI</em>
          </span>
        </NavLink>
        <div className="workspace-picker">
          <span className="workspace-avatar">M</span>
          <div>
            <strong>{user.tenant_name || "Financial operations"}</strong>
            <small>Sandbox workspace</small>
          </div>
          <span>⌄</span>
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav aria-label="Main navigation">
          {nav.map(([path, label, Icon]) => (
            <NavLink
              end={path === "/"}
              to={path}
              key={path}
              title={label}
              aria-label={label}
              onClick={() => {
                if (window.innerWidth < 600) setCollapsed(false);
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
              {path === "/operations" && <span className="new-label">AI</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sandbox-card">
            <ShieldCheck size={20} />
            <strong>You're in a safe sandbox</strong>
            <p>
              Synthetic data. Human oversight.
              <br />
              No real funds at risk.
            </p>
            <span className="live-dot" /> Sandbox active
          </div>
          <NavLink className="settings-link" to="/settings">
            <Settings size={18} />
            <span>Settings</span>
          </NavLink>
          <button
            className="user-card"
            onClick={async () => {
              try {
                await post("/auth/logout");
              } finally {
                clearSession();
                setAuthenticated(false);
                client.clear();
              }
            }}
          >
            <span className="user-avatar">{user.name?.[0] || "A"}</span>
            <span>
              <strong>{user.name || "Analyst"}</strong>
              <small>
                {user.role?.replaceAll("_", " ") || "Financial operations"}
              </small>
            </span>
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <button
              className="icon-button"
              aria-label="Toggle sidebar"
              onClick={() => setCollapsed(!collapsed)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>
              {location.pathname === "/settings"
                ? "Settings"
                : nav.find((n) => n[0] === location.pathname)?.[1] ||
                  "Overview"}
            </strong>
          </div>
          <div>
            <span className="environment-label">
              <span className="live-dot" /> Sandbox
            </span>
            <button
              className="icon-button"
              aria-label="Toggle colour theme"
              onClick={() => setDark(!dark)}
            >
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <NavLink
              to="/approvals"
              className="icon-button"
              aria-label="Review notifications"
            >
              <Bell size={18} />
            </NavLink>
            <NavLink
              to="/settings"
              className="icon-button"
              aria-label="Workspace information"
            >
              <CircleHelp size={18} />
            </NavLink>
          </div>
        </header>
        <div className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/risk" element={<Risk />} />
            <Route path="/operations" element={<Operations />} />
            <Route path="/monitor" element={<Operations monitor />} />
            <Route path="/reconciliation" element={<Reconciliation />} />
            <Route path="/approvals" element={<Approvals user={user} />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/settings" element={<SettingsPage user={user} />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
