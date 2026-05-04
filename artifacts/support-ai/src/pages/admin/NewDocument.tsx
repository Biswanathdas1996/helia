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
import { ArrowLeft, Loader2, UploadCloud, FileText, X } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { useState, useRef, useCallback } from "react";
import { Badge } from "@/components/ui/badge";

const ACCEPTED_EXTENSIONS = [".txt", ".md", ".csv", ".json"];
const ACCEPTED_MIME = ["text/plain", "text/markdown", "text/csv", "application/json", "text/x-markdown"];

function inferSourceType(filename: string): "text" | "pdf" | "docx" | "txt" {
  const ext = filename.split(".").pop()?.toLowerCase();
  if (ext === "pdf") return "pdf";
  if (ext === "docx" || ext === "doc") return "docx";
  if (ext === "txt") return "txt";
  return "text";
}

const formSchema = z.object({
  name: z.string().min(3, "Name must be at least 3 characters"),
  content: z.string().min(50, "Content must be at least 50 characters"),
});

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

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "", content: "" },
  });

  const readFileAsText = useCallback((file: File) => {
    const isText = ACCEPTED_MIME.includes(file.type) ||
      ACCEPTED_EXTENSIONS.some(ext => file.name.toLowerCase().endsWith(ext));
    if (!isText) {
      toast({
        title: "Unsupported file type",
        description: "Please drop a .txt, .md, .csv, or .json file, or paste text directly below.",
        variant: "destructive",
      });
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      form.setValue("content", text, { shouldValidate: true });
      const baseName = file.name.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ");
      form.setValue("name", baseName, { shouldValidate: true });
      setSourceType(inferSourceType(file.name));
      setDroppedFile({ name: file.name, size: file.size });
    };
    reader.readAsText(file);
  }, [form, toast]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) readFileAsText(file);
  }, [readFileAsText]);

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) readFileAsText(file);
  }, [readFileAsText]);

  const clearFile = () => {
    setDroppedFile(null);
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
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document ingested successfully" });
      setLocation(`/admin/documents/${doc.id}`);
    } catch {
      toast({ title: "Failed to ingest document", variant: "destructive" });
    }
  };

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
            <UploadCloud className="h-6 w-6 text-primary" /> Ingest Document
          </CardTitle>
          <CardDescription>
            Drop a file or paste text. The pipeline will chunk, remove PII, and flag duplicates automatically.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">

              {/* Drop zone */}
              {!droppedFile && !content ? (
                <div
                  ref={dropRef}
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  className={`relative flex flex-col items-center justify-center gap-3 rounded-sm border-2 border-dashed transition-colors cursor-pointer py-14 px-8 text-center ${dragOver ? "border-primary bg-accent" : "border-border hover:border-primary/50 hover:bg-secondary/40"}`}
                  onClick={() => document.getElementById("file-input")?.click()}
                >
                  <input
                    id="file-input"
                    type="file"
                    accept=".txt,.md,.csv,.json"
                    className="hidden"
                    onChange={handleFileInput}
                  />
                  <div className={`h-12 w-12 rounded-sm flex items-center justify-center ${dragOver ? "bg-primary text-primary-foreground" : "bg-secondary text-primary"}`}>
                    <UploadCloud className="h-6 w-6" />
                  </div>
                  <div>
                    <p className="font-semibold text-foreground text-sm">Drop your file here</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      Supported: .txt, .md, .csv, .json — or click to browse
                    </p>
                  </div>
                  <div className="flex items-center gap-6 pt-2">
                    <div className="h-px flex-1 bg-border" />
                    <span className="text-xs text-muted-foreground uppercase tracking-wider">or paste text below</span>
                    <div className="h-px flex-1 bg-border" />
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-1"
                    onClick={(e) => {
                      e.stopPropagation();
                      form.setValue("content", " ");
                      setDroppedFile({ name: "Pasted text", size: 0 });
                    }}
                  >
                    Paste text instead
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-3 rounded-sm border border-primary/30 bg-accent px-4 py-3">
                  <div className="h-9 w-9 rounded-sm bg-primary/10 flex items-center justify-center text-primary flex-shrink-0">
                    <FileText className="h-5 w-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">{droppedFile?.name}</p>
                    {droppedFile?.size ? (
                      <p className="text-xs text-muted-foreground">{(droppedFile.size / 1024).toFixed(1)} KB · {content.length.toLocaleString()} chars</p>
                    ) : (
                      <p className="text-xs text-muted-foreground">{content.trim().length.toLocaleString()} chars</p>
                    )}
                  </div>
                  <Button type="button" variant="ghost" size="icon" className="flex-shrink-0 h-7 w-7" onClick={clearFile}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              )}

              {/* Document name — shown once file is loaded */}
              {(droppedFile || content) && (
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

              {/* Inline textarea for paste-text mode */}
              {droppedFile?.name === "Pasted text" && (
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

              {/* Tags — shown once file is loaded */}
              {(droppedFile || content) && (
                <div className="space-y-2">
                  <Label htmlFor="tags-input">Tags <span className="text-muted-foreground font-normal">(optional — press Enter to add)</span></Label>
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
                          <button type="button" onClick={() => removeTag(tag)} className="ml-2 text-muted-foreground hover:text-foreground">
                            ×
                          </button>
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
                <Button type="submit" disabled={createDocument.isPending || (!droppedFile && !content)}>
                  {createDocument.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Process & Ingest
                </Button>
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
}
