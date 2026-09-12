"use client";

import {
  BrainCircuit,
  CheckCircle2,
  CircleHelp,
  Lightbulb,
  ListChecks,
  Loader2,
  Quote,
  RefreshCw,
  X,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type {
  LiveBrainstormEvidenceKind,
  LiveBrainstormReceipt,
} from "@/lib/types";

const FAILURE_COPY: Record<string, readonly [string, string]> = {
  brainstorm_unavailable: ["Die Strukturierung ist gerade nicht verfügbar.", "Structuring is currently unavailable."],
  session_unavailable: ["Die zugehörige Session ist nicht mehr verfügbar.", "The associated session is no longer available."],
  brainstorm_too_large: ["Der Gedankenstrom ist für eine vollständige Strukturierung zu groß.", "The thought stream is too large for complete structuring."],
  no_live_transcript: ["Es gibt noch keinen finalen Gedankenstrom.", "There is no final thought stream yet."],
  no_brainstorm_transcript: ["Es gibt noch keinen finalen Gedankenstrom.", "There is no final thought stream yet."],
  brainstorm_result_not_grounded: ["Das Ergebnis ließ sich nicht zuverlässig am Gedankenstrom belegen.", "The result could not be grounded reliably in the thought stream."],
  brainstorm_cancelled: ["Die Strukturierung wurde abgebrochen.", "Structuring was cancelled."],
  brainstorm_result_unavailable: ["Das gespeicherte Ergebnis ist nicht mehr lesbar.", "The saved result is no longer readable."],
  context_not_available: ["Der angeforderte Gedankenstand ist noch nicht vollständig angekommen.", "The requested thought-stream version has not arrived completely yet."],
  brainstorm_request_conflict: ["Diese Anfrage kollidiert mit einer früheren Strukturierung.", "This request conflicts with an earlier structure."],
  brainstorm_in_progress: ["Dieser Gedankenstand wird bereits strukturiert.", "This thought stream is already being structured."],
  capacity_unavailable: ["Die Strukturierung ist gerade nicht verfügbar.", "Structuring is unavailable right now."],
};

const NON_RETRYABLE_FAILURE_CODES = new Set([
  "brainstorm_too_large",
]);

function evidenceLabel(kind: LiveBrainstormEvidenceKind, german: boolean): string {
  const labels: Record<LiveBrainstormEvidenceKind, readonly [string, string]> = {
    summary: ["Zusammenfassung", "Summary"],
    themes: ["Thema", "Theme"],
    ideas: ["Idee", "Idea"],
    open_questions: ["Frage", "Question"],
    decisions: ["Entscheidung", "Decision"],
    next_steps: ["Nächster Schritt", "Next step"],
  };
  return labels[kind][german ? 0 : 1];
}

function failureCopy(receipt: LiveBrainstormReceipt, german: boolean): string {
  return (receipt.error_code && FAILURE_COPY[receipt.error_code]?.[german ? 0 : 1])
    || (german
      ? "Die Strukturierung konnte nicht abgeschlossen werden. Deine Rohgedanken bleiben gespeichert."
      : "Structuring could not be completed. Your raw thoughts remain stored.");
}

export function BrainstormResult({
  receipt,
  german,
  retrying,
  cancelling,
  canRetry,
  onRetry,
  onCancel,
  onLocateEvidence,
}: {
  receipt: LiveBrainstormReceipt | null;
  german: boolean;
  retrying: boolean;
  cancelling: boolean;
  canRetry: boolean;
  onRetry: () => void;
  onCancel: () => void;
  onLocateEvidence: (segmentId: string) => void;
}) {
  if (!receipt) {
    return (
      <div className="rounded-2xl border border-dashed border-border bg-card/45 p-5 text-sm leading-relaxed text-muted-foreground">
        <p>{german
          ? "Für diesen abgeschlossenen Gedankenstrom gibt es noch kein KI-Ergebnis. Du kannst den gespeicherten Stand jetzt strukturieren."
          : "This closed thought stream does not have an AI result yet. You can structure its stored snapshot now."}</p>
        {canRetry && <Button type="button" variant="outline" size="sm" className="mt-4 rounded-full" disabled={retrying} onClick={onRetry}>
          {retrying ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          {german ? "Gespeicherten Stand strukturieren" : "Structure stored snapshot"}
        </Button>}
      </div>
    );
  }

  if (receipt.status === "pending") {
    return (
      <div role="status" aria-live="polite" className="rounded-2xl border border-moss/25 bg-moss-surface/8 p-5">
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Loader2 className="size-4 animate-spin text-moss" />
          {german ? "Gedanken werden strukturiert" : "Structuring your thoughts"}
        </p>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {german
            ? "Der bestätigte Gedankenstand wird vollständig verarbeitet. Du kannst die Seite schließen und später zurückkehren."
            : "The confirmed thought stream is being processed in full. You can close this page and return later."}
        </p>
        <Button type="button" variant="outline" size="sm" className="mt-4 rounded-full" disabled={cancelling} onClick={onCancel}>
          {cancelling ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
          {german ? "Strukturierung abbrechen" : "Cancel structuring"}
        </Button>
      </div>
    );
  }

  if (receipt.status === "failed" || !receipt.result) {
    const retryAvailable = canRetry
      && !NON_RETRYABLE_FAILURE_CODES.has(receipt.error_code ?? "");
    return (
      <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-5">
        <p className="flex items-center gap-2 text-sm font-medium text-destructive">
          <XCircle className="size-4" />
          {german ? "Strukturierung nicht abgeschlossen" : "Structuring did not complete"}
        </p>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{failureCopy(receipt, german)}</p>
        {retryAvailable && <Button type="button" variant="outline" size="sm" className="mt-4 rounded-full" disabled={retrying} onClick={onRetry}>
          {retrying ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          {german ? "Mit demselben Gedankenstand erneut versuchen" : "Retry the same thought stream"}
        </Button>}
      </div>
    );
  }

  const result = receipt.result;
  return (
    <div className="space-y-4" data-brainstorm-result>
      <section aria-labelledby="brainstorm-summary" className="rounded-2xl border border-moss/25 bg-moss-surface/8 p-5">
        <h3 id="brainstorm-summary" className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-moss">
          {german ? "Zusammenfassung" : "Summary"}
        </h3>
        <p className="mt-3 text-sm leading-7 text-foreground/90">{result.summary}</p>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <ResultCards
          title={german ? "Themen" : "Themes"}
          icon={<BrainCircuit className="size-4 text-moss" />}
          items={result.themes.map((item) => ({ title: item.title, body: item.description }))}
        />
        <ResultCards
          title={german ? "Ideen" : "Ideas"}
          icon={<Lightbulb className="size-4 text-moss" />}
          items={result.ideas.map((item) => ({ title: item.title, body: item.description }))}
        />
        <ResultList
          title={german ? "Offene Fragen" : "Open questions"}
          icon={<CircleHelp className="size-4 text-moss" />}
          items={result.open_questions}
        />
        <ResultList
          title={german ? "Entscheidungen" : "Decisions"}
          icon={<CheckCircle2 className="size-4 text-moss" />}
          items={result.decisions}
        />
      </div>

      {result.next_steps.length > 0 && (
        <section aria-labelledby="brainstorm-next-steps" className="rounded-2xl border border-border bg-card p-5">
          <h3 id="brainstorm-next-steps" className="flex items-center gap-2 text-sm font-medium text-foreground">
            <ListChecks className="size-4 text-moss" /> {german ? "Nächste Schritte" : "Next steps"}
          </h3>
          <ol className="mt-4 space-y-3">
            {result.next_steps.map((step, index) => (
              <li key={`${step.action}-${index}`} className="flex gap-3 text-sm leading-relaxed text-muted-foreground">
                <span className="font-mono text-moss">{index + 1}.</span>
                <span>{step.action}{step.owner ? <span className="text-foreground/70"> · {step.owner}</span> : null}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section aria-labelledby="brainstorm-evidence" className="rounded-2xl border border-border bg-secondary/30 p-5">
        <h3 id="brainstorm-evidence" className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Quote className="size-4 text-moss" /> {german ? "Belege" : "Evidence"}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {german ? "Direkt mit deinen gespeicherten Rohgedanken verknüpft." : "Linked directly to your stored raw thoughts."}
        </p>
        <div className="mt-4 space-y-2">
          {result.evidence.map((item, index) => (
            <button
              key={`${item.segment_id}-${item.kind}-${item.index}-${index}`}
              type="button"
              onClick={() => onLocateEvidence(item.segment_id)}
              className="block w-full rounded-xl border border-transparent bg-background/75 p-3 text-left transition-colors hover:border-moss/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss"
              aria-label={`${evidenceLabel(item.kind, german)}: ${item.quote}`}
            >
              <blockquote className="text-xs leading-relaxed text-foreground/85">“{item.quote}”</blockquote>
              <span className="mt-1.5 block font-mono text-[0.5625rem] uppercase tracking-[0.1em] text-muted-foreground">
                {evidenceLabel(item.kind, german)} · {item.segment_id}
              </span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function ResultCards({
  title,
  icon,
  items,
}: {
  title: string;
  icon: React.ReactNode;
  items: Array<{ title: string; body: string }>;
}) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title} className="rounded-2xl border border-border bg-card p-5">
      <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">{icon}{title}</h3>
      <div className="mt-4 space-y-4">
        {items.map((item, index) => (
          <div key={`${item.title}-${index}`}>
            <p className="text-xs font-medium text-foreground">{item.title}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{item.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function ResultList({
  title,
  icon,
  items,
}: {
  title: string;
  icon: React.ReactNode;
  items: string[];
}) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title} className="rounded-2xl border border-border bg-card p-5">
      <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">{icon}{title}</h3>
      <ul className="mt-4 space-y-2 text-xs leading-relaxed text-muted-foreground">
        {items.map((item, index) => <li key={`${item}-${index}`} className="flex gap-2"><span className="text-moss">•</span><span>{item}</span></li>)}
      </ul>
    </section>
  );
}
