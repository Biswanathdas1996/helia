import type { ReactNode } from "react";
import { useGetAdminTrend, useListDocuments } from "@workspace/api-client-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  FileText,
  Ticket as TicketIcon,
  CheckCircle2,
  Loader2,
  ArrowUpRight,
  Database,
  Inbox,
  TrendingUp,
  Info,
  Target,
  Cpu,
  AlertTriangle,
  ShieldCheck,
  Gauge,
  Briefcase,
  ThumbsUp,
  ThumbsDown,
  MessageCircle,
  TicketCheck,
  AlertCircle,
} from "lucide-react";
import { format } from "date-fns";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

function AdminCardInfo({
  title,
  children,
  contentSide = "bottom",
  contentAlign = "start",
}: {
  title: string;
  children: ReactNode;
  contentSide?: "top" | "right" | "bottom" | "left";
  contentAlign?: "start" | "center" | "end";
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full sm:h-7 sm:w-7",
            "border border-border/55 bg-background/70 text-muted-foreground/80 shadow-sm shadow-black/[0.02]",
            "backdrop-blur-sm transition-[color,background-color,border-color,box-shadow]",
            "hover:border-border/80 hover:bg-muted/60 hover:text-foreground hover:shadow-black/[0.04]",
            "dark:border-border/40 dark:bg-background/30 dark:shadow-black/20 dark:hover:border-border/55 dark:hover:bg-muted/35",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ring-offset-background",
          )}
          aria-label={`${title}: more info`}
        >
          <Info className="h-3 w-3 sm:h-3.5 sm:w-3.5" strokeWidth={1.75} aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align={contentAlign}
        side={contentSide}
        className="w-[min(22rem,calc(100vw-2rem))] border-border/80 p-3 text-left shadow-lg"
      >
        <p className="text-xs leading-relaxed text-muted-foreground">{children}</p>
      </PopoverContent>
    </Popover>
  );
}

interface VectorIndexCoverage {
  total: number;
  withEmbedding: number;
  missingEmbedding: number;
  percent: number;
}

interface VectorIndexItem {
  name: string;
  status?: string | null;
  queryable?: boolean | null;
}

interface VectorIndexStatus {
  embeddingsAvailable: boolean;
  vectorSearchEnvFlag: boolean;
  embeddingModel: string;
  embeddingDim: number;
  indexName: string;
  exists: boolean;
  queryable: boolean;
  state?: string | null;
  indexes: VectorIndexItem[];
  embeddingCoverage: VectorIndexCoverage;
}

interface AdminInsights {
  windowDays: number;
  ragHealth: {
    avgTopScore: number;
    lowConfidenceRate: number;
    noResultsRate: number;
    avgCitationsUsed: number;
    sampleSize: number;
    lowConfidenceThreshold: number;
  };
  llmTelemetry: {
    chatModel: string;
    embeddingModel: string;
    avgLatencyMs: number;
    p50LatencyMs: number;
    p95LatencyMs: number;
    totalQueries: number;
    daily: Array<{ date: string; queries: number; avgLatencyMs: number }>;
  };
  knowledgeGaps: Array<{
    id: string;
    question: string;
    topScore: number;
    citationCount: number;
    canAnswer: boolean | null;
    createdAt: string;
  }>;
  business: {
    totalQueries: number;
    aiResolved: number;
    ticketOffers: number;
    ticketsCreatedFromChat: number;
    ticketsResolvedByAgent: number;
    ticketsOpen: number;
    ticketsTotal: number;
    deflectionRate: number;
    feedback: {
      up: number;
      down: number;
      rated: number;
      helpfulRate: number;
      comments: Array<{
        id: string;
        rating: "up" | "down";
        comment: string;
        createdAt: string | null;
      }>;
    };
  };
  grounding: {
    totalAnswers: number;
    groundedAnswers: number;
    refusedAnswers: number;
    citedAnswers: number;
    groundingVerdicts: number;
    helpfulCount: number;
    downvoteCount: number;
    ratedCount: number;
    groundedRate: number;
    citedRate: number;
    refusalRate: number;
    helpfulRate: number;
  };
}

