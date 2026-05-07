import type { ReactNode } from "react";
import { useGetAdminStats, useGetAdminTrend, useGetAdminActivity, useListDocuments } from "@workspace/api-client-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  FileText,
  MessageSquare,
  Ticket as TicketIcon,
  Zap,
  CheckCircle2,
  AlertCircle,
  Clock,
  Search,
  Loader2,
  ArrowUpRight,
  Database,
  Inbox,
  TrendingUp,
  Activity,
  Info,
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
  const { data: stats } = useGetAdminStats();
  const { data: trend } = useGetAdminTrend();
  const { data: activity } = useGetAdminActivity();
  const { data: documents } = useListDocuments();
  const vectorIndexStatus = useQuery<VectorIndexStatus, Error>({
    queryKey: ["admin-vector-index-status"],
    queryFn: () => adminJson<VectorIndexStatus>("/api/admin/vector-index"),
    refetchInterval: (query) => {
      const data = query.state.data as VectorIndexStatus | undefined;
      return data?.queryable ? false : 5000;
    },
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

  const kpis = stats
    ? [
        {
          title: "Total queries",
          value: stats.totalQueries,
          icon: MessageSquare,
          accent: "text-blue-600 dark:text-blue-400",
          tint: "bg-blue-500/[0.09] ring-1 ring-blue-500/[0.12] dark:bg-blue-400/[0.11] dark:ring-blue-400/18",
          stripe: "from-blue-500 via-blue-500/70 to-blue-600/60",
          bar: "from-blue-500/70 to-blue-600/45",
          glow: "bg-blue-500/[0.14] dark:bg-blue-400/[0.11]",
          businessHint:
            "How many times users have asked the assistant a question. Higher totals usually mean stronger self‑service adoption and less load on human agents. Pair this with Helpful rate to see whether volume reflects real value or frustration.",
        },
        {
          title: "Helpful rate",
          value: `${Math.round(stats.helpfulRate * 100)}%`,
          icon: CheckCircle2,
          accent: "text-emerald-600 dark:text-emerald-400",
          tint: "bg-emerald-500/[0.09] ring-1 ring-emerald-500/[0.12] dark:bg-emerald-400/[0.11] dark:ring-emerald-400/18",
          stripe: "from-emerald-500 via-emerald-500/70 to-emerald-600/55",
          bar: "from-emerald-500/70 to-emerald-600/40",
          glow: "bg-emerald-500/[0.14] dark:bg-emerald-400/[0.11]",
          businessHint:
            "Share of user‑rated answers marked helpful. It is a direct signal of answer quality and trust. Use it to prioritize content gaps, model tuning, and which topics deserve richer knowledge articles.",
        },
        {
          title: "Active documents",
          value: stats.approvedDocuments,
          icon: FileText,
          accent: "text-indigo-600 dark:text-indigo-400",
          tint: "bg-indigo-500/[0.09] ring-1 ring-indigo-500/[0.12] dark:bg-indigo-400/[0.11] dark:ring-indigo-400/18",
          stripe: "from-indigo-500 via-indigo-500/70 to-indigo-600/55",
          bar: "from-indigo-500/70 to-indigo-600/40",
          glow: "bg-indigo-500/[0.14] dark:bg-indigo-400/[0.11]",
          businessHint:
            "Knowledge sources approved for the assistant to use when answering. Each approved document expands what the bot can cite accurately. Stale or thin libraries here often show up as low helpful rates or rising tickets.",
        },
        {
          title: "Knowledge chunks",
          value: stats.totalChunks,
          icon: Zap,
          accent: "text-amber-600 dark:text-amber-400",
          tint: "bg-amber-500/[0.09] ring-1 ring-amber-500/[0.12] dark:bg-amber-400/[0.11] dark:ring-amber-400/18",
          stripe: "from-amber-500 via-amber-500/70 to-amber-600/50",
          bar: "from-amber-500/70 to-amber-600/38",
          glow: "bg-amber-500/[0.14] dark:bg-amber-400/[0.11]",
          businessHint:
            "Searchable slices of your knowledge base that power retrieval. More chunks generally mean finer‑grained answers, but only if content stays accurate. It also reflects embedding and index work behind the scenes.",
        },
        {
          title: "Open tickets",
          value: stats.openTickets,
          icon: AlertCircle,
          accent: "text-orange-600 dark:text-orange-400",
          tint: "bg-orange-500/[0.09] ring-1 ring-orange-500/[0.12] dark:bg-orange-400/[0.11] dark:ring-orange-400/18",
          stripe: "from-orange-500 via-orange-500/70 to-orange-600/50",
          bar: "from-orange-500/70 to-orange-600/38",
          glow: "bg-orange-500/[0.14] dark:bg-orange-400/[0.11]",
          businessHint:
            "Support cases not yet resolved. It is your live backlog and workload indicator. A growing count may mean the assistant or knowledge base is missing key answers, or that demand is outpacing team capacity.",
        },
        {
          title: "PII removed",
          value: stats.piiRemovedTotal,
          icon: Search,
          accent: "text-violet-600 dark:text-violet-400",
          tint: "bg-violet-500/[0.09] ring-1 ring-violet-500/[0.12] dark:bg-violet-400/[0.11] dark:ring-violet-400/18",
          stripe: "from-violet-500 via-violet-500/70 to-violet-600/50",
          bar: "from-violet-500/70 to-violet-600/38",
          glow: "bg-violet-500/[0.14] dark:bg-violet-400/[0.11]",
          businessHint:
            "Count of personally identifiable or sensitive data detections scrubbed before content or answers go out. It supports privacy and compliance posture and shows how often risky data appears in your pipeline.",
        },
      ]
    : [];

  const getActivityIcon = (kind: string) => {
    switch (kind) {
      case "document_uploaded":
        return <FileText className="h-4 w-4 text-blue-600 dark:text-blue-400" />;
      case "document_approved":
        return <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />;
      case "query_answered":
        return <MessageSquare className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />;
      case "ticket_opened":
        return <TicketIcon className="h-4 w-4 text-orange-600 dark:text-orange-400" />;
      default:
        return <Clock className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const activityKindUi = (kind: string) => {
    switch (kind) {
      case "document_uploaded":
        return {
          label: "Document",
          rail: "from-blue-500/90 to-blue-600/70 dark:from-blue-400/85 dark:to-blue-500/55",
          iconWrap:
            "border-blue-500/25 bg-blue-500/[0.1] shadow-sm shadow-blue-500/[0.06] ring-1 ring-blue-500/[0.08] dark:bg-blue-400/[0.12]",
        };
      case "document_approved":
        return {
          label: "Approved",
          rail: "from-emerald-500/90 to-emerald-600/70 dark:from-emerald-400/85 dark:to-emerald-500/55",
          iconWrap:
            "border-emerald-500/25 bg-emerald-500/[0.1] shadow-sm shadow-emerald-500/[0.06] ring-1 ring-emerald-500/[0.08] dark:bg-emerald-400/[0.12]",
        };
      case "query_answered":
        return {
          label: "Query",
          rail: "from-indigo-500/90 to-indigo-600/70 dark:from-indigo-400/85 dark:to-indigo-500/55",
          iconWrap:
            "border-indigo-500/25 bg-indigo-500/[0.1] shadow-sm shadow-indigo-500/[0.06] ring-1 ring-indigo-500/[0.08] dark:bg-indigo-400/[0.12]",
        };
      case "ticket_opened":
        return {
          label: "Ticket",
          rail: "from-orange-500/90 to-orange-600/70 dark:from-orange-400/85 dark:to-orange-500/55",
          iconWrap:
            "border-orange-500/25 bg-orange-500/[0.1] shadow-sm shadow-orange-500/[0.06] ring-1 ring-orange-500/[0.08] dark:bg-orange-400/[0.12]",
        };
      default:
        return {
          label: "Event",
          rail: "from-muted-foreground/50 to-muted-foreground/35",
          iconWrap: "border-border/70 bg-muted/40 shadow-sm ring-1 ring-border/50",
        };
    }
  };

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

        <section aria-label="Key metrics">
          <div className="grid grid-cols-2 gap-3 sm:gap-3.5 md:grid-cols-3 lg:grid-cols-6">
            {!stats
              ? Array.from({ length: 6 }, (_, i) => (
                  <div
                    key={i}
                    className={cn(
                      "relative overflow-hidden rounded-2xl border border-border/45 bg-card/60",
                      "p-4 shadow-sm backdrop-blur-sm dark:bg-card/40 dark:shadow-black/20 sm:p-[1.125rem]",
                    )}
                  >
                    <Skeleton className="mb-4 h-3 w-[62%] rounded-md" />
                    <Skeleton className="h-9 w-[48%] rounded-lg" />
                    <Skeleton className="mt-5 h-1 w-full rounded-full" />
                  </div>
                ))
              : kpis.map((kpi) => (
                  <div
                    key={kpi.title}
                    className={cn(
                      "group relative overflow-hidden rounded-2xl border border-border/50 bg-card/85",
                      "shadow-sm shadow-black/[0.02] backdrop-blur-md",
                      "transition-[box-shadow,border-color,transform] duration-300 ease-out",
                      "hover:-translate-y-0.5 hover:border-border/65 hover:shadow-md hover:shadow-black/[0.06]",
                      "dark:bg-card/55 dark:shadow-black/25 dark:hover:border-border/55 dark:hover:shadow-lg dark:hover:shadow-black/35",
                    )}
                  >
                    <div
                      className={cn(
                        "pointer-events-none absolute -right-10 -top-14 h-36 w-36 rounded-full blur-3xl",
                        "opacity-55 transition-opacity duration-300 group-hover:opacity-80",
                        kpi.glow,
                      )}
                      aria-hidden
                    />
                    <div
                      className={cn(
                        "pointer-events-none absolute left-0 top-0 bottom-0 w-[3px] bg-gradient-to-b opacity-90",
                        kpi.stripe,
                      )}
                      aria-hidden
                    />
                    <div
                      className={cn(
                        "pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent",
                        "via-white/45 to-transparent dark:via-white/[0.08]",
                      )}
                      aria-hidden
                    />
                    <div className="absolute bottom-2 right-2 z-10 sm:bottom-2.5 sm:right-2.5">
                      <AdminCardInfo title={kpi.title} contentSide="top" contentAlign="end">
                        {kpi.businessHint}
                      </AdminCardInfo>
                    </div>
                    <div className="relative flex flex-col gap-3.5 p-4 pb-5 pr-[2.875rem] sm:gap-4 sm:p-[1.125rem] sm:pb-[1.35rem] sm:pr-[3rem]">
                      <span
                        className={cn(
                          "pointer-events-none absolute right-4 top-4 z-[1]",
                          "flex h-10 w-10 items-center justify-center rounded-2xl sm:right-[1.125rem] sm:top-[1.125rem] sm:h-9 sm:w-9",
                          "shadow-sm shadow-black/[0.04] transition-transform duration-300 ease-out group-hover:scale-[1.04]",
                          kpi.tint,
                        )}
                        aria-hidden
                      >
                        <kpi.icon className={cn("h-[1.0625rem] w-[1.0625rem] sm:h-[1.05rem] sm:w-[1.05rem]", kpi.accent)} strokeWidth={2} />
                      </span>
                      <div className="min-w-0 max-w-[calc(100%-3rem)] sm:max-w-none sm:pr-0">
                        <p className="text-[11px] font-medium leading-snug tracking-wide text-muted-foreground sm:text-xs">
                          {kpi.title}
                        </p>
                      </div>
                      <p className="tabular-nums text-[1.5rem] font-semibold leading-none tracking-tight text-foreground sm:text-[1.625rem]">
                        {kpi.value}
                      </p>
                      <div
                        className={cn(
                          "pointer-events-none mt-0.5 h-1 w-full overflow-hidden rounded-full bg-muted/80",
                          "dark:bg-muted/45",
                        )}
                        aria-hidden
                      >
                        <div
                          className={cn(
                            "h-full w-[52%] rounded-full bg-gradient-to-r opacity-80 transition-[width,opacity] duration-500 ease-out",
                            "group-hover:w-[88%] group-hover:opacity-100",
                            kpi.bar,
                          )}
                        />
                      </div>
                    </div>
                  </div>
                ))}
          </div>
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
                    Atlas vector search readiness and chunk embedding coverage
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
                    <p className="mt-2 font-mono text-sm font-semibold tabular-nums text-foreground">
                      {vectorIndexStatus.data.state ?? "UNKNOWN"}
                    </p>
                  </div>
                  <div className="rounded-xl border border-border/60 bg-muted/20 px-3.5 py-3 dark:bg-muted/10">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Index</p>
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
                    Index name{" "}
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
                    <CardDescription className="text-[13px] leading-normal">
                      Last 14 days of assistant usage
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
                  <CardDescription className="line-clamp-2 text-[13px]">Documents awaiting approval</CardDescription>
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

        <Card
          className={cn(
            "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
            "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
            "dark:bg-card/70 dark:shadow-black/30",
            "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
          )}
        >
          <div
            className="pointer-events-none absolute -right-16 -top-24 h-44 w-44 rounded-full bg-violet-500/[0.07] blur-3xl dark:bg-violet-400/[0.05]"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute -left-12 bottom-0 h-36 w-36 rounded-full bg-primary/[0.06] blur-3xl dark:bg-primary/[0.05]"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
            aria-hidden
          />

          <CardHeader className="relative space-y-0 border-b-0 bg-transparent p-0 px-4 pb-0 pt-4 sm:px-5 sm:pt-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex min-w-0 gap-3 sm:gap-3">
                <div
                  className={cn(
                    "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl sm:h-10 sm:w-10",
                    "bg-gradient-to-br from-violet-500/14 to-primary/10 shadow-inner shadow-violet-500/5",
                    "ring-1 ring-violet-500/12 dark:from-violet-400/12 dark:to-primary/8 dark:ring-violet-400/10",
                  )}
                >
                  <Activity className="h-[1.15rem] w-[1.15rem] text-violet-600 dark:text-violet-400 sm:h-5 sm:w-5" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground sm:text-[11px]">
                    Live feed
                  </p>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <CardTitle className="text-[15px] font-semibold tracking-tight sm:text-base">Recent activity</CardTitle>
                    <AdminCardInfo title="Recent activity">
                      A chronological audit trail for the support AI program: uploads, approvals, chat queries answered, and
                      tickets opened. Use it to spot operational rhythm, who is taking action, and whether content or volume
                      shifts line up with issues your team is seeing.
                    </AdminCardInfo>
                  </div>
                  <CardDescription className="text-xs leading-snug sm:text-[13px] sm:leading-normal">
                    Latest actions across tickets, documents, and queries
                  </CardDescription>
                </div>
              </div>
            </div>
          </CardHeader>

          <CardContent className="relative p-0 px-4 pb-4 pt-3 sm:px-5 sm:pb-4 sm:pt-3">
            {!activity?.length ? (
              <div
                className={cn(
                  "flex flex-col items-center justify-center gap-2.5 rounded-xl border border-dashed border-border/60",
                  "bg-gradient-to-b from-muted/25 to-transparent px-4 py-9 text-center dark:from-muted/12 sm:py-10",
                )}
              >
                <div
                  className={cn(
                    "flex h-11 w-11 items-center justify-center rounded-xl",
                    "bg-gradient-to-br from-violet-500/12 to-primary/8 ring-1 ring-violet-500/12 sm:h-12 sm:w-12 sm:rounded-2xl",
                  )}
                >
                  <Inbox className="h-6 w-6 text-violet-600 dark:text-violet-400 sm:h-6 sm:w-6" strokeWidth={1.75} />
                </div>
                <div className="space-y-0.5">
                  <p className="text-sm font-semibold text-foreground">Nothing here yet</p>
                  <p className="mx-auto max-w-[18rem] text-[11px] leading-snug text-muted-foreground sm:text-xs sm:leading-relaxed">
                    Activity from uploads, reviews, chat, and tickets will appear as it happens.
                  </p>
                </div>
              </div>
            ) : (
              <ul className="space-y-1.5 sm:space-y-2" role="list">
                {activity.map((item) => {
                  const kind = activityKindUi(item.kind);
                  return (
                    <li key={item.id}>
                      <div
                        className={cn(
                          "group relative flex gap-2.5 overflow-hidden rounded-xl border border-border/50",
                          "bg-gradient-to-br from-muted/25 via-card to-card/80 p-2.5 transition-all duration-200",
                          "dark:from-muted/12 dark:via-card dark:to-card/90",
                          "hover:border-border hover:from-muted/35 hover:shadow-md hover:shadow-black/[0.04] dark:hover:shadow-black/25",
                          "sm:gap-3 sm:p-3 sm:rounded-2xl",
                        )}
                      >
                        <div
                          className={cn(
                            "absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-gradient-to-b opacity-90 sm:top-2.5 sm:bottom-2.5",
                            kind.rail,
                          )}
                          aria-hidden
                        />
                        <div
                          className={cn(
                            "relative z-[1] flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border sm:h-9 sm:w-9 sm:rounded-xl",
                            kind.iconWrap,
                          )}
                        >
                          {getActivityIcon(item.kind)}
                        </div>
                        <div className="min-w-0 flex-1 space-y-1 pl-0.5 sm:pl-0.5">
                          <Badge
                            variant="secondary"
                            className={cn(
                              "h-5 rounded-md border border-border/60 px-1.5 font-medium",
                              "bg-background/90 text-[10px] shadow-sm backdrop-blur-sm sm:h-[22px] sm:px-2 sm:text-[11px]",
                            )}
                          >
                            {kind.label}
                          </Badge>
                          <p className="text-[13px] font-medium leading-snug text-foreground sm:text-sm">{item.title}</p>
                          {item.subtitle ? (
                            <p className="line-clamp-2 text-xs leading-snug text-muted-foreground sm:text-[13px] sm:leading-relaxed">
                              {item.subtitle}
                            </p>
                          ) : null}
                          <p className="text-[11px] text-muted-foreground sm:text-xs">
                            {format(new Date(item.createdAt), "MMM d, h:mm a")}
                            {item.actor ? (
                              <>
                                {" "}
                                · <span className="font-medium text-foreground/80">{item.actor}</span>
                              </>
                            ) : null}
                          </p>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}