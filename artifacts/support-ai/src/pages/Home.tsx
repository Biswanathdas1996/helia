import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { ArrowRight, Bot, ShieldCheck, Zap } from "lucide-react";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b border-border/40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="h-6 w-6 bg-primary rounded-sm" />
            <span className="text-xl font-bold tracking-tight text-foreground">Helia</span>
          </div>
          <div className="flex items-center gap-4">
            <Link href="/sign-in" className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
              Sign In
            </Link>
            <Link href="/sign-up" className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-9 px-4 py-2">
              Get Started
            </Link>
          </div>
        </div>
      </header>

      <main className="flex-1 flex flex-col">
        <section className="relative py-24 sm:py-32 flex-1 flex flex-col justify-center items-center text-center px-4 overflow-hidden">
          <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-background to-background" />
          
          <div className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80 mb-8">
            Introducing Helia Support AI
          </div>
          
          <h1 className="text-5xl md:text-7xl font-extrabold tracking-tighter text-foreground max-w-4xl mx-auto leading-tight">
            Intelligent resolution for <span className="text-primary">complex queries.</span>
          </h1>
          
          <p className="mt-6 text-xl text-muted-foreground max-w-2xl mx-auto">
            Helia turns your internal documentation into a highly precise, context-aware support assistant. Deliver instant answers with verifiable citations.
          </p>
          
          <div className="mt-10 flex flex-col sm:flex-row items-center gap-4">
            <Link href="/sign-up" className="inline-flex items-center justify-center rounded-md text-base font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-12 px-8 py-3 w-full sm:w-auto shadow-sm">
              Start Building
              <ArrowRight className="ml-2 h-4 w-4" />
            </Link>
            <Link href="/sign-in" className="inline-flex items-center justify-center rounded-md text-base font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-12 px-8 py-3 w-full sm:w-auto">
              Sign In
            </Link>
          </div>
        </section>

        <section className="py-24 bg-muted/50 border-t">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-12">
              <div className="flex flex-col items-center text-center">
                <div className="h-12 w-12 rounded-lg bg-primary/10 flex items-center justify-center mb-6 text-primary">
                  <Bot className="h-6 w-6" />
                </div>
                <h3 className="text-xl font-semibold text-foreground mb-3">Context Aware</h3>
                <p className="text-muted-foreground">Understands nuance in customer questions and cross-references multiple documents to formulate precise answers.</p>
              </div>
              <div className="flex flex-col items-center text-center">
                <div className="h-12 w-12 rounded-lg bg-primary/10 flex items-center justify-center mb-6 text-primary">
                  <ShieldCheck className="h-6 w-6" />
                </div>
                <h3 className="text-xl font-semibold text-foreground mb-3">Verifiable Citations</h3>
                <p className="text-muted-foreground">Every claim is backed by direct citations to your source material. Trust but verify.</p>
              </div>
              <div className="flex flex-col items-center text-center">
                <div className="h-12 w-12 rounded-lg bg-primary/10 flex items-center justify-center mb-6 text-primary">
                  <Zap className="h-6 w-6" />
                </div>
                <h3 className="text-xl font-semibold text-foreground mb-3">Seamless Escalation</h3>
                <p className="text-muted-foreground">When the AI isn't sure, it cleanly escalates to human agents with full conversation context attached.</p>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="py-8 border-t border-border/40 text-center text-sm text-muted-foreground">
        <p>© {new Date().getFullYear()} Helia. All rights reserved.</p>
      </footer>
    </div>
  );
}