async function adminJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`;
    throw new Error(message);
  }

  return (await response.json()) as T;
}

export default function AdminDashboard() {
  const queryClient = useQueryClient();
  const { data: trend } = useGetAdminTrend();
  const { data: documents } = useListDocuments();
  const vectorIndexStatus = useQuery<VectorIndexStatus, Error>({
    queryKey: ["admin-vector-index-status"],
    queryFn: () => adminJson<VectorIndexStatus>("/api/admin/vector-index"),
    refetchInterval: (query) => {
      const data = query.state.data as VectorIndexStatus | undefined;
      return data?.queryable ? false : 5000;
    },
  });
  const insights = useQuery<AdminInsights, Error>({
    queryKey: ["admin-insights"],
    queryFn: () => adminJson<AdminInsights>("/api/admin/insights"),
    refetchInterval: 60_000,
  });

  const ensureVectorIndex = useMutation<Record<string, unknown>, Error, void>({
    mutationFn: () => adminJson<Record<string, unknown>>("/api/admin/vector-index", { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["admin-vector-index-status"] });
    },
  });
  
  const pendingDocs = documents?.filter(d => d.status === "pending") || [];

  const queryTrendSummary =
    trend && trend.length > 0
      ? (() => {
          const total = trend.reduce((s, d) => s + d.queries, 0);
          const peakDay = trend.reduce((a, b) => (b.queries > a.queries ? b : a));
          return { total, peakDay };
        })()
      : null;

  const ragHealth = insights.data?.ragHealth;
  const llmTelemetry = insights.data?.llmTelemetry;
  const knowledgeGaps = insights.data?.knowledgeGaps ?? [];
  const grounding = insights.data?.grounding;
  const business = insights.data?.business;
  const peakLatency = llmTelemetry
    ? llmTelemetry.daily.reduce((m, d) => Math.max(m, d.avgLatencyMs), 0) || 1
    : 1;

  return (
    <div className="relative isolate min-h-full">
      <div
        className="pointer-events-none absolute inset-x-0 -top-24 h-[28rem] max-w-[100vw] overflow-hidden opacity-90"
        aria-hidden
      >
        <div className="absolute left-1/2 top-0 h-80 w-[min(90rem,140%)] -translate-x-1/2 rounded-[100%] bg-[radial-gradient(ellipse_at_center,_hsl(var(--primary)/0.07)_0%,_transparent_65%)]" />
        <div className="absolute right-[-10%] top-12 h-72 w-72 rounded-full bg-primary/[0.04] blur-3xl" />
        <div className="absolute left-[-5%] top-32 h-56 w-56 rounded-full blur-3xl bg-indigo-500/[0.05] dark:bg-indigo-400/[0.07]" />
      </div>

      <div className="relative px-6 py-8 sm:px-8 lg:px-10 max-w-7xl mx-auto space-y-10">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4 lg:items-center">
          <div className="min-w-0 flex-1 space-y-1">
            <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
              Admin dashboard
            </h1>
            <p className="max-w-xl text-xs leading-snug text-muted-foreground sm:text-[13px] sm:leading-normal">
              Health, ingestion, and review queue at a glance.
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
            <Link href="/admin/tickets">
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-1.5 rounded-md border-border/80 bg-background/70 px-3 text-xs shadow-xs backdrop-blur-sm"
              >
                <TicketIcon className="h-3.5 w-3.5" />
                Tickets
              </Button>
            </Link>
            <Link href="/admin/documents">
              <Button size="sm" className="h-8 gap-1.5 rounded-md px-3 text-xs shadow-sm shadow-primary/15">
                <FileText className="h-3.5 w-3.5" />
                Knowledge
                <ArrowUpRight className="h-3 w-3 opacity-70" />
              </Button>
            </Link>
          </div>
        </header>

        <section
          aria-label="Usage trend and document review queue"
          className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)] lg:gap-6"
        >
          <Card
            className={cn(
              "relative col-span-1 overflow-hidden rounded-2xl border-border/50 bg-card/90",
              "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
              "dark:bg-card/70 dark:shadow-black/30",
              "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
              "lg:col-span-1",
            )}
          >
            <div
              className="pointer-events-none absolute -right-20 -top-28 h-48 w-48 rounded-full bg-primary/[0.09] blur-3xl dark:bg-primary/[0.06]"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute -left-16 bottom-0 h-40 w-40 rounded-full bg-sky-500/[0.06] blur-3xl dark:bg-sky-400/[0.05]"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
              aria-hidden
            />

            <CardHeader className="relative space-y-0 border-b-0 bg-transparent pb-0 pt-5 sm:pt-6">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex min-w-0 gap-3.5 sm:gap-4">
                  <div
                    className={cn(
                      "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl sm:h-12 sm:w-12",
                      "bg-gradient-to-br from-primary/15 to-sky-500/10 shadow-inner shadow-primary/5",
                      "ring-1 ring-primary/12 dark:from-primary/12 dark:to-sky-400/8 dark:ring-primary/10",
                    )}
                  >
                    <TrendingUp className="h-5 w-5 text-primary sm:h-[1.35rem] sm:w-[1.35rem]" strokeWidth={1.75} />
                  </div>
                  <div className="min-w-0 space-y-1">
                    <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                      Analytics
                    </p>
                    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                      <CardTitle className="text-base font-semibold tracking-tight sm:text-lg">Query volume</CardTitle>
                      <AdminCardInfo title="Query volume">
                        Daily assistant usage over the last two weeks. Spikes can follow launches, incidents, or marketing
                        campaigns; sustained growth usually means self‑service is replacing repetitive agent work. Compare
                        peaks to staffing and content changes to explain the pattern.
                      </AdminCardInfo>
                    </div>
                    <CardDescription className="line-clamp-1 text-[13px] leading-normal">
                      Last 14 days—taller bars = more questions that day (busy periods & growth).
                    </CardDescription>
                  </div>
                </div>
                {queryTrendSummary ? (
                  <div className="flex shrink-0 flex-wrap items-end gap-x-4 gap-y-2 sm:flex-col sm:items-end">
                    <div className="text-right">
                      <p className="text-[11px] font-medium text-muted-foreground">Period total</p>
                      <p className="text-2xl font-semibold tabular-nums tracking-tight text-foreground sm:text-[1.65rem]">
                        {queryTrendSummary.total}
                      </p>
                    </div>
                    <div className="hidden text-right sm:block">
                      <p className="text-[11px] font-medium text-muted-foreground">Peak day</p>
                      <p className="text-sm font-medium tabular-nums text-foreground">
                        {queryTrendSummary.peakDay.queries}{" "}
                        <span className="font-normal text-muted-foreground">
                          · {format(new Date(queryTrendSummary.peakDay.date), "MMM d")}
                        </span>
                      </p>
                    </div>
                  </div>
                ) : null}
              </div>
            </CardHeader>

            <CardContent className="relative px-4 pb-5 pt-4 sm:px-6 sm:pb-6 sm:pt-5">
              <div
                className={cn(
                  "h-[260px] w-full rounded-2xl border border-border/45 sm:h-[292px]",
                  "bg-gradient-to-b from-muted/35 via-muted/10 to-transparent",
                  "p-2 shadow-inner shadow-black/[0.02] dark:from-muted/20 dark:shadow-black/20 sm:p-3",
                )}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={trend} margin={{ top: 12, right: 8, left: -18, bottom: 4 }}>
                    <defs>
                      <linearGradient id="colorQueries" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.42} />
                        <stop offset="92%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid
                      strokeDasharray="4 8"
                      vertical={false}
                      stroke="hsl(var(--border))"
                      strokeOpacity={0.55}
                    />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(val) => format(new Date(val), "MMM d")}
                      fontSize={11}
                      tickLine={false}
                      axisLine={false}
                      dy={8}
                      tickMargin={8}
                      stroke="hsl(var(--muted-foreground) / 0.35)"
                      tick={{ fill: "hsl(var(--muted-foreground))", opacity: 0.85 }}
                    />
                    <YAxis
                      fontSize={11}
                      tickLine={false}
                      axisLine={false}
                      width={36}
                      stroke="hsl(var(--muted-foreground) / 0.35)"
                      tick={{ fill: "hsl(var(--muted-foreground))", opacity: 0.85 }}
                    />
                    <Tooltip
                      cursor={{ stroke: "hsl(var(--primary) / 0.25)", strokeWidth: 1 }}
                      contentStyle={{
                        borderRadius: "12px",
                        border: "1px solid hsl(var(--border) / 0.9)",
                        background: "hsl(var(--popover) / 0.96)",
                        backdropFilter: "blur(10px)",
                        boxShadow: "0 18px 50px -24px hsl(0 0% 0% / 0.35)",
                        padding: "10px 12px",
                      }}
                      labelStyle={{ fontWeight: 600, marginBottom: 4 }}
                      labelFormatter={(val) => format(new Date(val), "MMM d, yyyy")}
                    />
                    <Area
                      type="monotone"
                      dataKey="queries"
                      stroke="hsl(var(--primary))"
                      strokeWidth={2.25}
                      fillOpacity={1}
                      fill="url(#colorQueries)"
                      activeDot={{
                        r: 5,
                        strokeWidth: 2,
                        stroke: "hsl(var(--background))",
                        fill: "hsl(var(--primary))",
                      }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          <Card
            className={cn(
              "relative flex flex-col overflow-hidden rounded-2xl border-border/50 bg-card/90",
              "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-all duration-300",
              "dark:bg-card/70 dark:shadow-black/30",
              "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
            )}
          >
            <div
              className="pointer-events-none absolute right-0 top-0 h-32 w-32 rounded-full bg-amber-500/[0.07] blur-2xl dark:bg-amber-400/[0.06]"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
              aria-hidden
            />

            <CardHeader className="relative flex flex-row items-start justify-between space-y-0 border-b border-border/55 bg-muted/10 px-4 pb-4 pt-5 sm:px-6 sm:pb-5 sm:pt-6">
              <div className="flex min-w-0 gap-3.5">
                <div
                  className={cn(
                    "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl sm:h-12 sm:w-12",
                    "bg-gradient-to-br from-amber-500/14 to-orange-500/10 shadow-inner shadow-amber-500/5",
                    "ring-1 ring-amber-500/12 dark:from-amber-400/12 dark:to-orange-400/8 dark:ring-amber-400/10",
                  )}
                >
                  <Inbox className="h-5 w-5 text-amber-800 dark:text-amber-300 sm:h-[1.35rem] sm:w-[1.35rem]" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-1 pr-2">
                  <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">Inbox</p>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-base font-semibold tracking-tight sm:text-lg">Pending review</CardTitle>
                    <AdminCardInfo title="Pending review">
                      New knowledge uploads that still need a human gate before the assistant can use them. The queue size
                      reflects editorial workload and risk control: approving good content improves answers, while holding
                      back unsafe or duplicate material protects users and brand.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="line-clamp-1 text-[13px] leading-normal">
                    Queued until approved; only then may the assistant use them in customer-facing answers.
                  </CardDescription>
                </div>
              </div>
              <Badge
                variant="secondary"
                className={cn(
                  "h-7 shrink-0 rounded-lg border border-border/60 px-2.5 font-mono text-xs tabular-nums",
                  "bg-background/80 shadow-sm backdrop-blur-sm",
                )}
              >
                {pendingDocs.length}
              </Badge>
            </CardHeader>
            <CardContent className="relative flex-1 overflow-auto p-0">
              <div className="max-h-[260px] sm:max-h-[292px]">
                {pendingDocs.length === 0 ? (
                  <div
                    className={cn(
                      "mx-3 mb-3 mt-2 flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/60",
                      "bg-gradient-to-b from-muted/25 to-transparent px-6 py-12 text-center",
                      "dark:from-muted/12",
                    )}
                  >
                    <div
                      className={cn(
                        "flex h-14 w-14 items-center justify-center rounded-2xl",
                        "bg-gradient-to-br from-emerald-500/12 to-teal-500/8 ring-1 ring-emerald-500/15",
                        "dark:from-emerald-400/10 dark:to-teal-400/6",
                      )}
                    >
                      <CheckCircle2 className="h-7 w-7 text-emerald-600 dark:text-emerald-400" strokeWidth={1.75} />
                    </div>
                    <div className="space-y-1">
                      <p className="text-sm font-semibold text-foreground">All caught up</p>
                      <p className="mx-auto max-w-[17rem] text-xs leading-relaxed text-muted-foreground">
                        New uploads will show up here for review.
                      </p>
                    </div>
                  </div>
                ) : (
                  <ul className="divide-y divide-border/60">
                    {pendingDocs.map((doc) => (
                      <li
                        key={doc.id}
                        className={cn(
                          "group flex items-start justify-between gap-3 px-4 py-3.5 transition-colors sm:px-5",
                          "hover:bg-muted/40 dark:hover:bg-muted/25",
                        )}
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium leading-snug">{doc.name}</p>
                          <p className="mt-1 truncate text-xs text-muted-foreground">
                            {doc.piiCount} PII flags · {doc.duplicateCount} duplicates
                          </p>
                        </div>
                        <Link href={`/admin/documents/${doc.id}`}>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="shrink-0 rounded-lg opacity-80 transition-opacity group-hover:opacity-100"
                          >
                            Review
                            <ArrowUpRight className="ml-1 h-3.5 w-3.5" />
                          </Button>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </CardContent>
          </Card>
        </section>

        {/* Business outcomes */}
        <Card
          className={cn(
            "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
            "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
            "dark:bg-card/70 dark:shadow-black/30",
            "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
          )}
        >
          <div
            className="pointer-events-none absolute -right-16 -top-24 h-48 w-48 rounded-full bg-emerald-500/[0.10] blur-3xl dark:bg-emerald-400/[0.07]"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute -left-12 bottom-0 h-40 w-40 rounded-full bg-primary/[0.07] blur-3xl dark:bg-primary/[0.05]"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
            aria-hidden
          />

          <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/15 px-5 pb-4 pt-5 sm:pb-5 sm:pt-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
              <div className="flex min-w-0 gap-4">
                <div
                  className={cn(
                    "flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl",
                    "bg-gradient-to-br from-emerald-500/15 to-primary/12 shadow-inner shadow-emerald-500/5",
                    "ring-1 ring-emerald-500/15 dark:from-emerald-400/12 dark:to-primary/10 dark:ring-emerald-400/12",
                  )}
                >
                  <Briefcase className="h-6 w-6 text-emerald-700 dark:text-emerald-300" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-1">
                  <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                    Business outcomes
                  </p>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-base font-semibold tracking-tight sm:text-lg">
                      Resolution &amp; deflection
                    </CardTitle>
                    <AdminCardInfo title="Resolution & deflection">
                      The bottom-line view: how many user questions the assistant resolved itself, how many escalated
                      into tickets that a human agent then handled, and how users rated the experience. Deflection rate
                      is the share of resolutions that the AI handled without raising a ticket — it is the cost-saving
                      KPI for self-service.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="text-[13px] leading-normal">
                    Last {insights.data?.windowDays ?? 14} days · {business?.totalQueries ?? 0} queries
                  </CardDescription>
                </div>
              </div>
              {business ? (
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <p className="text-[11px] font-medium text-muted-foreground">Deflection rate</p>
                  <p
                    className={cn(
                      "text-2xl font-semibold tabular-nums tracking-tight sm:text-[1.65rem]",
                      business.deflectionRate >= 60
                        ? "text-emerald-600 dark:text-emerald-400"
                        : business.deflectionRate >= 30
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-foreground",
                    )}
                  >
                    {business.deflectionRate}%
                  </p>
                  <p className="max-w-[14rem] text-right text-[10px] leading-snug text-muted-foreground tabular-nums">
                    {business.aiResolved + business.ticketsCreatedFromChat > 0
                      ? `${business.aiResolved} ÷ (${business.aiResolved} + ${business.ticketsCreatedFromChat})`
                      : "No AI-resolved vs. ticket-created outcomes in this window"}
                  </p>
                </div>
              ) : null}
            </div>
          </CardHeader>

          <CardContent className="relative space-y-5 p-5 sm:p-6">
            {insights.isLoading || !business ? (
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-24 rounded-xl" />
                ))}
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <BusinessTile
                    icon={<CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />}
                    label="Resolved by AI"
                    value={business.aiResolved}
                    sub={`of ${business.totalQueries} queries`}
                    tone="emerald"
                  />
                  <BusinessTile
                    icon={<TicketCheck className="h-4 w-4 text-cyan-600 dark:text-cyan-400" />}
                    label="Resolved by agent"
                    value={business.ticketsResolvedByAgent}
                    sub={`of ${business.ticketsTotal} tickets`}
                    tone="cyan"
                  />
                  <BusinessTile
                    icon={<TicketIcon className="h-4 w-4 text-amber-600 dark:text-amber-400" />}
                    label="Escalated to ticket"
                    value={business.ticketsCreatedFromChat}
                    sub={`+${business.ticketOffers} offered`}
                    tone="amber"
                  />
                  <BusinessTile
                    icon={<AlertCircle className="h-4 w-4 text-orange-600 dark:text-orange-400" />}
                    label="Open backlog"
                    value={business.ticketsOpen}
                    sub="awaiting agent"
                    tone="orange"
                  />
                </div>

                <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
                  <div className="space-y-3 rounded-xl border border-border/50 bg-gradient-to-br from-muted/30 to-transparent p-4 dark:from-muted/15">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-muted-foreground">User feedback</p>
                      <span className="text-sm font-semibold tabular-nums text-foreground">
                        {business.feedback.helpfulRate}% helpful
                      </span>
                    </div>
                    <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted/80 dark:bg-muted/40">
                      {business.feedback.rated > 0 ? (
                        <>
                          <div
                            className="h-full bg-gradient-to-r from-emerald-500 to-teal-500"
                            style={{
                              width: `${(business.feedback.up / business.feedback.rated) * 100}%`,
                            }}
                          />
                          <div
                            className="h-full bg-gradient-to-r from-rose-500 to-orange-500"
                            style={{
                              width: `${(business.feedback.down / business.feedback.rated) * 100}%`,
                            }}
                          />
                        </>
                      ) : null}
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="flex items-center gap-2 rounded-lg border border-emerald-500/25 bg-emerald-500/[0.06] px-2.5 py-1.5 dark:bg-emerald-400/[0.08]">
                        <ThumbsUp className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                        <span className="text-sm font-semibold tabular-nums text-foreground">
                          {business.feedback.up}
                        </span>
                        <span className="text-[11px] text-muted-foreground">helpful</span>
                      </div>
                      <div className="flex items-center gap-2 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2.5 py-1.5 dark:bg-rose-400/[0.08]">
                        <ThumbsDown className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" />
                        <span className="text-sm font-semibold tabular-nums text-foreground">
                          {business.feedback.down}
                        </span>
                        <span className="text-[11px] text-muted-foreground">not helpful</span>
                      </div>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      {business.feedback.rated} rated · {business.totalQueries - business.feedback.rated} unrated
                    </p>
                  </div>

                  <div className="rounded-xl border border-border/50 bg-card/40 dark:bg-card/30">
                    <div className="flex items-center justify-between border-b border-border/50 px-4 py-2.5">
                      <div className="flex items-center gap-1.5">
                        <MessageCircle className="h-3.5 w-3.5 text-muted-foreground" />
                        <p className="text-xs font-medium text-foreground">Recent comments</p>
                      </div>
                      <Badge
                        variant="secondary"
                        className="h-5 rounded-md border border-border/60 bg-background/80 px-1.5 font-mono text-[10px] tabular-nums"
                      >
                        {business.feedback.comments.length}
                      </Badge>
                    </div>
                    {business.feedback.comments.length === 0 ? (
                      <p className="px-4 py-6 text-center text-xs text-muted-foreground">
                        No written feedback in this window.
                      </p>
                    ) : (
                      <ul className="max-h-[210px] divide-y divide-border/55 overflow-auto">
                        {business.feedback.comments.map((c) => (
                          <li key={c.id} className="flex gap-2.5 px-4 py-2.5">
                            <span
                              className={cn(
                                "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                                c.rating === "up"
                                  ? "bg-emerald-500/15 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300"
                                  : "bg-rose-500/15 text-rose-700 dark:bg-rose-400/15 dark:text-rose-300",
                              )}
                              aria-hidden
                            >
                              {c.rating === "up" ? (
                                <ThumbsUp className="h-3 w-3" />
                              ) : (
                                <ThumbsDown className="h-3 w-3" />
                              )}
                            </span>
                            <div className="min-w-0 flex-1">
                              <p className="line-clamp-3 text-[13px] leading-snug text-foreground">{c.comment}</p>
                              {c.createdAt ? (
                                <p className="mt-0.5 text-[11px] text-muted-foreground">
                                  {format(new Date(c.createdAt), "MMM d, h:mm a")}
                                </p>
                              ) : null}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <section
          aria-label="AI insights"
          className="grid grid-cols-1 gap-5 lg:grid-cols-2 lg:gap-6"
        >
          {/* RAG retrieval health */}
          <Card
            className={cn(
              "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
              "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
              "dark:bg-card/70 dark:shadow-black/30",
              "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
            )}
          >
            <div
              className="pointer-events-none absolute -right-16 -top-24 h-44 w-44 rounded-full bg-cyan-500/[0.10] blur-3xl dark:bg-cyan-400/[0.07]"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
              aria-hidden
            />

            <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 px-4 pb-4 pt-5 sm:px-5 sm:pb-4 sm:pt-5">
              <div className="flex min-w-0 gap-3.5">
                <div
                  className={cn(
                    "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl",
                    "bg-gradient-to-br from-cyan-500/15 to-sky-500/10 shadow-inner shadow-cyan-500/5",
                    "ring-1 ring-cyan-500/15 dark:from-cyan-400/12 dark:to-sky-400/8 dark:ring-cyan-400/12",
                  )}
                >
                  <Target className="h-5 w-5 text-cyan-700 dark:text-cyan-300" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                    Retrieval
                  </p>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-base font-semibold tracking-tight sm:text-[15px]">
                      RAG retrieval health
                    </CardTitle>
                    <AdminCardInfo title="RAG retrieval health">
                      Quality of the vector search step before the LLM ever sees a question. Average top-match cosine
                      similarity tells you how confidently the index found relevant chunks; the low-confidence rate
                      flags how often that match was weak (below {ragHealth?.lowConfidenceThreshold ?? 0.6}). High low
                      confidence usually means missing or stale knowledge.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="text-[12px] leading-snug">
                    Last {insights.data?.windowDays ?? 14} days · {ragHealth?.sampleSize ?? 0} answers. These numbers
                    describe how reliably the system found relevant help articles before drafting a reply.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>

            <CardContent className="relative p-4 sm:p-5">
              {insights.isLoading ? (
                <div className="space-y-3">
                  <Skeleton className="h-16 w-full rounded-xl" />
                  <Skeleton className="h-12 w-full rounded-xl" />
                </div>
              ) : ragHealth ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-2.5">
                    <MetricTile
                      label="Avg top-match score"
                      description="How well the top search hit matched the question—higher usually means more on-topic."
                      value={ragHealth.avgTopScore.toFixed(3)}
                      hint="cosine"
                      tone="cyan"
                    />
                    <MetricTile
                      label="Avg citations / answer"
                      description="Average source excerpts attached per answer (more detail, longer replies)."
                      value={ragHealth.avgCitationsUsed.toFixed(1)}
                      hint="chunks"
                      tone="cyan"
                    />
                  </div>
                  <div className="space-y-2.5 rounded-xl border border-border/50 bg-gradient-to-br from-muted/30 to-transparent px-3.5 py-3 dark:from-muted/15">
                    <div className="flex items-baseline justify-between gap-2">
                      <div className="min-w-0 space-y-0.5">
                        <p className="text-xs font-medium text-muted-foreground">Low-confidence rate</p>
                        <p className="text-[10px] leading-snug text-muted-foreground">
                          How often the best available match still looked weak or nothing turned up—often a sign of gaps,
                          outdated articles, or unusual wording.
                        </p>
                      </div>
                      <p className="shrink-0 text-sm font-semibold tabular-nums text-foreground">
                        {ragHealth.lowConfidenceRate}%
                      </p>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/80 dark:bg-muted/40">
                      <div
                        className={cn(
                          "h-full rounded-full bg-gradient-to-r transition-all duration-500",
                          ragHealth.lowConfidenceRate >= 30
                            ? "from-amber-500 to-orange-500"
                            : "from-cyan-500 to-sky-500",
                        )}
                        style={{ width: `${Math.min(100, ragHealth.lowConfidenceRate)}%` }}
                      />
                    </div>
                    <p className="text-[11px] leading-snug text-muted-foreground">
                      Counts cases where the closest snippet scored below {ragHealth.lowConfidenceThreshold} on the match
                      scale, or the search returned nothing ({ragHealth.noResultsRate}% had zero results).
                    </p>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No retrieval data yet.</p>
              )}
            </CardContent>
          </Card>

          {/* LLM telemetry */}
          <Card
            className={cn(
              "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
              "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
              "dark:bg-card/70 dark:shadow-black/30",
              "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
            )}
          >
            <div
              className="pointer-events-none absolute -right-16 -top-24 h-44 w-44 rounded-full bg-violet-500/[0.10] blur-3xl dark:bg-violet-400/[0.07]"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
              aria-hidden
            />

            <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 px-4 pb-4 pt-5 sm:px-5 sm:pb-4 sm:pt-5">
              <div className="flex min-w-0 gap-3.5">
                <div
                  className={cn(
                    "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl",
                    "bg-gradient-to-br from-violet-500/15 to-primary/10 shadow-inner shadow-violet-500/5",
                    "ring-1 ring-violet-500/15 dark:from-violet-400/12 dark:to-primary/8 dark:ring-violet-400/12",
                  )}
                >
                  <Cpu className="h-5 w-5 text-violet-700 dark:text-violet-300" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                    Inference
                  </p>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-base font-semibold tracking-tight sm:text-[15px]">
                      LLM telemetry
                    </CardTitle>
                    <AdminCardInfo title="LLM telemetry">
                      End-to-end latency for the answer pipeline (retrieval + LLM + grounding checks). p95 is the user-felt
                      tail; if it diverges from p50 you have a long tail problem worth chasing. Daily curve helps you spot
                      regressions after a model or prompt change.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="text-[12px] leading-snug">
                    Time from question to answer in milliseconds (thousandths of a second). Shorter is snappier; the curve
                    below shows day-to-day variation. Models: {llmTelemetry?.chatModel ?? "—"} · embeds{" "}
                    {llmTelemetry?.embeddingModel ?? "—"}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>

            <CardContent className="relative p-4 sm:p-5">
              {insights.isLoading ? (
                <div className="space-y-3">
                  <Skeleton className="h-16 w-full rounded-xl" />
                  <Skeleton className="h-20 w-full rounded-xl" />
                </div>
              ) : llmTelemetry ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-2.5">
                    <MetricTile
                      label="Avg latency"
                      description="Average wait—mixes quick replies and slower ones."
                      value={`${llmTelemetry.avgLatencyMs}`}
                      hint="ms"
                      tone="violet"
                    />
                    <MetricTile
                      label="p50"
                      description="Typical wait: half of replies finish faster than this."
                      value={`${llmTelemetry.p50LatencyMs}`}
                      hint="ms"
                      tone="violet"
                    />
                    <MetricTile
                      label="p95"
                      description="Slowest ~5% of replies—highlights bad days and spikes."
                      value={`${llmTelemetry.p95LatencyMs}`}
                      hint="ms"
                      tone="violet"
                    />
                  </div>
                  <div className="rounded-xl border border-border/50 bg-gradient-to-br from-muted/30 to-transparent px-3.5 py-3 dark:from-muted/15">
                    <div className="flex items-center justify-between gap-2 pb-2">
                      <div className="min-w-0 space-y-0.5">
                        <p className="text-xs font-medium text-muted-foreground">Daily avg latency · 7d</p>
                        <p className="text-[10px] leading-snug text-muted-foreground">
                          Each bar is one day’s average response time so you can spot slowdowns after changes or incidents.
                        </p>
                      </div>
                      <span className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-background/70 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                        <Gauge className="h-3 w-3" /> ms
                      </span>
                    </div>
                    <div className="flex h-16 items-end gap-1.5">
                      {llmTelemetry.daily.map((d) => {
                        const h = Math.max(4, Math.round((d.avgLatencyMs / peakLatency) * 100));
                        return (
                          <div
                            key={d.date}
                            className="group relative flex-1"
                            title={`${format(new Date(d.date), "MMM d")} · ${d.avgLatencyMs}ms · ${d.queries} queries`}
                          >
                            <div
                              className="w-full rounded-t-sm bg-gradient-to-t from-violet-500/70 to-violet-400/40 transition-opacity group-hover:opacity-100"
                              style={{ height: `${h}%` }}
                            />
                          </div>
                        );
                      })}
                    </div>
                    <div className="mt-1.5 flex justify-between text-[10px] text-muted-foreground">
                      <span>{llmTelemetry.daily[0] ? format(new Date(llmTelemetry.daily[0].date), "MMM d") : ""}</span>
                      <span>
                        {llmTelemetry.daily[llmTelemetry.daily.length - 1]
                          ? format(new Date(llmTelemetry.daily[llmTelemetry.daily.length - 1].date), "MMM d")
                          : ""}
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No telemetry yet.</p>
              )}
            </CardContent>
          </Card>

          {/* Knowledge gap detector */}
          <Card
            className={cn(
              "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
              "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
              "dark:bg-card/70 dark:shadow-black/30",
              "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
            )}
          >
            <div
              className="pointer-events-none absolute -right-16 -top-24 h-44 w-44 rounded-full bg-amber-500/[0.10] blur-3xl dark:bg-amber-400/[0.07]"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
              aria-hidden
            />

            <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 px-4 pb-4 pt-5 sm:px-5 sm:pb-4 sm:pt-5">
              <div className="flex min-w-0 gap-3.5">
                <div
                  className={cn(
                    "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl",
                    "bg-gradient-to-br from-amber-500/15 to-orange-500/10 shadow-inner shadow-amber-500/5",
                    "ring-1 ring-amber-500/15 dark:from-amber-400/12 dark:to-orange-400/8 dark:ring-amber-400/12",
                  )}
                >
                  <AlertTriangle className="h-5 w-5 text-amber-700 dark:text-amber-300" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                    Content gaps
                  </p>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-base font-semibold tracking-tight sm:text-[15px]">
                      Knowledge gap detector
                    </CardTitle>
                    <AdminCardInfo title="Knowledge gap detector">
                      Recent user questions where the assistant either refused (couldn't ground an answer) or pulled
                      only weak retrieval matches. Each row is a content backlog candidate — adding documents that
                      cover these topics directly raises the helpful rate.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="text-[12px] leading-snug">
                    Real questions where the assistant had little to lean on—good candidates for new FAQs or articles so
                    the next customer gets a solid answer.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>

            <CardContent className="relative max-h-[320px] overflow-auto p-0">
              {insights.isLoading ? (
                <div className="space-y-2 p-4">
                  <Skeleton className="h-12 w-full rounded-lg" />
                  <Skeleton className="h-12 w-full rounded-lg" />
                  <Skeleton className="h-12 w-full rounded-lg" />
                </div>
              ) : knowledgeGaps.length === 0 ? (
                <div
                  className={cn(
                    "mx-3 mb-3 mt-3 flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/60",
                    "bg-gradient-to-b from-muted/25 to-transparent px-6 py-10 text-center dark:from-muted/12",
                  )}
                >
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500/12 to-teal-500/8 ring-1 ring-emerald-500/15">
                    <CheckCircle2 className="h-6 w-6 text-emerald-600 dark:text-emerald-400" strokeWidth={1.75} />
                  </div>
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-foreground">No detectable gaps</p>
                    <p className="mx-auto max-w-[18rem] text-xs leading-relaxed text-muted-foreground">
                      Every recent question matched the knowledge base with confidence.
                    </p>
                  </div>
                </div>
              ) : (
                <ul className="divide-y divide-border/60">
                  {knowledgeGaps.map((gap) => {
                    const reason = gap.canAnswer === false
                      ? "Refused"
                      : gap.citationCount === 0
                        ? "No matches"
                        : "Low confidence";
                    return (
                      <li key={gap.id} className="px-4 py-3 sm:px-5">
                        <div className="flex items-start justify-between gap-3">
                          <p className="line-clamp-2 flex-1 text-sm font-medium leading-snug text-foreground">
                            {gap.question}
                          </p>
                          <Badge
                            variant="secondary"
                            className={cn(
                              "shrink-0 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 text-[10px] font-medium text-amber-700",
                              "dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-300",
                            )}
                          >
                            {reason}
                          </Badge>
                        </div>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          top score{" "}
                          <span className="font-mono font-medium text-foreground/80 tabular-nums">
                            {gap.topScore.toFixed(3)}
                          </span>{" "}
                          · {gap.citationCount} citations · {format(new Date(gap.createdAt), "MMM d, h:mm a")}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* Grounding & safety */}
          <Card
            className={cn(
              "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
              "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
              "dark:bg-card/70 dark:shadow-black/30",
              "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
            )}
          >
            <div
              className="pointer-events-none absolute -right-16 -top-24 h-44 w-44 rounded-full bg-emerald-500/[0.10] blur-3xl dark:bg-emerald-400/[0.07]"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
              aria-hidden
            />

            <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 px-4 pb-4 pt-5 sm:px-5 sm:pb-4 sm:pt-5">
              <div className="flex min-w-0 gap-3.5">
                <div
                  className={cn(
                    "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl",
                    "bg-gradient-to-br from-emerald-500/15 to-teal-500/10 shadow-inner shadow-emerald-500/5",
                    "ring-1 ring-emerald-500/15 dark:from-emerald-400/12 dark:to-teal-400/8 dark:ring-emerald-400/12",
                  )}
                >
                  <ShieldCheck className="h-5 w-5 text-emerald-700 dark:text-emerald-300" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                    Safety
                  </p>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-base font-semibold tracking-tight sm:text-[15px]">
                      Grounding &amp; safety
                    </CardTitle>
                    <AdminCardInfo title="Grounding & safety">
                      Share of answers the assistant could ground in retrieved knowledge vs. those it refused (a feature,
                      not a bug — refusal beats hallucination). Cited rate measures how often answers carry source
                      attributions; helpful rate is the user verdict on quality.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="text-[12px] leading-snug">
                    {grounding?.totalAnswers ?? 0} answers · {grounding?.ratedCount ?? 0} rated. The rows below show how
                    often answers stayed tied to real sources, cited them clearly, declined when unsure, and earned
                    positive feedback.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>

            <CardContent className="relative space-y-3 p-4 sm:p-5">
              {insights.isLoading ? (
                <div className="space-y-3">
                  <Skeleton className="h-10 w-full rounded-xl" />
                  <Skeleton className="h-10 w-full rounded-xl" />
                  <Skeleton className="h-10 w-full rounded-xl" />
                </div>
              ) : grounding ? (
                <>
                  <SafetyBar
                    label="Grounded answer rate"
                    description="Share of answers drawn from your real sources—not guessed from memory."
                    value={grounding.groundedRate}
                    detail={`${grounding.groundedAnswers} / ${grounding.totalAnswers} answers`}
                    tone="emerald"
                  />
                  <SafetyBar
                    label="Cited (with sources)"
                    description="How often answers named or linked the exact documents or passages used."
                    value={grounding.citedRate}
                    detail={`${grounding.citedAnswers} / ${grounding.totalAnswers} answers`}
                    tone="cyan"
                  />
                  <SafetyBar
                    label="Refusal rate"
                    description="How often the assistant declined rather than guess without solid grounding."
                    value={grounding.refusalRate}
                    detail={`${grounding.refusedAnswers} refusals`}
                    tone="amber"
                    invert
                  />
                  <SafetyBar
                    label="Helpful rate"
                    description="Share of ratings that said the answer was helpful (not a full quality survey)."
                    value={grounding.helpfulRate}
                    detail={`${grounding.helpfulCount} helpful · ${grounding.downvoteCount} not helpful`}
                    tone="emerald"
                  />
                </>
              ) : (
                <p className="text-sm text-muted-foreground">No grounding data yet.</p>
              )}
            </CardContent>
          </Card>
        </section>

        <Card
          className={cn(
            "group relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
            "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
            "dark:bg-card/70 dark:shadow-black/30",
            "hover:shadow-lg hover:shadow-black/[0.06] dark:hover:shadow-black/40",
          )}
        >
          <div
            className="pointer-events-none absolute -right-16 -top-24 h-44 w-44 rounded-full bg-cyan-500/15 blur-3xl dark:bg-cyan-400/12"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute -left-12 bottom-0 h-32 w-32 rounded-full bg-violet-500/10 blur-3xl dark:bg-violet-400/8"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
            aria-hidden
          />

          <CardHeader className="relative space-y-0 border-b border-border/60 bg-muted/15 pb-4 pt-5 sm:pb-5">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
              <div className="flex min-w-0 gap-4">
                <div
                  className={cn(
                    "flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl",
                    "bg-gradient-to-br from-cyan-500/15 to-violet-500/12 shadow-inner shadow-cyan-500/5",
                    "ring-1 ring-cyan-500/15 dark:from-cyan-400/12 dark:to-violet-400/10 dark:ring-cyan-400/12",
                  )}
                >
                  <Database className="h-6 w-6 text-cyan-700 dark:text-cyan-300" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-base font-semibold tracking-tight sm:text-lg">
                      Vector index status
                    </CardTitle>
                    <AdminCardInfo title="Vector index status">
                      Whether your knowledge can be found semantically in MongoDB Atlas vector search. When the index is
                      queryable and coverage is high, the assistant can match user questions to the right chunks. This card
                      is your technical readiness signal before you invest in more content.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="text-[13px] leading-normal">
                    Whether your help content is indexed so the assistant can search it by meaning—not just keywords—and
                    how much of your library is fully prepared for that search.
                  </CardDescription>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                {vectorIndexStatus.isFetching && !vectorIndexStatus.isLoading && (
                  <span className="flex items-center gap-1.5 rounded-full border border-border/60 bg-background/60 px-2.5 py-1 text-[11px] font-medium text-muted-foreground backdrop-blur-sm">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Checking
                  </span>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 rounded-lg border-border/80 bg-background/70 text-xs shadow-xs backdrop-blur-sm"
                  onClick={() => vectorIndexStatus.refetch()}
                  disabled={vectorIndexStatus.isFetching}
                >
                  {vectorIndexStatus.isFetching ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
                  Refresh
                </Button>
                <Button
                  size="sm"
                  className="h-8 rounded-lg px-3 text-xs shadow-sm shadow-primary/15"
                  onClick={() => ensureVectorIndex.mutate()}
                  disabled={ensureVectorIndex.isPending}
                >
                  {ensureVectorIndex.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
                  Ensure index
                </Button>
              </div>
            </div>
          </CardHeader>

          <CardContent className="relative space-y-5 pt-5 sm:pt-6">
            {vectorIndexStatus.isLoading ? (
              <div className="flex h-24 items-center justify-center gap-2 rounded-xl border border-dashed border-border/70 bg-muted/20 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Checking Atlas indexing status…
              </div>
            ) : vectorIndexStatus.error ? (
              <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                Failed to load index status: {vectorIndexStatus.error.message}
              </div>
            ) : vectorIndexStatus.data ? (
              <>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                  <div className="rounded-xl border border-border/60 bg-muted/20 px-3.5 py-3 dark:bg-muted/10">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Search</p>
                    <p className="mt-1 text-[10px] leading-snug text-muted-foreground">
                      Can the assistant run “smart” lookups on your content right now, or is the index still warming up?
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                      <Badge
                        variant={vectorIndexStatus.data.queryable ? "default" : "secondary"}
                        className="h-6 rounded-md px-2 text-[11px] font-semibold"
                      >
                        {vectorIndexStatus.data.queryable ? "Queryable" : "Building"}
                      </Badge>
                    </div>
                  </div>
                  <div className="rounded-xl border border-border/60 bg-muted/20 px-3.5 py-3 dark:bg-muted/10">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Atlas state</p>
                    <p className="mt-1 text-[10px] leading-snug text-muted-foreground">
                      The database’s own status for the search index (for example ready, building, or recovering).
                    </p>
                    <p className="mt-2 font-mono text-sm font-semibold tabular-nums text-foreground">
                      {vectorIndexStatus.data.state ?? "UNKNOWN"}
                    </p>
                  </div>
                  <div className="rounded-xl border border-border/60 bg-muted/20 px-3.5 py-3 dark:bg-muted/10">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Index</p>
                    <p className="mt-1 text-[10px] leading-snug text-muted-foreground">
                      Whether the specialized search structure your app expects is actually present in the database.
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Badge
                        variant={vectorIndexStatus.data.exists ? "outline" : "destructive"}
                        className="h-6 rounded-md px-2 text-[11px] font-semibold"
                      >
                        {vectorIndexStatus.data.exists ? "Found" : "Missing"}
                      </Badge>
                    </div>
                  </div>
                  <div className="rounded-xl border border-border/60 bg-muted/20 px-3.5 py-3 dark:bg-muted/10">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Vector flag</p>
                    <p className="mt-1 text-[10px] leading-snug text-muted-foreground">
                      A system switch that must be on for this style of semantic search to work in your environment.
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Badge
                        variant={vectorIndexStatus.data.vectorSearchEnvFlag ? "outline" : "destructive"}
                        className="h-6 rounded-md px-2 text-[11px] font-semibold"
                      >
                        {vectorIndexStatus.data.vectorSearchEnvFlag ? "Enabled" : "Disabled"}
                      </Badge>
                    </div>
                    {!vectorIndexStatus.data.vectorSearchEnvFlag ? (
                      <p className="mt-2 text-[10px] leading-snug text-muted-foreground">
                        Set <code className="rounded bg-muted/80 px-0.5 font-mono text-[10px]">MONGODB_VECTOR_SEARCH</code> to
                        enable.
                      </p>
                    ) : null}
                  </div>
                </div>

                <div className="space-y-2.5 rounded-xl border border-border/50 bg-gradient-to-br from-muted/30 to-transparent px-4 py-3.5 dark:from-muted/15">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="text-xs font-medium text-muted-foreground">Embedding coverage</p>
                    <p className="text-sm tabular-nums text-foreground">
                      <span className="font-semibold">{vectorIndexStatus.data.embeddingCoverage.withEmbedding}</span>
                      <span className="text-muted-foreground"> / {vectorIndexStatus.data.embeddingCoverage.total} </span>
                      <span className="text-muted-foreground">
                        ({Math.round(vectorIndexStatus.data.embeddingCoverage.percent)}%)
                      </span>
                    </p>
                  </div>
                  <div
                    className="h-2 w-full overflow-hidden rounded-full bg-muted/90 dark:bg-muted/50"
                    role="progressbar"
                    aria-valuenow={Math.round(vectorIndexStatus.data.embeddingCoverage.percent)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="Embedding coverage percentage"
                  >
                    <div
                      className={cn(
                        "h-full rounded-full bg-gradient-to-r transition-all duration-500 ease-out",
                        vectorIndexStatus.data.embeddingCoverage.percent >= 100
                          ? "from-emerald-500 to-teal-500"
                          : "from-cyan-500 to-violet-500",
                      )}
                      style={{ width: `${Math.min(100, Math.max(0, vectorIndexStatus.data.embeddingCoverage.percent))}%` }}
                    />
                  </div>
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    Share of your documents that have been converted into the numeric form search needs. Missing pieces
                    cannot be matched well until they are processed.
                  </p>
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    Technical index name{" "}
                    <code className="rounded-md bg-muted/80 px-1.5 py-0.5 font-mono text-[11px] text-foreground dark:bg-muted/40">
                      {vectorIndexStatus.data.indexName}
                    </code>
                  </p>
                </div>

                {ensureVectorIndex.error ? (
                  <p className="text-sm text-destructive">Ensure index failed: {ensureVectorIndex.error.message}</p>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">No status data yet.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function BusinessTile({
  icon,
  label,
  value,
  sub,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  sub: string;
  tone: "emerald" | "cyan" | "amber" | "orange";
}) {
  const toneCls: Record<string, string> = {
    emerald: "from-emerald-500/12 to-teal-500/6 ring-emerald-500/15 dark:from-emerald-400/10 dark:to-teal-400/4",
    cyan: "from-cyan-500/12 to-sky-500/6 ring-cyan-500/15 dark:from-cyan-400/10 dark:to-sky-400/4",
    amber: "from-amber-500/12 to-orange-500/6 ring-amber-500/15 dark:from-amber-400/10 dark:to-orange-400/4",
    orange: "from-orange-500/12 to-red-500/6 ring-orange-500/15 dark:from-orange-400/10 dark:to-red-400/4",
  };
  return (
    <div
      className={cn(
        "relative rounded-xl border border-border/55 bg-gradient-to-br p-4 ring-1",
        toneCls[tone],
      )}
    >
      <div className="flex items-center gap-1.5">
        {icon}
        <p className="truncate text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums leading-none text-foreground sm:text-[1.625rem]">
        {value}
      </p>
      <p className="mt-2 text-[11px] leading-snug text-muted-foreground">{sub}</p>
    </div>
  );
}

function MetricTile({
  label,
  description,
  value,
  hint,
  tone,
}: {
  label: string;
  description?: string;
  value: string;
  hint?: string;
  tone: "cyan" | "violet" | "emerald" | "amber";
}) {
  const toneCls: Record<string, string> = {
    cyan: "from-cyan-500/12 to-sky-500/6 ring-cyan-500/15 dark:from-cyan-400/10 dark:to-sky-400/4",
    violet: "from-violet-500/12 to-primary/6 ring-violet-500/15 dark:from-violet-400/10 dark:to-primary/4",
    emerald: "from-emerald-500/12 to-teal-500/6 ring-emerald-500/15 dark:from-emerald-400/10 dark:to-teal-400/4",
    amber: "from-amber-500/12 to-orange-500/6 ring-amber-500/15 dark:from-amber-400/10 dark:to-orange-400/4",
  };
  return (
    <div
      className={cn(
        "rounded-xl border border-border/55 bg-gradient-to-br ring-1 px-3 py-2.5",
        toneCls[tone],
      )}
    >
      <p className="truncate text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {description ? (
        <p className="mt-1 line-clamp-1 text-[10px] leading-snug text-muted-foreground" title={description}>
          {description}
        </p>
      ) : null}
      <p className="mt-1 text-lg font-semibold tabular-nums leading-none text-foreground">
        {value}
        {hint ? <span className="ml-1 text-[10px] font-normal text-muted-foreground">{hint}</span> : null}
      </p>
    </div>
  );
}

function SafetyBar({
  label,
  description,
  value,
  detail,
  tone,
  invert = false,
}: {
  label: string;
  description?: string;
  value: number;
  detail: string;
  tone: "emerald" | "cyan" | "amber";
  invert?: boolean;
}) {
  const toneCls: Record<string, string> = {
    emerald: "from-emerald-500 to-teal-500",
    cyan: "from-cyan-500 to-sky-500",
    amber: "from-amber-500 to-orange-500",
  };
  const ariaLabel = description ? `${label}: ${value}%. ${description}` : `${label}: ${value}%`;
  return (
    <div className="space-y-1.5 rounded-xl border border-border/50 bg-gradient-to-br from-muted/25 to-transparent px-3.5 py-2.5 dark:from-muted/12">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-xs font-medium text-foreground">{label}</p>
        <p className="text-sm font-semibold tabular-nums text-foreground">{value}%</p>
      </div>
      {description ? (
        <p className="line-clamp-1 text-[11px] leading-snug text-muted-foreground" title={description}>
          {description}
        </p>
      ) : null}
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted/80 dark:bg-muted/40"
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={ariaLabel}
      >
        <div
          className={cn(
            "h-full rounded-full bg-gradient-to-r transition-all duration-500",
            invert && value >= 25 ? "from-amber-500 to-orange-500" : toneCls[tone],
          )}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
      <p className="text-[10px] text-muted-foreground">{detail}</p>
    </div>
  );
}