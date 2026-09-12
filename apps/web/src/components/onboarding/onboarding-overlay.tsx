"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  FileText,
  Globe,
  Languages,
  MessageSquareText,
  Route,
  Table2,
  Telescope,
  X,
} from "lucide-react";

import MossField from "@/components/brand/moss-field";
import SixMark from "@/components/brand/six-mark";
import { Button } from "@/components/ui/button";
import { track } from "@/lib/analytics";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const EASE = [0.22, 1, 0.36, 1] as const;

const STARTERS = [
  "How do LLM ensembles perform at title/abstract screening?",
  "Which methods detect hallucinations in large language models?",
  "Does retrieval-augmented generation reduce factual errors?",
  "What benchmarks exist for automated systematic reviews?",
];

const variants = {
  enter: (dir: number) => ({ opacity: 0, x: dir > 0 ? 48 : -48 }),
  center: { opacity: 1, x: 0 },
  exit: (dir: number) => ({ opacity: 0, x: dir > 0 ? -48 : 48 }),
};

/**
 * First-run welcome. A full-screen branded takeover (the waitlist's
 * iridescent artwork) that walks a new user through the product in four steps
 * and hands them their first question. Shows once (server-stamped) and can be
 * re-opened from the user menu. Closing marks onboarding done.
 */
export default function OnboardingOverlay({
  open,
  onClose,
  firstName,
}: {
  open: boolean;
  onClose: () => void;
  firstName: string;
}) {
  const router = useRouter();
  const { refresh } = useAuth();
  const [[step, dir], setStep] = useState<[number, number]>([0, 0]);
  const name = firstName?.trim() || "there";
  const LAST = 3;

  // reset to the first step whenever the overlay (re)opens
  useEffect(() => {
    if (open) setStep([0, 0]);
  }, [open]);

  // escape closes, like a dialog
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
      if (e.key === "ArrowRight" && step < LAST) go(step + 1);
      if (e.key === "ArrowLeft" && step > 0) go(step - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, step]);

  function go(next: number) {
    setStep([next, next > step ? 1 : -1]);
  }

  async function finish() {
    // the step they were on says whether they read it or skipped out early
    track("onboarding_done", { step });
    onClose();
    try {
      await api.markOnboarded();
      await refresh();
    } catch {
      // the overlay is already closed; a failed stamp just means it may
      // show again next login, which is harmless
    }
  }

  async function startWith(question: string) {
    // bridge the question across the navigation to the composer on home
    sessionStorage.setItem("six:onboarding-seed", question);
    await finish();
    router.push("/");
    // if home is already mounted, nudge it to consume the seed now
    window.dispatchEvent(new CustomEvent("six:seed-search"));
  }

  async function startTour() {
    await finish();
    // let the overlay unmount before the spotlight measures the page
    setTimeout(
      () => window.dispatchEvent(new CustomEvent("six:start-tour")),
      250,
    );
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] bg-background">
      {/* the thin ivory border is the page; the panel is the artwork inside it */}
      <div className="absolute inset-2 overflow-hidden rounded-2xl bg-moss-surface shadow-[0_10px_60px_-15px_rgba(12,29,25,0.45)] sm:inset-3 sm:rounded-3xl">
        <MossField className="absolute inset-0" />
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(130%_105%_at_50%_12%,rgba(7,20,17,0.28)_35%,rgba(7,20,17,0.62)_100%)]" />
        <div className="grain pointer-events-none absolute inset-0 opacity-15 mix-blend-soft-light" />

        <div className="relative z-10 grid h-full grid-rows-[auto_1fr_auto] px-5 py-5 sm:px-10 sm:py-7">
        {/* header: brand + skip */}
        <header className="flex items-center justify-between">
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
          <button
            type="button"
            onClick={finish}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.18em] text-ivory/70 transition-colors hover:bg-white/10 hover:text-ivory sm:text-[12px]"
          >
            Skip <X className="size-3.5" />
          </button>
        </header>

        {/* steps */}
        <div className="relative flex min-h-0 items-center justify-center">
          <AnimatePresence mode="wait" custom={dir}>
            <motion.div
              key={step}
              custom={dir}
              variants={variants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.4, ease: EASE }}
              className="w-full max-w-3xl text-center"
            >
              {step === 0 && <WelcomeStep name={name} />}
              {step === 1 && <TwoWaysStep />}
              {step === 2 && <AssistantStep />}
              {step === 3 && (
                <StartStep
                  name={name}
                  onPick={startWith}
                  onClose={finish}
                  onTour={startTour}
                />
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* footer: progress + nav */}
        <footer className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {[0, 1, 2, 3].map((i) => (
              <button
                key={i}
                type="button"
                onClick={() => go(i)}
                aria-label={`Step ${i + 1}`}
                className={cn(
                  "h-1.5 rounded-full transition-all",
                  i === step ? "w-6 bg-ivory" : "w-1.5 bg-ivory/35 hover:bg-ivory/60",
                )}
              />
            ))}
          </div>

          <div className="flex items-center gap-2.5">
            {step > 0 && (
              <Button
                variant="ghost"
                onClick={() => go(step - 1)}
                className="h-10 rounded-full px-4 text-[0.875rem] text-ivory/80 hover:bg-white/10 hover:text-ivory"
              >
                <ArrowLeft className="size-4" /> Back
              </Button>
            )}
            {step < LAST ? (
              <Button
                onClick={() => go(step + 1)}
                className="h-10 rounded-full bg-ivory px-5 text-[0.875rem] text-pine hover:bg-ivory/90"
              >
                Continue <ArrowRight className="size-4" />
              </Button>
            ) : (
              <Button
                onClick={finish}
                className="h-10 rounded-full bg-ivory px-5 text-[0.875rem] text-pine hover:bg-ivory/90"
              >
                Start exploring
              </Button>
            )}
          </div>
        </footer>
        </div>

        <div className="pointer-events-none absolute inset-0 rounded-[inherit] ring-1 ring-inset ring-white/20" />
      </div>
    </div>
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="rise rise-1 font-mono text-[11px] uppercase tracking-[0.3em] text-ivory/60 sm:text-[12px]">
      {children}
    </p>
  );
}

