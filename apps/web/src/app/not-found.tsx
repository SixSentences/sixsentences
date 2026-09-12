import Link from "next/link";

import { Button } from "@/components/ui/button";
import MossField from "@/components/brand/moss-field";
import SixMark from "@/components/brand/six-mark";

/**
 * Global 404. Renders bare (root layout only — no sidebar, no auth gate) so a
 * missing URL greets everyone. Same frame as the waitlist: the page is a thin
 * ivory border, the iridescent artwork fills the panel edge to edge. A 404
 * that reads like the product, and one way back into the application.
 */
export default function NotFound() {
  return (
    <main className="fixed inset-2 overflow-hidden rounded-2xl bg-moss shadow-[0_10px_60px_-15px_rgba(12,29,25,0.45)] sm:inset-3 sm:rounded-3xl">
      <MossField className="absolute inset-0" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(120%_95%_at_50%_18%,transparent_52%,rgba(7,20,17,0.32)_100%)]" />
      <div className="grain pointer-events-none absolute inset-0 opacity-15 mix-blend-soft-light" />

      <div className="relative z-10 grid h-full grid-rows-[auto_1fr_auto] px-5 py-5 sm:px-10 sm:py-7">
        <header className="rise rise-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <SixMark
              animated
              title="SixSentences_"
              className="h-7 w-7 text-ivory sm:h-8 sm:w-8"
            />
            <span className="font-mono text-[12px] tracking-[0.26em] text-ivory/90 sm:text-[13px]">
              SIXSENTENCES_
            </span>
          </div>
          <span className="hidden font-mono text-[11px] tracking-[0.24em] text-ivory/70 sm:block sm:text-[12px]">
            ERROR 404
          </span>
        </header>

        <section className="flex min-h-0 flex-col items-center justify-center text-center">
          <p className="rise rise-1 font-mono text-[11px] uppercase tracking-[0.3em] text-ivory/60 sm:text-[12px]">
            Page not found
          </p>
          <h1 className="rise rise-1 mt-4 max-w-4xl text-balance font-display text-[clamp(3rem,7vw+1rem,6rem)] leading-[1.02] tracking-[-0.01em] text-ivory">
            This page went <em className="italic">unscreened.</em>
          </h1>
          <p className="rise rise-2 mt-6 max-w-[34rem] text-pretty text-[17px] leading-relaxed text-ivory/80 sm:mt-8 sm:text-[19px]">
            We searched, we screened, we came up empty. This address isn&apos;t
            in this workspace. No matching record, not even a preprint.
          </p>

          <Button
            asChild
            className="rise rise-3 mt-10 h-11 rounded-full bg-ivory px-6 text-[0.9375rem] text-pine hover:bg-ivory/90 sm:mt-12"
          >
            <Link href="/">Back to home</Link>
          </Button>
        </section>

        <footer className="rise rise-5 flex items-center justify-between font-mono text-[11px] tracking-[0.24em] text-ivory/60 sm:text-[12px]">
          <span className="whitespace-nowrap">
            © 2026<span className="hidden sm:inline"> SIXSENTENCES_</span>
          </span>
          <span className="hidden sm:block">
            ASK · PROTOCOL · SEARCH · SCREEN · RANK · REPORT
          </span>
        </footer>
      </div>

      <div className="pointer-events-none absolute inset-0 rounded-[inherit] ring-1 ring-inset ring-white/20" />
    </main>
  );
}
