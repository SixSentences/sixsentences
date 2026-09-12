"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  CheckCircle2,
  CircleHelp,
  Headphones,
  KeyRound,
  Laptop,
  Lightbulb,
  ListChecks,
  ListTree,
  Loader2,
  LockKeyhole,
  MessageSquareText,
  MoreHorizontal,
  Quote,
  Radio,
  RefreshCw,
  ShieldOff,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatClock, formatDate } from "@/lib/format";
import type {
  LiveBrainstormEvidenceKind,
  LiveBrainstormReceipt,
  LiveCompanionDevice,
  LiveSession,
  LiveSessionEvent,
  LiveSessionPage,
  LiveSessionStatus,
  LiveTranscriptSegment,
} from "@/lib/types";
import { userFacingStoredErrorMessage } from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

type ProjectFilter = "all" | "none" | number;
const MAX_RENDERED_LIVE_SEGMENTS = 240;

type CompanionPairRequest = {
  state: string;
  codeChallenge: string;
  deviceName?: string;
};

function safeCompanionDeepLink(value: string, expectedState: string): string {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "sixsentences:" ||
      url.hostname !== "companion" ||
      url.pathname !== "/pair" ||
      url.searchParams.get("state") !== expectedState ||
      !url.searchParams.get("code")
    ) return "";
    return url.toString();
  } catch {
    return "";
  }
}

const statusMetaEn: Record<
  LiveSessionStatus,
  { label: string; detail: string; classes: string; icon: typeof Radio }
> = {
  recording: {
    label: "Recording",
    detail: "Mic and system audio are being transcribed on this Mac.",
    classes: "border-red-400/25 bg-red-400/8 text-red-300",
    icon: Radio,
  },
  completed: {
    label: "Ready",
    detail: "The session is available as a full interview transcript.",
    classes: "border-emerald-400/25 bg-emerald-400/8 text-emerald-300",
    icon: CheckCircle2,
  },
  cancelled: {
    label: "Cancelled",
    detail: "Capture was stopped without creating an interview.",
    classes: "border-border bg-secondary/55 text-muted-foreground",
    icon: XCircle,
  },
  failed: {
    label: "Failed",
    detail: "The desktop companion could not complete this session.",
    classes: "border-destructive/25 bg-destructive/8 text-destructive",
    icon: XCircle,
  },
};

const statusMetaDe: typeof statusMetaEn = {
  recording: { ...statusMetaEn.recording, label: "Aufnahme", detail: "Mikrofon und Systemaudio werden auf diesem Mac transkribiert." },
  completed: { ...statusMetaEn.completed, label: "Bereit", detail: "Das Gespräch ist als vollständiges Interview-Transkript verfügbar." },
  cancelled: { ...statusMetaEn.cancelled, label: "Abgebrochen", detail: "Die Aufnahme wurde beendet, ohne ein Interview anzulegen." },
  failed: { ...statusMetaEn.failed, label: "Fehlgeschlagen", detail: "Der Desktop Companion konnte dieses Gespräch nicht abschließen." },
};

const brainstormStatusMetaEn: typeof statusMetaEn = {
  recording: {
    ...statusMetaEn.recording,
    label: "Thinking",
    detail: "Only your microphone is transcribed. System audio stays off.",
  },
  completed: {
    ...statusMetaEn.completed,
    label: "Captured",
    detail: "Your private thought stream is frozen and ready to structure.",
  },
  cancelled: {
    ...statusMetaEn.cancelled,
    detail: "The private brainstorm was stopped without further processing.",
  },
  failed: {
    ...statusMetaEn.failed,
    detail: "The desktop companion could not finish this private brainstorm.",
  },
};

const brainstormStatusMetaDe: typeof statusMetaEn = {
  recording: {
    ...brainstormStatusMetaEn.recording,
    label: "Denken",
    detail: "Nur dein Mikrofon wird transkribiert. Systemaudio bleibt aus.",
  },
  completed: {
    ...brainstormStatusMetaEn.completed,
    label: "Erfasst",
    detail: "Dein privater Gedankenstrom ist eingefroren und kann strukturiert werden.",
  },
  cancelled: {
    ...brainstormStatusMetaEn.cancelled,
    label: "Abgebrochen",
    detail: "Das private Brainstorming wurde ohne weitere Verarbeitung beendet.",
  },
  failed: {
    ...brainstormStatusMetaEn.failed,
    label: "Fehlgeschlagen",
    detail: "Der Desktop Companion konnte dieses private Brainstorming nicht abschließen.",
  },
};

function asSegment(event: LiveSessionEvent): LiveTranscriptSegment | null {
  if (event.type !== "segment") return null;
  const payload = event.payload;
  if (
    typeof payload.speaker !== "string" ||
    typeof payload.text !== "string" ||
    typeof payload.start_ms !== "number" ||
    typeof payload.end_ms !== "number" ||
    (payload.channel !== "microphone" && payload.channel !== "system")
  ) return null;
  return {
    id: typeof payload.segment_id === "string" ? payload.segment_id : undefined,
    channel: payload.channel,
    speaker: payload.speaker,
    start_ms: payload.start_ms,
    end_ms: payload.end_ms,
    text: payload.text,
    is_final: true,
  };
}

function StatusBadge({ session, german }: { session: LiveSession; german: boolean }) {
  const meta = (
    session.purpose === "brainstorm"
      ? german ? brainstormStatusMetaDe : brainstormStatusMetaEn
      : german ? statusMetaDe : statusMetaEn
  )[session.status];
  const Icon = meta.icon;
  return (
    <Badge variant="outline" className={cn("rounded-full px-2.5 py-1", meta.classes)}>
      <Icon className={cn("size-3", session.status === "recording" && "animate-pulse")} />
      {meta.label}
    </Badge>
  );
}

function newestBrainstorm(receipts: LiveBrainstormReceipt[]): LiveBrainstormReceipt | null {
  return receipts.reduce<LiveBrainstormReceipt | null>(
    (latest, receipt) =>
      latest === null || receipt.created_at >= latest.created_at ? receipt : latest,
    null,
  );
}

function evidenceKindLabel(kind: LiveBrainstormEvidenceKind, german: boolean): string {
  const labels: Record<LiveBrainstormEvidenceKind, [string, string]> = {
    summary: ["Zusammenfassung", "Summary"],
    themes: ["Thema", "Theme"],
    ideas: ["Idee", "Idea"],
    open_questions: ["Offene Frage", "Open question"],
    decisions: ["Entscheidung", "Decision"],
    next_steps: ["Nächster Schritt", "Next step"],
  };
  return labels[kind][german ? 0 : 1];
}

