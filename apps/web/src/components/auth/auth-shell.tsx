"use client";

import MossField from "@/components/brand/moss-field";
import SixMark from "@/components/brand/six-mark";
import { publicLegalUrl } from "@/lib/public-links";

/**
 * Split auth screen: the landing page's moss shader sits flush on the left,
 * with the form on clean paper on the right. On small screens the
 * artwork collapses to a slim brand header.
 */
export default function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="fixed inset-2 grid overflow-hidden rounded-2xl bg-background shadow-[0_10px_60px_-15px_rgba(12,29,25,0.2)] ring-1 ring-border/70 sm:inset-3 sm:rounded-3xl lg:grid-cols-[1.1fr_1fr]">
      {/* Artwork panel */}
      <section className="relative hidden overflow-hidden lg:block lg:rounded-l-3xl">
        <div className="absolute inset-0 bg-pine" />
        <MossField className="absolute inset-0" />
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(120%_95%_at_50%_18%,transparent_52%,rgba(7,20,17,0.34)_100%)]" />
        <div className="grain pointer-events-none absolute inset-0 opacity-15 mix-blend-soft-light" />

        <div className="relative z-10 flex h-full flex-col justify-between p-10">
          <div className="rise rise-1 flex items-center gap-3">
            <SixMark animated title="SixSentences_" className="h-8 w-8 text-ivory" />
            <span className="font-mono text-[0.8125rem] tracking-[0.26em] text-ivory/90">
              SIXSENTENCES_
            </span>
          </div>

          <div className="rise rise-2 max-w-md">
            <h2 className="font-display text-[clamp(2.4rem,3.2vw+1rem,3.6rem)] leading-[1.05] text-ivory">
              The librarian that <em className="italic">never sleeps.</em>
            </h2>
            <p className="mt-5 text-[0.9375rem] leading-relaxed text-ivory/75">
              Ask a research question in the evening and wake up to a ranked,
              screened, fully documented literature base with a PRISMA flow you
              can cite.
            </p>
          </div>

          <div className="rise rise-3 flex items-center justify-between gap-6 font-mono text-[0.6875rem] tracking-[0.24em] text-ivory/60">
            <span className="whitespace-nowrap">EARLY ACCESS · 2026</span>
            <span className="hidden whitespace-nowrap 2xl:inline">
              ASK · PROTOCOL · SEARCH · SCREEN · RANK · REPORT
            </span>
          </div>
        </div>

        <div className="pointer-events-none absolute inset-0 rounded-[inherit] ring-1 ring-inset ring-white/20" />
      </section>

      {/* Form column */}
      <section className="auth-form-surface relative isolate flex min-h-0 flex-col overflow-x-hidden overflow-y-auto">
        <div aria-hidden="true" className="auth-dot-field" />

        <div className="relative z-10 flex items-center gap-2.5 p-4 sm:p-6 lg:hidden">
          <SixMark title="SixSentences_" className="h-6 w-6 text-foreground" />
          <span className="font-mono text-[0.75rem] tracking-[0.24em] text-foreground/90">
            SIXSENTENCES_
          </span>
        </div>
        <div className="relative z-10 flex flex-1 items-center justify-center px-4 pb-8 pt-4 sm:px-6 sm:pb-10 lg:pt-16">
          <div className="w-full max-w-sm">{children}</div>
        </div>
        <nav className="relative z-10 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 px-4 pb-6 font-mono text-[10px] tracking-[0.2em] text-foreground/45">
          <a
            href={publicLegalUrl("imprint")}
            className="transition hover:text-foreground"
          >
            IMPRINT
          </a>
          <a
            href={publicLegalUrl("privacy")}
            className="transition hover:text-foreground"
          >
            PRIVACY
          </a>
          <a
            href={publicLegalUrl("terms")}
            className="transition hover:text-foreground"
          >
            TERMS
          </a>
        </nav>
      </section>
    </main>
  );
}
