import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getListConversationsQueryKey, useGetMe } from "@workspace/api-client-react";
import { Link, useLocation } from "wouter";
import { LayoutDashboard, MessageSquare, Ticket as TicketIcon, LogOut, Loader2, FileText, UploadCloud, Trash2 } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { PwcWordmark } from "@/components/layout/PwcWordmark";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";

export function Shell({ children }: { children: React.ReactNode }) {
  const { user: authUser, logout } = useAuth();
  const [location, setLocation] = useLocation();
  const { data: me, isLoading } = useGetMe();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [clearMemoryOpen, setClearMemoryOpen] = useState(false);
  const [isClearingMemory, setIsClearingMemory] = useState(false);

  const navItems = [
    { href: "/app", label: "Chat", icon: MessageSquare },
    { href: "/app/tickets", label: "Tickets", icon: TicketIcon },
  ];

  const adminNavItems = me?.role === "admin" ? [
    { href: "/admin", label: "Dashboard", icon: LayoutDashboard },
    { href: "/admin/documents", label: "Documents", icon: FileText },
    { href: "/admin/documents/new", label: "Upload Document", icon: UploadCloud },
    { href: "/admin/tickets", label: "Admin Tickets", icon: TicketIcon },
  ] : [];

  const handleClearMemory = async () => {
    setIsClearingMemory(true);
    try {
      const res = await fetch("/api/me/memory", {
        method: "DELETE",
        credentials: "include",
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail ?? body?.error ?? "Failed to clear memory");
      }

      queryClient.removeQueries({
        predicate: (query) => {
          const [firstKey] = query.queryKey;
          return typeof firstKey === "string" && firstKey.startsWith("/api/chat/conversations");
        },
      });
      await queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
      setLocation("/app");
      setClearMemoryOpen(false);
      toast({ title: "Agent memory cleared" });
    } catch {
      toast({
        title: "Failed to clear memory",
        description: "Long-term or local memory could not be fully cleared.",
        variant: "destructive",
      });
    } finally {
      setIsClearingMemory(false);
    }
  };

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <AlertDialog
        open={clearMemoryOpen}
        onOpenChange={(open) => {
          if (!isClearingMemory) {
            setClearMemoryOpen(open);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear all saved memory?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes your saved conversations and the long-term memory stored for your account, including Mem0. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isClearingMemory}>Cancel</AlertDialogCancel>
            <Button type="button" variant="destructive" disabled={isClearingMemory} onClick={handleClearMemory}>
              {isClearingMemory ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
              Clear memory
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Sidebar */}
      <div className="w-64 flex-shrink-0 flex flex-col bg-sidebar border-r border-sidebar-border text-sidebar-foreground">
        <div className="relative shrink-0 border-b border-sidebar-border/45">
          <div className="h-0.5 w-full pwc-accent-bar shrink-0" aria-hidden />
          <div className="relative px-3.5 py-4">
            <div
              className="pointer-events-none absolute inset-0 bg-[radial-gradient(100%_120%_at_0%_-20%,hsl(var(--sidebar-accent)/0.18)_0%,transparent_58%)]"
              aria-hidden
            />
            <Link
              href="/app"
              className="relative flex rounded-xl bg-black px-2.5 py-2 outline-none ring-sidebar-ring transition-colors hover:bg-neutral-950 focus-visible:ring-2 focus-visible:ring-sidebar-ring focus-visible:ring-offset-2 focus-visible:ring-offset-black"
            >
              <PwcWordmark variant="dark" product="Helia" size="lg" layout="inline" />
            </Link>
          </div>
        </div>
        
        <div className="flex-1 overflow-y-auto py-4">
          <nav className="space-y-1 px-2">
            {navItems.map((item) => {
              const isActive = location === item.href || location.startsWith(`${item.href}/`);
              return (
                <Link key={item.href} href={item.href} className={`flex items-center px-3 py-2 text-sm font-medium rounded-md transition-colors ${isActive ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"}`}>
                  <item.icon className="mr-3 h-5 w-5 flex-shrink-0" aria-hidden="true" />
                  {item.label}
                </Link>
              );
            })}
          </nav>
          {adminNavItems.length > 0 && (
            <div className="mt-6 px-2">
              <p className="px-3 mb-1 text-xs font-semibold uppercase tracking-wider text-sidebar-foreground/40">Admin</p>
              <nav className="space-y-1">
                {adminNavItems.map((item) => {
                  const isActive = location === item.href || (item.href !== "/admin" && location.startsWith(`${item.href}/`));
                  return (
                    <Link key={item.href} href={item.href} className={`flex items-center px-3 py-2 text-sm font-medium rounded-md transition-colors ${isActive ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"}`}>
                      <item.icon className="mr-3 h-5 w-5 flex-shrink-0" aria-hidden="true" />
                      {item.label}
                    </Link>
                  );
                })}
              </nav>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-sidebar-border">
          {isLoading ? (
            <div className="flex items-center justify-center p-2"><Loader2 className="h-4 w-4 animate-spin text-sidebar-foreground/50" /></div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center group">
                <Avatar className="h-9 w-9">
                  <AvatarImage src={authUser?.imageUrl ?? undefined} alt={[authUser?.firstName, authUser?.lastName].filter(Boolean).join(" ") || "User"} />
                  <AvatarFallback className="bg-sidebar-accent text-sidebar-accent-foreground">{authUser?.firstName?.charAt(0) || "U"}</AvatarFallback>
                </Avatar>
                <div className="ml-3 flex-1 overflow-hidden">
                  <p className="text-sm font-medium truncate">{[authUser?.firstName, authUser?.lastName].filter(Boolean).join(" ")}</p>
                  <p className="text-xs text-sidebar-foreground/60 truncate">{authUser?.email}</p>
                </div>
                <Button variant="ghost" size="icon" className="text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent opacity-0 group-hover:opacity-100 transition-opacity" onClick={() => logout()}>
                  <LogOut className="h-4 w-4" />
                  <span className="sr-only">Sign out</span>
                </Button>
              </div>
              <Button
                type="button"
                variant="outline"
                className="w-full justify-start gap-2 border-sidebar-border/70 bg-sidebar-accent/20 text-sidebar-foreground/85 hover:bg-destructive/10 hover:text-sidebar-foreground"
                onClick={() => setClearMemoryOpen(true)}
                disabled={isClearingMemory}
              >
                <Trash2 className="h-4 w-4" />
                Clear saved memory
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 bg-background overflow-hidden">
        <main className="flex-1 relative z-0 overflow-y-auto focus:outline-none">
          {children}
        </main>
      </div>
    </div>
  );
}
