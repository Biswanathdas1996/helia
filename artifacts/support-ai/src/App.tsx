import { useEffect, useRef } from "react";
import { ClerkProvider, SignIn, SignUp, Show, useClerk } from "@clerk/react";
import { publishableKeyFromHost } from "@clerk/react/internal";
import { shadcn } from "@clerk/themes";
import { Switch, Route, Redirect, useLocation, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";

import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import { Shell } from "@/components/layout/Shell";
import { AdminGuard } from "@/components/AdminGuard";

import LandingPage from "@/pages/Home";
import Chat from "@/pages/app/Chat";
import Tickets from "@/pages/app/Tickets";
import TicketDetail from "@/pages/app/TicketDetail";
import NewTicket from "@/pages/app/NewTicket";

import AdminDashboard from "@/pages/admin/Dashboard";
import AdminDocuments from "@/pages/admin/Documents";
import AdminDocumentDetail from "@/pages/admin/DocumentDetail";
import AdminNewDocument from "@/pages/admin/NewDocument";
import AdminTickets from "@/pages/admin/Tickets";

// Setup
const configuredClerkPubKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;
const clerkPubKey = configuredClerkPubKey
  ? publishableKeyFromHost(window.location.hostname, configuredClerkPubKey)
  : null;
const clerkProxyUrl = import.meta.env.VITE_CLERK_PROXY_URL;
const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
const isClerkEnabled = Boolean(clerkPubKey);

function stripBase(path: string): string {
  return basePath && path.startsWith(basePath)
    ? path.slice(basePath.length) || "/"
    : path;
}

const clerkAppearance = {
  theme: shadcn,
  cssLayerName: "clerk",
  options: {
    logoPlacement: "inside" as const,
    logoLinkUrl: basePath || "/",
    logoImageUrl: `${window.location.origin}${basePath}/logo.svg`,
  },
  variables: {
    colorPrimary: "hsl(21, 99%, 41%)",
    colorForeground: "hsl(0, 0%, 18%)",
    colorMutedForeground: "hsl(0, 0%, 40%)",
    colorDanger: "hsl(5, 75%, 50%)",
    colorBackground: "hsl(0, 0%, 100%)",
    colorInput: "hsl(0, 0%, 89%)",
    colorInputForeground: "hsl(0, 0%, 18%)",
    colorNeutral: "hsl(0, 0%, 89%)",
    fontFamily: "'Helvetica Neue', Helvetica, Arial, 'Inter', sans-serif",
    borderRadius: "0.25rem",
  },
  elements: {
    rootBox: "w-full flex justify-center",
    cardBox: "bg-white rounded-2xl w-[440px] max-w-full overflow-hidden shadow-lg border border-border",
    card: "!shadow-none !border-0 !bg-transparent !rounded-none",
    footer: "!shadow-none !border-0 !bg-transparent !rounded-none",
    headerTitle: "text-foreground font-semibold text-xl",
    headerSubtitle: "text-muted-foreground text-sm",
    socialButtonsBlockButtonText: "text-foreground font-medium",
    formFieldLabel: "text-foreground font-medium text-sm",
    footerActionLink: "text-primary hover:text-primary/90 font-medium",
    footerActionText: "text-muted-foreground",
    dividerText: "text-muted-foreground",
    identityPreviewEditButton: "text-primary",
    formFieldSuccessText: "text-green-600",
    alertText: "text-destructive",
    logoBox: "mb-6 flex justify-center",
    logoImage: "h-8 object-contain",
    socialButtonsBlockButton: "border border-input hover:bg-accent hover:text-accent-foreground",
    formButtonPrimary: "bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm",
    formFieldInput: "flex h-10 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
    footerAction: "mt-4 text-center text-sm",
    dividerLine: "bg-border",
    alert: "border-destructive/50 text-destructive border rounded-md p-3 text-sm",
    otpCodeFieldInput: "border-input rounded-md",
    formFieldRow: "mb-4",
    main: "w-full",
  },
};

function SignInPage() {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-background px-4 relative">
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/5 via-background to-background" />
      <SignIn routing="path" path={`${basePath}/sign-in`} signUpUrl={`${basePath}/sign-up`} />
    </div>
  );
}

