import { Switch, Route, Redirect, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import { Shell } from "@/components/layout/Shell";
import { AdminGuard } from "@/components/AdminGuard";
import { AuthProvider, useAuth } from "@/hooks/use-auth";

import LandingPage from "@/pages/Home";
import SignInPage from "@/pages/SignIn";
import SignUpPage from "@/pages/SignUp";
import Chat from "@/pages/app/Chat";
import Tickets from "@/pages/app/Tickets";
import TicketDetail from "@/pages/app/TicketDetail";
import NewTicket from "@/pages/app/NewTicket";

import AdminDashboard from "@/pages/admin/Dashboard";
import AdminDocuments from "@/pages/admin/Documents";
import AdminDocumentDetail from "@/pages/admin/DocumentDetail";
import AdminNewDocument from "@/pages/admin/NewDocument";
import AdminTickets from "@/pages/admin/Tickets";
import AdminZohoTickets from "@/pages/admin/ZohoTickets";
import AdminArchitecture from "@/pages/admin/Architecture";

const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
const queryClient = new QueryClient();

function HomeRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return <Redirect to={user.role === "admin" ? "/admin" : "/app"} />;
  return <LandingPage />;
}

function ProtectedApp() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Redirect to="/sign-in" />;
  return (
    <Shell>
      <Switch>
        <Route path="/app" component={Chat} />
        <Route path="/app/conversations/:id" component={Chat} />
        <Route path="/app/tickets" component={Tickets} />
        <Route path="/app/tickets/new" component={NewTicket} />
        <Route path="/app/tickets/:id" component={TicketDetail} />
      </Switch>
    </Shell>
  );
}

function ProtectedAdmin() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Redirect to="/sign-in" />;
  return (
    <AdminGuard>
      <Switch>
        <Route path="/admin" component={AdminDashboard} />
        <Route path="/admin/architecture" component={AdminArchitecture} />
        <Route path="/admin/documents" component={AdminDocuments} />
        <Route path="/admin/documents/new" component={AdminNewDocument} />
        <Route path="/admin/documents/:id" component={AdminDocumentDetail} />
        <Route path="/admin/zoho-tickets" component={AdminZohoTickets} />
        <Route path="/admin/tickets" component={AdminTickets} />
      </Switch>
    </AdminGuard>
  );
}

function Routes() {
  return (
    <Switch>
      <Route path="/" component={HomeRedirect} />
      <Route path="/sign-in" component={SignInPage} />
      <Route path="/sign-up" component={SignUpPage} />
      <Route path="/app/*?" component={ProtectedApp} />
      <Route path="/admin/*?" component={ProtectedAdmin} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <TooltipProvider>
      <WouterRouter base={basePath}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Routes />
          </AuthProvider>
        </QueryClientProvider>
      </WouterRouter>
      <Toaster />
    </TooltipProvider>
  );
}

export default App;
