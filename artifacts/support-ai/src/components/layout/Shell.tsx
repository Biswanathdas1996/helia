import { useUser, useClerk } from "@clerk/react";
import { useGetMe } from "@workspace/api-client-react";
import { Link, useLocation } from "wouter";
import { LayoutDashboard, MessageSquare, Ticket as TicketIcon, LogOut, Loader2 } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";

export function Shell({ children }: { children: React.ReactNode }) {
  const { user } = useUser();
  const { signOut } = useClerk();
  const [location] = useLocation();
  const { data: me, isLoading } = useGetMe();

  const navItems = [
    { href: "/app", label: "Chat", icon: MessageSquare },
    { href: "/app/tickets", label: "Tickets", icon: TicketIcon },
  ];

  if (me?.role === "admin") {
    navItems.push({ href: "/admin", label: "Admin", icon: LayoutDashboard });
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar */}
      <div className="w-64 flex-shrink-0 flex flex-col bg-sidebar border-r border-sidebar-border text-sidebar-foreground">
        <div className="h-14 flex items-center px-4 font-bold text-xl tracking-tight border-b border-sidebar-border">
          <span className="text-primary mr-1">■</span> Helia
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
        </div>

        <div className="p-4 border-t border-sidebar-border">
          {isLoading ? (
            <div className="flex items-center justify-center p-2"><Loader2 className="h-4 w-4 animate-spin text-sidebar-foreground/50" /></div>
          ) : (
            <div className="flex items-center group">
              <Avatar className="h-9 w-9">
                <AvatarImage src={user?.imageUrl} alt={user?.fullName || "User"} />
                <AvatarFallback className="bg-sidebar-accent text-sidebar-accent-foreground">{user?.firstName?.charAt(0) || "U"}</AvatarFallback>
              </Avatar>
              <div className="ml-3 flex-1 overflow-hidden">
                <p className="text-sm font-medium truncate">{user?.fullName}</p>
                <p className="text-xs text-sidebar-foreground/60 truncate">{user?.primaryEmailAddress?.emailAddress}</p>
              </div>
              <Button variant="ghost" size="icon" className="text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent opacity-0 group-hover:opacity-100 transition-opacity" onClick={() => signOut()}>
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
