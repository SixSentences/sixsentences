"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  ChevronDown,
  GitMerge,
  Layers3,
  Link2,
  Loader2,
  Network,
  Quote,
  RefreshCw,
  Unlink,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  createClientId,
  readProjectRetryIntent,
  readProjectSynthesisIntent,
  removeProjectRetryIntent,
  removeProjectSynthesisIntent,
  writeProjectRetryIntent,
  writeProjectSynthesisIntent,
} from "@/lib/brainstorming";
import { api, ApiError } from "@/lib/api";
import type {
  LiveSession,
  Project,
  ProjectBrainstormDocument,
  ProjectBrainstormEvidence,
  ProjectBrainstormResult,
  ProjectBrainstormSynthesisPage,
  ProjectBrainstormSynthesisReceipt,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const PROJECT_RECEIPT_LIMIT = 20;
const MAX_SELECTED_SESSIONS = 50;
const SOURCE_PAGE_SIZE = 12;

function projectErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== "object") return null;
  const code = (error.detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

function errorCopy(error: unknown, german: boolean): string {
  const code = projectErrorCode(error);
  if (code === "project_brainstorm_revision_conflict") {
    return german
      ? "Das Projektdokument wurde inzwischen geändert. Die aktuelle Version wurde geladen; prüfe die Auswahl und versuche es erneut."
      : "The project document changed in the meantime. Its current version was loaded; review the selection and retry.";
  }
  if (code === "project_brainstorm_session_scope_invalid" || code === "project_brainstorm_sources_unavailable") {
    return german
      ? "Mindestens ein ausgewähltes Brainstorming ist nicht mehr abgeschlossen oder gehört nicht mehr zu diesem Projekt."
      : "At least one selected brainstorm is no longer completed or no longer belongs to this project.";
  }
  if (code === "project_brainstorm_structure_required") {
    return german
      ? "Jede ausgewählte Session braucht zuerst eine abgeschlossene, belegte Einzelstruktur. Öffne die markierten Brainstormings und strukturiere fehlende Ergebnisse."
      : "Every selected session first needs a completed, grounded individual structure. Open the selected brainstorms and structure any missing results.";
  }
  if (code === "project_brainstorm_too_large" || code === "project_brainstorm_too_many_sessions") {
    return german
      ? "Die ausgewählten Quellen überschreiten die sichere Verarbeitungsgrenze. Wähle weniger Sessions und versuche es erneut."
      : "The selected sources exceed the safe processing bound. Select fewer sessions and retry.";
  }
  if (code === "project_brainstorm_in_progress") {
    return german
      ? "Für dieses Projekt läuft bereits eine Synthese. Der Status wird automatisch aktualisiert."
      : "A synthesis is already running for this project. Its status will update automatically.";
  }
  if (code === "capacity_unavailable") {
    return german
      ? "Die Projektsynthese ist gerade nicht verfügbar. Deine Auswahl bleibt erhalten."
      : "Project synthesis is unavailable right now. Your selection is preserved.";
  }
  return german
    ? "Die Projektsynthese konnte nicht bestätigt werden. Deine Auswahl und die bisherigen Dokumente bleiben erhalten."
    : "Project synthesis could not be confirmed. Your selection and existing documents remain available.";
}

function receiptFailureCopy(receipt: ProjectBrainstormSynthesisReceipt, german: boolean): string {
  if (receipt.error_code === "project_brainstorm_cancelled") {
    return german ? "Diese Projektsynthese wurde abgebrochen." : "This project synthesis was cancelled.";
  }
  if (receipt.error_code === "project_brainstorm_result_not_grounded") {
    return german
      ? "Die Verbindungen ließen sich nicht zuverlässig auf alle ausgewählten Rohgedanken zurückführen. Es wurde kein neues Projektdokument übernommen."
      : "The connections could not be grounded reliably in every selected raw thought. No new project document was accepted.";
  }
  if (receipt.error_code === "project_brainstorm_sources_unavailable") {
    return german
      ? "Mindestens eine eingefrorene Quelle ist nicht mehr verfügbar. Das bisherige Projektdokument bleibt unverändert."
      : "At least one frozen source is no longer available. The existing project document remains unchanged.";
  }
  if (receipt.error_code === "capacity_unavailable") {
    return german
      ? "Dieser Versuch konnte nicht verarbeitet werden. Die Quellen können später erneut verwendet werden."
      : "This attempt could not be processed. The sources can be retried later.";
  }
  return german
    ? "Die Projektsynthese wurde nicht abgeschlossen. Das bisherige Projektdokument bleibt unverändert."
    : "Project synthesis did not complete. The existing project document remains unchanged.";
}

function formatUpdated(value: string, german: boolean): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return german ? "Zeit unbekannt" : "Time unknown";
  return new Intl.DateTimeFormat(german ? "de-DE" : "en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(timestamp);
}

export function ProjectBrainstormWorkspace({
  userId,
  german,
  project,
  sessions,
  onOpenSession,
}: {
  userId: number;
  german: boolean;
  project: Pick<Project, "id" | "name">;
  sessions: LiveSession[];
  onOpenSession: (sessionId: string, segmentId?: string) => void;
}) {
  const queryClient = useQueryClient();
  const projectFenceRef = useRef(0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [includeAll, setIncludeAll] = useState(false);
  const [creating, setCreating] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [sourcePage, setSourcePage] = useState(0);
  const [outputLanguage, setOutputLanguage] = useState<"de" | "en">(german ? "de" : "en");
  const [uncertainCreate, setUncertainCreate] = useState(false);

  const documentQuery = useQuery({
    queryKey: ["project-brainstorm-document", userId, project.id],
    queryFn: ({ signal }) => api.liveProjectBrainstormDocument(project.id, signal),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    refetchOnReconnect: true,
    refetchOnWindowFocus: true,
  });
  const syntheses = useQuery({
    queryKey: ["project-brainstorm-syntheses", userId, project.id, PROJECT_RECEIPT_LIMIT, 0],
    queryFn: ({ signal }) => api.liveProjectBrainstormSyntheses(
      project.id,
      PROJECT_RECEIPT_LIMIT,
      0,
      signal,
    ),
    refetchInterval: (query) => query.state.data?.syntheses.some((item) => item.status === "pending")
      ? 2_000
      : false,
    refetchIntervalInBackground: false,
    refetchOnReconnect: true,
    refetchOnWindowFocus: true,
  });

  const document = documentQuery.data ?? null;
  const receipts = syntheses.data?.syntheses ?? [];
  const latestReceipt = receipts[0] ?? null;
  const pendingReceipt = receipts.find((receipt) => receipt.status === "pending") ?? null;
  const completedSessions = useMemo(
    () => sessions.filter((session) => (
      session.purpose === "brainstorm"
      && session.project_id === project.id
      && session.status === "completed"
    )),
    [project.id, sessions],
  );
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const sourcePageCount = Math.max(1, Math.ceil(completedSessions.length / SOURCE_PAGE_SIZE));
  const visibleSourceSessions = completedSessions.slice(
    sourcePage * SOURCE_PAGE_SIZE,
    (sourcePage + 1) * SOURCE_PAGE_SIZE,
  );

  useEffect(() => {
    projectFenceRef.current += 1;
    setActionError(null);
    const stored = readProjectSynthesisIntent(userId, project.id);
    setIncludeAll(stored?.request.include_all_completed ?? false);
    setSelectedIds(stored?.request.session_ids ?? []);
    setOutputLanguage(stored?.request.output_language ?? (german ? "de" : "en"));
    setUncertainCreate(Boolean(stored));
    setSourcePage(0);
  }, [german, project.id, userId]);

  useEffect(() => {
    if (sourcePage < sourcePageCount) return;
    setSourcePage(sourcePageCount - 1);
  }, [sourcePage, sourcePageCount]);

  useEffect(() => {
    if (latestReceipt?.status !== "completed") return;
    void queryClient.invalidateQueries({
      queryKey: ["project-brainstorm-document", userId, project.id],
    });
  }, [latestReceipt?.id, latestReceipt?.status, project.id, queryClient, userId]);

  useEffect(() => {
    const stored = readProjectSynthesisIntent(userId, project.id);
    if (!stored) return;
    if (receipts.some((receipt) => receipt.client_request_id === stored.request.client_request_id)) {
      removeProjectSynthesisIntent(userId, project.id);
      setUncertainCreate(false);
    }
  }, [project.id, receipts, userId]);

  useEffect(() => {
    for (const receipt of receipts) {
      if (!receipt.retry_of_id) continue;
      const stored = readProjectRetryIntent(userId, project.id, receipt.retry_of_id);
      if (stored?.request.client_request_id === receipt.client_request_id) {
        removeProjectRetryIntent(userId, project.id, receipt.retry_of_id);
      }
    }
  }, [project.id, receipts, userId]);

  const setReceipt = (receipt: ProjectBrainstormSynthesisReceipt) => {
    queryClient.setQueryData<ProjectBrainstormSynthesisPage>(
      ["project-brainstorm-syntheses", userId, project.id, PROJECT_RECEIPT_LIMIT, 0],
      (current) => ({
        syntheses: [
          receipt,
          ...(current?.syntheses ?? []).filter((item) => item.id !== receipt.id),
        ].slice(0, PROJECT_RECEIPT_LIMIT),
        total: current?.syntheses.some((item) => item.id === receipt.id)
          ? (current.total ?? current.syntheses.length)
          : (current?.total ?? 0) + 1,
      }),
    );
  };

  const toggleSession = (sessionId: string, checked: boolean) => {
    if (creating || pendingReceipt || uncertainCreate) return;
    setIncludeAll(false);
    setSelectedIds((current) => {
      if (!checked) return current.filter((id) => id !== sessionId);
      if (current.includes(sessionId) || current.length >= MAX_SELECTED_SESSIONS) return current;
      return [...current, sessionId];
    });
    setActionError(null);
  };

  const createSynthesis = async () => {
    if (creating || pendingReceipt || (!includeAll && selectedIds.length < 2)) return;
    const fence = projectFenceRef.current;
    setCreating(true);
    setActionError(null);
    try {
      const stored = readProjectSynthesisIntent(userId, project.id);
      const request = stored?.request ?? {
        client_request_id: createClientId("structure"),
        output_language: outputLanguage,
        session_ids: includeAll ? [] : selectedIds,
        include_all_completed: includeAll,
        expected_document_revision: document?.revision ?? 0,
        schema_version: 1 as const,
      };
      if (!stored && !writeProjectSynthesisIntent({
        version: 1,
        user_id: userId,
        project_id: project.id,
        request,
      })) {
        setActionError(german
          ? "Der sichere Retry-Beleg konnte in diesem Browser nicht gespeichert werden. Es wurde nichts gesendet."
          : "The safe retry receipt could not be stored in this browser. Nothing was sent.");
        return;
      }
      const receipt = await api.liveProjectBrainstormSynthesisCreate(project.id, request);
      removeProjectSynthesisIntent(userId, project.id);
      setUncertainCreate(false);
      if (fence !== projectFenceRef.current) return;
      setReceipt(receipt);
    } catch (error) {
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (rejected) {
        removeProjectSynthesisIntent(userId, project.id);
        setUncertainCreate(false);
      } else {
        const stored = readProjectSynthesisIntent(userId, project.id);
        if (stored) {
          setIncludeAll(stored.request.include_all_completed);
          setSelectedIds(stored.request.session_ids);
          setOutputLanguage(stored.request.output_language);
          setUncertainCreate(true);
        }
      }
      if (fence !== projectFenceRef.current) return;
      setActionError(errorCopy(error, german));
      if (error instanceof ApiError && error.status === 409) {
        await Promise.allSettled([documentQuery.refetch(), syntheses.refetch()]);
      }
    } finally {
      if (fence === projectFenceRef.current) setCreating(false);
    }
  };

  const cancelSynthesis = async () => {
    if (!pendingReceipt || cancelling) return;
    const fence = projectFenceRef.current;
    setCancelling(true);
    setActionError(null);
    try {
      const receipt = await api.liveProjectBrainstormSynthesisCancel(project.id, pendingReceipt.id);
      if (fence !== projectFenceRef.current) return;
      setReceipt(receipt);
    } catch (error) {
      if (fence === projectFenceRef.current) setActionError(errorCopy(error, german));
    } finally {
      if (fence === projectFenceRef.current) setCancelling(false);
    }
  };

  const retrySynthesis = async (receipt: ProjectBrainstormSynthesisReceipt) => {
    if (retrying || pendingReceipt) return;
    const fence = projectFenceRef.current;
    setRetrying(true);
    setActionError(null);
    try {
      const stored = readProjectRetryIntent(userId, project.id, receipt.id);
      const request = stored?.request ?? {
        client_request_id: createClientId("structure"),
        expected_document_revision: document?.revision ?? 0,
      };
      if (!stored && !writeProjectRetryIntent({
        version: 1,
        user_id: userId,
        project_id: project.id,
        synthesis_id: receipt.id,
        request,
      })) {
        setActionError(german
          ? "Der sichere Retry-Beleg konnte in diesem Browser nicht gespeichert werden. Es wurde nichts gesendet."
          : "The safe retry receipt could not be stored in this browser. Nothing was sent.");
        return;
      }
      const next = await api.liveProjectBrainstormSynthesisRetry(project.id, receipt.id, request);
      removeProjectRetryIntent(userId, project.id, receipt.id);
      if (fence !== projectFenceRef.current) return;
      setReceipt(next);
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        removeProjectRetryIntent(userId, project.id, receipt.id);
      }
      if (fence !== projectFenceRef.current) return;
      setActionError(errorCopy(error, german));
      if (error instanceof ApiError && error.status === 409) {
        await Promise.allSettled([documentQuery.refetch(), syntheses.refetch()]);
      }
    } finally {
      if (fence === projectFenceRef.current) setRetrying(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5" data-project-brainstorm-workspace>
      <header className="rounded-3xl border border-moss/20 bg-moss-surface/8 p-5 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.16em] text-moss">
              <Network className="size-3.5" /> {german ? "Projektgedächtnis" : "Project memory"}
            </p>
            <h2 className="mt-2 font-display text-3xl text-foreground">{project.name}</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              {german
                ? "Ein lebendes Hauptdokument verbindet nur nachweislich zusammengehörige Brainstormings. Unabhängige Gedanken bleiben als eigene Cluster sichtbar."
                : "A living master document connects only demonstrably related brainstorms. Independent thoughts remain visible as separate clusters."}
            </p>
          </div>
          {document && (
            <p className="shrink-0 text-[0.6875rem] text-muted-foreground">
              <Check className="mr-1 inline size-3.5 text-moss" />
              {german ? "Aktualisiert" : "Updated"} {formatUpdated(document.updated_at, german)}
            </p>
          )}
        </div>
      </header>

      {document?.update_available && (
        <section role="status" className="rounded-2xl border border-amber-500/25 bg-amber-500/5 p-4">
          <p className="text-sm font-medium text-foreground">{german ? "Weitere Projektquellen nicht eingearbeitet" : "Project sources not yet incorporated"}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {german
              ? "Es sind abgeschlossene Projekt-Brainstormings verfügbar, die in dieser belegten Fassung nicht enthalten sind. Das sichtbare Hauptdokument bleibt unverändert, bis du in der Quellenauswahl bewusst eine neue Synthese startest."
              : "Completed project brainstorms are available that are not included in this grounded version. The visible master document remains unchanged until you deliberately start a new synthesis from the source selection."}
          </p>
        </section>
      )}

      <section aria-labelledby="project-brainstorm-sources" className="rounded-2xl border border-border bg-card p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 id="project-brainstorm-sources" className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Layers3 className="size-4 text-moss" /> {german ? "Brainstormings verbinden" : "Combine brainstorms"}
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {german
                ? "Wähle 2–50 abgeschlossene Sessions aus diesem Projekt. Rohgedanken und Einzelstrukturen bleiben unverändert."
                : "Select 2–50 completed sessions from this project. Raw thoughts and individual structures remain unchanged."}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <label htmlFor="project-brainstorm-output-language" className="sr-only">{german ? "Sprache des Projektdokuments" : "Project document language"}</label>
            <div className="relative">
              <select
                id="project-brainstorm-output-language"
                value={outputLanguage}
                onChange={(event) => setOutputLanguage(event.target.value === "de" ? "de" : "en")}
                disabled={creating || Boolean(pendingReceipt) || uncertainCreate}
                className="peer h-9 appearance-none rounded-full border border-border bg-background pl-3 pr-8 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto"
                aria-label={german ? "Sprache des Projektdokuments" : "Project document language"}
              >
                <option value="de">DE</option>
                <option value="en">EN</option>
              </select>
              <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden" />
            </div>
            <Button
              type="button"
              className="rounded-full"
              disabled={creating || Boolean(pendingReceipt) || documentQuery.isLoading || documentQuery.isError || syntheses.isLoading || syntheses.isError || (!includeAll && selectedIds.length < 2)}
              onClick={() => void createSynthesis()}
            >
              {creating ? <Loader2 className="size-4 animate-spin" /> : <GitMerge className="size-4" />}
              {includeAll
                ? (german ? "Alle verbinden" : "Combine all")
                : (german ? `${selectedIds.length} verbinden` : `Combine ${selectedIds.length}`)}
            </Button>
          </div>
        </div>

        {uncertainCreate && (
          <div role="status" className="mt-4 rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-xs leading-relaxed text-muted-foreground">
            {german
              ? "Der letzte Start wurde nicht bestätigt. Auswahl, Sprache und sichere Anfrage-ID sind eingefroren; erneutes Verbinden prüft genau dieselbe Anfrage."
              : "The previous start was not confirmed. Selection, language, and the safe request ID are frozen; combining again checks that exact same request."}
          </div>
        )}

        {completedSessions.length < 2 ? (
          <div className="mt-4 rounded-xl border border-dashed border-border p-4 text-xs leading-relaxed text-muted-foreground">
            {german
              ? "Für eine projektweite Synthese braucht es mindestens zwei abgeschlossene Brainstormings in diesem Projekt. Ordne bestehende Sessions im jeweiligen Detail einem Projekt zu oder starte eine neue Session."
              : "A project synthesis needs at least two completed brainstorms in this project. Assign existing sessions from their detail view or start a new session."}
          </div>
        ) : (
          <fieldset className="mt-4">
            <legend className="sr-only">{german ? "Quell-Brainstormings auswählen" : "Select source brainstorms"}</legend>
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-secondary/25 p-3 text-xs">
              <Checkbox
                checked={includeAll}
                disabled={creating || Boolean(pendingReceipt) || uncertainCreate}
                onCheckedChange={(checked) => {
                  setIncludeAll(checked === true);
                  if (checked === true) setSelectedIds([]);
                  setActionError(null);
                }}
                aria-label={german ? "Alle abgeschlossenen Brainstormings dieses Projekts verwenden" : "Use all completed brainstorms in this project"}
              />
              <span>
                <span className="block font-medium text-foreground">{german ? "Alle abgeschlossenen verwenden" : "Use all completed"}</span>
                <span className="mt-0.5 block text-muted-foreground">{german ? "Bezieht auch ältere Sessions außerhalb dieser kompakten Liste serverseitig ein." : "Also includes older sessions beyond this compact list on the server."}</span>
              </span>
            </label>
            <p className="mt-3 font-mono text-[0.5625rem] uppercase tracking-[0.1em] text-muted-foreground">
              {german
                ? `${completedSessions.length} neueste geladene abgeschlossene Sessions · Auswahl maximal ${MAX_SELECTED_SESSIONS}`
                : `${completedSessions.length} latest loaded completed sessions · select up to ${MAX_SELECTED_SESSIONS}`}
            </p>
            <div className={cn("mt-3 grid gap-2 lg:grid-cols-2", includeAll && "opacity-55")}>
              {visibleSourceSessions.map((item) => (
                <div key={item.id} className="flex items-start gap-3 rounded-xl border border-border p-3 hover:border-moss/30">
                  <Checkbox
                    id={`project-brainstorm-source-${item.id}`}
                    checked={selectedSet.has(item.id)}
                    disabled={includeAll || creating || Boolean(pendingReceipt) || uncertainCreate}
                    onCheckedChange={(checked) => toggleSession(item.id, checked === true)}
                    aria-label={`${german ? "Auswählen" : "Select"}: ${item.title}`}
                  />
                  <label htmlFor={`project-brainstorm-source-${item.id}`} className="min-w-0 flex-1 cursor-pointer">
                    <span className="block truncate text-xs font-medium text-foreground">{item.title}</span>
                    <span className="mt-1 block text-[0.6875rem] text-muted-foreground">{item.segment_count} {german ? "Rohgedanken" : "raw thoughts"}</span>
                  </label>
                  <button
                    type="button"
                    className="rounded-full p-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss"
                    onClick={() => onOpenSession(item.id)}
                    aria-label={german ? `${item.title} öffnen` : `Open ${item.title}`}
                  >
                    <ArrowRight className="size-3.5" />
                  </button>
                </div>
              ))}
            </div>
            {sourcePageCount > 1 && (
              <nav aria-label={german ? "Seiten der geladenen Brainstormings" : "Loaded brainstorm pages"} className="mt-3 flex items-center justify-between gap-3">
                <Button type="button" variant="ghost" size="sm" className="rounded-full" disabled={sourcePage === 0 || creating || Boolean(pendingReceipt)} onClick={() => setSourcePage((page) => Math.max(0, page - 1))}>{german ? "Zurück" : "Previous"}</Button>
                <span className="text-[0.6875rem] text-muted-foreground">{german ? "Seite" : "Page"} {sourcePage + 1}/{sourcePageCount}</span>
                <Button type="button" variant="ghost" size="sm" className="rounded-full" disabled={sourcePage >= sourcePageCount - 1 || creating || Boolean(pendingReceipt)} onClick={() => setSourcePage((page) => Math.min(sourcePageCount - 1, page + 1))}>{german ? "Weiter" : "Next"}</Button>
              </nav>
            )}
          </fieldset>
        )}
      </section>

      {pendingReceipt && (
        <section role="status" aria-live="polite" className="rounded-2xl border border-moss/25 bg-moss-surface/8 p-5">
          <p className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Loader2 className="size-4 animate-spin text-moss" />
            {german ? "Projektgedanken werden verbunden" : "Connecting project thoughts"}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            {german
              ? "Die eingefrorenen Quellen werden geprüft. Das bisherige Hauptdokument bleibt bis zu einem vollständig belegten Ergebnis unverändert."
              : "The frozen sources are being checked. The existing master document remains unchanged until a fully grounded result is ready."}
          </p>
          <Button type="button" variant="outline" size="sm" className="mt-4 rounded-full" disabled={cancelling} onClick={() => void cancelSynthesis()}>
            {cancelling ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
            {german ? "Synthese abbrechen" : "Cancel synthesis"}
          </Button>
        </section>
      )}

      {actionError && (
        <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-4 text-xs leading-relaxed text-muted-foreground">
          <p>{actionError}</p>
          <Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={() => { setActionError(null); void Promise.all([documentQuery.refetch(), syntheses.refetch()]); }}>
            <RefreshCw className="size-3.5" /> {german ? "Aktualisieren" : "Refresh"}
          </Button>
        </div>
      )}

      {latestReceipt?.status === "failed" && !pendingReceipt && (
        <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-5">
          <p className="text-sm font-medium text-destructive">{german ? "Letzte Synthese nicht übernommen" : "Latest synthesis was not accepted"}</p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{receiptFailureCopy(latestReceipt, german)}</p>
          <Button type="button" variant="outline" size="sm" className="mt-4 rounded-full" disabled={retrying} onClick={() => void retrySynthesis(latestReceipt)}>
            {retrying ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
            {german ? "Mit denselben Quellen erneut versuchen" : "Retry the same frozen sources"}
          </Button>
        </div>
      )}

      {documentQuery.isLoading ? (
        <div role="status" className="flex justify-center py-16 text-muted-foreground"><Loader2 className="size-5 animate-spin" /><span className="sr-only">{german ? "Projektdokument wird geladen" : "Loading project document"}</span></div>
      ) : documentQuery.isError && !(documentQuery.error instanceof ApiError && documentQuery.error.status === 404) ? (
        <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-5 text-sm text-muted-foreground">
          <p>{german ? "Das Projektdokument konnte nicht geladen werden." : "Could not load the project document."}</p>
          <Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={() => void documentQuery.refetch()}>{german ? "Erneut versuchen" : "Retry"}</Button>
        </div>
      ) : document?.result ? (
        <ProjectDocumentResult result={document.result} document={document} german={german} sessions={sessions} onOpenSession={onOpenSession} />
      ) : (
        <div className="rounded-3xl border border-dashed border-border bg-card/35 p-8 text-center">
          <Network className="mx-auto size-7 text-moss" />
          <h3 className="mt-4 font-serif text-2xl text-foreground">{german ? "Noch kein Hauptdokument" : "No master document yet"}</h3>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
            {german
              ? "Wähle mindestens zwei abgeschlossene Brainstormings. Die erste Synthese erstellt Cluster, belegte Verbindungen und bewusst getrennte Gedankenstränge."
              : "Select at least two completed brainstorms. The first synthesis creates clusters, grounded connections, and deliberately separate lines of thought."}
          </p>
        </div>
      )}

      {syntheses.isError && (
        <div role="alert" className="flex items-center justify-between gap-3 rounded-xl border border-destructive/20 bg-destructive/5 p-3 text-xs text-muted-foreground">
          <span>{german ? "Der Verlauf der Projektsynthesen konnte nicht geladen werden." : "Could not load the project synthesis history."}</span>
          <button type="button" className="font-medium text-foreground underline underline-offset-2" onClick={() => void syntheses.refetch()}>{german ? "Erneut" : "Retry"}</button>
        </div>
      )}
      {(syntheses.data?.total ?? 0) > PROJECT_RECEIPT_LIMIT && (
        <p className="text-center text-[0.6875rem] text-muted-foreground">
          {german ? `Angezeigt werden die neuesten ${PROJECT_RECEIPT_LIMIT} Synthesen.` : `Showing the latest ${PROJECT_RECEIPT_LIMIT} syntheses.`}
        </p>
      )}
    </div>
  );
}

function ProjectDocumentResult({
  result,
  document,
  german,
  sessions,
  onOpenSession,
}: {
  result: ProjectBrainstormResult;
  document: ProjectBrainstormDocument;
  german: boolean;
  sessions: LiveSession[];
  onOpenSession: (sessionId: string, segmentId?: string) => void;
}) {
  const unconnected = new Set(result.unconnected_cluster_ids);
  const sessionById = new Map(sessions.map((session) => [session.id, session]));
  return (
    <article aria-labelledby="project-document-title" className="space-y-5">
      <section className="rounded-3xl border border-border bg-card p-5 sm:p-7">
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-moss">{german ? "Hauptdokument" : "Master document"} · v{document.revision}</p>
        <h3 id="project-document-title" className="mt-2 font-display text-3xl text-foreground">{result.title}</h3>
        <p className="mt-4 text-sm leading-7 text-foreground/90">{result.summary}</p>
        <EvidenceList evidence={result.summary_evidence} german={german} onOpenSession={onOpenSession} />
      </section>

      {result.connections.length > 0 && (
        <section aria-labelledby="project-connections" className="rounded-2xl border border-moss/25 bg-moss-surface/8 p-5">
          <h3 id="project-connections" className="flex items-center gap-2 text-sm font-medium text-foreground"><Link2 className="size-4 text-moss" />{german ? "Belegte Verbindungen" : "Grounded connections"}</h3>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {result.connections.map((connection, index) => (
              <article key={`${connection.title}-${index}`} className="rounded-xl border border-moss/20 bg-background/70 p-4">
                <p className="text-sm font-medium text-foreground">{connection.title}</p>
                <p className="mt-2 text-xs leading-6 text-muted-foreground">{connection.description}</p>
                <p className="mt-3 font-mono text-[0.5625rem] uppercase tracking-[0.1em] text-moss">{connection.cluster_ids.join(" ↔ ")}</p>
                <EvidenceList evidence={connection.evidence} german={german} onOpenSession={onOpenSession} compact />
              </article>
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="project-document-clusters-heading">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h3 id="project-document-clusters-heading" className="flex items-center gap-2 text-sm font-medium text-foreground"><Layers3 className="size-4 text-moss" />{german ? "Gedankencluster" : "Thought clusters"}</h3>
            <p className="mt-1 text-xs text-muted-foreground">{german ? "Nähe wird nur dort gezeigt, wo Quellen sie tragen." : "Proximity is shown only where sources support it."}</p>
          </div>
          <span className="text-[0.6875rem] text-muted-foreground">{result.clusters.length} {german ? "Cluster" : "clusters"}</span>
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {result.clusters.map((cluster) => (
            <article key={cluster.id} className={cn("rounded-2xl border bg-card p-5", unconnected.has(cluster.id) ? "border-border" : "border-moss/20")}>
              <p className="flex items-center gap-2 font-mono text-[0.5625rem] uppercase tracking-[0.11em] text-muted-foreground">
                {unconnected.has(cluster.id) ? <Unlink className="size-3.5" /> : <Link2 className="size-3.5 text-moss" />}
                {unconnected.has(cluster.id) ? (german ? "Eigenständiger Strang" : "Independent thread") : (german ? "Verbunden" : "Connected")}
              </p>
              <h4 className="mt-2 text-base font-medium text-foreground">{cluster.title}</h4>
              <p className="mt-2 text-xs leading-6 text-muted-foreground">{cluster.summary}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {cluster.session_ids.map((sessionId) => (
                  <button key={sessionId} type="button" className="rounded-full border border-border bg-background px-2.5 py-1 text-[0.625rem] text-muted-foreground hover:border-moss/30 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss" onClick={() => onOpenSession(sessionId)}>
                    {sessionById.get(sessionId)?.title ?? sessionId}
                  </button>
                ))}
              </div>
              <EvidenceList evidence={cluster.evidence} german={german} onOpenSession={onOpenSession} />
            </article>
          ))}
        </div>
      </section>

      {result.unconnected_cluster_ids.length > 0 && (
        <div className="rounded-2xl border border-border bg-secondary/25 p-4 text-xs leading-relaxed text-muted-foreground">
          <p className="flex items-center gap-2 font-medium text-foreground"><Unlink className="size-4" />{german ? "Bewusst nicht verbunden" : "Deliberately unconnected"}</p>
          <p className="mt-1">{german ? "Diese Stränge passen nach aktueller Beleglage nicht sauber zu den übrigen Ideen. Sie bleiben vollständig erhalten, ohne eine künstliche Beziehung zu behaupten." : "Current evidence does not cleanly connect these threads to the other ideas. They remain fully preserved without claiming an artificial relationship."}</p>
        </div>
      )}

      {document.manual_markdown.trim() && (
        <section aria-labelledby="project-manual-notes" className="rounded-2xl border border-border bg-card p-5">
          <h3 id="project-manual-notes" className="text-sm font-medium text-foreground">{german ? "Eigene Projektnotizen" : "Your project notes"}</h3>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-foreground/90">{document.manual_markdown}</p>
        </section>
      )}
    </article>
  );
}

function EvidenceList({
  evidence,
  german,
  onOpenSession,
  compact = false,
}: {
  evidence: ProjectBrainstormEvidence[];
  german: boolean;
  onOpenSession: (sessionId: string, segmentId?: string) => void;
  compact?: boolean;
}) {
  if (evidence.length === 0) return null;
  return (
    <details className="mt-4 rounded-xl border border-border/70 bg-background/65">
      <summary className="cursor-pointer px-3 py-2 text-[0.6875rem] font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss">
        <Quote className="mr-1.5 inline size-3.5 text-moss" />{german ? "Quellen anzeigen" : "Show sources"} ({evidence.length})
      </summary>
      <div className={cn("space-y-2 border-t border-border/70 p-2", !compact && "sm:p-3")}>
        {evidence.map((item, index) => (
          <button key={`${item.session_id}-${item.segment_id}-${index}`} type="button" className="block w-full rounded-lg p-2 text-left hover:bg-secondary/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss" onClick={() => onOpenSession(item.session_id, item.segment_id)}>
            <blockquote className="text-xs leading-relaxed text-foreground/85">“{item.quote}”</blockquote>
            <span className="mt-1 block font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted-foreground">{item.session_id} · {item.segment_id}</span>
          </button>
        ))}
      </div>
    </details>
  );
}
