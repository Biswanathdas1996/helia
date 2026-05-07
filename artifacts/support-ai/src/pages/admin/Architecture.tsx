import type { ReactNode } from "react";
import {
  Activity,
  ArrowRight,
  Boxes,
  Brain,
  Cloud,
  Cpu,
  Database,
  FileSearch,
  FileUp,
  GitBranch,
  Globe,
  KeyRound,
  Layers,
  Lock,
  MessageSquare,
  Network,
  Package,
  Search,
  Server,
  ShieldCheck,
  Sparkles,
  Workflow,
  Zap,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

interface PipelineStep {
  n: number;
  title: string;
  desc: string;
  source?: string;
}

interface StackItem {
  name: string;
  detail: string;
}

interface IntegrationItem {
  name: string;
  icon: typeof Cloud;
  purpose: string;
  envHint?: string;
  optional?: boolean;
}

const overviewLayers: Array<{
  title: string;
  subtitle: string;
  icon: typeof Server;
  accent: string;
  tint: string;
  items: StackItem[];
}> = [
  {
    title: "Frontend",
    subtitle: "support-ai (React SPA)",
    icon: Layers,
    accent: "text-sky-700 dark:text-sky-300",
    tint: "from-sky-500/15 to-blue-500/10 ring-sky-500/15 dark:from-sky-400/12 dark:to-blue-400/8 dark:ring-sky-400/12",
    items: [
      { name: "React 18 + TypeScript", detail: "Vite, wouter routing, TanStack Query" },
      { name: "Design system", detail: "Tailwind + shadcn/ui (Radix primitives)" },
      { name: "API client", detail: "Orval-generated hooks from OpenAPI spec" },
      { name: "Validation", detail: "Zod (zod/v4) at the form/data boundary" },
    ],
  },
  {
    title: "API server",
    subtitle: "artifacts/api-server (FastAPI)",
    icon: Server,
    accent: "text-violet-700 dark:text-violet-300",
    tint: "from-violet-500/15 to-fuchsia-500/10 ring-violet-500/15 dark:from-violet-400/12 dark:to-fuchsia-400/8 dark:ring-violet-400/12",
    items: [
      { name: "FastAPI + uvicorn", detail: "Python 3.12, Pydantic v2 schemas" },
      { name: "Auth", detail: "Custom JWT cookie + bcrypt; admin / user roles" },
      { name: "Middleware", detail: "CORS + Prometheus metrics middleware" },
      { name: "Routers", detail: "auth, me, admin, documents, extract, chat, messages, tickets" },
    ],
  },
  {
    title: "Data & retrieval",
    subtitle: "MongoDB Atlas + cache",
    icon: Database,
    accent: "text-emerald-700 dark:text-emerald-300",
    tint: "from-emerald-500/15 to-teal-500/10 ring-emerald-500/15 dark:from-emerald-400/12 dark:to-teal-400/8 dark:ring-emerald-400/12",
    items: [
      { name: "MongoDB Atlas", detail: "Single cluster: app data + retrieval corpus" },
      { name: "Vector search", detail: "Atlas Vector Search over 768-d Gemini embeddings" },
      { name: "Lexical search", detail: "Mongo $text (BM25-ish) for hybrid recall" },
      { name: "Cache", detail: "Redis when REDIS_URL set; in-process LRU otherwise" },
    ],
  },
  {
    title: "AI gateway",
    subtitle: "PwC GenAI (OpenAI-compatible)",
    icon: Brain,
    accent: "text-amber-700 dark:text-amber-300",
    tint: "from-amber-500/15 to-orange-500/10 ring-amber-500/15 dark:from-amber-400/12 dark:to-orange-400/8 dark:ring-amber-400/12",
    items: [
      { name: "Chat model", detail: "vertex_ai.gemini-2.5-flash-image" },
      { name: "Embeddings", detail: "vertex_ai.gemini-embedding (768-d)" },
      { name: "Wire format", detail: "/chat/completions, /embeddings (OpenAI-compatible)" },
      { name: "Client", detail: "app/pwc_ai.py wrapped by app/llm.py" },
    ],
  },
];

const retrievalSteps: PipelineStep[] = [
  {
    n: 1,
    title: "Query rewrite + intent",
    desc: "LLM call in JSON mode normalizes the user question and tags intent. Skippable via DISABLE_QUERY_REWRITE.",
    source: "app/query_rewrite.py",
  },
  {
    n: 2,
    title: "Hybrid search",
    desc: "BM25 ($text) ∪ Atlas Vector Search over chunks.embedding. Falls back to BM25-only when MONGODB_VECTOR_SEARCH is off.",
    source: "app/retrieval.py · app/embeddings.py",
  },
  {
    n: 3,
    title: "Jaccard dedup",
    desc: "Token-set overlap removes redundant chunks before fusion so similar passages do not dominate the context window.",
    source: "app/text.py",
  },
  {
    n: 4,
    title: "RRF + LLM rerank",
    desc: "Reciprocal-rank fusion merges the two leg orderings, then an LLM rerank scores the top-N. Skippable via DISABLE_RERANK.",
    source: "app/retrieval.py",
  },
  {
    n: 5,
    title: "Top-K assembly",
    desc: "Default K=5 chunks returned with citation metadata for the chat agent to reason over and cite back to the user.",
    source: "app/retrieval.py",
  },
];

const ingestionSteps: PipelineStep[] = [
  {
    n: 1,
    title: "PII detect + mask",
    desc: "Sensitive entities are flagged and replaced before chunking so they never reach the embedding model or the index.",
    source: "app/pii.py",
  },
  {
    n: 2,
    title: "Chunk",
    desc: "Token-windowed chunking, ~180 words with 30-word overlap. Tunes recall vs. context fragmentation.",
    source: "app/text.py",
  },
  {
    n: 3,
    title: "Embed",
    desc: "Batched embedding generation through the PwC gateway when the provider supports it.",
    source: "app/embeddings.py",
  },
  {
    n: 4,
    title: "Cross-document dedup",
    desc: "Cosine similarity when embeddings exist; Jaccard fallback otherwise. Stops near-duplicate uploads from inflating the corpus.",
    source: "app/embeddings.py · app/text.py",
  },
  {
    n: 5,
    title: "Persist",
    desc: "Write to documents + chunks collections (with embedding field) and a detailed ingestionReport on the document.",
    source: "app/routes/documents.py",
  },
  {
    n: 6,
    title: "Admin review",
    desc: "Approve / reject gate. Only approved chunks become eligible for retrieval.",
    source: "/admin/documents",
  },
];

const integrations: IntegrationItem[] = [
  {
    name: "PwC GenAI gateway",
    icon: Brain,
    purpose: "Chat completions and embeddings via OpenAI-compatible endpoints.",
    envHint: "PWC_AI_API_KEY + PWC_TOKEN",
  },
  {
    name: "MongoDB Atlas",
    icon: Database,
    purpose: "App data, audit logs, ingestion corpus, and Atlas Vector Search index.",
    envHint: "MONGODB_URI",
  },
  {
    name: "Redis",
    icon: Zap,
    purpose: "Production cache + cross-replica rate limiting. Falls back to in-process LRU when absent.",
    envHint: "REDIS_URL",
    optional: true,
  },
  {
    name: "Zoho Desk",
    icon: Globe,
    purpose: "Tickets created locally are mirrored to Zoho. /api/tickets/active-summary auto-refreshes open status.",
    envHint: "ZOHO_*",
    optional: true,
  },
  {
    name: "Atlas Vector Search",
    icon: Search,
    purpose: "Vector leg of retrieval over 768-d Gemini embeddings. See infra/atlas-vector-index.json.",
    envHint: "MONGODB_VECTOR_SEARCH=true",
    optional: true,
  },
  {
    name: "Prometheus",
    icon: Activity,
    purpose: "Scrape /api/metrics for HTTP counters/histograms. Scraper infra is environment-specific.",
    optional: true,
  },
];

const envVars: Array<{ key: string; required: boolean; desc: string }> = [
  { key: "MONGODB_URI", required: true, desc: "Atlas connection string (app data + retrieval cluster)." },
  { key: "SESSION_SECRET", required: true, desc: "JWT signing secret for the auth cookie." },
  { key: "PWC_AI_API_KEY", required: true, desc: "PwC GenAI gateway credential (chat + embeddings)." },
  { key: "PWC_TOKEN", required: true, desc: "Companion token for the PwC gateway." },
  { key: "REDIS_URL", required: false, desc: "Enables Redis-backed cache and shared rate limiting." },
  { key: "MONGODB_VECTOR_SEARCH", required: false, desc: "Set to 'true' once the Atlas vector index is created." },
  { key: "ZOHO_*", required: false, desc: "OAuth + org settings for Zoho Desk ticket mirroring." },
  { key: "DISABLE_QUERY_REWRITE", required: false, desc: "Skip rewrite stage to trade quality for latency." },
  { key: "DISABLE_RERANK", required: false, desc: "Skip LLM rerank stage to trade quality for latency." },
];

function SectionHeader({
  icon: Icon,
  eyebrow,
  title,
  description,
  iconWrap,
  iconColor,
}: {
  icon: typeof Server;
  eyebrow: string;
  title: string;
  description: string;
  iconWrap: string;
  iconColor: string;
}) {
  return (
    <div className="flex min-w-0 gap-4">
      <div
        className={cn(
          "flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl",
          "shadow-inner ring-1",
          iconWrap,
        )}
      >
        <Icon className={cn("h-6 w-6", iconColor)} strokeWidth={1.75} />
      </div>
      <div className="min-w-0 space-y-1">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">{eyebrow}</p>
        <CardTitle className="text-base font-semibold tracking-tight sm:text-lg">{title}</CardTitle>
        <CardDescription className="text-[13px] leading-normal">{description}</CardDescription>
      </div>
    </div>
  );
}

function FlowBox({
  icon: Icon,
  title,
  subtitle,
  accent,
  children,
}: {
  icon: typeof Server;
  title: string;
  subtitle: string;
  accent: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "relative flex min-h-[88px] flex-1 flex-col gap-1.5 rounded-2xl border border-border/55",
        "bg-gradient-to-br p-3.5 shadow-sm shadow-black/[0.03] backdrop-blur-sm",
        "dark:shadow-black/25",
        accent,
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-foreground/85" strokeWidth={1.85} />
        <p className="text-[13px] font-semibold tracking-tight text-foreground">{title}</p>
      </div>
      <p className="text-[11px] leading-snug text-muted-foreground">{subtitle}</p>
      {children}
    </div>
  );
}

function FlowArrow() {
  return (
    <div className="flex items-center justify-center text-muted-foreground/70">
      <ArrowRight className="hidden h-4 w-4 lg:block" />
      <ArrowRight className="h-4 w-4 rotate-90 lg:hidden" />
    </div>
  );
}

function PipelineList({ steps }: { steps: PipelineStep[] }) {
  return (
    <ol className="space-y-2.5" role="list">
      {steps.map((step) => (
        <li
          key={step.n}
          className={cn(
            "group relative flex gap-3 overflow-hidden rounded-xl border border-border/55",
            "bg-gradient-to-br from-muted/25 via-card to-card/85 p-3.5 transition-colors",
            "dark:from-muted/12 dark:via-card dark:to-card/90",
            "hover:border-border hover:from-muted/35",
          )}
        >
          <div
            className={cn(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-primary/25",
              "bg-primary/[0.08] font-mono text-xs font-semibold tabular-nums text-primary",
              "ring-1 ring-primary/[0.06] dark:bg-primary/[0.12]",
            )}
          >
            {step.n}
          </div>
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm font-semibold leading-snug text-foreground">{step.title}</p>
            <p className="text-[13px] leading-relaxed text-muted-foreground">{step.desc}</p>
            {step.source ? (
              <p className="font-mono text-[11px] text-muted-foreground/85">
                <span className="text-muted-foreground/60">source · </span>
                {step.source}
              </p>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function AdminArchitecture() {
  return (
    <div className="relative isolate min-h-full">
      <div
        className="pointer-events-none absolute inset-x-0 -top-24 h-[28rem] max-w-[100vw] overflow-hidden opacity-90"
        aria-hidden
      >
        <div className="absolute left-1/2 top-0 h-80 w-[min(90rem,140%)] -translate-x-1/2 rounded-[100%] bg-[radial-gradient(ellipse_at_center,_hsl(var(--primary)/0.07)_0%,_transparent_65%)]" />
        <div className="absolute right-[-10%] top-12 h-72 w-72 rounded-full bg-violet-500/[0.05] blur-3xl dark:bg-violet-400/[0.07]" />
        <div className="absolute left-[-5%] top-32 h-56 w-56 rounded-full bg-sky-500/[0.05] blur-3xl dark:bg-sky-400/[0.06]" />
      </div>

      <div className="relative mx-auto max-w-7xl space-y-8 px-6 py-8 sm:px-8 lg:px-10">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4 lg:items-center">
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">Reference</p>
            <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
              System architecture
            </h1>
            <p className="max-w-2xl text-xs leading-snug text-muted-foreground sm:text-[13px] sm:leading-normal">
              How the Helia support assistant is wired together — frontend, API server, retrieval and ingestion
              pipelines, integrations, and runtime configuration.
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
            <Badge
              variant="outline"
              className="h-7 gap-1.5 rounded-lg border-border/70 bg-background/70 px-2.5 font-mono text-[11px] backdrop-blur-sm"
            >
              <GitBranch className="h-3.5 w-3.5" />
              monorepo · pnpm + uv
            </Badge>
            <Badge
              variant="outline"
              className="h-7 gap-1.5 rounded-lg border-border/70 bg-background/70 px-2.5 font-mono text-[11px] backdrop-blur-sm"
            >
              <Package className="h-3.5 w-3.5" />
              Node 24 · Python 3.12
            </Badge>
          </div>
        </header>

        {/* High-level data flow */}
        <Card
          className={cn(
            "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
            "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
            "dark:bg-card/70 dark:shadow-black/30",
            "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
          )}
        >
          <div
            className="pointer-events-none absolute -right-16 -top-24 h-44 w-44 rounded-full bg-primary/[0.07] blur-3xl dark:bg-primary/[0.05]"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute -left-12 bottom-0 h-36 w-36 rounded-full bg-cyan-500/[0.06] blur-3xl dark:bg-cyan-400/[0.05]"
            aria-hidden
          />
          <div
            className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
            aria-hidden
          />

          <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/15 pb-4 pt-5 sm:pb-5">
            <SectionHeader
              icon={Network}
              eyebrow="Topology"
              title="High-level data flow"
              description="A user request traverses these components end to end."
              iconWrap="bg-gradient-to-br from-cyan-500/15 to-violet-500/12 ring-cyan-500/15 shadow-cyan-500/5 dark:from-cyan-400/12 dark:to-violet-400/10 dark:ring-cyan-400/12"
              iconColor="text-cyan-700 dark:text-cyan-300"
            />
          </CardHeader>

          <CardContent className="relative pt-5 sm:pt-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-stretch">
              <FlowBox
                icon={MessageSquare}
                title="User"
                subtitle="Chats or uploads docs in the React SPA"
                accent="from-sky-500/[0.08] to-transparent dark:from-sky-400/[0.07]"
              />
              <FlowArrow />
              <FlowBox
                icon={Layers}
                title="support-ai (Vite)"
                subtitle="React + TanStack Query + Orval hooks"
                accent="from-blue-500/[0.08] to-transparent dark:from-blue-400/[0.07]"
              />
              <FlowArrow />
              <FlowBox
                icon={Server}
                title="FastAPI server"
                subtitle="JWT auth, routers, metrics middleware"
                accent="from-violet-500/[0.08] to-transparent dark:from-violet-400/[0.07]"
              />
              <FlowArrow />
              <FlowBox
                icon={Workflow}
                title="Retrieval / ingestion"
                subtitle="Hybrid RAG · PII + chunk + embed"
                accent="from-fuchsia-500/[0.08] to-transparent dark:from-fuchsia-400/[0.07]"
              />
              <FlowArrow />
              <FlowBox
                icon={Database}
                title="Atlas + cache"
                subtitle="Mongo collections + vector index + Redis"
                accent="from-emerald-500/[0.08] to-transparent dark:from-emerald-400/[0.07]"
              />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <FlowBox
                icon={Brain}
                title="PwC GenAI"
                subtitle="Gemini chat + embeddings via OpenAI-compatible API"
                accent="from-amber-500/[0.08] to-transparent dark:from-amber-400/[0.07]"
              />
              <FlowBox
                icon={Globe}
                title="Zoho Desk"
                subtitle="Tickets mirrored when ZOHO_* env vars are set"
                accent="from-orange-500/[0.08] to-transparent dark:from-orange-400/[0.07]"
              />
              <FlowBox
                icon={Activity}
                title="Prometheus"
                subtitle="HTTP counters + latency histograms via /api/metrics"
                accent="from-rose-500/[0.08] to-transparent dark:from-rose-400/[0.07]"
              />
              <FlowBox
                icon={ShieldCheck}
                title="audit_logs"
                subtitle="Mongo collection capturing admin + retrieval events"
                accent="from-teal-500/[0.08] to-transparent dark:from-teal-400/[0.07]"
              />
            </div>
          </CardContent>
        </Card>

        {/* Tabbed deep-dive */}
        <Tabs defaultValue="stack" className="space-y-5">
          <TabsList className="h-auto flex-wrap gap-1 rounded-xl border border-border/55 bg-card/70 p-1 backdrop-blur-sm">
            <TabsTrigger value="stack" className="gap-1.5 rounded-lg text-xs">
              <Boxes className="h-3.5 w-3.5" />
              Stack
            </TabsTrigger>
            <TabsTrigger value="pipelines" className="gap-1.5 rounded-lg text-xs">
              <Workflow className="h-3.5 w-3.5" />
              Pipelines
            </TabsTrigger>
            <TabsTrigger value="integrations" className="gap-1.5 rounded-lg text-xs">
              <Cloud className="h-3.5 w-3.5" />
              Integrations
            </TabsTrigger>
            <TabsTrigger value="security" className="gap-1.5 rounded-lg text-xs">
              <Lock className="h-3.5 w-3.5" />
              Auth & security
            </TabsTrigger>
            <TabsTrigger value="config" className="gap-1.5 rounded-lg text-xs">
              <KeyRound className="h-3.5 w-3.5" />
              Config
            </TabsTrigger>
          </TabsList>

          {/* Stack tab */}
          <TabsContent value="stack" className="space-y-5">
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {overviewLayers.map((layer) => (
                <Card
                  key={layer.title}
                  className={cn(
                    "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
                    "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
                    "dark:bg-card/70 dark:shadow-black/30",
                    "hover:shadow-lg hover:shadow-black/[0.05] dark:hover:shadow-black/40",
                  )}
                >
                  <div
                    className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent opacity-80 dark:via-white/12"
                    aria-hidden
                  />
                  <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                    <SectionHeader
                      icon={layer.icon}
                      eyebrow="Layer"
                      title={layer.title}
                      description={layer.subtitle}
                      iconWrap={cn("bg-gradient-to-br shadow-inner", layer.tint)}
                      iconColor={layer.accent}
                    />
                  </CardHeader>
                  <CardContent className="relative pt-5">
                    <ul className="space-y-2.5">
                      {layer.items.map((item) => (
                        <li
                          key={item.name}
                          className="flex items-start justify-between gap-3 rounded-lg border border-border/40 bg-muted/15 px-3 py-2 dark:bg-muted/10"
                        >
                          <span className="text-sm font-medium text-foreground">{item.name}</span>
                          <span className="max-w-[60%] text-right text-[12px] leading-snug text-muted-foreground">
                            {item.detail}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </CardContent>
                </Card>
              ))}
            </div>

            <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm shadow-black/[0.03] backdrop-blur-sm dark:bg-card/70 dark:shadow-black/30">
              <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                <SectionHeader
                  icon={Cpu}
                  eyebrow="API server"
                  title="FastAPI module map"
                  description="What lives where under artifacts/api-server/app."
                  iconWrap="bg-gradient-to-br from-violet-500/15 to-fuchsia-500/10 shadow-violet-500/5 ring-violet-500/15 dark:from-violet-400/12 dark:to-fuchsia-400/8 dark:ring-violet-400/12"
                  iconColor="text-violet-700 dark:text-violet-300"
                />
              </CardHeader>
              <CardContent className="relative pt-5">
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                  {[
                    { f: "server.py", d: "FastAPI factory, metrics middleware, /api/metrics" },
                    { f: "auth.py", d: "JWT cookie auth + role logic (admin/user)" },
                    { f: "db.py", d: "motor client, index init, next_id counter" },
                    { f: "pii.py", d: "PII detection + masking" },
                    { f: "text.py", d: "tokenize / chunk / Jaccard" },
                    { f: "llm.py", d: "Thin wrapper: chat / chat_stream / embed" },
                    { f: "pwc_ai.py", d: "PwC GenAI gateway client" },
                    { f: "embeddings.py", d: "Batched embed_batch, cosine, $vectorSearch" },
                    { f: "retrieval.py", d: "Hybrid RAG pipeline" },
                    { f: "cache.py", d: "Redis-or-in-memory cache" },
                    { f: "rate_limit.py", d: "Sliding-window rate limiter" },
                    { f: "zoho.py", d: "Zoho Desk OAuth + ticket mirror" },
                    { f: "audit.py", d: "audit_logs writes" },
                    { f: "metrics.py", d: "Prometheus counters + histograms" },
                    { f: "schemas.py", d: "Pydantic request/response bodies" },
                    { f: "serialize.py", d: "Response shaping" },
                    { f: "chat_agent.py", d: "Decision policy: clarify · answer · ticket" },
                    { f: "routes/", d: "health · auth · me · admin · documents · extract · chat · messages · tickets" },
                  ].map((mod) => (
                    <div
                      key={mod.f}
                      className="rounded-lg border border-border/45 bg-muted/15 px-3 py-2 dark:bg-muted/10"
                    >
                      <p className="font-mono text-[12px] font-semibold text-foreground">{mod.f}</p>
                      <p className="mt-0.5 text-[12px] leading-snug text-muted-foreground">{mod.d}</p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Pipelines tab */}
          <TabsContent value="pipelines" className="space-y-5">
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm shadow-black/[0.03] backdrop-blur-sm dark:bg-card/70 dark:shadow-black/30">
                <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                  <SectionHeader
                    icon={FileSearch}
                    eyebrow="Read path"
                    title="Retrieval pipeline"
                    description="app/retrieval.py — query → context for the chat agent."
                    iconWrap="bg-gradient-to-br from-indigo-500/15 to-blue-500/10 shadow-indigo-500/5 ring-indigo-500/15 dark:from-indigo-400/12 dark:to-blue-400/8 dark:ring-indigo-400/12"
                    iconColor="text-indigo-700 dark:text-indigo-300"
                  />
                </CardHeader>
                <CardContent className="relative pt-5">
                  <PipelineList steps={retrievalSteps} />
                </CardContent>
              </Card>

              <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm shadow-black/[0.03] backdrop-blur-sm dark:bg-card/70 dark:shadow-black/30">
                <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                  <SectionHeader
                    icon={FileUp}
                    eyebrow="Write path"
                    title="Ingestion pipeline"
                    description="app/routes/documents.py — upload → reviewable chunks."
                    iconWrap="bg-gradient-to-br from-emerald-500/15 to-teal-500/10 shadow-emerald-500/5 ring-emerald-500/15 dark:from-emerald-400/12 dark:to-teal-400/8 dark:ring-emerald-400/12"
                    iconColor="text-emerald-700 dark:text-emerald-300"
                  />
                </CardHeader>
                <CardContent className="relative pt-5">
                  <PipelineList steps={ingestionSteps} />
                </CardContent>
              </Card>
            </div>

            <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm shadow-black/[0.03] backdrop-blur-sm dark:bg-card/70 dark:shadow-black/30">
              <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                <SectionHeader
                  icon={Sparkles}
                  eyebrow="Agent"
                  title="Chat agent decision loop"
                  description="app/chat_agent.py — picks one of four actions per turn."
                  iconWrap="bg-gradient-to-br from-fuchsia-500/15 to-pink-500/10 shadow-fuchsia-500/5 ring-fuchsia-500/15 dark:from-fuchsia-400/12 dark:to-pink-400/8 dark:ring-fuchsia-400/12"
                  iconColor="text-fuchsia-700 dark:text-fuchsia-300"
                />
              </CardHeader>
              <CardContent className="relative pt-5">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    {
                      a: "ask_clarifying_question",
                      d: "Asks one of up to 3 grounded clarifying questions when key facts are missing.",
                      tone: "from-sky-500/[0.1] to-transparent border-sky-500/20",
                    },
                    {
                      a: "answer",
                      d: "Replies with retrieved context and citation indices.",
                      tone: "from-emerald-500/[0.1] to-transparent border-emerald-500/20",
                    },
                    {
                      a: "offer_ticket",
                      d: "Offers a ticket once unresolved attempts pass the threshold.",
                      tone: "from-amber-500/[0.1] to-transparent border-amber-500/20",
                    },
                    {
                      a: "create_ticket",
                      d: "Creates a ticket locally; mirrored to Zoho when configured.",
                      tone: "from-rose-500/[0.1] to-transparent border-rose-500/20",
                    },
                  ].map((act) => (
                    <div
                      key={act.a}
                      className={cn(
                        "rounded-xl border bg-gradient-to-br p-3.5 shadow-sm shadow-black/[0.02] dark:shadow-black/20",
                        act.tone,
                      )}
                    >
                      <p className="font-mono text-[12px] font-semibold text-foreground">{act.a}</p>
                      <p className="mt-1.5 text-[12px] leading-relaxed text-muted-foreground">{act.d}</p>
                    </div>
                  ))}
                </div>
                <p className="mt-4 text-[12px] leading-relaxed text-muted-foreground">
                  State carried across turns includes <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">summary</code>,{" "}
                  <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">knownFacts</code>,{" "}
                  <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">missingFacts</code>,{" "}
                  <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">clarificationCount</code>, and{" "}
                  <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">solutionAttempts</code> — used to gate when
                  to keep clarifying versus offer a ticket.
                </p>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Integrations tab */}
          <TabsContent value="integrations" className="space-y-5">
            <div className="grid grid-cols-1 gap-3.5 md:grid-cols-2 lg:grid-cols-3">
              {integrations.map((int) => (
                <Card
                  key={int.name}
                  className={cn(
                    "relative overflow-hidden rounded-2xl border-border/50 bg-card/90",
                    "shadow-sm shadow-black/[0.03] backdrop-blur-sm transition-shadow duration-300",
                    "dark:bg-card/70 dark:shadow-black/30",
                    "hover:shadow-md hover:shadow-black/[0.05] dark:hover:shadow-black/40",
                  )}
                >
                  <CardContent className="relative space-y-2.5 p-4">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2.5">
                        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-primary/12 to-violet-500/8 ring-1 ring-primary/12 dark:from-primary/10">
                          <int.icon className="h-4 w-4 text-primary" strokeWidth={1.85} />
                        </div>
                        <p className="text-sm font-semibold text-foreground">{int.name}</p>
                      </div>
                      <Badge
                        variant={int.optional ? "outline" : "default"}
                        className="h-5 rounded-md px-1.5 text-[10px] font-medium"
                      >
                        {int.optional ? "Optional" : "Required"}
                      </Badge>
                    </div>
                    <p className="text-[12px] leading-relaxed text-muted-foreground">{int.purpose}</p>
                    {int.envHint ? (
                      <p className="font-mono text-[11px] text-foreground/70">
                        <span className="text-muted-foreground/60">env · </span>
                        {int.envHint}
                      </p>
                    ) : null}
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          {/* Security tab */}
          <TabsContent value="security" className="space-y-5">
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm backdrop-blur-sm dark:bg-card/70">
                <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                  <SectionHeader
                    icon={ShieldCheck}
                    eyebrow="Identity"
                    title="Auth model"
                    description="Cookie-based JWT, two roles, first-user bootstrap."
                    iconWrap="bg-gradient-to-br from-emerald-500/15 to-teal-500/10 shadow-emerald-500/5 ring-emerald-500/15 dark:from-emerald-400/12 dark:to-teal-400/8 dark:ring-emerald-400/12"
                    iconColor="text-emerald-700 dark:text-emerald-300"
                  />
                </CardHeader>
                <CardContent className="relative pt-5">
                  <ul className="space-y-2.5 text-[13px] leading-relaxed text-muted-foreground">
                    <li>
                      <span className="font-semibold text-foreground">Sessions: </span>
                      JWT signed with <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">SESSION_SECRET</code>,
                      stored in an httpOnly cookie. Validated by <code className="font-mono text-[11px]">app/auth.py</code> on
                      every protected route.
                    </li>
                    <li>
                      <span className="font-semibold text-foreground">Roles: </span>
                      <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">admin</code> and{" "}
                      <code className="rounded bg-muted/70 px-1 font-mono text-[11px]">user</code>. Frontend gating via{" "}
                      <code className="font-mono text-[11px]">AdminGuard.tsx</code>; server enforces on /admin routes.
                    </li>
                    <li>
                      <span className="font-semibold text-foreground">Bootstrap: </span>
                      The first user to register is auto-promoted to <code className="font-mono text-[11px]">admin</code>; everyone
                      after registers as <code className="font-mono text-[11px]">user</code>.
                    </li>
                    <li>
                      <span className="font-semibold text-foreground">Passwords: </span>
                      bcrypt-hashed; never logged or returned to the client.
                    </li>
                  </ul>
                </CardContent>
              </Card>

              <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm backdrop-blur-sm dark:bg-card/70">
                <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                  <SectionHeader
                    icon={Lock}
                    eyebrow="Data"
                    title="Data protection"
                    description="What we strip, throttle, and audit."
                    iconWrap="bg-gradient-to-br from-rose-500/15 to-pink-500/10 shadow-rose-500/5 ring-rose-500/15 dark:from-rose-400/12 dark:to-pink-400/8 dark:ring-rose-400/12"
                    iconColor="text-rose-700 dark:text-rose-300"
                  />
                </CardHeader>
                <CardContent className="relative pt-5">
                  <ul className="space-y-2.5 text-[13px] leading-relaxed text-muted-foreground">
                    <li>
                      <span className="font-semibold text-foreground">PII scrubbing: </span>
                      <code className="font-mono text-[11px]">app/pii.py</code> masks sensitive entities before chunking, so they
                      never reach embeddings or the index.
                    </li>
                    <li>
                      <span className="font-semibold text-foreground">Rate limiting: </span>
                      Sliding-window limiter (<code className="font-mono text-[11px]">app/rate_limit.py</code>), shared via Redis
                      when configured.
                    </li>
                    <li>
                      <span className="font-semibold text-foreground">Audit log: </span>
                      Mongo <code className="font-mono text-[11px]">audit_logs</code> collection captures admin actions and
                      retrieval events.
                    </li>
                    <li>
                      <span className="font-semibold text-foreground">Review gate: </span>
                      Newly ingested documents are unsearchable until an admin approves them in /admin/documents.
                    </li>
                  </ul>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* Config tab */}
          <TabsContent value="config" className="space-y-5">
            <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm backdrop-blur-sm dark:bg-card/70">
              <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                <SectionHeader
                  icon={KeyRound}
                  eyebrow="Runtime"
                  title="Environment variables"
                  description="What the API server reads on boot. See .env.example for the full list."
                  iconWrap="bg-gradient-to-br from-amber-500/15 to-orange-500/10 shadow-amber-500/5 ring-amber-500/15 dark:from-amber-400/12 dark:to-orange-400/8 dark:ring-amber-400/12"
                  iconColor="text-amber-700 dark:text-amber-300"
                />
              </CardHeader>
              <CardContent className="relative pt-5">
                <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/50">
                  {envVars.map((v) => (
                    <li
                      key={v.key}
                      className="flex items-start justify-between gap-4 bg-muted/10 px-4 py-3 dark:bg-muted/5"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-[12px] font-semibold text-foreground">{v.key}</p>
                        <p className="mt-0.5 text-[12px] leading-snug text-muted-foreground">{v.desc}</p>
                      </div>
                      <Badge
                        variant={v.required ? "default" : "outline"}
                        className="h-5 shrink-0 rounded-md px-1.5 text-[10px] font-medium"
                      >
                        {v.required ? "Required" : "Optional"}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>

            <Card className="relative overflow-hidden rounded-2xl border-border/50 bg-card/90 shadow-sm backdrop-blur-sm dark:bg-card/70">
              <CardHeader className="relative space-y-0 border-b border-border/55 bg-muted/10 pb-4 pt-5">
                <SectionHeader
                  icon={Cpu}
                  eyebrow="Local dev"
                  title="Key commands"
                  description="What to run from the workspace root."
                  iconWrap="bg-gradient-to-br from-slate-500/15 to-zinc-500/10 shadow-slate-500/5 ring-slate-500/15 dark:from-slate-400/12 dark:to-zinc-400/8 dark:ring-slate-400/12"
                  iconColor="text-slate-700 dark:text-slate-300"
                />
              </CardHeader>
              <CardContent className="relative space-y-2 pt-5">
                {[
                  { c: "pnpm run typecheck", d: "Full typecheck across all JS/TS packages" },
                  { c: "pnpm run build", d: "Typecheck + build all JS/TS packages" },
                  {
                    c: "pnpm --filter @workspace/api-spec run codegen",
                    d: "Regenerate API hooks + Zod schemas from the OpenAPI spec",
                  },
                  {
                    c: "cd artifacts/api-server && uvicorn app.server:app --host 0.0.0.0 --port 8080",
                    d: "Run the FastAPI server",
                  },
                  { c: "docker compose up --build", d: "Full stack: api :8080, web :8081, redis sidecar" },
                ].map((cmd) => (
                  <div
                    key={cmd.c}
                    className="rounded-lg border border-border/50 bg-muted/15 px-3.5 py-2.5 dark:bg-muted/10"
                  >
                    <p className="font-mono text-[12px] font-semibold text-foreground">{cmd.c}</p>
                    <p className="mt-0.5 text-[12px] leading-snug text-muted-foreground">{cmd.d}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
