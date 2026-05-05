import { useLocation, useParams } from "wouter";
import { useGetDocument, useApproveDocument, useRejectDocument, useDeleteDocument, useExcludeDocumentDuplicateChunk, useListDocuments, getListDocumentsQueryKey, getGetDocumentQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { ArrowLeft, Check, X, Trash2, Search, AlertTriangle, FileText, Loader2, CheckCircle2, GitBranch, Files, CircleCheck, Hourglass, Database, ChevronRight, ScanSearch, Layers2, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";

type DedupDebugInfo = {
  mode: string;
  dedupMethod: string | null;
  embeddingsAvailable: boolean;
  vectorSearchEnabled: boolean;
  thresholds: {
    jaccard: number | null;
    cosine: number | null;
  };
};

function parseDedupDebug(value: unknown): DedupDebugInfo | null {
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  const thresholdsObj =
    obj.thresholds && typeof obj.thresholds === "object"
      ? (obj.thresholds as Record<string, unknown>)
      : null;

  return {
    mode: typeof obj.mode === "string" ? obj.mode : "unknown",
    dedupMethod: typeof obj.dedupMethod === "string" ? obj.dedupMethod : null,
    embeddingsAvailable: Boolean(obj.embeddingsAvailable),
    vectorSearchEnabled: Boolean(obj.vectorSearchEnabled),
    thresholds: {
      jaccard: typeof thresholdsObj?.jaccard === "number" ? thresholdsObj.jaccard : null,
      cosine: typeof thresholdsObj?.cosine === "number" ? thresholdsObj.cosine : null,
    },
  };
}

function parseIngestionDedupMethod(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  return typeof obj.dedupMethod === "string" ? obj.dedupMethod : null;
}

export default function AdminDocumentDetail() {
  const params = useParams<{ id: string }>();
  const id = parseInt(params.id!, 10);
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  
  const { data: doc, isLoading } = useGetDocument(id, {
    query: { enabled: !!id, queryKey: getGetDocumentQueryKey(id) }
  });
  const { data: allDocs } = useListDocuments({
    query: { enabled: !!id, queryKey: getListDocumentsQueryKey() }
  });

  const approve = useApproveDocument();
  const reject = useRejectDocument();
  const remove = useDeleteDocument();
  const excludeDuplicate = useExcludeDocumentDuplicateChunk();

  const [rejectReason, setRejectReason] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [lineageQuery, setLineageQuery] = useState("");
  const [activeSourcePosition, setActiveSourcePosition] = useState<number | null>(null);

  const rootId = doc?.rootDocumentId ?? doc?.id ?? id;
  const lineageDocs = !allDocs?.length
    ? []
    : allDocs
        .filter(d => (d.rootDocumentId ?? d.id) === rootId)
        .sort((a, b) => {
          const av = a.documentVersion ?? 0;
          const bv = b.documentVersion ?? 0;
          if (av !== bv) return bv - av;
          return b.id - a.id;
        });
  const lineageStats = {
    total: lineageDocs.length,
    approved: lineageDocs.filter(d => d.status === "approved").length,
    pending: lineageDocs.filter(d => d.status === "pending").length,
    totalChunks: lineageDocs.reduce((sum, d) => sum + (d.chunkCount ?? 0), 0),
  };
  const lineageOriginId = !lineageDocs.length
    ? null
    : [...lineageDocs]
        .sort((a, b) => {
          const av = a.documentVersion ?? 0;
          const bv = b.documentVersion ?? 0;
          if (av !== bv) return av - bv;
          return a.id - b.id;
        })[0]?.id ?? null;
  const q = lineageQuery.trim().toLowerCase();
  const filteredLineageDocs = !q
    ? lineageDocs
    : lineageDocs.filter(item => {
        const haystack = [
          item.name,
          String(item.id),
          String(item.documentVersion ?? 0),
          item.status,
          item.lastIngestionRunId ?? "",
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(q);
      });

  if (isLoading) return <div className="p-8 flex justify-center"><Loader2 className="animate-spin h-8 w-8 text-primary" /></div>;
  if (!doc) return <div className="p-8">Document not found</div>;

  const debugSource = (doc as unknown as Record<string, unknown>).dedupDebug;
  const dedupDebug = parseDedupDebug(debugSource);
  const dedupMethodLabel = dedupDebug?.dedupMethod ?? parseIngestionDedupMethod((doc as unknown as Record<string, unknown>).ingestionReport);

  const handleApprove = async () => {
    try {
      await approve.mutateAsync({ id });
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document approved and indexed" });
    } catch(err) {
      toast({ title: "Failed to approve", variant: "destructive" });
    }
  };

  const handleReject = async () => {
    try {
      await reject.mutateAsync({ id, data: { reason: rejectReason } });
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document rejected" });
      setRejectOpen(false);
    } catch(err) {
      toast({ title: "Failed to reject", variant: "destructive" });
    }
  };

  const handleDelete = async () => {
    try {
      await remove.mutateAsync({ id });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document deleted" });
      setLocation("/admin/documents");
    } catch(err) {
      toast({ title: "Failed to delete", variant: "destructive" });
    }
  };

  const handleExcludeDuplicateChunk = async (sourceChunkPosition: number | undefined) => {
    if (typeof sourceChunkPosition !== "number") {
      toast({ title: "Missing source chunk position", variant: "destructive" });
      return;
    }

    try {
      setActiveSourcePosition(sourceChunkPosition);
      await excludeDuplicate.mutateAsync({ id, sourcePosition: sourceChunkPosition });
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: doc.status === "pending" ? "Duplicate chunk excluded" : "Duplicate chunk removed" });
    } catch {
      toast({ title: "Failed to update duplicate chunk", variant: "destructive" });
    } finally {
      setActiveSourcePosition(null);
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const isPending = doc.status === "pending";

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-6">
      <Button variant="ghost" onClick={() => setLocation("/admin/documents")} className="-ml-4 mb-2">
        <ArrowLeft className="mr-2 h-4 w-4" />
        Back to Knowledge Base
      </Button>

      <div className="flex justify-between items-start">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-3xl font-bold">{doc.name}</h1>
            <Badge variant={doc.status === 'approved' ? 'default' : doc.status === 'rejected' ? 'destructive' : 'secondary'} className="capitalize">
              {doc.status}
            </Badge>
          </div>
          <div className="flex gap-2 mb-4">
            {doc.tags.map(tag => <Badge key={tag} variant="outline">{tag}</Badge>)}
          </div>
          <p className="text-sm text-muted-foreground">
            Uploaded {format(new Date(doc.createdAt), 'PPP')} • Source: {doc.sourceType.toUpperCase()}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Version {doc.documentVersion ?? 0}
            {doc.rootDocumentId ? ` • Root #${doc.rootDocumentId}` : ""}
            {doc.lastIngestionRunId ? ` • Run ${doc.lastIngestionRunId}` : ""}
          </p>
        </div>

        <div className="flex gap-2">
          {isPending && (
            <>
              <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
                <DialogTrigger asChild>
                  <Button variant="outline" className="border-destructive text-destructive hover:bg-destructive/10"><X className="mr-2 h-4 w-4" /> Reject</Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Reject Document</DialogTitle>
                    <DialogDescription>
                      Provide a reason for rejecting this document. It will not be indexed.
                    </DialogDescription>
                  </DialogHeader>
                  <Textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)} placeholder="Reason (optional)" />
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setRejectOpen(false)}>Cancel</Button>
                    <Button variant="destructive" onClick={handleReject} disabled={reject.isPending}>Reject Document</Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
              <Button onClick={handleApprove} disabled={approve.isPending} className="bg-green-600 hover:bg-green-700 text-white">
                <Check className="mr-2 h-4 w-4" /> Approve & Index
              </Button>
            </>
          )}
          {!isPending && (
            <Button variant="outline" className="border-destructive text-destructive hover:bg-destructive/10" onClick={handleDelete} disabled={remove.isPending}>
              <Trash2 className="mr-2 h-4 w-4" /> Delete
            </Button>
          )}
        </div>
      </div>

      {doc.rejectionReason && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 mt-0.5" />
          <div>
            <h4 className="font-semibold">Rejection Reason</h4>
            <p className="text-sm mt-1">{doc.rejectionReason}</p>
          </div>
        </div>
      )}

      {/* Ingestion Report */}
      <Card className="border-primary/20 shadow-sm bg-primary/5">
        <CardHeader className="pb-3">
          <CardTitle className="text-lg flex items-center gap-2 text-primary">
            <CheckCircle2 className="h-5 w-5" /> Ingestion Report
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Size Reduction</p>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold">{formatBytes(doc.finalSize)}</span>
                <span className="text-sm text-muted-foreground line-through">{formatBytes(doc.originalSize)}</span>
              </div>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Knowledge Chunks</p>
              <span className="text-2xl font-bold font-mono">{doc.chunkCount}</span>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">PII Removed</p>
              <span className="text-2xl font-bold text-purple-600">{doc.piiCount}</span>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Duplicates Skipped</p>
              <span className="text-2xl font-bold text-orange-600">{doc.duplicateCount}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden border-border/60 bg-gradient-to-b from-card via-card to-muted/25 shadow-md shadow-black/[0.03] dark:shadow-black/20">
        <CardHeader className="relative space-y-3 border-b border-border/50 bg-muted/20 pb-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-2">
              <CardTitle className="flex items-center gap-3 text-xl font-semibold tracking-tight">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/15">
                  <GitBranch className="h-5 w-5 text-primary" aria-hidden />
                </span>
                Lineage Trace
              </CardTitle>
              <CardDescription className="max-w-xl text-[13px] leading-relaxed sm:pl-[52px] sm:-mt-1">
                All document versions tied to root{" "}
                <span className="inline-flex items-center rounded-md border bg-background/80 px-1.5 py-0.5 font-mono text-xs font-medium tabular-nums text-foreground shadow-sm">
                  #{rootId}
                </span>
                . Open a sibling to inspect its ingestion and lineage.
              </CardDescription>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 pt-2 sm:grid-cols-4 sm:gap-3 sm:pt-4">
            <div className="flex gap-3 rounded-xl border border-border/60 bg-background/60 p-4 shadow-sm backdrop-blur-sm dark:bg-background/40">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                <Files className="h-4 w-4 text-muted-foreground" aria-hidden />
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Family docs</p>
                <p className="text-2xl font-semibold tabular-nums tracking-tight">{lineageStats.total}</p>
              </div>
            </div>
            <div className="flex gap-3 rounded-xl border border-emerald-500/15 bg-emerald-500/[0.06] p-4 shadow-sm dark:bg-emerald-500/[0.08]">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15">
                <CircleCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" aria-hidden />
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Approved</p>
                <p className="text-2xl font-semibold tabular-nums tracking-tight text-emerald-700 dark:text-emerald-400">{lineageStats.approved}</p>
              </div>
            </div>
            <div className="flex gap-3 rounded-xl border border-amber-500/15 bg-amber-500/[0.06] p-4 shadow-sm dark:bg-amber-500/[0.08]">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-500/15">
                <Hourglass className="h-4 w-4 text-amber-600 dark:text-amber-400" aria-hidden />
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Pending</p>
                <p className="text-2xl font-semibold tabular-nums tracking-tight text-amber-800 dark:text-amber-400">{lineageStats.pending}</p>
              </div>
            </div>
            <div className="col-span-2 flex gap-3 rounded-xl border border-border/60 bg-background/60 p-4 shadow-sm backdrop-blur-sm dark:bg-background/40 sm:col-span-1">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                <Database className="h-4 w-4 text-muted-foreground" aria-hidden />
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Indexed chunks</p>
                <p className="font-mono text-2xl font-semibold tabular-nums tracking-tight">{lineageStats.totalChunks}</p>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative max-w-md flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <Input
                value={lineageQuery}
                onChange={e => setLineageQuery(e.target.value)}
                placeholder="Filter by name, id, version, status, run id…"
                className="h-10 border-border/70 bg-background/80 pl-9 shadow-sm backdrop-blur-sm transition-shadow placeholder:text-muted-foreground/70 focus-visible:ring-primary/25"
              />
            </div>
            <p className="text-xs tabular-nums text-muted-foreground sm:text-right">
              Showing{" "}
              <span className="font-medium text-foreground">{filteredLineageDocs.length}</span>
              {" "}of{" "}
              <span className="font-medium text-foreground">{lineageDocs.length}</span>
            </p>
          </div>

          {filteredLineageDocs.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border/80 bg-muted/20 px-4 py-10 text-center">
              <p className="text-sm font-medium text-foreground">No documents match</p>
              <p className="mt-1 text-xs text-muted-foreground">Adjust the filter or clear the search to see the full lineage.</p>
            </div>
          ) : (
            <ul className="space-y-2.5" role="list">
              {filteredLineageDocs.map(item => {
                const current = item.id === doc.id;
                const isRoot = item.id === rootId;
                const isOrigin = lineageOriginId !== null && item.id === lineageOriginId;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => !current && setLocation(`/admin/documents/${item.id}`)}
                      disabled={current}
                      className={cn(
                        "group relative flex w-full items-center gap-4 overflow-hidden rounded-xl border px-4 py-3.5 text-left transition-all duration-200",
                        current
                          ? "cursor-default border-primary/40 bg-gradient-to-r from-primary/[0.08] via-primary/[0.04] to-transparent ring-1 ring-primary/15"
                          : "border-border/60 bg-card/80 hover:border-border hover:bg-muted/35 hover:shadow-md active:scale-[0.997]"
                      )}
                    >
                      <span
                        className={cn(
                          "absolute left-0 top-2 bottom-2 w-[3px] rounded-full transition-colors",
                          current ? "bg-primary" : "bg-border group-hover:bg-primary/60"
                        )}
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1 pl-2">
                        <p className="truncate text-sm font-medium leading-snug">{item.name}</p>
                        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                          <span className="inline-flex items-center gap-1 font-mono tabular-nums">
                            <span className="text-muted-foreground/80">Doc</span> #{item.id}
                          </span>
                          <span className="hidden text-border sm:inline">·</span>
                          <span>v{item.documentVersion ?? 0}</span>
                          {item.lastIngestionRunId && (
                            <>
                              <span className="text-border">·</span>
                              <span className="truncate font-mono text-[11px]" title={item.lastIngestionRunId}>{item.lastIngestionRunId}</span>
                            </>
                          )}
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5 sm:gap-2">
                        {current && (
                          <Badge variant="secondary" className="font-medium shadow-sm">
                            Current
                          </Badge>
                        )}
                        {isRoot && (
                          <Badge variant="outline" className="border-primary/35 bg-background/90 font-medium shadow-sm">
                            Root
                          </Badge>
                        )}
                        {isOrigin && (
                          <Badge variant="outline" className="border-dashed font-medium opacity-90">
                            Origin
                          </Badge>
                        )}
                        <Badge
                          variant={item.status === "approved" ? "default" : item.status === "rejected" ? "destructive" : "secondary"}
                          className="capitalize shadow-sm"
                        >
                          {item.status}
                        </Badge>
                        {!current && (
                          <ChevronRight className="h-4 w-4 text-muted-foreground opacity-60 transition-transform group-hover:translate-x-0.5 group-hover:opacity-100 hidden sm:block" aria-hidden />
                        )}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      <Tabs defaultValue="pii" className="w-full space-y-5">
        <div className="rounded-2xl border border-border/60 bg-muted/25 p-1.5 shadow-sm backdrop-blur-sm sm:bg-muted/30">
          <TabsList className="mb-0 grid h-auto w-full grid-cols-2 gap-1 rounded-xl border border-border/40 bg-background/70 p-1 shadow-inner sm:flex sm:flex-1">
            <TabsTrigger
              value="pii"
              className="group gap-2 rounded-lg py-3 text-sm shadow-none transition-all data-[state=active]:bg-gradient-to-br data-[state=active]:from-violet-500/15 data-[state=active]:via-violet-500/5 data-[state=active]:to-transparent data-[state=active]:text-foreground data-[state=active]:shadow-sm data-[state=active]:ring-1 data-[state=active]:ring-violet-500/25 dark:data-[state=active]:from-violet-500/20"
            >
              <ScanSearch className="h-4 w-4 shrink-0 text-muted-foreground transition-colors group-data-[state=active]:text-violet-600 dark:group-data-[state=active]:text-violet-400" />
              <span className="min-w-0 truncate text-left sm:text-center">PII Findings</span>
              <span className="ml-auto inline-flex min-w-[1.75rem] items-center justify-center rounded-full bg-muted/80 px-2 py-0.5 font-mono text-[11px] font-semibold tabular-nums text-muted-foreground ring-1 ring-border/60 transition-colors group-data-[state=active]:bg-violet-500/15 group-data-[state=active]:text-violet-700 group-data-[state=active]:ring-violet-500/25 dark:group-data-[state=active]:text-violet-300">
                {doc.piiFindings.length}
              </span>
            </TabsTrigger>
            <TabsTrigger
              value="duplicates"
              className="group gap-2 rounded-lg py-3 text-sm shadow-none transition-all data-[state=active]:bg-gradient-to-br data-[state=active]:from-amber-500/15 data-[state=active]:via-amber-500/5 data-[state=active]:to-transparent data-[state=active]:text-foreground data-[state=active]:shadow-sm data-[state=active]:ring-1 data-[state=active]:ring-amber-500/25 dark:data-[state=active]:from-amber-500/18"
            >
              <Layers2 className="h-4 w-4 shrink-0 text-muted-foreground transition-colors group-data-[state=active]:text-amber-600 dark:group-data-[state=active]:text-amber-400" />
              <span className="min-w-0 truncate text-left sm:text-center">Potential duplicates</span>
              <span className="ml-auto inline-flex min-w-[1.75rem] items-center justify-center rounded-full bg-muted/80 px-2 py-0.5 font-mono text-[11px] font-semibold tabular-nums text-muted-foreground ring-1 ring-border/60 transition-colors group-data-[state=active]:bg-amber-500/15 group-data-[state=active]:text-amber-800 group-data-[state=active]:ring-amber-500/25 dark:group-data-[state=active]:text-amber-200">
                {doc.duplicateFindings.length}
              </span>
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="pii" className="mt-0 focus-visible:outline-none focus-visible:ring-0">
          <Card className="overflow-hidden border-border/60 shadow-md ring-1 ring-border/40">
            <CardHeader className="space-y-1 border-b border-border/50 bg-gradient-to-r from-violet-500/[0.07] via-transparent to-transparent pb-5 dark:from-violet-500/10">
              <CardTitle className="flex flex-wrap items-center gap-2 text-lg font-semibold tracking-tight">
                <span className="flex items-center gap-2 text-violet-700 dark:text-violet-400">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-500/10 ring-1 ring-violet-500/20 dark:bg-violet-500/15 dark:ring-violet-500/30">
                    <ScanSearch className="h-[18px] w-[18px]" aria-hidden />
                  </span>
                  Detected identifiers
                </span>
              </CardTitle>
              <CardDescription className="text-sm leading-relaxed">
                Values flagged by NER / detectors are masked before ingestion. Review types and confidence before approval.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              {doc.piiFindings.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
                  <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted ring-1 ring-border/60">
                    <CheckCircle2 className="h-6 w-6 text-emerald-600 dark:text-emerald-400" aria-hidden />
                  </span>
                  <p className="text-sm font-medium text-foreground">No sensitive patterns detected</p>
                  <p className="max-w-sm text-sm text-muted-foreground">This pass did not surface person names, contact data, or other configured PII in extractable spans.</p>
                </div>
              ) : (
                <ul className="divide-y divide-border/60" role="list">
                  {doc.piiFindings.map((finding, i) => {
                    const confidencePct =
                      typeof finding.confidence === "number" ? Math.round(finding.confidence * 100) : null;
                    return (
                      <li key={i} className="group relative">
                        <div className="absolute left-0 top-3 bottom-3 w-[3px] rounded-full bg-violet-500/35 transition-colors group-hover:bg-violet-500/60 dark:bg-violet-400/35 dark:group-hover:bg-violet-400/55" aria-hidden />
                        <div className="px-6 py-4 pl-8 transition-colors hover:bg-muted/35">
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                              <Badge
                                variant="outline"
                                className="rounded-lg border-violet-200/80 bg-violet-500/[0.08] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-violet-800 dark:border-violet-500/35 dark:bg-violet-500/15 dark:text-violet-300"
                              >
                                {finding.type.replace(/_/g, " ")}
                              </Badge>
                              {finding.detector && (
                                <Badge variant="secondary" className="rounded-lg px-2 py-0.5 text-[11px] font-normal text-muted-foreground">
                                  {finding.detector}
                                </Badge>
                              )}
                              {confidencePct !== null && (
                                <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
                                  <span className="font-mono tabular-nums font-medium text-foreground/80">{confidencePct}%</span>
                                  <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-muted ring-1 ring-border/40">
                                    <span
                                      className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-violet-500/80 to-violet-600 dark:from-violet-400 dark:to-violet-500"
                                      style={{ width: `${Math.min(100, confidencePct)}%` }}
                                    />
                                  </span>
                                  <span className="hidden sm:inline">confidence</span>
                                </span>
                              )}
                            </div>
                            <div className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
                              <span className="hidden sm:inline">Mask</span>
                              <ArrowRight className="hidden h-3.5 w-3.5 opacity-60 sm:block" aria-hidden />
                              <code className="rounded-lg bg-muted/80 px-2 py-1 font-mono text-[11px] font-medium ring-1 ring-border/60">
                                {finding.replacement}
                              </code>
                            </div>
                          </div>
                          <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-border/50 bg-muted/40 p-3 font-mono text-xs leading-relaxed text-foreground shadow-inner ring-1 ring-black/[0.02] dark:ring-white/[0.04]">
                            {finding.value}
                          </pre>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="duplicates" className="mt-0 focus-visible:outline-none focus-visible:ring-0">
          <Card className="overflow-hidden border-border/60 shadow-md ring-1 ring-border/40">
            <CardHeader className="space-y-3 border-b border-border/50 bg-gradient-to-r from-amber-500/[0.08] via-transparent to-transparent pb-5 dark:from-amber-500/10">
              <CardTitle className="flex flex-wrap items-center gap-2 text-lg font-semibold tracking-tight">
                <span className="flex items-center gap-2 text-amber-900 dark:text-amber-400">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/10 ring-1 ring-amber-500/25 dark:bg-amber-500/15">
                    <Layers2 className="h-[18px] w-[18px]" aria-hidden />
                  </span>
                  Overlapping content
                </span>
              </CardTitle>
              <CardDescription>
                {doc.status === "pending"
                  ? "Potential duplicates are auto-removed on approval; you can manually exclude them now."
                  : "Manually remove duplicate chunks when needed."}
              </CardDescription>
              <div className="flex flex-wrap gap-2 pt-1">
                {dedupMethodLabel && (
                  <Badge variant="outline" className="rounded-lg text-[11px] font-medium">
                    Method: {dedupMethodLabel}
                  </Badge>
                )}
                {typeof dedupDebug?.thresholds.jaccard === "number" && (
                  <Badge variant="outline" className="rounded-lg text-[11px] font-medium">
                    Jaccard ≥ {(dedupDebug.thresholds.jaccard * 100).toFixed(0)}%
                  </Badge>
                )}
                {typeof dedupDebug?.thresholds.cosine === "number" && (
                  <Badge variant="outline" className="rounded-lg text-[11px] font-medium">
                    Cosine ≥ {(dedupDebug.thresholds.cosine * 100).toFixed(0)}%
                  </Badge>
                )}
                {dedupDebug && (
                  <Badge variant="outline" className="rounded-lg text-[11px] font-medium">
                    {dedupDebug.vectorSearchEnabled ? "Vector search on" : "Vector search off"}
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {doc.duplicateFindings.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
                  <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted ring-1 ring-border/60">
                    <Layers2 className="h-6 w-6 text-muted-foreground" aria-hidden />
                  </span>
                  <p className="text-sm font-medium text-foreground">
                    {doc.status === "pending" ? "No overlap above preview threshold" : "No overlapping chunks"}
                  </p>
                  <p className="max-w-sm text-sm text-muted-foreground">
                    {doc.status === "pending"
                      ? "Nothing matched strongly enough during preview deduplication. Approving may still normalize near-duplicates depending on backend rules."
                      : "Dedup scans did not flag similar or identical passages for manual removal."}
                  </p>
                </div>
              ) : (
                <ul className="divide-y divide-border/60" role="list">
                  {doc.duplicateFindings.map((finding, i) => {
                    const findingExtra = finding as unknown as Record<string, unknown>;
                    const matchedChunkPosition = typeof findingExtra.matchedChunkPosition === "number"
                      ? findingExtra.matchedChunkPosition
                      : null;
                    const matchedSnippet = typeof findingExtra.matchedSnippet === "string"
                      ? findingExtra.matchedSnippet
                      : null;

                    const dupReason = finding.method === "hash"
                      ? "Exact duplicate — these chunks share identical content (same hash)."
                      : finding.method === "embedding"
                        ? `Semantically near-identical — vector similarity is ${Math.round(finding.similarity * 100)}%, meaning the chunks convey the same meaning even if worded differently.`
                        : finding.method === "jaccard"
                          ? `High term overlap — ${Math.round(finding.similarity * 100)}% of words are shared between these chunks.`
                          : `Content similarity of ${Math.round(finding.similarity * 100)}% exceeds the deduplication threshold.`;

                    const matchedLabel = typeof finding.matchedDocumentId === "number"
                      ? `Matched chunk in "${finding.matchedDocumentName ?? `Document #${finding.matchedDocumentId}`}"${typeof matchedChunkPosition === "number" ? ` · position #${matchedChunkPosition}` : ""}`
                      : `Same document · position #${matchedChunkPosition ?? "?"}`;
                    const simPct = Math.round(finding.similarity * 100);

                    return (
                    <li key={i} className="group relative">
                      <div
                        className="absolute left-0 top-3 bottom-3 w-[3px] rounded-full bg-amber-500/40 transition-colors group-hover:bg-amber-500/70 dark:bg-amber-400/35 dark:group-hover:bg-amber-400/60"
                        aria-hidden
                      />
                      <div className="space-y-3 px-6 py-4 pl-8 text-sm transition-colors hover:bg-muted/35">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
                          <span className="inline-flex items-center gap-2">
                            <span className="relative h-1.5 w-12 overflow-hidden rounded-full bg-muted ring-1 ring-border/40 sm:w-16">
                              <span
                                className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-amber-500 to-orange-600 dark:from-amber-400 dark:to-orange-500"
                                style={{ width: `${Math.min(100, simPct)}%` }}
                              />
                            </span>
                            <span className="rounded-lg bg-amber-500/12 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-amber-900 ring-1 ring-amber-500/25 dark:bg-amber-500/18 dark:text-amber-300">
                              {simPct}% match
                            </span>
                          </span>
                          {finding.method && (
                            <Badge variant="secondary" className="w-fit rounded-lg px-2 py-0.5 text-[11px] font-normal">
                              {finding.method}
                            </Badge>
                          )}
                          <span className="text-xs leading-snug text-muted-foreground">
                            {typeof finding.matchedDocumentId === "number"
                              ? `vs Document #${finding.matchedDocumentId}${finding.matchedDocumentName ? ` — ${finding.matchedDocumentName}` : ""}`
                              : "within this document"}
                          </span>
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-8 shrink-0 rounded-lg px-3 text-xs font-medium shadow-sm"
                          disabled={
                            excludeDuplicate.isPending ||
                            activeSourcePosition === finding.sourceChunkPosition ||
                            typeof finding.sourceChunkPosition !== "number"
                          }
                          onClick={() => handleExcludeDuplicateChunk(finding.sourceChunkPosition)}
                        >
                          {excludeDuplicate.isPending && activeSourcePosition === finding.sourceChunkPosition
                            ? "Removing..."
                            : doc.status === "pending"
                              ? "Exclude Chunk"
                              : "Delete Chunk"}
                        </Button>
                      </div>
                      <p className="rounded-lg border border-amber-200/60 bg-gradient-to-r from-amber-500/[0.06] to-transparent px-3 py-2 text-xs leading-relaxed text-muted-foreground ring-1 ring-amber-500/10 dark:border-amber-500/25 dark:from-amber-500/08">
                        {dupReason}
                      </p>
                      <div className="grid gap-3 md:grid-cols-2">
                        <div>
                          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                            Incoming chunk{typeof finding.sourceChunkPosition === "number" ? ` · position #${finding.sourceChunkPosition}` : ""}
                          </p>
                          <p className="min-h-[3rem] whitespace-pre-wrap break-words rounded-xl border border-amber-200/50 bg-amber-500/[0.06] p-3 text-xs italic leading-relaxed text-muted-foreground shadow-inner ring-1 ring-amber-500/10 dark:border-amber-900/35 dark:bg-amber-950/25">
                            "{finding.snippet}"
                          </p>
                        </div>
                        <div>
                          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{matchedLabel}</p>
                          {matchedSnippet ? (
                            <p className="min-h-[3rem] whitespace-pre-wrap break-words rounded-xl border border-border/60 bg-muted/50 p-3 text-xs italic leading-relaxed text-muted-foreground shadow-inner ring-1 ring-border/40">
                              "{matchedSnippet}"
                            </p>
                          ) : (
                            <p className="rounded-xl border border-dashed border-border/70 bg-muted/30 p-3 text-xs italic leading-relaxed text-muted-foreground">
                              {typeof matchedChunkPosition === "number"
                                ? `Matched chunk at position #${matchedChunkPosition}${typeof finding.matchedDocumentId === "number" ? ` in Document #${finding.matchedDocumentId}` : " within this document"} — re-upload or re-approve to load preview.`
                                : "Matched chunk content unavailable — re-upload or re-approve to load preview."}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                    </li>
                  );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}