function SignUpPage() {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-background px-4 relative">
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/5 via-background to-background" />
      <SignUp routing="path" path={`${basePath}/sign-up`} signInUrl={`${basePath}/sign-in`} />
    </div>
  );
}

function HomeRedirect() {
  return (
    <>
      <Show when="signed-in">
        <Redirect to="/app" />
      </Show>
      <Show when="signed-out">
        <LandingPage />
      </Show>
    </>
  );
}

function ProtectedApp() {
  return (
    <>
      <Show when="signed-in">
        <Shell>
          <Switch>
            <Route path="/app" component={Chat} />
            <Route path="/app/conversations/:id" component={Chat} />
            <Route path="/app/tickets" component={Tickets} />
            <Route path="/app/tickets/new" component={NewTicket} />
            <Route path="/app/tickets/:id" component={TicketDetail} />
          </Switch>
        </Shell>
      </Show>
      <Show when="signed-out">
        <Redirect to="/" />
      </Show>
    </>
  );
}

function ProtectedAdmin() {
  return (
    <>
      <Show when="signed-in">
        <AdminGuard>
          <Switch>
            <Route path="/admin" component={AdminDashboard} />
            <Route path="/admin/documents" component={AdminDocuments} />
            <Route path="/admin/documents/new" component={AdminNewDocument} />
            <Route path="/admin/documents/:id" component={AdminDocumentDetail} />
            <Route path="/admin/tickets" component={AdminTickets} />
          </Switch>
        </AdminGuard>
      </Show>
      <Show when="signed-out">
        <Redirect to="/" />
      </Show>
    </>
  );
}

function ClerkQueryClientCacheInvalidator() {
  const { addListener } = useClerk();
  const queryClient = useQueryClient();
  const prevUserIdRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    const unsubscribe = addListener(({ user }) => {
      const userId = user?.id ?? null;
      if (prevUserIdRef.current !== undefined && prevUserIdRef.current !== userId) {
        queryClient.clear();
      }
      prevUserIdRef.current = userId;
    });
    return unsubscribe;
  }, [addListener, queryClient]);

  return null;
}

const queryClient = new QueryClient();

function LocalStartupFallback() {
  return (
    <div className="min-h-[100dvh] bg-background">
      <div className="border-b border-border bg-amber-50 px-4 py-3 text-sm text-amber-950">
        Local startup mode is running without Clerk configuration. Set
        {" "}
        <code className="font-mono">VITE_CLERK_PUBLISHABLE_KEY</code>
        {" "}
        to enable sign-in flows, and set
        {" "}
        <code className="font-mono">MONGODB_URI</code>
        {" "}
        for data-backed API features.
      </div>
      <LandingPage />
    </div>
  );
}

function ClerkProviderWithRoutes() {
  const [, setLocation] = useLocation();

  return (
    <ClerkProvider
      publishableKey={clerkPubKey}
      proxyUrl={clerkProxyUrl}
      appearance={clerkAppearance}
      signInUrl={`${basePath}/sign-in`}
      signUpUrl={`${basePath}/sign-up`}
      routerPush={(to) => setLocation(stripBase(to))}
      routerReplace={(to) => setLocation(stripBase(to), { replace: true })}
    >
      <QueryClientProvider client={queryClient}>
        <ClerkQueryClientCacheInvalidator />
        <Switch>
          <Route path="/" component={HomeRedirect} />
          <Route path="/sign-in/*?" component={SignInPage} />
          <Route path="/sign-up/*?" component={SignUpPage} />
          
          <Route path="/app/*?" component={ProtectedApp} />
          <Route path="/admin/*?" component={ProtectedAdmin} />
          
          <Route component={NotFound} />
        </Switch>
      </QueryClientProvider>
    </ClerkProvider>
  );
}

function App() {
  return (
    <TooltipProvider>
      <WouterRouter base={basePath}>
        {isClerkEnabled ? <ClerkProviderWithRoutes /> : <LocalStartupFallback />}
      </WouterRouter>
      <Toaster />
    </TooltipProvider>
  );
}

export default App;
