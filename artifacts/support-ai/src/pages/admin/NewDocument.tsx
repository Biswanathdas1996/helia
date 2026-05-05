import { useCreateDocument, getListDocumentsQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "wouter";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ArrowLeft, Loader2, UploadCloud, FileText, X, Scan, AlertTriangle } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { useState, useRef, useCallback } from "react";
import { Badge } from "@/components/ui/badge";

const ACCEPTED = ".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.png,.jpg,.jpeg,.webp,.gif,.tiff,.tif,.txt,.md,.csv,.json";

const FILE_TYPE_LABELS: Record<string, string> = {
  pdf: "PDF", doc: "Word", docx: "Word", ppt: "PowerPoint", pptx: "PowerPoint",
  xls: "Excel", xlsx: "Excel", png: "Image", jpg: "Image", jpeg: "Image",
  webp: "Image", gif: "Image", tiff: "Image", tif: "Image",
  txt: "Text", md: "Markdown", csv: "CSV", json: "JSON",
};

function inferSourceType(filename: string): "text" | "pdf" | "docx" | "txt" {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pdf") return "pdf";
  if (ext === "docx" || ext === "doc") return "docx";
  if (ext === "txt") return "txt";
  return "text";
}

function getFileLabel(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  return FILE_TYPE_LABELS[ext] ?? "File";
}

const formSchema = z.object({
  name: z.string().min(3, "Name must be at least 3 characters"),
  content: z.string().min(50, "Content must be at least 50 characters"),
});

type DuplicateConflict = {
  message: string;
  duplicateDocumentId: number;
  duplicateDocumentName?: string;
  duplicateDocumentStatus?: string;
};

function extractDuplicateConflict(err: unknown): DuplicateConflict | null {
  if (!err || typeof err !== "object") return null;
  const e = err as { status?: unknown; data?: unknown; message?: unknown };
  if (typeof e.status !== "number" || e.status !== 409) return null;
  if (!e.data || typeof e.data !== "object") return null;

  const data = e.data as { error?: unknown; detail?: unknown };
  const payload =
    data.error && typeof data.error === "object"
      ? (data.error as Record<string, unknown>)
      : data.detail && typeof data.detail === "object"
        ? (data.detail as Record<string, unknown>)
        : null;
  if (!payload) return null;
  if (payload.code !== "exact_duplicate") return null;
  if (typeof payload.duplicateDocumentId !== "number") return null;

  const name =
    typeof payload.duplicateDocumentName === "string" ? payload.duplicateDocumentName : undefined;
  const status =
    typeof payload.duplicateDocumentStatus === "string" ? payload.duplicateDocumentStatus : undefined;
  const fallback = `Exact duplicate of document #${payload.duplicateDocumentId}.`;
  const message = typeof payload.message === "string" ? payload.message : fallback;

  return {
    message,
    duplicateDocumentId: payload.duplicateDocumentId,
    duplicateDocumentName: name,
    duplicateDocumentStatus: status,
  };
}