function Headline({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="rise rise-1 mt-4 text-balance font-display text-[clamp(2.4rem,5vw+1rem,4.25rem)] leading-[1.02] tracking-[-0.01em] text-ivory">
      {children}
    </h2>
  );
}

function WelcomeStep({ name }: { name: string }) {
  return (
    <div className="flex flex-col items-center">
      <Eyebrow>Welcome</Eyebrow>
      <Headline>
        Hello, {name}. <em className="italic">Let&apos;s begin.</em>
      </Headline>
      <p className="rise rise-2 mt-6 max-w-xl text-pretty text-[1.0625rem] leading-relaxed text-ivory/80 sm:text-[1.1875rem]">
        SixSentences_ is the research librarian that never sleeps. Give us sixty
        seconds and we&apos;ll show you how it turns a question into citable
        answers.
      </p>
    </div>
  );
}

function ModeCard({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-white/15 bg-white/[0.08] p-5 text-left backdrop-blur-sm">
      <span className="grid size-9 place-items-center rounded-xl bg-ivory/15 text-ivory">
        {icon}
      </span>
      <p className="mt-3 font-display text-[1.25rem] text-ivory">{title}</p>
      <p className="mt-1.5 text-[0.875rem] leading-relaxed text-ivory/75">{children}</p>
    </div>
  );
}

function TwoWaysStep() {
  return (
    <div className="flex flex-col items-center">
      <Eyebrow>Two ways to ask</Eyebrow>
      <Headline>A quick answer, or the whole map.</Headline>
      <div className="rise rise-2 mt-8 grid w-full max-w-2xl gap-4 sm:grid-cols-2">
        <ModeCard icon={<MessageSquareText className="size-4.5" />} title="Just ask">
          Ask a question for an AI-assisted answer with citations to retrieved
          sources. Review the passages before relying on the result.
        </ModeCard>
        <ModeCard icon={<Telescope className="size-4.5" />} title="Run a search">
          Configure a literature search with recorded screening decisions,
          ranked results and available full texts. Review its coverage and
          decisions, then export the search record and PRISMA flow.
        </ModeCard>
      </div>
    </div>
  );
}

function FeatureChip({
  icon,
  children,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.08] px-3 py-1.5 text-[0.8125rem] text-ivory/85">
      <span className="text-ivory/70">{icon}</span>
      {children}
    </span>
  );
}

function AssistantStep() {
  return (
    <div className="flex flex-col items-center">
      <Eyebrow>Grounded by design</Eyebrow>
      <Headline>Never a made-up answer.</Headline>
      <p className="rise rise-2 mt-6 max-w-xl text-pretty text-[1.0625rem] leading-relaxed text-ivory/80">
        The assistant only tells you what the sources actually say, and every
        claim links back to where it came from. It also reads PDFs, compares
        papers, and works across languages.
      </p>
      <div className="rise rise-3 mt-6 flex flex-wrap items-center justify-center gap-2">
        <FeatureChip icon={<BadgeCheck className="size-3.5" />}>Verified citations</FeatureChip>
        <FeatureChip icon={<FileText className="size-3.5" />}>PDF reader</FeatureChip>
        <FeatureChip icon={<Table2 className="size-3.5" />}>Data tables</FeatureChip>
        <FeatureChip icon={<Languages className="size-3.5" />}>Translation</FeatureChip>
        <FeatureChip icon={<Globe className="size-3.5" />}>Live web</FeatureChip>
      </div>
    </div>
  );
}

function StartStep({
  name,
  onPick,
  onClose,
  onTour,
}: {
  name: string;
  onPick: (q: string) => void;
  onClose: () => void;
  onTour: () => void;
}) {
  return (
    <div className="flex flex-col items-center">
      <Eyebrow>Your turn</Eyebrow>
      <Headline>What are you curious about, {name}?</Headline>
      <Button
        onClick={onTour}
        className="rise rise-2 mt-6 h-11 rounded-full bg-ivory px-6 text-[0.9375rem] text-pine hover:bg-ivory/90"
      >
        <Route className="size-4" />
        Show me around the workspace first
      </Button>
      <p className="rise rise-2 mt-5 text-[1rem] text-ivory/75">
        Or pick a question to start with:
      </p>
      <div className="rise rise-3 mt-7 grid w-full max-w-2xl gap-2.5 sm:grid-cols-2">
        {STARTERS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="group flex items-center gap-3 rounded-2xl border border-white/15 bg-white/[0.08] px-4 py-3 text-left text-[0.875rem] leading-snug text-ivory/90 backdrop-blur-sm transition-colors hover:border-ivory hover:bg-ivory hover:text-pine"
          >
            <MessageSquareText className="size-4 shrink-0 text-ivory/60 transition-colors group-hover:text-pine" />
            <span className="min-w-0">{q}</span>
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={onClose}
        className="rise rise-4 mt-6 text-[0.8125rem] text-ivory/70 underline decoration-ivory/30 underline-offset-4 transition hover:text-ivory hover:decoration-ivory"
      >
        I&apos;ll start on my own
      </button>
    </div>
  );
}