const brainstormFailureMessages: Record<string, readonly [german: string, english: string]> = {
  brainstorm_unavailable: [
    "Die Strukturierung ist derzeit nicht verfügbar. Starte sie im Companion erneut.",
    "Structuring is currently unavailable. Start it again in the companion.",
  ],
  session_unavailable: [
    "Die zugehörige Brainstorming-Session ist nicht mehr verfügbar.",
    "The associated brainstorm session is no longer available.",
  ],
  brainstorm_too_large: [
    "Der Gedankenstrom überschreitet die unterstützte Größe für eine vollständige Strukturierung.",
    "The thought stream exceeds the supported size for complete structuring.",
  ],
  no_live_transcript: [
    "In diesem Stand ist kein finaler Gedankenstrom vorhanden.",
    "There is no final thought stream in this version.",
  ],
  no_brainstorm_transcript: [
    "In diesem Stand ist kein finaler Gedankenstrom vorhanden.",
    "There is no final thought stream in this version.",
  ],
  brainstorm_result_not_grounded: [
    "Die Struktur ließ sich nicht zuverlässig am eingefrorenen Gedankenstrom belegen.",
    "The structure could not be grounded reliably in the frozen thought stream.",
  ],
  brainstorm_cancelled: [
    "Die Strukturierung wurde abgebrochen.",
    "Structuring was cancelled.",
  ],
  brainstorm_result_unavailable: [
    "Das gespeicherte Ergebnis ist nicht mehr lesbar. Starte die Strukturierung erneut.",
    "The saved result is no longer readable. Start structuring again.",
  ],
  session_not_brainstorm: [
    "Diese Session ist kein Brainstorming.",
    "This session is not a brainstorm.",
  ],
  brainstorm_session_unavailable: [
    "Dieses Brainstorming kann nicht mehr strukturiert werden.",
    "This brainstorm can no longer be structured.",
  ],
  context_not_available: [
    "Der angeforderte Gedankenstand ist noch nicht vollständig angekommen.",
    "The requested thought-stream version has not arrived completely yet.",
  ],
  invalid_brainstorm_cutoff: [
    "Der bestätigte Gedankenstand ist ungültig. Starte die Strukturierung erneut.",
    "The confirmed thought-stream cutoff is invalid. Start structuring again.",
  ],
  brainstorm_request_conflict: [
    "Diese Strukturierungsanfrage steht im Konflikt mit einer früheren Anfrage.",
    "This structuring request conflicts with an earlier request.",
  ],
  brainstorm_in_progress: [
    "Dieser Gedankenstand wird bereits strukturiert.",
    "This thought-stream version is already being structured.",
  ],
  capacity_unavailable: [
    "Die Strukturierung ist momentan nicht verfügbar.",
    "Structuring is unavailable right now.",
  ],
  brainstorm_is_solo: [
    "Brainstorming ist ein Solo-Modus ohne Teilnehmenden-Einwilligung.",
    "Brainstorm is a solo mode without participant consent.",
  ],
  brainstorm_microphone_only: [
    "Brainstorming akzeptiert ausschließlich deinen Mikrofon-Text.",
    "Brainstorm accepts microphone transcript only.",
  ],
  brainstorm_too_many_segments: [
    "Der Gedankenstrom enthält zu viele Abschnitte für eine vollständige Strukturierung.",
    "The thought stream contains too many sections for complete structuring.",
  ],
  brainstorm_complete_payload_required: [
    "Die Strukturierungsangaben fehlen. Starte den Abschluss im Companion erneut.",
    "Structuring details are missing. Start completion again in the companion.",
  ],
  brainstorm_cutoff_mismatch: [
    "Der bestätigte Gedankenstand ist nicht mehr aktuell. Starte den Abschluss erneut.",
    "The confirmed thought-stream cutoff is no longer current. Start completion again.",
  ],
};

function brainstormFailureMessage(errorCode: string | null, german: boolean): string {
  const localized = errorCode ? brainstormFailureMessages[errorCode] : undefined;
  if (localized) return localized[german ? 0 : 1];
  return german
    ? "Die Strukturierung konnte nicht abgeschlossen werden. Starte sie im Companion erneut."
    : "Structuring could not be completed. Start it again in the companion.";
}

function earlierTranscriptCopy(
  count: number,
  visibleCount: number,
  isBrainstorm: boolean,
  german: boolean,
): string {
  const formattedCount = count.toLocaleString(german ? "de-DE" : "en-US");
  if (german) {
    const stored = isBrainstorm
      ? count === 1 ? "früherer Gedanke bleibt" : "frühere Gedanken bleiben"
      : count === 1 ? "früheres Transkriptsegment bleibt" : "frühere Transkriptsegmente bleiben";
    return `${formattedCount} ${stored} gespeichert. Angezeigt werden die neuesten ${visibleCount}.`;
  }
  const stored = isBrainstorm
    ? count === 1 ? "earlier thought remains" : "earlier thoughts remain"
    : count === 1 ? "earlier transcript segment remains" : "earlier transcript segments remain";
  return `${formattedCount} ${stored} stored. Showing the latest ${visibleCount}.`;
}

