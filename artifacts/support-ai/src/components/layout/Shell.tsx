import { useGetMe } from "@workspace/api-client-react";
import { Link, useLocation } from "wouter";
import { LayoutDashboard, MessageSquare, Ticket as TicketIcon, LogOut, Loader2, FileText, UploadCloud } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { PwcWordmark } from "@/components/layout/PwcWordmark";
import { useAuth } from "@/hooks/use-auth";

export function Shell({ children }: { children: React.ReactNode }) {
  const { user: authUser, logout } = useAuth();
  const [location] = useLocation();
  const { data: me, isLoading } = useGetMe();

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

  return (
    <div className="flex h-screen overflow-hidden bg-background">
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
