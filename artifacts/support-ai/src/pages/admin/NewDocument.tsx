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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ArrowLeft, Loader2, UploadCloud } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";

const formSchema = z.object({
  name: z.string().min(3, "Name is required"),
  sourceType: z.enum(["text", "pdf", "docx", "txt"]),
  content: z.string().min(50, "Content must be at least 50 characters"),
});

export default function NewDocument() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const createDocument = useCreateDocument();
  
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      sourceType: "text",
      content: "",
    },
  });

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
        data: { 
          ...values, 
          tags 
        } 
      });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document ingested successfully" });
      setLocation(`/admin/documents/${doc.id}`); // Go straight to the detail/report view
    } catch (err) {
      toast({ title: "Failed to ingest document", variant: "destructive" });
    }
  };

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
            Paste document content below. Our ingestion pipeline will automatically chunk the text, remove PII, and flag duplicate content.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
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
                
                <FormField
                  control={form.control}
                  name="sourceType"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Source Type</FormLabel>
                      <Select onValueChange={field.onChange} defaultValue={field.value}>
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue placeholder="Select type" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          <SelectItem value="text">Plain Text</SelectItem>
                          <SelectItem value="pdf">PDF (Text extracted)</SelectItem>
                          <SelectItem value="docx">Word (Text extracted)</SelectItem>
                          <SelectItem value="txt">TXT File</SelectItem>
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <FormItem>
                <FormLabel>Tags (Press Enter to add)</FormLabel>
                <FormControl>
                  <div className="space-y-2">
                    <Input 
                      placeholder="e.g. policy, billing, technical..." 
                      value={tagInput}
                      onChange={e => setTagInput(e.target.value)}
                      onKeyDown={addTag}
                    />
                    {tags.length > 0 && (
                      <div className="flex flex-wrap gap-2 pt-2">
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
                </FormControl>
              </FormItem>
              
              <FormField
                control={form.control}
                name="content"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Raw Content</FormLabel>
                    <FormControl>
                      <Textarea 
                        placeholder="Paste the full document text here..." 
                        className="min-h-[300px] font-mono text-sm leading-relaxed"
                        {...field} 
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="flex justify-end gap-4 pt-4 border-t border-border">
                <Button variant="outline" type="button" onClick={() => setLocation("/admin/documents")}>
                  Cancel
                </Button>
                <Button type="submit" disabled={createDocument.isPending}>
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