function BrainstormResultPanel({
  receipt,
  loading,
  failedToLoad,
  german,
}: {
  receipt: LiveBrainstormReceipt | null;
  loading: boolean;
  failedToLoad: boolean;
  german: boolean;
}) {
  if (loading) {
    return (
      <div role="status" className="flex items-center gap-2 rounded-2xl bg-secondary/45 p-4 text-[0.75rem] text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        {german ? "Strukturierung wird geladen…" : "Loading brainstorm structure…"}
      </div>
    );
  }
  if (failedToLoad) {
    return (
      <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-4 text-[0.75rem] text-destructive">
        {german ? "Die private Brainstorm-Struktur konnte nicht geladen werden." : "The private brainstorm structure could not be loaded."}
      </div>
    );
  }
  if (!receipt) {
    return (
      <div className="rounded-2xl border border-dashed border-border p-4 text-[0.75rem] leading-relaxed text-muted-foreground">
        {german
          ? "Noch keine Struktur vorhanden. Beende die Session im Companion mit „Stoppen & strukturieren“."
          : "No structure yet. End the session in the companion with “Stop & structure”."}
      </div>
    );
  }
  if (receipt.status === "pending") {
    return (
      <div role="status" className="rounded-2xl border border-moss/25 bg-moss-surface/8 p-4">
        <p className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
          <Loader2 className="size-3.5 animate-spin text-moss" />
          {german ? "Gedanken werden strukturiert…" : "Structuring your thoughts…"}
        </p>
        <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
          {german
            ? "Die konfigurierte API verarbeitet den vollständig eingefrorenen Gedankenstrom. Du kannst diese Seite schließen und später zurückkommen."
            : "The configured API is processing the complete frozen thought stream. You can close this page and return later."}
        </p>
      </div>
    );
  }
  if (receipt.status === "failed" || !receipt.result) {
    return (
      <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-4">
        <p className="flex items-center gap-2 text-[0.75rem] font-medium text-destructive">
          <XCircle className="size-3.5" /> {german ? "Strukturierung fehlgeschlagen" : "Structuring failed"}
        </p>
        <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
          {brainstormFailureMessage(receipt.error_code, german)}
        </p>
      </div>
    );
  }

  const result = receipt.result;
  return (
    <div className="space-y-4" data-brainstorm-result>
      <div className="rounded-2xl border border-moss/25 bg-moss-surface/8 p-4">
        <p className="font-mono text-[0.59375rem] uppercase tracking-[0.16em] text-moss">
          {german ? "Zusammenfassung" : "Summary"}
        </p>
        <p className="mt-2 text-[0.8125rem] leading-relaxed text-foreground/90">{result.summary}</p>
      </div>

      {(result.themes.length > 0 || result.ideas.length > 0) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {result.themes.length > 0 && (
            <section className="rounded-2xl bg-secondary/45 p-4" aria-label={german ? "Themen" : "Themes"}>
              <h4 className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
                <BrainCircuit className="size-3.5 text-moss" /> {german ? "Themen" : "Themes"}
              </h4>
              <div className="mt-3 space-y-3">
                {result.themes.map((theme, index) => (
                  <div key={`${theme.title}-${index}`}>
                    <p className="text-[0.6875rem] font-medium text-foreground">{theme.title}</p>
                    <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted-foreground">{theme.description}</p>
                  </div>
                ))}
              </div>
            </section>
          )}
          {result.ideas.length > 0 && (
            <section className="rounded-2xl bg-secondary/45 p-4" aria-label={german ? "Ideen" : "Ideas"}>
              <h4 className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
                <Lightbulb className="size-3.5 text-moss" /> {german ? "Ideen" : "Ideas"}
              </h4>
              <div className="mt-3 space-y-3">
                {result.ideas.map((idea, index) => (
                  <div key={`${idea.title}-${index}`}>
                    <p className="text-[0.6875rem] font-medium text-foreground">{idea.title}</p>
                    <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted-foreground">{idea.description}</p>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {(result.open_questions.length > 0 || result.decisions.length > 0) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {result.open_questions.length > 0 && (
            <section className="rounded-2xl border border-border p-4" aria-label={german ? "Offene Fragen" : "Open questions"}>
              <h4 className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
                <CircleHelp className="size-3.5 text-moss" /> {german ? "Offene Fragen" : "Open questions"}
              </h4>
              <ul className="mt-3 space-y-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                {result.open_questions.map((question, index) => <li key={`${question}-${index}`}>• {question}</li>)}
              </ul>
            </section>
          )}
          {result.decisions.length > 0 && (
            <section className="rounded-2xl border border-border p-4" aria-label={german ? "Entscheidungen" : "Decisions"}>
              <h4 className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
                <CheckCircle2 className="size-3.5 text-moss" /> {german ? "Entscheidungen" : "Decisions"}
              </h4>
              <ul className="mt-3 space-y-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                {result.decisions.map((decision, index) => <li key={`${decision}-${index}`}>• {decision}</li>)}
              </ul>
            </section>
          )}
        </div>
      )}

      {result.next_steps.length > 0 && (
        <section className="rounded-2xl border border-border p-4" aria-label={german ? "Nächste Schritte" : "Next steps"}>
          <h4 className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
            <ListChecks className="size-3.5 text-moss" /> {german ? "Nächste Schritte" : "Next steps"}
          </h4>
          <ol className="mt-3 space-y-2">
            {result.next_steps.map((step, index) => (
              <li key={`${step.action}-${index}`} className="flex gap-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                <span className="font-mono text-moss">{index + 1}.</span>
                <span>{step.action}{step.owner ? ` · ${step.owner}` : ""}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {result.evidence.length > 0 && (
        <section className="rounded-2xl bg-secondary/35 p-4" aria-label={german ? "Belege" : "Evidence"}>
          <h4 className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
            <Quote className="size-3.5 text-moss" /> {german ? "Belege aus deinem Gedankenstrom" : "Evidence from your thought stream"}
          </h4>
          <div className="mt-3 max-h-52 space-y-2 overflow-y-auto pr-1">
            {result.evidence.map((evidence, index) => (
              <blockquote key={`${evidence.segment_id}-${evidence.kind}-${evidence.index}-${index}`} className="border-l border-moss/35 pl-3">
                <p className="text-[0.6875rem] leading-relaxed text-foreground/80">“{evidence.quote}”</p>
                <footer className="mt-1 font-mono text-[0.5625rem] uppercase tracking-[0.1em] text-muted-foreground">
                  {evidenceKindLabel(evidence.kind, german)} · {evidence.segment_id}
                </footer>
              </blockquote>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function SessionCard({ session, onDelete, focused = false }: { session: LiveSession; onDelete: () => void; focused?: boolean }) {
  const queryClient = useQueryClient();
  const { me } = useAuth();
  const german = me?.language === "de";
  const isBrainstorm = session.purpose === "brainstorm";
  const isCompletedConversation = !isBrainstorm && session.status === "completed";
  const transcriptHref =
    isCompletedConversation && session.interview_id
      ? `/interviews/${session.interview_id}`
      : null;
  const titleId = `live-session-title-${session.id}`;
  const [manuallyExpanded, setManuallyExpanded] = useState(false);
  const eventCursor = useRef(0);
  const [eventLedger, setEventLedger] = useState<{
    events: LiveSessionEvent[];
    earlierStoredSegmentCount: number;
  }>({ events: [], earlierStoredSegmentCount: 0 });
  const expanded =
    session.status === "recording" ||
    (!isCompletedConversation && (focused || manuallyExpanded));
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (session.status !== "recording") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [session.status]);
  const events = useQuery({
    queryKey: ["live-session-events", session.id],
    queryFn: () => api.liveSessionEvents(session.id, eventCursor.current),
    enabled: expanded || session.status === "recording",
    refetchInterval: (query) =>
      query.state.data?.has_more ? 50 : session.status === "recording" ? 1_000 : false,
  });
  useEffect(() => {
    const page = events.data;
    if (!page) return;
    queryClient.setQueriesData<LiveSessionPage>({ queryKey: ["live-sessions"] }, (current) =>
      current
        ? {
            ...current,
            sessions: current.sessions.map((item) =>
              item.id === page.session.id && page.session.revision >= item.revision
                ? page.session
                : item,
            ),
          }
        : current,
    );
    const incomingSegments = page.events.filter(
      (event) =>
        event.type === "segment" &&
        (!isBrainstorm || event.payload.channel === "microphone"),
    );
    setEventLedger((current) => {
      let visibleEvents = current.events;
      if (incomingSegments.length > 0) {
        const bySequence = new Map(
          current.events.map((event) => [event.sequence, event]),
        );
        for (const event of incomingSegments) bySequence.set(event.sequence, event);
        visibleEvents = [...bySequence.values()]
          .sort((left, right) => left.sequence - right.sequence)
          .slice(-MAX_RENDERED_LIVE_SEGMENTS);
      }
      const earlierStoredSegmentCount = page.has_more
        ? 0
        : Math.max(0, page.session.segment_count - visibleEvents.length);
      if (
        visibleEvents === current.events &&
        earlierStoredSegmentCount === current.earlierStoredSegmentCount
      ) return current;
      return { events: visibleEvents, earlierStoredSegmentCount };
    });
    if (page.cursor > eventCursor.current) eventCursor.current = page.cursor;
  }, [events.data, isBrainstorm, queryClient]);
  const asks = useQuery({
    queryKey: ["live-session-asks", session.id],
    queryFn: () => api.liveSessionAsks(session.id),
    enabled: expanded && !isBrainstorm && session.ask_count > 0,
    refetchInterval: session.status === "recording" && expanded ? 2_000 : false,
  });
  const brainstorms = useQuery({
    queryKey: ["live-session-brainstorms", session.id],
    queryFn: () => api.liveSessionBrainstorms(session.id),
    enabled: expanded && isBrainstorm,
    refetchInterval: (query) =>
      newestBrainstorm(query.state.data?.brainstorms ?? [])?.status === "pending"
        ? 1_000
        : false,
  });
  const eventSegments = eventLedger.events
    .map(asSegment)
    .filter((segment): segment is LiveTranscriptSegment =>
      segment !== null && (!isBrainstorm || segment.channel === "microphone"),
    );
  const askHistory = asks.data ?? [];
  const latestBrainstorm = newestBrainstorm(brainstorms.data?.brainstorms ?? []);
  const elapsed =
    session.status === "recording"
      ? Math.max(session.duration_ms, now - new Date(session.started_at).getTime())
      : session.duration_ms;
  const meta = (
    isBrainstorm
      ? german ? brainstormStatusMetaDe : brainstormStatusMetaEn
      : german ? statusMetaDe : statusMetaEn
  )[session.status];

  return (
    <article
      data-live-session-card
      data-live-session-purpose={session.purpose}
      data-live-session-focused={focused ? "true" : undefined}
      ref={(node) => {
        if (node && focused) node.scrollIntoView({ block: "center", behavior: "smooth" });
      }}
      className={cn(
        "group relative overflow-hidden rounded-3xl border bg-card transition-all",
        focused && "ring-2 ring-moss/25",
        session.status === "recording"
          ? "border-moss/35"
          : "border-border",
        transcriptHref && "hover:-translate-y-0.5 hover:border-moss/40 hover:shadow-sm",
      )}
    >
      {transcriptHref && (
        <Link
          href={transcriptHref}
          aria-labelledby={titleId}
          className="absolute inset-0 z-10 rounded-3xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-moss/50"
        />
      )}
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-secondary">
            {isBrainstorm
              ? <BrainCircuit className="size-4 text-moss" />
              : <Headphones className="size-4 text-moss" />}
          </div>
          <StatusBadge session={session} german={german} />
        </div>
        <h2 id={titleId} className="mt-4 line-clamp-2 text-[0.9375rem] font-medium text-foreground">
          {session.title}
        </h2>
        <p className="mt-2 min-h-9 text-[0.71875rem] leading-relaxed text-muted-foreground">{meta.detail}</p>
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t border-border pt-3 font-mono text-[0.6875rem] text-muted-foreground">
          <span>{formatClock(elapsed)}</span>
          <span>
            {session.segment_count}{" "}
            {isBrainstorm ? (german ? "Gedanken" : "thoughts") : (german ? "Segmente" : "segments")}
          </span>
          {!isBrainstorm && session.ask_count > 0 && (
            <span>{session.ask_count} {german ? "Fragen" : "questions"}</span>
          )}
          <span className="truncate">{formatDate(session.created_at)}</span>
          {session.project?.name && <span className="truncate">{session.project.name}</span>}
        </div>

        {expanded && (
          <div className="mt-4 space-y-5 border-t border-border pt-4">
            <div>
              <p className="mb-3 flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                <MessageSquareText className="size-3 text-moss" />
                {isBrainstorm
                  ? (german ? "Gedankenstrom" : "Thought stream")
                  : (german ? "Live-Transkript" : "Live transcript")}
              </p>
              {events.isLoading ? (
                <div className="flex items-center gap-2 text-[0.75rem] text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" /> {german ? "Transkript wird geladen…" : "Loading transcript…"}
                </div>
              ) : eventSegments.length === 0 ? (
                <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
                  {isBrainstorm
                    ? (german ? "Finale Gedanken erscheinen, sobald der Companion deine Sprache erfasst hat." : "Final thoughts appear when the companion has captured your speech.")
                    : (german ? "Finale Sprecherbeiträge erscheinen, sobald der Companion Sprache erfasst hat." : "Final speaker turns appear when the companion has captured speech.")}
                </p>
              ) : (
                <>
                  {eventLedger.earlierStoredSegmentCount > 0 && (
                    <p
                      data-earlier-live-segments
                      className="mb-3 rounded-xl bg-secondary/45 px-3 py-2 text-[0.6875rem] leading-relaxed text-muted-foreground"
                    >
                      {earlierTranscriptCopy(
                        eventLedger.earlierStoredSegmentCount,
                        eventSegments.length,
                        isBrainstorm,
                        german,
                      )}
                    </p>
                  )}
                  <div className="max-h-72 space-y-3 overflow-y-auto pr-1" aria-live="polite">
                    {eventSegments.map((segment, index) => (
                      <div key={segment.id ?? `${segment.start_ms}-${index}`} className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-3">
                        <span className="pt-0.5 font-mono text-[0.625rem] text-muted-foreground">
                          {formatClock(segment.start_ms)}
                        </span>
                        <div className="min-w-0">
                          <p className="flex items-center gap-1.5 text-[0.6875rem] font-medium text-moss">
                            {segment.speaker}
                            <span className="font-normal text-muted-foreground/70">
                              · {isBrainstorm
                                ? (german ? "du" : "you")
                                : segment.channel === "microphone"
                                  ? (german ? "du" : "you")
                                  : (german ? "Systemaudio" : "system audio")}
                            </span>
                          </p>
                          <p className="mt-0.5 break-words text-[0.75rem] leading-relaxed text-foreground/85">
                            {segment.text}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
            {isBrainstorm && (
              <div className="border-t border-border pt-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                    <ListTree className="size-3 text-moss" /> {german ? "Struktur" : "Structure"}
                  </p>
                  <p className="flex items-center gap-1.5 text-[0.625rem] text-muted-foreground">
                    <LockKeyhole className="size-3 text-moss" />
                    {german ? "Nur für dich" : "Creator-private"}
                    {latestBrainstorm && ` · ${german ? "bis Segment" : "through segment"} ${latestBrainstorm.context_through_sequence}`}
                  </p>
                </div>
                <BrainstormResultPanel
                  receipt={latestBrainstorm}
                  loading={brainstorms.isLoading}
                  failedToLoad={brainstorms.isError}
                  german={german}
                />
              </div>
            )}
            {!isBrainstorm && askHistory.length > 0 && (
              <div className="border-t border-border pt-4">
                <p className="mb-3 flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  <MessageSquareText className="size-3 text-moss" /> {german ? "Fragenverlauf" : "Ask history"}
                </p>
                <div className="space-y-3">
                  {askHistory.map((ask) => (
                    <div key={ask.id} className="rounded-2xl bg-secondary/45 p-3">
                      <p className="text-[0.6875rem] font-medium text-foreground">{ask.question}</p>
                      <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
                        {ask.status === "failed"
                          ? userFacingStoredErrorMessage(
                              ask.error,
                              german
                                ? "Diese Frage konnte nicht beantwortet werden. Bitte versuche es erneut."
                                : "This question could not be answered. Please try again.",
                            )
                          : ask.answer}
                      </p>
                      {ask.transcript_sources.length > 0 && (
                        <p className="mt-2 font-mono text-[0.59375rem] text-moss">
                          {german ? "Transkript bei" : "Transcript at"} {ask.transcript_sources.map((source) => formatClock(source.start_ms)).join(", ")}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
          {!isCompletedConversation &&
            (session.status === "recording" || session.segment_count > 0 || session.ask_count > 0) && (
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full px-3"
              aria-expanded={expanded}
              onClick={() => setManuallyExpanded((value) => !value)}
            >
              {isBrainstorm
                ? expanded
                  ? (german ? "Brainstorming schließen" : "Hide brainstorm")
                  : (german ? "Brainstorming öffnen" : "Open brainstorm")
                : expanded
                  ? (german ? "Live-Ansicht schließen" : "Hide live view")
                  : (german ? "Live-Ansicht öffnen" : "Open live view")}
            </Button>
          )}
          {session.status !== "recording" && (
            <SessionCardMenu
              label={session.title}
              german={german}
              onDelete={onDelete}
            />
          )}
        </div>
      </div>
    </article>
  );
}

function SessionCardMenu({
  label,
  german,
  onDelete,
}: {
  label: string;
  german: boolean;
  onDelete: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={german ? `Aktionen für ${label}` : `Actions for ${label}`}
          className="relative z-20 ml-auto cursor-pointer rounded-full p-1.5 text-muted-foreground opacity-100 transition-opacity hover:bg-secondary hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss/45 sm:opacity-0 sm:group-hover:opacity-100 data-[state=open]:opacity-100"
        >
          <MoreHorizontal className="size-4" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="z-50 w-52 rounded-2xl">
        <DropdownMenuItem variant="destructive" onSelect={onDelete}>
          <Trash2 className="size-3.5" aria-hidden="true" />
          {german ? "Löschen" : "Delete"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function LiveSessionsPanel({
  projectFilter,
  focusedSessionId,
  sessionPurpose,
  onFocusedSessionOutsideScope,
  onFocusedSessionDeleted,
  pairRequest,
  onPairComplete,
}: {
  projectFilter: ProjectFilter;
  focusedSessionId?: string | null;
  sessionPurpose?: LiveSession["purpose"];
  onFocusedSessionOutsideScope?: (session: LiveSession) => void;
  onFocusedSessionDeleted?: () => void;
  pairRequest?: CompanionPairRequest | null;
  onPairComplete?: () => void;
}) {
  const queryClient = useQueryClient();
  const { me } = useAuth();
  const german = me?.language === "de";
  const [devicesOpen, setDevicesOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<LiveSession | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<LiveCompanionDevice | null>(null);

  useEffect(() => {
    const openDevices = () => setDevicesOpen(true);
    window.addEventListener("six:open-companion-devices", openDevices);
    return () => {
      window.removeEventListener("six:open-companion-devices", openDevices);
    };
  }, []);

  const config = useQuery({
    queryKey: ["live-session-config"],
    queryFn: api.liveSessionConfig,
  });
  const devices = useQuery({
    queryKey: ["live-companion-devices"],
    queryFn: api.liveCompanionDevices,
    enabled: devicesOpen,
    refetchOnWindowFocus: true,
  });
  const sessions = useQuery({
    queryKey: ["live-sessions", sessionPurpose ?? "all"],
    queryFn: () => api.liveSessions(100, sessionPurpose),
    refetchInterval: (query) => {
      const active = query.state.data?.sessions.some(
        (session) => session.status === "recording",
      );
      return active ? (config.data?.poll_interval_ms ?? 1_000) : 3_000;
    },
    refetchIntervalInBackground: false,
    refetchOnMount: "always",
    refetchOnReconnect: true,
    refetchOnWindowFocus: "always",
  });
  const focusedSession = useQuery({
    queryKey: ["live-session-focus", focusedSessionId],
    queryFn: () => api.liveSession(focusedSessionId!),
    enabled: Boolean(focusedSessionId),
    retry: false,
    refetchOnWindowFocus: false,
  });
  useEffect(() => {
    if (
      pairRequest ||
      !focusedSessionId ||
      !sessionPurpose ||
      !onFocusedSessionOutsideScope
    ) return;
    const confirmedSession =
      focusedSession.data ??
      sessions.data?.sessions.find((session) => session.id === focusedSessionId);
    if (confirmedSession && confirmedSession.purpose !== sessionPurpose) {
      onFocusedSessionOutsideScope(confirmedSession);
    }
  }, [
    focusedSessionId,
    focusedSession.data,
    onFocusedSessionOutsideScope,
    pairRequest,
    sessionPurpose,
    sessions.data?.sessions,
  ]);
  const remove = useMutation({
    mutationFn: (id: string) => api.liveSessionDelete(id),
    onSuccess: (_, deletedId) => {
      const removedBrainstorm = deleteTarget?.purpose === "brainstorm";
      queryClient.setQueriesData<LiveSessionPage>({ queryKey: ["live-sessions"] }, (current) =>
        current
          ? {
              ...current,
              sessions: current.sessions.filter((session) => session.id !== deletedId),
            }
          : current,
      );
      queryClient.removeQueries({
        queryKey: ["live-session-focus", deletedId],
        exact: true,
      });
      if (deletedId === focusedSessionId) onFocusedSessionDeleted?.();
      setDeleteTarget(null);
      toast.success(
        removedBrainstorm
          ? (german ? "Brainstorming gelöscht." : "Brainstorm deleted.")
          : (german ? "Live-Gespräch gelöscht." : "Live session deleted."),
      );
      void queryClient.invalidateQueries({ queryKey: ["live-sessions"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : deleteTarget?.purpose === "brainstorm"
            ? (german ? "Das Brainstorming konnte nicht gelöscht werden." : "Could not delete the brainstorm.")
            : (german ? "Das Gespräch konnte nicht gelöscht werden." : "Could not delete the session."),
      ),
  });
  const revokeDevice = useMutation({
    mutationFn: (id: number) => api.liveCompanionDeviceRevoke(id),
    onSuccess: () => {
      setRevokeTarget(null);
      toast.success(
        german
          ? "Der Companion-Zugriff wurde entzogen."
          : "Companion access revoked.",
      );
      void queryClient.invalidateQueries({ queryKey: ["live-companion-devices"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : german
            ? "Der Zugriff konnte nicht entzogen werden."
            : "Could not revoke access.",
      ),
  });
  const pair = useMutation({
    mutationFn: async () => {
      if (!pairRequest) throw new Error("The companion login request is missing.");
      const response = await api.liveCompanionPair({
        code_challenge: pairRequest.codeChallenge,
        state: pairRequest.state,
        ...(pairRequest.deviceName ? { device_name: pairRequest.deviceName } : {}),
      });
      const deepLink = safeCompanionDeepLink(response.deep_link, pairRequest.state);
      if (!deepLink || response.state !== pairRequest.state) {
        throw new Error("SixSentences returned an invalid companion callback.");
      }
      return deepLink;
    },
    onSuccess: (deepLink) => {
      onPairComplete?.();
      toast.success(german ? "Dieser Mac wird verbunden…" : "Connecting this Mac…");
      window.setTimeout(() => window.location.assign(deepLink), 0);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : (german ? "Die Verbindung ist fehlgeschlagen." : "Connection failed.")),
  });
  const availableSessions = useMemo(() => {
    const listed = sessions.data?.sessions ?? [];
    if (!focusedSession.data) return listed;
    const listedIds = new Set(listed.map((session) => session.id));
    return listedIds.has(focusedSession.data.id)
      ? listed.map((session) =>
          session.id === focusedSession.data?.id ? focusedSession.data : session,
        )
      : [focusedSession.data, ...listed];
  }, [focusedSession.data, sessions.data?.sessions]);
  const visibleSessions = useMemo(
    () =>
      availableSessions.filter(
        (session) =>
          (!sessionPurpose || session.purpose === sessionPurpose) &&
          (session.id === focusedSessionId ||
            (projectFilter === "all"
              ? true
              : projectFilter === "none"
                ? session.project_id === null
                : session.project_id === projectFilter)),
      ),
    [availableSessions, focusedSessionId, projectFilter, sessionPurpose],
  );
  const activeCount = visibleSessions.filter((session) => session.status === "recording").length;
  const completedCount = visibleSessions.filter((session) => session.status === "completed").length;
  const readyConversationCount = visibleSessions.filter(
    (session) => session.purpose === "conversation" && session.status === "completed",
  ).length;
  const brainstormCount = visibleSessions.filter((session) => session.purpose === "brainstorm").length;
  const brainstormView = sessionPurpose === "brainstorm";
  const conversationView = sessionPurpose === "conversation";
  return (
    <section
      className="mt-7"
      data-live-sessions-panel
      data-session-purpose={sessionPurpose ?? "all"}
    >
      <div
        className={cn(
          "grid grid-cols-2 gap-3",
          sessionPurpose ? "sm:grid-cols-3" : "sm:grid-cols-4",
        )}
      >
        <div className="rounded-2xl border border-border bg-card p-4">
          <p className="font-mono text-xl text-foreground">{visibleSessions.length}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {brainstormView
              ? german ? "Brainstormings" : "brainstorms"
              : conversationView
                ? german ? "Live-Gespräche" : "live conversations"
                : german ? "Companion-Sessions" : "companion sessions"}
          </p>
        </div>
        <div className="rounded-2xl border border-border bg-card p-4">
          <p className="font-mono text-xl text-foreground">{activeCount}</p>
          <p className="mt-1 text-xs text-muted-foreground">{german ? "jetzt aktiv" : "recording now"}</p>
        </div>
        {sessionPurpose ? (
          <div className="hidden rounded-2xl border border-border bg-card p-4 sm:block">
            <p className="font-mono text-xl text-foreground">{completedCount}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {german ? "abgeschlossen" : "completed"}
            </p>
          </div>
        ) : (
          <>
            <div className="hidden rounded-2xl border border-border bg-card p-4 sm:block">
              <p className="font-mono text-xl text-foreground">{readyConversationCount}</p>
              <p className="mt-1 text-xs text-muted-foreground">{german ? "Transkripte bereit" : "transcripts ready"}</p>
            </div>
            <div className="hidden rounded-2xl border border-border bg-card p-4 sm:block">
              <p className="font-mono text-xl text-foreground">{brainstormCount}</p>
              <p className="mt-1 text-xs text-muted-foreground">{german ? "Brainstormings" : "brainstorms"}</p>
            </div>
          </>
        )}
      </div>

      {config.isLoading || sessions.isLoading || focusedSession.isLoading ? (
        <div className="mt-8 flex justify-center py-20">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : visibleSessions.length === 0 ? (
        <div className="mt-8 rounded-3xl border border-border bg-card px-5 py-8 sm:px-8 sm:py-10">
          <div className="max-w-3xl">
            <div>
              <div className="grid size-12 place-items-center rounded-2xl bg-secondary">
                <Laptop className="size-5 text-moss" />
              </div>
              <h2 className="mt-4 font-serif text-2xl text-foreground">
                {brainstormView
                  ? german ? "Gedanken frei aussprechen und strukturieren" : "Speak freely and structure your thoughts"
                  : conversationView
                    ? german ? "Live-Gespräche direkt erfassen" : "Capture live conversations directly"
                    : german ? "Gespräche und Gedanken direkt erfassen" : "Capture conversations and thoughts directly"}
              </h2>
              <p className="mt-2 max-w-2xl text-[0.8125rem] leading-relaxed text-muted-foreground">
                {brainstormView
                  ? german
                    ? "Ein separat bereitgestellter, kompatibler Desktop-Client kann private Brainstormings vom Mikrofon an diesen Arbeitsbereich senden."
                    : "A separately supplied compatible desktop client can send private microphone brainstorms to this workspace."
                  : conversationView
                    ? german
                      ? "Ein separat bereitgestellter, kompatibler Desktop-Client kann nach Einwilligung transkribierte Live-Gespräche an diesen Arbeitsbereich senden."
                      : "A separately supplied compatible desktop client can send consented, transcribed live conversations to this workspace."
                    : german
                      ? "Diese Ansicht unterstützt Sessions aus einem separat bereitgestellten, kompatiblen Desktop-Client."
                      : "This view supports sessions from a separately supplied compatible desktop client."}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="mt-8 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-card px-4 py-3">
            <p className="text-[0.75rem] text-muted-foreground">
              {brainstormView
                ? german
                  ? "Brainstorming im Companion starten; Struktur und Ergebnisse erscheinen hier."
                  : "Start a brainstorm in the Companion; its structure and results appear here."
                : conversationView
                  ? german
                    ? "Live-Gespräch im Companion starten; Transkript und Status erscheinen hier."
                    : "Start a live conversation in the Companion; its transcript and status appear here."
                  : german
                    ? "Aufnahme im Companion starten; die Session erscheint im passenden Arbeitsbereich."
                    : "Start capture in the Companion; the session appears in its matching workspace."}
            </p>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {visibleSessions.map((session) => (
              <SessionCard
                key={session.id}
                session={session}
                focused={session.id === focusedSessionId}
                onDelete={() => setDeleteTarget(session)}
              />
            ))}
          </div>
        </>
      )}

      <Dialog
        open={Boolean(pairRequest)}
        onOpenChange={(open) => {
          if (!open && pairRequest) onPairComplete?.();
        }}
      >
        <DialogContent className="max-h-[min(92vh,54rem)] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">{german ? "Desktop-Client verbinden" : "Connect desktop client"}</DialogTitle>
            <DialogDescription>
              {german ? "Ein separat bereitgestellter, kompatibler Desktop-Client fordert Zugriff auf diesen Arbeitsbereich an." : "A separately supplied compatible desktop client is requesting access to this workspace."}
            </DialogDescription>
          </DialogHeader>
          {pairRequest && (
            <div className="rounded-2xl border border-moss/30 bg-moss-surface/8 p-4">
              <div className="flex items-start gap-3">
                <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-moss/12 text-moss">
                  <KeyRound className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[0.8125rem] font-medium text-foreground">
                    {pairRequest.deviceName
                      ? german
                        ? `${pairRequest.deviceName} mit deinem Konto verbinden`
                        : `Connect ${pairRequest.deviceName} to your account`
                      : german
                        ? "Diesen Mac mit deinem Konto verbinden"
                        : "Connect this Mac to your account"}
                  </p>
                  <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
                    {german
                      ? "Die Desktop-App erhält einen eingeschränkten Companion-Schlüssel. Sie bekommt keinen Zugriff auf dein Passwort und der einmalige Anmeldecode ist nur fünf Minuten gültig."
                      : "The desktop app receives a limited companion key. It never sees your password, and the one-time authorization code expires after five minutes."}
                  </p>
                  <Button
                    className="mt-3 rounded-full"
                    disabled={pair.isPending}
                    onClick={() => pair.mutate()}
                  >
                    {pair.isPending ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
                    {german ? "Diesen Mac verbinden" : "Connect this Mac"}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={devicesOpen} onOpenChange={setDevicesOpen}>
        <DialogContent
          showCloseButton={false}
          className="max-h-[min(92vh,46rem)] overflow-y-auto sm:max-w-xl"
        >
          <DialogHeader className="sr-only">
            <DialogTitle>{german ? "Verbundene Geräte" : "Connected devices"}</DialogTitle>
            <DialogDescription>
              {german
                ? "Verwalte Desktop Companions mit Zugriff auf dein Konto."
                : "Manage desktop companions with access to your account."}
            </DialogDescription>
          </DialogHeader>
          <section
            aria-labelledby="connected-companions-title"
            className="rounded-2xl border border-border bg-secondary/25 p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-3">
                <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-background text-moss shadow-sm ring-1 ring-border">
                  <Laptop className="size-4" />
                </div>
                <div className="min-w-0">
                  <h3
                    id="connected-companions-title"
                    className="text-[0.8125rem] font-medium text-foreground"
                  >
                    {german ? "Verbundene Geräte" : "Connected devices"}
                  </h3>
                  <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {german
                      ? "Companions mit Zugriff auf deine Live-Gespräche und privaten Brainstormings."
                      : "Companions with access to your live conversations and private brainstorms."}
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="rounded-full text-muted-foreground"
                  aria-label={german ? "Geräteliste aktualisieren" : "Refresh device list"}
                  disabled={devices.isFetching}
                  onClick={() => void devices.refetch()}
                >
                  <RefreshCw className={cn("size-3.5", devices.isFetching && "animate-spin")} />
                </Button>
                <DialogClose asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="rounded-full text-muted-foreground"
                    aria-label={german ? "Geräte schließen" : "Close devices"}
                  >
                    <X className="size-3.5" />
                  </Button>
                </DialogClose>
              </div>
            </div>

            {devices.isLoading ? (
              <div
                role="status"
                className="mt-4 flex items-center gap-2 rounded-xl bg-background/60 px-3 py-4 text-[0.75rem] text-muted-foreground"
              >
                <Loader2 className="size-3.5 animate-spin" />
                {german ? "Geräte werden geladen…" : "Loading devices…"}
              </div>
            ) : devices.isError ? (
              <div className="mt-4 rounded-xl border border-destructive/20 bg-destructive/5 px-3 py-3">
                <p className="text-[0.75rem] text-destructive">
                  {german
                    ? "Die verbundenen Geräte konnten nicht geladen werden."
                    : "Connected devices could not be loaded."}
                </p>
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="mt-1 h-auto p-0 text-[0.6875rem]"
                  onClick={() => void devices.refetch()}
                >
                  {german ? "Erneut versuchen" : "Try again"}
                </Button>
              </div>
            ) : (devices.data?.devices.length ?? 0) === 0 ? (
              <div className="mt-4 rounded-xl border border-dashed border-border bg-background/45 px-3 py-4">
                <p className="text-[0.75rem] font-medium text-foreground">
                  {german ? "Noch kein Gerät verbunden" : "No connected devices yet"}
                </p>
                <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  {german
                    ? "Öffne den Companion und verbinde diesen Mac über den Browser. Danach erscheint er hier."
                    : "Open the companion and connect this Mac through the browser. It will appear here afterwards."}
                </p>
              </div>
            ) : (
              <ul
                aria-label={german ? "Verbundene Companion-Geräte" : "Connected companion devices"}
                className="mt-4 max-h-64 space-y-2 overflow-y-auto pr-1"
              >
                {devices.data?.devices.map((device) => (
                  <li
                    key={device.id}
                    className="rounded-xl border border-border bg-background/70 p-3"
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <p className="flex min-w-0 items-center gap-2 text-[0.75rem] font-medium text-foreground">
                          <span
                            aria-hidden="true"
                            className="size-1.5 shrink-0 rounded-full bg-moss shadow-[0_0_0_3px_hsl(var(--moss)/0.1)]"
                          />
                          <span className="truncate">{device.name}</span>
                        </p>
                        <dl className="mt-2 grid gap-x-5 gap-y-1.5 text-[0.65625rem] text-muted-foreground sm:grid-cols-2">
                          <div>
                            <dt className="sr-only">{german ? "Verbunden seit" : "Connected since"}</dt>
                            <dd>
                              {german ? "Verbunden" : "Connected"} · {formatDate(device.created_at, german ? "de" : "en")}
                            </dd>
                          </div>
                          <div>
                            <dt className="sr-only">{german ? "Zuletzt aktiv" : "Last active"}</dt>
                            <dd>
                              {device.last_used_at
                                ? `${german ? "Zuletzt aktiv" : "Last active"} · ${formatDate(device.last_used_at, german ? "de" : "en")}`
                                : german
                                  ? "Zuletzt aktiv · noch nie"
                                  : "Last active · never"}
                            </dd>
                          </div>
                          <div>
                            <dt className="sr-only">{german ? "Schlüssel-Prefix" : "Key prefix"}</dt>
                            <dd className="flex items-center gap-1.5">
                              <span>Prefix</span>
                              <code
                                title={german ? "Nur die nicht-geheime Schlüsselkennung" : "Non-secret key identifier only"}
                                className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[0.625rem] text-foreground/75"
                              >
                                {device.prefix}
                              </code>
                            </dd>
                          </div>
                          <div>
                            <dt className="sr-only">{german ? "Ablaufdatum" : "Expiry date"}</dt>
                            <dd>
                              {device.expires_at
                                ? `${german ? "Läuft ab" : "Expires"} · ${formatDate(device.expires_at, german ? "de" : "en")}`
                                : german
                                  ? "Kein automatisches Ablaufdatum"
                                  : "No automatic expiry"}
                            </dd>
                          </div>
                        </dl>
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 shrink-0 self-start rounded-full px-2.5 text-[0.6875rem] text-destructive hover:bg-destructive/8 hover:text-destructive"
                        aria-label={
                          german
                            ? `Zugriff für ${device.name} entziehen`
                            : `Revoke access for ${device.name}`
                        }
                        onClick={() => setRevokeTarget(device)}
                      >
                        <ShieldOff className="size-3.5" />
                        {german ? "Zugriff entziehen" : "Revoke access"}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </DialogContent>
      </Dialog>

      <ConfirmDeleteDialog
        target={
          deleteTarget
            ? deleteTarget.purpose === "brainstorm"
              ? german
                ? {
                    title: "Dieses private Brainstorming löschen?",
                    description: "Der Gedankenstrom und alle dazugehörigen privaten Strukturierungen werden dauerhaft entfernt.",
                    action: "Brainstorming löschen",
                    cancel: "Behalten",
                  }
                : {
                    title: "Delete this private brainstorm?",
                    description: "The thought stream and all of its private structures will be permanently removed.",
                    action: "Delete brainstorm",
                    cancel: "Keep brainstorm",
                  }
              : german
                ? {
                    title: "Dieses Live-Gespräch löschen?",
                    description: "Der abgeschlossene oder beendete Eintrag wird entfernt. Ein bereits erzeugtes Interview-Transkript bleibt erhalten.",
                    action: "Gespräch löschen",
                    cancel: "Behalten",
                  }
                : {
                    title: "Delete this live session?",
                    description: "The completed or stopped session record will be removed. A materialized interview transcript is not deleted here.",
                    action: "Delete session",
                    cancel: "Keep session",
                  }
            : null
        }
        pending={remove.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && remove.mutate(deleteTarget.id)}
      />
      <ConfirmDeleteDialog
        target={
          revokeTarget
            ? german
              ? {
                  title: `Zugriff für ${revokeTarget.name} entziehen?`,
                  description:
                    "Der Companion verliert sofort den Zugriff auf Live-Gespräche. Du kannst das Gerät später erneut über den Browser verbinden.",
                  action: "Zugriff entziehen",
                  cancel: "Gerät behalten",
                }
              : {
                  title: `Revoke access for ${revokeTarget.name}?`,
                  description:
                    "The companion immediately loses access to live sessions. You can connect the device again through the browser later.",
                  action: "Revoke access",
                  cancel: "Keep device",
                }
            : null
        }
        pending={revokeDevice.isPending}
        onCancel={() => setRevokeTarget(null)}
        onConfirm={() => revokeTarget && revokeDevice.mutate(revokeTarget.id)}
      />
    </section>
  );
}