export default function NewDocument() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const createDocument = useCreateDocument();
  const dropRef = useRef<HTMLDivElement>(null);

  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [droppedFile, setDroppedFile] = useState<{ name: string; size: number } | null>(null);
  const [sourceType, setSourceType] = useState<"text" | "pdf" | "docx" | "txt">("text");
  const [extracting, setExtracting] = useState(false);
  const [pasteMode, setPasteMode] = useState(false);
  const [duplicateConflict, setDuplicateConflict] = useState<DuplicateConflict | null>(null);

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "", content: "" },
  });

  const extractFile = useCallback(async (file: File) => {
    setExtracting(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("/api/documents/extract", {
        method: "POST",
        body: formData,
        credentials: "include",
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({ error: "Extraction failed" })) as { error?: unknown; detail?: unknown };
        const errVal = payload.error ?? payload.detail;
        const msg =
          typeof errVal === "string"
            ? errVal
            : errVal && typeof errVal === "object" && "message" in errVal && typeof (errVal as { message?: unknown }).message === "string"
              ? String((errVal as { message: string }).message)
              : "Extraction failed";
        throw new Error(msg);
      }
      const { text } = await res.json() as { text: string };
      if (!text || text.trim().length < 10) throw new Error("No text could be extracted from this file.");
      const baseName = file.name.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ");
      form.setValue("content", text, { shouldValidate: true });
      form.setValue("name", baseName, { shouldValidate: true });
      setSourceType(inferSourceType(file.name));
      setDroppedFile({ name: file.name, size: file.size });
      setDuplicateConflict(null);
    } catch (err) {
      toast({
        title: "Could not extract file",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setExtracting(false);
    }
  }, [form, toast]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) extractFile(file);
  }, [extractFile]);

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) extractFile(file);
    e.target.value = "";
  }, [extractFile]);

  const clearFile = () => {
    setDroppedFile(null);
    setPasteMode(false);
    setDuplicateConflict(null);
    form.setValue("content", "");
    form.setValue("name", "");
    setSourceType("text");
  };

  const addTag = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && tagInput.trim() && !tags.includes(tagInput.trim())) {
      e.preventDefault();
      setTags([...tags, tagInput.trim()]);
      setTagInput("");
    }
  };

  const removeTag = (tagToRemove: string) => {
    setTags(tags.filter(t => t !== tagToRemove));
  };

  const onSubmit = async (values: z.infer<typeof formSchema>) => {
    try {
      const doc = await createDocument.mutateAsync({
        data: { ...values, sourceType, tags },
      });
      setDuplicateConflict(null);
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document submitted for approval" });
      setLocation(`/admin/documents/${doc.id}`);
    } catch (err) {
      const conflict = extractDuplicateConflict(err);
      if (conflict) {
        setDuplicateConflict(conflict);
        toast({
          title: "Duplicate blocked",
          description: conflict.message,
          variant: "destructive",
        });
        return;
      }
      toast({
        title: "Failed to submit document",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    }
  };

  const hasContent = droppedFile || pasteMode;
  const content = form.watch("content");

  return (
    <div className="p-8 max-w-3xl mx-auto">
      <Button variant="ghost" onClick={() => setLocation("/admin/documents")} className="mb-6 -ml-4">
        <ArrowLeft className="mr-2 h-4 w-4" />
        Back
      </Button>

      <Card>
        <CardHeader>
          <CardTitle className="text-2xl flex items-center gap-2">
            <UploadCloud className="h-6 w-6 text-primary" /> Submit Document
          </CardTitle>
          <CardDescription>
            Drop any file — PDF, Word, PowerPoint, Excel, image, or plain text. AI extracts content now, then ingestion and indexing run only after approval.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">

              {/* Drop zone — shown when no file loaded */}
              {!hasContent && (
                <div
                  ref={dropRef}
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  className={`relative flex flex-col items-center justify-center gap-3 rounded-sm border-2 border-dashed transition-colors cursor-pointer py-14 px-8 text-center ${dragOver ? "border-primary bg-accent" : "border-border hover:border-primary/50 hover:bg-secondary/40"} ${extracting ? "pointer-events-none opacity-60" : ""}`}
                  onClick={() => !extracting && document.getElementById("file-input")?.click()}
                >
                  <input
                    id="file-input"
                    type="file"
                    accept={ACCEPTED}
                    className="hidden"
                    onChange={handleFileInput}
                  />

                  {extracting ? (
                    <>
                      <div className="h-12 w-12 rounded-sm bg-primary/10 flex items-center justify-center text-primary">
                        <Scan className="h-6 w-6 animate-pulse" />
                      </div>
                      <div>
                        <p className="font-semibold text-foreground text-sm">Extracting text with AI…</p>
                        <p className="text-xs text-muted-foreground mt-1">This may take a few seconds for large files</p>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className={`h-12 w-12 rounded-sm flex items-center justify-center ${dragOver ? "bg-primary text-primary-foreground" : "bg-secondary text-primary"}`}>
                        <UploadCloud className="h-6 w-6" />
                      </div>
                      <div>
                        <p className="font-semibold text-foreground text-sm">Drop your file here or click to browse</p>
                        <p className="text-xs text-muted-foreground mt-1">
                          PDF · Word · PowerPoint · Excel · Images · TXT · CSV · JSON · Markdown
                        </p>
                      </div>

                      {/* Format badges */}
                      <div className="flex flex-wrap justify-center gap-1.5 pt-1 max-w-sm">
                        {["PDF", "DOCX", "PPTX", "XLSX", "PNG/JPG", "TXT"].map(fmt => (
                          <span key={fmt} className="text-[10px] font-medium uppercase tracking-wide bg-secondary text-muted-foreground px-2 py-0.5 rounded-sm">
                            {fmt}
                          </span>
                        ))}
                      </div>

                      <div className="flex items-center gap-6 pt-2 w-full max-w-xs">
                        <div className="h-px flex-1 bg-border" />
                        <span className="text-xs text-muted-foreground uppercase tracking-wider">or</span>
                        <div className="h-px flex-1 bg-border" />
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setPasteMode(true);
                          setDroppedFile({ name: "Pasted text", size: 0 });
                        }}
                      >
                        Paste text instead
                      </Button>
                    </>
                  )}
                </div>
              )}

              {/* File chip — shown after file loaded */}
              {hasContent && (
                <div className="flex items-center gap-3 rounded-sm border border-primary/30 bg-accent px-4 py-3">
                  <div className="h-9 w-9 rounded-sm bg-primary/10 flex items-center justify-center text-primary flex-shrink-0">
                    <FileText className="h-5 w-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">
                      {droppedFile?.name}
                      {droppedFile?.name && droppedFile.name !== "Pasted text" && (
                        <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-primary bg-primary/10 px-1.5 py-0.5 rounded-sm">
                          {getFileLabel(droppedFile.name)}
                        </span>
                      )}
                    </p>
                    {droppedFile && droppedFile.size > 0 ? (
                      <p className="text-xs text-muted-foreground">
                        {(droppedFile.size / 1024).toFixed(1)} KB · {content.length.toLocaleString()} chars extracted
                      </p>
                    ) : (
                      <p className="text-xs text-muted-foreground">{content.trim().length.toLocaleString()} chars</p>
                    )}
                  </div>
                  <Button type="button" variant="ghost" size="icon" className="flex-shrink-0 h-7 w-7" onClick={clearFile}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              )}

              {/* Document name */}
              {hasContent && (
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Document Name</FormLabel>
                      <FormControl>
                        <Input placeholder="e.g. Q3 SLA Policy" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {duplicateConflict && (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertTitle>Exact duplicate blocked</AlertTitle>
                  <AlertDescription>
                    <p>{duplicateConflict.message}</p>
                    <div className="mt-3">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setLocation(`/admin/documents/${duplicateConflict.duplicateDocumentId}`)}
                      >
                        Open existing document
                      </Button>
                    </div>
                  </AlertDescription>
                </Alert>
              )}

              {/* Textarea — only for paste mode */}
              {pasteMode && (
                <FormField
                  control={form.control}
                  name="content"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Document Content</FormLabel>
                      <FormControl>
                        <Textarea
                          placeholder="Paste the full document text here..."
                          className="min-h-[240px] font-mono text-sm leading-relaxed"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {/* Tags */}
              {hasContent && (
                <div className="space-y-2">
                  <Label htmlFor="tags-input">
                    Tags{" "}
                    <span className="text-muted-foreground font-normal">(optional — press Enter to add)</span>
                  </Label>
                  <Input
                    id="tags-input"
                    placeholder="e.g. policy, billing, technical..."
                    value={tagInput}
                    onChange={e => setTagInput(e.target.value)}
                    onKeyDown={addTag}
                  />
                  {tags.length > 0 && (
                    <div className="flex flex-wrap gap-2 pt-1">
                      {tags.map(tag => (
                        <Badge key={tag} variant="secondary" className="px-2 py-1">
                          {tag}
                          <button type="button" onClick={() => removeTag(tag)} className="ml-2 text-muted-foreground hover:text-foreground">×</button>
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <div className="flex justify-end gap-4 pt-4 border-t border-border">
                <Button variant="outline" type="button" onClick={() => setLocation("/admin/documents")}>
                  Cancel
                </Button>
                <Button type="submit" disabled={createDocument.isPending || extracting || !hasContent}>
                  {createDocument.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Submit for Approval
                </Button>
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
}
