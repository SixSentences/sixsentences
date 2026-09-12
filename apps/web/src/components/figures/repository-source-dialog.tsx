"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CircleStop,
  ExternalLink,
  FileCode2,
  GitBranch,
  GitFork,
  KeyRound,
  Loader2,
  LockKeyhole,
  Network,
  PencilLine,
  Plus,
  RefreshCcw,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { RepositoryManuscriptPanel } from "@/components/figures/repository-manuscript-panel";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type {
  RepositoryAnalysis,
  RepositoryAnalysisCreate,
  RepositoryAnalysisStatus,
  RepositoryConnection,
  RepositoryDiagramKind,
  RepositoryEvidence,
  GithubRepositoryConnectionCreate,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { userFacingStoredErrorMessage } from "@/lib/user-facing-error";

const STORAGE_VERSION = 2;
const STORAGE_PREFIX = "six:repository-analysis:";
const MOVING_STATUSES: RepositoryAnalysisStatus[] = [
  "queued",
  "fetching",
  "analyzing",
];
const TERMINAL_STATUSES: RepositoryAnalysisStatus[] = [
  "ready",
  "error",
  "cancelled",
  "needs_scope",
];
const GITHUB_REPOSITORY =
  /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?\/?$/;
const COMMIT_SHA = /^[0-9a-f]{40}$/i;

function repositoryIdentityKey(repositoryUrl: string): string | null {
  const candidate = repositoryUrl.trim();
  if (!GITHUB_REPOSITORY.test(candidate)) return null;
  try {
    const parsed = new URL(candidate);
    return parsed.pathname
      .replace(/\/$/, "")
      .replace(/\.git$/i, "")
      .toLowerCase();
  } catch {
    return null;
  }
}

type RepositoryIntent = {
  request_id: string;
  repository_url: string;
  repository_connection_id: string | null;
  ref: string;
  subpath: string;
  diagram_kind: RepositoryDiagramKind;
  language: "en" | "de";
};

type RepositoryAnalysisBinding = Readonly<{
  requestId: string;
  normalizedGoal: string;
  projectId: number | null;
  settingsFingerprint: string;
}>;

type RepositoryAnalysisSubmission = RepositoryAnalysisBinding & Readonly<{
  body: Readonly<RepositoryAnalysisCreate>;
}>;

function repositorySettingsFingerprint(intent: RepositoryIntent): string {
  return JSON.stringify([
    intent.repository_url.trim(),
    intent.repository_connection_id,
    intent.ref.trim(),
    intent.subpath.trim().replace(/^\.\//, ""),
    intent.diagram_kind,
    intent.language,
  ]);
}

function repositoryAnalysisBindingMatches(
  submitted: RepositoryAnalysisBinding,
  current: RepositoryAnalysisBinding,
): boolean {
  return submitted.requestId === current.requestId
    && submitted.normalizedGoal === current.normalizedGoal
    && submitted.projectId === current.projectId
    && submitted.settingsFingerprint === current.settingsFingerprint;
}

type StoredRepositoryRequest = Omit<RepositoryIntent, "repository_url"> & {
  repository_url?: string;
};

type StoredRepositoryIntent = {
  schema_version: typeof STORAGE_VERSION;
  request: StoredRepositoryRequest;
  analysis_id?: string;
  analysis_request_id?: string;
};

type HydratedRepositoryIntent = Omit<StoredRepositoryIntent, "request"> & {
  request: RepositoryIntent;
};

type RepositorySourceDialogProps = {
  userId: number;
  userLanguage: "en" | "de";
  projectId: number | null;
  activeAnalysis: RepositoryAnalysis | null;
  requestedAnalysisId?: string | null;
  analysisGoal: string;
  onEditGoal: () => void;
  onUseAnalysis: (analysis: RepositoryAnalysis) => void;
  onAnalysisDeleted?: (analysisId: string) => void;
};

function createRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16);
    return (token === "x" ? value : (value & 0x3) | 0x8).toString(16);
  });
}

function storageKey(userId: number): string {
  return `${STORAGE_PREFIX}${userId}:intent`;
}

function emptyIntent(language: "en" | "de"): RepositoryIntent {
  return {
    request_id: createRequestId(),
    repository_url: "",
    repository_connection_id: null,
    ref: "",
    subpath: "",
    diagram_kind: "architecture",
    language,
  };
}

function readStoredIntent(
  userId: number,
  language: "en" | "de",
): HydratedRepositoryIntent {
  if (typeof window === "undefined") {
    return { schema_version: STORAGE_VERSION, request: emptyIntent(language) };
  }
  try {
    const raw = window.sessionStorage.getItem(storageKey(userId));
    if (!raw) throw new Error("missing");
    const value = JSON.parse(raw) as Partial<StoredRepositoryIntent>;
    const request = value.request as Partial<StoredRepositoryRequest> | undefined;
    if (
      value.schema_version !== STORAGE_VERSION
      || !request
      || typeof request.request_id !== "string"
      || !(
        request.repository_connection_id === null
        || typeof request.repository_connection_id === "string"
      )
      || (
        request.repository_connection_id === null
        && typeof request.repository_url !== "string"
      )
      || (
        request.repository_url !== undefined
        && typeof request.repository_url !== "string"
      )
      || typeof request.ref !== "string"
      || typeof request.subpath !== "string"
      || !["architecture", "flow", "deployment", "module"].includes(
        String(request.diagram_kind),
      )
      || !["en", "de"].includes(String(request.language))
    ) {
      throw new Error("invalid");
    }
    return {
      schema_version: STORAGE_VERSION,
      request: {
        ...(request as Omit<RepositoryIntent, "repository_url">),
        repository_url: request.repository_url ?? "",
      },
      ...(typeof value.analysis_id === "string"
        ? { analysis_id: value.analysis_id }
        : {}),
      ...(typeof value.analysis_request_id === "string"
        ? { analysis_request_id: value.analysis_request_id }
        : {}),
    };
  } catch {
    window.sessionStorage.removeItem(storageKey(userId));
    return { schema_version: STORAGE_VERSION, request: emptyIntent(language) };
  }
}

function pathIsSafe(path: string): boolean {
  return Boolean(path)
    && !path.startsWith("/")
    && !path.includes("\\")
    && !path.split("/").includes("..")
    && !path.includes("\0");
}

function evidencePath(evidence: RepositoryEvidence): string {
  const candidate = evidence.path ?? evidence.source_path ?? "";
  return typeof candidate === "string" ? candidate : "";
}

function evidenceLines(evidence: RepositoryEvidence): [number | null, number | null] {
  const rawStart = evidence.start_line ?? evidence.line_start;
  const rawEnd = evidence.end_line ?? evidence.line_end;
  const start = Number.isInteger(rawStart) && Number(rawStart) > 0
    ? Number(rawStart)
    : null;
  const end = Number.isInteger(rawEnd) && Number(rawEnd) >= (start ?? 1)
    ? Number(rawEnd)
    : start;
  return [start, end];
}

export function immutableRepositoryEvidenceUrl(
  analysis: RepositoryAnalysis,
  evidence: RepositoryEvidence,
): string | null {
  const path = evidencePath(evidence);
  if (!analysis.commit_sha || !COMMIT_SHA.test(analysis.commit_sha) || !pathIsSafe(path)) {
    return null;
  }
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  const [start, end] = evidenceLines(evidence);
  const lineHash = start ? `#L${start}${end && end !== start ? `-L${end}` : ""}` : "";
  return `https://github.com/${encodeURIComponent(analysis.owner)}/${encodeURIComponent(
    analysis.name,
  )}/blob/${analysis.commit_sha}/${encodedPath}${lineHash}`;
}

function statusProgress(analysis: RepositoryAnalysis): number {
  if (analysis.coverage?.complete === true) return 100;
  return {
    queued: 8,
    fetching: 28,
    analyzing: 68,
    needs_scope: 76,
    ready: 100,
    error: 100,
    cancelled: 100,
  }[analysis.status];
}

function numberFromCoverage(
  analysis: RepositoryAnalysis,
  ...keys: string[]
): number | null {
  for (const key of keys) {
    const value = analysis.coverage?.[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function statusCopy(
  status: RepositoryAnalysisStatus,
  german: boolean,
): { label: string; detail: string } {
  const copies: Record<RepositoryAnalysisStatus, [string, string, string, string]> = {
    queued: ["Queued", "Waiting for an analysis worker.", "Eingereiht", "Wartet auf einen Analyse-Worker."],
    fetching: ["Fetching", "Pinning and reading the repository snapshot.", "Repository wird geladen", "Der Repository-Stand wird fixiert und eingelesen."],
    analyzing: ["Analyzing", "Building a source-grounded repository specification.", "Analyse läuft", "Eine quellengestützte Repository-Spezifikation wird aufgebaut."],
    needs_scope: ["Needs a narrower scope", "Choose a subpath or a more focused goal, then start a new analysis.", "Engerer Umfang nötig", "Wähle einen Unterpfad oder ein engeres Ziel und starte eine neue Analyse."],
    ready: ["Verified spec ready", "Review the canonical nodes, edges and source evidence before styling it.", "Verifizierte Spec bereit", "Prüfe die kanonischen Knoten, Kanten und Quellbelege vor der Gestaltung."],
    error: ["Analysis failed", "The repository was not used as figure grounding.", "Analyse fehlgeschlagen", "Das Repository wurde nicht als Figure-Grounding verwendet."],
    cancelled: ["Cancelled", "No figure was rendered from this analysis.", "Abgebrochen", "Aus dieser Analyse wurde keine Figure gerendert."],
  };
  const [enLabel, enDetail, deLabel, deDetail] = copies[status];
  return german ? { label: deLabel, detail: deDetail } : { label: enLabel, detail: enDetail };
}

export function RepositorySourceDialog({
  userId,
  userLanguage,
  projectId,
  activeAnalysis,
  requestedAnalysisId,
  analysisGoal,
  onEditGoal,
  onUseAnalysis,
  onAnalysisDeleted,
}: RepositorySourceDialogProps) {
  const queryClient = useQueryClient();
  const german = userLanguage === "de";
  const formId = useId();
  const stored = useMemo(
    () => readStoredIntent(userId, userLanguage),
    [userId, userLanguage],
  );
  const [open, setOpen] = useState(false);
  const [intent, setIntent] = useState<RepositoryIntent>(stored.request);
  const [analysisId, setAnalysisId] = useState<string | null>(
    stored.analysis_id ?? activeAnalysis?.public_id ?? null,
  );
  const [analysisRequestId, setAnalysisRequestId] = useState<string | null>(
    stored.analysis_request_id
      ?? (activeAnalysis ? `selected:${activeAnalysis.public_id}` : null),
  );
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [requestSubmitted, setRequestSubmitted] = useState(
    Boolean(
      activeAnalysis
      || (stored.analysis_id
        && (stored.analysis_request_id === stored.request.request_id
          || stored.analysis_request_id === `selected:${stored.analysis_id}`)),
    ),
  );
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [connectionDeleteTarget, setConnectionDeleteTarget] = useState<RepositoryConnection | null>(null);
  const [showPrivateConnection, setShowPrivateConnection] = useState(false);
  const [connectionToken, setConnectionToken] = useState("");
  const [credentialStorageConfirmed, setCredentialStorageConfirmed] = useState(false);
  const connectionRequestRef = useRef<GithubRepositoryConnectionCreate | null>(null);
  const previousGoalRef = useRef(analysisGoal.trim());
  const previousProjectRef = useRef(projectId);

  useEffect(() => {
    if (!requestedAnalysisId) return;
    setAnalysisId(requestedAnalysisId);
    setAnalysisRequestId(`selected:${requestedAnalysisId}`);
    setRequestSubmitted(true);
    setOpen(true);
  }, [requestedAnalysisId]);

  useEffect(() => {
    if (!open || !activeAnalysis || requestedAnalysisId) return;
    setAnalysisId(activeAnalysis.public_id);
    setAnalysisRequestId(`selected:${activeAnalysis.public_id}`);
    setRequestSubmitted(true);
  }, [activeAnalysis, open, requestedAnalysisId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const { repository_url: repositoryUrl, ...storedRequest } = intent;
    const value: StoredRepositoryIntent = {
      schema_version: STORAGE_VERSION,
      request: intent.repository_connection_id
        ? storedRequest
        : { ...storedRequest, repository_url: repositoryUrl },
      ...(analysisId ? { analysis_id: analysisId } : {}),
      ...(analysisRequestId ? { analysis_request_id: analysisRequestId } : {}),
    };
    try {
      window.sessionStorage.setItem(storageKey(userId), JSON.stringify(value));
    } catch {
      // A hardened browser may deny session storage. The form remains usable.
    }
  }, [analysisId, analysisRequestId, intent, userId]);

  const connections = useQuery({
    queryKey: ["repository-connections", userId],
    queryFn: api.repositoryConnections,
    enabled: open,
    retry: false,
  });

  const list = useQuery({
    queryKey: ["repository-analyses", userId],
    queryFn: api.repositoryAnalyses,
    enabled: open,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((analysis) =>
        MOVING_STATUSES.includes(analysis.status),
      )
        ? 4_000
        : false,
  });

  const current = useQuery({
    queryKey: ["repository-analysis", userId, analysisId],
    queryFn: () => api.repositoryAnalysis(analysisId as string),
    enabled: open && Boolean(analysisId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !TERMINAL_STATUSES.includes(status) ? 2_000 : false;
    },
    retry: false,
  });

  const analysis = current.data
    ?? list.data?.find((candidate) => candidate.public_id === analysisId)
    ?? (activeAnalysis?.public_id === analysisId ? activeAnalysis : null);

  const updateIntent = (patch: Partial<Omit<RepositoryIntent, "request_id">>) => {
    setIntent((value) => ({ ...value, ...patch, request_id: createRequestId() }));
    setAnalysisRequestId(null);
    setRequestSubmitted(false);
    setRightsConfirmed(false);
    create.reset();
  };

  const prepareNewAnalysis = () => {
    setIntent((value) => ({ ...value, request_id: createRequestId() }));
    setAnalysisId(null);
    setAnalysisRequestId(null);
    setRequestSubmitted(false);
    setRightsConfirmed(false);
    create.reset();
  };

  const repositoryError = intent.repository_url.trim()
    && !GITHUB_REPOSITORY.test(intent.repository_url.trim())
    ? german
      ? "Verwende eine vollständige GitHub-Repository-URL ohne Query oder Fragment."
      : "Use a complete GitHub repository URL without a query or fragment."
    : null;
  const subpathError = intent.subpath.trim()
    && !pathIsSafe(intent.subpath.trim().replace(/^\.\//, ""))
    ? german
      ? "Der Unterpfad muss relativ sein und darf kein .. enthalten."
      : "The subpath must be relative and cannot contain `..`."
    : null;
  const selectedConnection = (connections.data ?? []).find(
    (connection) => connection.id === intent.repository_connection_id,
  );
  useEffect(() => {
    if (
      !selectedConnection
      || intent.repository_url === selectedConnection.repository_url
    ) return;
    setIntent((value) => value.repository_connection_id === selectedConnection.id
      ? { ...value, repository_url: selectedConnection.repository_url }
      : value);
  }, [intent.repository_url, selectedConnection]);
  const connectionPending = Boolean(
    intent.repository_connection_id
    && (connections.isLoading || connections.isFetching),
  );
  const staleConnection = Boolean(
    intent.repository_connection_id
    && !connections.isLoading
    && connections.data
    && !selectedConnection,
  );
  const currentAnalysisBinding: RepositoryAnalysisBinding = {
    requestId: intent.request_id,
    normalizedGoal: analysisGoal.trim(),
    projectId,
    settingsFingerprint: repositorySettingsFingerprint(intent),
  };
  const currentAnalysisBindingRef = useRef(currentAnalysisBinding);
  currentAnalysisBindingRef.current = currentAnalysisBinding;

  const createAnalysisSubmission = (): RepositoryAnalysisSubmission => {
    const body: RepositoryAnalysisCreate = {
      request_id: currentAnalysisBinding.requestId,
      repository_url: intent.repository_url.trim(),
      goal: currentAnalysisBinding.normalizedGoal,
      diagram_kind: intent.diagram_kind,
      language: intent.language,
      rights_confirmed: true,
      ...(intent.repository_connection_id
        ? { repository_connection_id: intent.repository_connection_id }
        : {}),
      ...(intent.ref.trim() ? { ref: intent.ref.trim() } : {}),
      ...(intent.subpath.trim()
        ? { subpath: intent.subpath.trim().replace(/^\.\//, "") }
        : {}),
      ...(projectId !== null ? { project_id: projectId } : {}),
    };
    return Object.freeze({
      ...currentAnalysisBinding,
      body: Object.freeze(body),
    });
  };

  const create = useMutation({
    mutationFn: (submission: RepositoryAnalysisSubmission) =>
      api.repositoryAnalysisCreate(submission.body),
    onSuccess: (created, submission) => {
      if (!repositoryAnalysisBindingMatches(
        submission,
        currentAnalysisBindingRef.current,
      )) {
        void queryClient.invalidateQueries({
          queryKey: ["repository-analyses", userId],
        });
        return;
      }
      setAnalysisId(created.public_id);
      setAnalysisRequestId(submission.requestId);
      setRequestSubmitted(true);
      setRightsConfirmed(false);
      queryClient.setQueryData(
        ["repository-analysis", userId, created.public_id],
        created,
      );
      void queryClient.invalidateQueries({ queryKey: ["repository-analyses", userId] });
    },
  });

  useEffect(() => {
    const normalizedGoal = analysisGoal.trim();
    if (previousGoalRef.current === normalizedGoal) return;
    previousGoalRef.current = normalizedGoal;
    setIntent((value) => ({ ...value, request_id: createRequestId() }));
    setAnalysisId(null);
    setAnalysisRequestId(null);
    setRequestSubmitted(false);
    setRightsConfirmed(false);
    create.reset();
  }, [analysisGoal]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (previousProjectRef.current === projectId) return;
    previousProjectRef.current = projectId;
    setIntent((value) => ({ ...value, request_id: createRequestId() }));
    setAnalysisId(null);
    setAnalysisRequestId(null);
    setRequestSubmitted(false);
    setRightsConfirmed(false);
    create.reset();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const connect = useMutation({
    mutationFn: async () => {
      const request = connectionRequestRef.current;
      connectionRequestRef.current = null;
      if (!request) {
        throw new Error(
          german
            ? "Gib den GitHub-Token erneut ein. Er wurde nicht gespeichert."
            : "Enter the GitHub token again. It was not stored.",
        );
      }
      try {
        return await api.repositoryConnectionCreate(request);
      } catch {
        // Never retain a server payload near the credential mutation cache.
        throw new Error("private_repository_connection_failed");
      }
    },
    retry: false,
    onSuccess: (connection) => {
      queryClient.setQueryData<RepositoryConnection[]>(
        ["repository-connections", userId],
        (current) => [
          connection,
          ...(current ?? []).filter((item) => item.id !== connection.id),
        ],
      );
      updateIntent({
        repository_connection_id: connection.id,
        repository_url: connection.repository_url,
      });
      setShowPrivateConnection(false);
    },
    onSettled: () => {
      connectionRequestRef.current = null;
      void queryClient.invalidateQueries({
        queryKey: ["repository-connections", userId],
      });
    },
  });

  useEffect(() => {
    if (!open || !connect.isError || !connections.data) return;
    const repositoryKey = repositoryIdentityKey(intent.repository_url);
    const recovered = repositoryKey
      ? connections.data.find(
          (connection) => repositoryIdentityKey(connection.repository_url) === repositoryKey,
        )
      : null;
    if (!recovered) return;
    updateIntent({
      repository_connection_id: recovered.id,
      repository_url: recovered.repository_url,
    });
    setShowPrivateConnection(false);
    connect.reset();
  }, [connect.isError, connections.data, intent.repository_url, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const canCreate = Boolean(
    intent.repository_url.trim()
    && !repositoryError
    && !subpathError
    && analysisGoal.trim().length >= 3
    && !connectionPending
    && !staleConnection
    && (!intent.repository_connection_id || Boolean(selectedConnection))
    && !connect.isPending
    && rightsConfirmed
    && !requestSubmitted,
  );

  const submitPrivateConnection = () => {
    if (connect.isPending) return;
    const accessToken = connectionToken.trim();
    setConnectionToken("");
    setCredentialStorageConfirmed(false);
    connect.reset();
    if (
      !accessToken
      || !credentialStorageConfirmed
      || repositoryError
      || !intent.repository_url.trim()
    ) return;
    connectionRequestRef.current = {
      repository_url: intent.repository_url.trim(),
      access_token: accessToken,
      credential_storage_confirmed: true,
    };
    connect.mutate();
  };

  const removeConnection = useMutation({
    mutationFn: (id: string) => api.repositoryConnectionDelete(id),
    onSuccess: (_, id) => {
      if (intent.repository_connection_id === id) {
        updateIntent({ repository_connection_id: null, repository_url: "" });
      }
      setConnectionDeleteTarget(null);
      queryClient.removeQueries({ queryKey: ["repository-connections", userId] });
      void queryClient.invalidateQueries({ queryKey: ["repository-connections", userId] });
      void queryClient.invalidateQueries({ queryKey: ["repository-analyses", userId] });
      if (analysisId) {
        void queryClient.invalidateQueries({
          queryKey: ["repository-analysis", userId, analysisId],
        });
      }
    },
    onError: () => {
      setConnectionDeleteTarget(null);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({
        queryKey: ["repository-connections", userId],
      });
    },
  });

  useEffect(() => {
    const removedId = removeConnection.variables;
    if (
      !removeConnection.isError
      || !removedId
      || !connections.data
      || connections.data.some((connection) => connection.id === removedId)
    ) return;
    if (intent.repository_connection_id === removedId) {
      updateIntent({ repository_connection_id: null, repository_url: "" });
    }
    removeConnection.reset();
  }, [connections.data, intent.repository_connection_id, removeConnection.isError, removeConnection.variables]); // eslint-disable-line react-hooks/exhaustive-deps

  const cancel = useMutation({
    mutationFn: (id: string) => api.repositoryAnalysisCancel(id),
    onSuccess: (cancelled) => {
      queryClient.setQueryData(
        ["repository-analysis", userId, cancelled.public_id],
        cancelled,
      );
      void queryClient.invalidateQueries({ queryKey: ["repository-analyses", userId] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.repositoryAnalysisDelete(id),
    onSuccess: (_, id) => {
      if (analysisId === id) {
        setAnalysisId(null);
        setAnalysisRequestId(null);
        setIntent((value) => ({ ...value, request_id: createRequestId() }));
        setRequestSubmitted(false);
        setRightsConfirmed(false);
      }
      setDeleteTarget(null);
      onAnalysisDeleted?.(id);
      queryClient.removeQueries({ queryKey: ["repository-analysis", userId, id] });
      void queryClient.invalidateQueries({ queryKey: ["repository-analyses", userId] });
    },
  });

  const nodes = analysis?.diagram_spec?.nodes ?? [];
  const edges = analysis?.diagram_spec?.edges ?? [];
  const explicitGroups = analysis?.diagram_spec?.groups ?? [];
  const groupLabels = Array.from(new Set([
    ...explicitGroups.map((group) => group.label),
    ...nodes.map((node) => node.group).filter((value): value is string => Boolean(value)),
  ]));
  const parserIds = Array.from(new Set(
    (analysis?.analysis_metadata?.parser_ids ?? analysis?.evidence.map((item) => item.parser_id) ?? [])
      .filter((value): value is string => typeof value === "string" && Boolean(value)),
  ));
  const genericParserIds = parserIds.filter((id) =>
    /(?:source-inventory|docs-declared|generic)/i.test(id),
  );
  const preciseParserIds = parserIds.filter((id) =>
    /(?:-ast-|tree-sitter|scip|compiler|language-server)/i.test(id),
  );
  const staticParserIds = parserIds.filter((id) =>
    !genericParserIds.includes(id) && !preciseParserIds.includes(id),
  );
  const analysisMode = analysis?.diagram_spec?.analysis_mode
    ?? analysis?.analysis_metadata?.analysis_mode;
  const nodeLabels = new Map(nodes.map((node) => [node.id, node.label]));
  const evidenceIndexes = new Map<string, number>();
  (analysis?.evidence ?? []).forEach((evidence, index) => {
    [evidence.id, evidence.evidence_id, evidence.hash].forEach((value) => {
      if (value) evidenceIndexes.set(value, index);
    });
  });
  const evidenceReferences = (ids: string[] | undefined) => {
    if (!ids?.length) return null;
    return (
      <span className="inline-flex flex-wrap gap-1" aria-label={german ? "Belegverweise" : "Evidence references"}>
        {ids.map((id) => {
          const index = evidenceIndexes.get(id);
          return index === undefined ? (
            <span key={id} title={id} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[0.53125rem] text-muted-foreground">
              {id.slice(0, 8)}
            </span>
          ) : (
            <a
              key={id}
              href={`#${formId}-evidence-${index}`}
              title={`${german ? "Zu Beleg" : "Jump to evidence"} ${index + 1}: ${id}`}
              className="rounded bg-accent px-1.5 py-0.5 font-mono text-[0.53125rem] text-moss hover:underline"
            >
              E{index + 1}
            </a>
          );
        })}
      </span>
    );
  };
  const coverageItems = analysis
    ? [
        [german ? "Analysiert" : "Analyzed", numberFromCoverage(analysis, "analyzed_files")],
        [german ? "Geeignet" : "Eligible", numberFromCoverage(analysis, "eligible_files")],
        [german ? "Im Umfang" : "In scope", numberFromCoverage(analysis, "files_in_scope")],
        [german ? "Ausgeschlossen" : "Excluded", numberFromCoverage(analysis, "excluded_files")],
        [german ? "Archiveinträge" : "Archive entries", numberFromCoverage(analysis, "archive_entries")],
      ].filter((entry): entry is [string, number] => entry[1] !== null)
    : [];
  const exclusionReasons = analysis?.coverage?.excluded_by_reason
    ? Object.entries(analysis.coverage.excluded_by_reason).filter(
        (entry): entry is [string, number] =>
          typeof entry[1] === "number" && Number.isFinite(entry[1]),
      )
    : [];
  const coverageAvailable = Boolean(
    analysis?.coverage
    && (
      typeof analysis.coverage.complete === "boolean"
      || coverageItems.length
      || typeof analysis.coverage.eligible_bytes === "number"
      || typeof analysis.coverage.analyzed_bytes === "number"
      || exclusionReasons.length
    ),
  );
  const copy = analysis ? statusCopy(analysis.status, german) : null;
  const needsScopeMessage = analysis?.status === "needs_scope"
    ? analysis.error_code === "repository_archive_too_large"
      ? german
        ? "Das Repository-Archiv überschreitet das aktuelle V1-Importlimit. Ein Unterpfad verkleinert den Archiv-Download nicht; analysiere vorerst einen kleineren, separat veröffentlichten Repository-Stand."
        : "The repository archive exceeds the current V1 import limit. A subpath does not reduce the archive download; for now, analyze a smaller repository snapshot published separately."
      : userFacingStoredErrorMessage(
          analysis.error,
          german
            ? "Der Analyseumfang ist zu groß. Ein Unterpfad oder ein enger formuliertes Ziel kann den relevanten Datei- und Textumfang reduzieren."
            : "The analysis scope is too large. A subpath or a more focused goal can reduce the relevant file and text scope.",
        )
    : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && connect.isPending) return;
        setOpen(nextOpen);
        if (!nextOpen) {
          setConnectionToken("");
          setCredentialStorageConfirmed(false);
          connectionRequestRef.current = null;
          setShowPrivateConnection(false);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn(
            "h-8 shrink-0 rounded-full text-[0.75rem]",
            activeAnalysis && "border-moss/40 bg-accent/60",
          )}
        >
          <GitFork className="size-3.5" />
          {activeAnalysis
            ? german ? "Repository verknüpft" : "Repository linked"
            : german ? "Aus GitHub-Repository" : "From GitHub repository"}
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[min(92dvh,54rem)] w-[calc(100vw-1.5rem)] max-w-[48rem] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="shrink-0 border-b border-border px-5 py-5 pr-12 sm:px-7 sm:py-6">
          <div className="mb-1.5 font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-moss">
            Visual Lab · {german ? "Repository-Analyse" : "Repository analysis"}
          </div>
          <DialogTitle className="flex items-center gap-2.5 text-[1.0625rem]">
            <span aria-hidden="true" className="grid size-8 place-items-center rounded-full border border-moss/25 bg-accent/45">
              <Network className="size-4 text-moss" />
            </span>
            {german ? "Repository-Quelle einrichten" : "Set up repository source"}
          </DialogTitle>
          <DialogDescription className="max-w-2xl text-[0.75rem] leading-relaxed">
            {german
              ? "Lege Ziel, GitHub-Stand und Berechtigungen fest. SixSentences erstellt zuerst eine belegte Spec, die du vor dem Verknüpfen prüfst."
              : "Define the goal, GitHub snapshot, and permissions. SixSentences creates an evidenced spec for you to review before attaching it."}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <form
            id={formId}
            method="post"
            aria-label={german ? "Repository-Analyse konfigurieren" : "Configure repository analysis"}
            className="space-y-6 p-5 sm:p-7"
            onSubmit={(event) => {
              event.preventDefault();
              if (canCreate && !create.isPending) {
                create.mutate(createAnalysisSubmission());
              }
            }}
          >
            <section
              aria-labelledby={`${formId}-goal-heading`}
              className={cn(
                "rounded-2xl border p-4 sm:p-5",
                analysisGoal.trim()
                  ? "border-moss/25 bg-accent/25"
                  : "border-amber-500/30 bg-amber-500/5",
              )}
            >
              <div className="flex items-start gap-3">
                <span aria-hidden="true" className="grid size-7 shrink-0 place-items-center rounded-full bg-moss font-mono text-[0.625rem] font-medium text-primary-foreground">
                  1
                </span>
                <div className="min-w-0 flex-1">
                  <p id={`${formId}-goal-heading`} className="text-[0.75rem] font-medium text-foreground">
                    {german ? "Visual-Brief aus dem Hauptfeld" : "Visual brief from the main field"}
                  </p>
                  <p className="mt-1 line-clamp-3 text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {analysisGoal.trim() || (german
                      ? "Beschreibe zuerst im Visual Lab, was die Grafik erklären soll."
                      : "First describe what the visual should explain in Visual Lab.")}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 shrink-0 rounded-full px-3 text-[0.6875rem]"
                  disabled={connect.isPending}
                  onClick={() => {
                    if (connect.isPending) return;
                    setOpen(false);
                    onEditGoal();
                  }}
                >
                  <PencilLine className="size-3.5" />
                  {german ? "Bearbeiten" : "Edit"}
                </Button>
              </div>
            </section>

            <section aria-labelledby={`${formId}-source-heading`} className="rounded-2xl border border-border bg-card p-4 sm:p-5">
              <div className="flex items-start gap-3">
                <span aria-hidden="true" className="grid size-7 shrink-0 place-items-center rounded-full border border-border bg-secondary font-mono text-[0.625rem] font-medium text-foreground">
                  2
                </span>
                <div className="min-w-0 flex-1">
                  <p id={`${formId}-source-heading`} className="text-[0.75rem] font-medium text-foreground">
                    {german ? "Verbindung und Quelle" : "Connection and source"}
                  </p>
                  <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {german
                      ? "Wähle öffentlichen Zugriff oder eine gespeicherte, repository-spezifische Verbindung."
                      : "Choose public access or a saved, repository-scoped connection."}
                  </p>
                </div>
              </div>

              <div className="mt-4 space-y-3.5 sm:ml-10">
                <div className="space-y-1.5">
                  <Label htmlFor={`${formId}-connection`} className="text-[0.6875rem]">
                    {german ? "Quelle" : "Source"}
                  </Label>
                  <Select
                    value={intent.repository_connection_id ?? "public"}
                    disabled={connections.isLoading || connect.isPending || create.isPending}
                    onValueChange={(value) => {
                      setConnectionToken("");
                      setCredentialStorageConfirmed(false);
                      connectionRequestRef.current = null;
                      setShowPrivateConnection(false);
                      if (value === "public") {
                        updateIntent({ repository_connection_id: null, repository_url: "" });
                        return;
                      }
                      const connection = (connections.data ?? []).find((item) => item.id === value);
                      if (connection) {
                        updateIntent({
                          repository_connection_id: connection.id,
                          repository_url: connection.repository_url,
                        });
                      }
                    }}
                  >
                    <SelectTrigger id={`${formId}-connection`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="public">
                        {german ? "Öffentliches GitHub-Repository" : "Public GitHub repository"}
                      </SelectItem>
                      {(connections.data ?? []).map((connection) => (
                        <SelectItem key={connection.id} value={connection.id}>
                          {connection.owner}/{connection.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor={`${formId}-url`} className="text-[0.6875rem]">
                    {german ? "GitHub-Repository" : "GitHub repository"}
                  </Label>
                  <Input
                    id={`${formId}-url`}
                    inputMode="url"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    readOnly={Boolean(selectedConnection)}
                    disabled={connect.isPending || create.isPending}
                    value={intent.repository_url}
                    aria-invalid={Boolean(repositoryError || staleConnection)}
                    aria-describedby={repositoryError ? `${formId}-url-error` : undefined}
                    placeholder="https://github.com/owner/repository"
                    onChange={(event) => {
                      setConnectionToken("");
                      setCredentialStorageConfirmed(false);
                      connectionRequestRef.current = null;
                      updateIntent({ repository_url: event.target.value });
                    }}
                  />
                  {repositoryError ? (
                    <p id={`${formId}-url-error`} className="text-[0.65625rem] text-destructive">
                      {repositoryError}
                    </p>
                  ) : null}
                </div>

                {selectedConnection ? (
                  <div className="rounded-lg bg-secondary/55 px-2.5 py-2 text-[0.65625rem]">
                    <div className="flex items-center gap-2">
                      <LockKeyhole className="size-3.5 shrink-0 text-moss" />
                      <span className="min-w-0 flex-1 truncate">
                        {german ? "Private Verbindung aktiv" : "Private connection active"} · {selectedConnection.name}
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
                        disabled={create.isPending}
                        aria-label={german ? "Private Verbindung entfernen" : "Remove private connection"}
                        onClick={() => {
                          removeConnection.reset();
                          setConnectionDeleteTarget(selectedConnection);
                        }}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                    <p className="mt-1.5 leading-relaxed text-muted-foreground">
                      {german
                        ? "Wenn du die Analyse später im Visual Lab oder Writer verwendest, werden abgeleitete Architektur bzw. Texte im jeweiligen Workspace-Artefakt sichtbar. Repository-Identität, Commit, rohe Belegdatensätze, Quelllinks und Rohcode bleiben dort verborgen; geprüfte, abgeleitete Komponenten- oder Verzeichnislabels können pfadähnlich sein."
                        : "When you later use the analysis in Visual Lab or Writer, derived architecture or text becomes visible in that workspace artifact. Repository identity, commit, raw evidence records, source links, and raw code stay hidden; screened, derived component or directory labels may be path-like."}
                    </p>
                  </div>
                ) : staleConnection ? (
                  <div role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-[0.65625rem] leading-relaxed text-amber-700 dark:text-amber-300">
                    {german
                      ? "Diese private Verbindung ist nicht mehr verfügbar. Wähle eine andere Quelle."
                      : "This private connection is no longer available. Choose another source."}
                  </div>
                ) : (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-[0.65625rem]"
                    disabled={connect.isPending || create.isPending}
                    onClick={() => {
                      if (connect.isPending) return;
                      connect.reset();
                      setConnectionToken("");
                      setCredentialStorageConfirmed(false);
                      setShowPrivateConnection((value) => !value);
                    }}
                  >
                    <Plus className="size-3.5" />
                    {german ? "Privates Repository verbinden" : "Connect private repository"}
                  </Button>
                )}

                {showPrivateConnection && !selectedConnection ? (
                  <div className="space-y-2 rounded-lg border border-moss/25 bg-accent/25 p-2.5">
                    <div className="space-y-1">
                      <Label htmlFor={`${formId}-connection-token`} className="text-[0.625rem]">
                        {german ? "GitHub Fine-grained Token" : "GitHub fine-grained token"}
                      </Label>
                      <Input
                        id={`${formId}-connection-token`}
                        type="password"
                        autoComplete="new-password"
                        autoCapitalize="none"
                        autoCorrect="off"
                        spellCheck={false}
                        value={connectionToken}
                        placeholder="github_pat_…"
                        disabled={connect.isPending || create.isPending}
                        onChange={(event) => {
                          setCredentialStorageConfirmed(false);
                          connectionRequestRef.current = null;
                          setConnectionToken(event.target.value);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            submitPrivateConnection();
                          }
                        }}
                      />
                    </div>
                    <label
                      htmlFor={`${formId}-credential-storage`}
                      className="flex cursor-pointer items-start gap-2 rounded-lg border border-border bg-card/70 p-2"
                    >
                      <Checkbox
                        id={`${formId}-credential-storage`}
                        checked={credentialStorageConfirmed}
                        disabled={connect.isPending || create.isPending}
                        onCheckedChange={(checked) => {
                          setCredentialStorageConfirmed(checked === true);
                        }}
                        className="mt-0.5"
                      />
                      <span className="text-[0.59375rem] leading-relaxed text-muted-foreground">
                        <span className="block font-medium text-foreground">
                          {german ? "Token-Verarbeitung prüfen" : "Review token handling"}
                        </span>
                        {german
                          ? "Fahre nur fort, wenn die konfigurierte API den PAT verschlüsselt speichert, ausschließlich für serverseitige Read-only-GitHub-Abrufe nutzt und von KI-Anbieter-Eingaben ausschließt. Prüfe diese Zusagen beim Betreiber."
                          : "Continue only if the configured API stores the PAT encrypted, uses it solely for server-side read-only GitHub fetches, and excludes it from AI-provider inputs. Verify these guarantees with the deployment operator."}
                      </span>
                    </label>
                    <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <p className="max-w-xs text-[0.59375rem] leading-relaxed text-muted-foreground">
                        <KeyRound className="mr-1 inline size-3" />
                        {german
                          ? "Der Token wird einmalig gesendet und sofort aus diesem Formular entfernt. Nutze einen Fine-grained Token mit Read-only-Zugriff nur auf dieses Repository."
                          : "The token is sent once and cleared from this form immediately. Use a fine-grained token with read-only access to this repository only."}
                        <span className="mt-1 block font-medium text-foreground">
                          Repository access: only this repository · Contents: Read-only
                        </span>
                        <a
                          href="https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-1 inline-flex items-center gap-1 text-moss hover:underline"
                        >
                          {german ? "GitHub-Anleitung öffnen" : "Open GitHub instructions"}
                          <ExternalLink className="size-3" />
                        </a>
                      </p>
                      <Button
                        type="button"
                        size="sm"
                        className="h-9 w-full shrink-0 text-[0.65625rem] sm:w-auto"
                        disabled={
                          connect.isPending
                          || create.isPending
                          || !connectionToken.trim()
                          || !credentialStorageConfirmed
                          || !intent.repository_url.trim()
                          || Boolean(repositoryError)
                        }
                        onClick={submitPrivateConnection}
                      >
                        {connect.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <LockKeyhole className="size-3.5" />}
                        {german ? "Verbinden" : "Connect"}
                      </Button>
                    </div>
                    {connect.isError ? (
                      <p role="alert" className="text-[0.65625rem] leading-relaxed text-destructive">
                        {german
                          ? "Die private GitHub-Verbindung konnte nicht erstellt werden. Prüfe Repository und Read-only-Token."
                          : "The private GitHub connection could not be created. Check the repository and read-only token."}
                        <span className="mt-0.5 block text-muted-foreground">
                          {german ? "Der Token wurde gelöscht. Gib ihn für einen neuen Versuch erneut ein." : "The token was cleared. Enter it again for another attempt."}
                        </span>
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {connections.isError ? (
                  <p role="alert" className="text-[0.65625rem] text-destructive">
                    {german ? "Private Verbindungen konnten nicht geladen werden." : "Private connections could not be loaded."}
                  </p>
                ) : null}
                {removeConnection.isError ? (
                  <p role="alert" className="text-[0.65625rem] text-destructive">
                    {german
                      ? "Das Ergebnis der Trennung konnte nicht bestätigt werden. Der Zugriff wird aktualisiert; falls er danach noch erscheint, versuche es erneut."
                      : "The disconnect result could not be confirmed. Access is being refreshed; if it still appears, try again."}
                  </p>
                ) : null}
              </div>

              <div className="mt-5 border-t border-border pt-4 sm:ml-10">
                <p className="text-[0.71875rem] font-medium text-foreground">
                  {german ? "Stand und Ausgabe" : "Snapshot and output"}
                </p>
                <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
                  {german
                    ? "Grenze den Repository-Stand bei Bedarf ein und lege die gewünschte Darstellung fest."
                    : "Optionally narrow the repository snapshot and choose the intended presentation."}
                </p>

                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={`${formId}-ref`}>
                      {german ? "Branch, Tag oder Commit" : "Branch, tag or commit"}
                      <span className="ml-1 font-normal text-muted-foreground">({german ? "optional" : "optional"})</span>
                    </Label>
                    <Input
                      id={`${formId}-ref`}
                      value={intent.ref}
                      disabled={create.isPending}
                      maxLength={200}
                      placeholder="main"
                      onChange={(event) => updateIntent({ ref: event.target.value })}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`${formId}-subpath`}>
                      {german ? "Unterpfad" : "Subpath"}
                      <span className="ml-1 font-normal text-muted-foreground">({german ? "optional" : "optional"})</span>
                    </Label>
                    <Input
                      id={`${formId}-subpath`}
                      value={intent.subpath}
                      disabled={create.isPending}
                      maxLength={500}
                      aria-invalid={Boolean(subpathError)}
                      aria-describedby={subpathError ? `${formId}-subpath-error` : undefined}
                      placeholder="packages/api"
                      onChange={(event) => updateIntent({ subpath: event.target.value })}
                    />
                    {subpathError ? (
                      <p id={`${formId}-subpath-error`} className="text-[0.6875rem] text-destructive">
                        {subpathError}
                      </p>
                    ) : null}
                  </div>
                </div>

                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={`${formId}-kind`}>{german ? "Grafiktyp" : "Visual type"}</Label>
                    <Select
                      value={intent.diagram_kind}
                      disabled={create.isPending}
                      onValueChange={(value) =>
                        updateIntent({ diagram_kind: value as RepositoryDiagramKind })
                      }
                    >
                      <SelectTrigger id={`${formId}-kind`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="architecture">{german ? "Systemarchitektur" : "System architecture"}</SelectItem>
                        <SelectItem value="flow">{german ? "Anfrage- oder Datenfluss" : "Request or data flow"}</SelectItem>
                        <SelectItem value="deployment">{german ? "Deployment / Infrastruktur" : "Deployment / infrastructure"}</SelectItem>
                        <SelectItem value="module">{german ? "Modulübersicht" : "Module map"}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`${formId}-language`}>{german ? "Ausgabesprache" : "Output language"}</Label>
                    <Select
                      value={intent.language}
                      disabled={create.isPending}
                      onValueChange={(value) => updateIntent({ language: value as "en" | "de" })}
                    >
                      <SelectTrigger id={`${formId}-language`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="en">English</SelectItem>
                        <SelectItem value="de">Deutsch</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            </section>

            <section aria-labelledby={`${formId}-rights-heading`} className="space-y-3">
              <div className="flex items-start gap-3">
                <span aria-hidden="true" className="grid size-7 shrink-0 place-items-center rounded-full border border-border bg-secondary font-mono text-[0.625rem] font-medium text-foreground">
                  3
                </span>
                <div>
                  <p id={`${formId}-rights-heading`} className="text-[0.75rem] font-medium text-foreground">
                    {german ? "Berechtigung bestätigen" : "Confirm permission"}
                  </p>
                  <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {german
                      ? "Prüfe die Verarbeitungshinweise, bevor du die Analyse startest."
                      : "Review the processing details before starting the analysis."}
                  </p>
                </div>
              </div>

              <label
                htmlFor={`${formId}-rights`}
                className="flex cursor-pointer items-start gap-3 rounded-2xl border border-border bg-secondary/25 p-4 transition-colors hover:bg-secondary/35 sm:ml-10"
              >
                <Checkbox
                  id={`${formId}-rights`}
                  checked={rightsConfirmed}
                  disabled={create.isPending}
                  onCheckedChange={(checked) => setRightsConfirmed(checked === true)}
                  className="mt-0.5"
                />
                <span className="min-w-0 text-[0.71875rem] leading-relaxed text-muted-foreground">
                  <span className="block font-medium text-foreground">
                    {german ? "Analyseberechtigung und KI-Verarbeitung bestätigen" : "Confirm analysis rights and AI processing"}
                  </span>
                  {german
                    ? "Ich bin zur Analyse dieses Repositorys berechtigt. Für die KI-gestützte Analyse darf mein Visual-Brief aus dem Hauptfeld zusammen mit ausschließlich opaken Kandidaten-IDs, serverseitigen Typen sowie opaker Graphstruktur und Graphmetriken an den konfigurierten KI-Anbieter übertragen werden. Bei einem späteren Rendering dürfen zusätzlich geprüfte, abgeleitete Komponentenbezeichnungen übertragen werden. Rohcode, rohe Belegdatensätze und Quelllinks werden nicht übertragen."
                    : "I am authorized to analyze this repository. For AI-assisted analysis, my visual brief from the main field may be sent to the configured AI provider together with opaque candidate IDs, server-defined types, and opaque graph topology and metrics only. A later render may additionally send screened, derived component labels. Raw code, raw evidence records, and source links are not transmitted."}
                  {intent.repository_connection_id ? (
                    <span className="mt-1 block">
                      {german
                        ? "Erst ein späterer, bewusster Render- oder Writer-Schritt teilt geprüfte, abgeleitete Topologie, Komponenten- oder Verzeichnislabels bzw. Prosa mit berechtigten Mitgliedern des Projekts oder der Organisation; solche Labels können pfadähnlich sein. Repository-Identität, rohe Belegdatensätze, Quelllinks, Commit und Rohcode bleiben verborgen."
                        : "Only a later, explicit Render or Writer step shares screened, derived topology, component or directory labels, or prose with authorized project or organization members; those labels may be path-like. Repository identity, raw evidence records, source links, commit, and raw code stay hidden."}
                    </span>
                  ) : null}
                </span>
              </label>
            </section>

            {create.isError ? (
              <div role="alert" className="rounded-xl border border-destructive/25 bg-destructive/5 p-3 text-[0.71875rem] leading-relaxed text-destructive">
                {create.error instanceof Error
                  ? create.error.message
                  : german ? "Die Analyse konnte nicht gestartet werden." : "The analysis could not be started."}
                <p className="mt-1 text-muted-foreground">
                  {german
                    ? "Ein erneuter Versuch verwendet dieselbe Request-ID, damit ein verlorenes Server-Resultat nicht doppelt angelegt wird."
                    : "Retrying uses the same request ID so a lost server response cannot create a duplicate."}
                </p>
              </div>
            ) : null}

            <div className="flex flex-col gap-3 rounded-2xl border border-border bg-card p-4 sm:ml-10 sm:flex-row sm:items-center sm:justify-between">
              <p className="max-w-md text-[0.6875rem] leading-relaxed text-muted-foreground">
                {german
                  ? "Die Analyse rendert nichts automatisch. Du prüfst zuerst Spec und Belege."
                  : "Analysis never renders automatically. You review its spec and evidence first."}
              </p>
              <Button type="submit" className="w-full shrink-0 sm:w-auto" disabled={!canCreate || create.isPending}>
                {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <GitBranch className="size-4" />}
                {create.isError
                  ? german ? "Dieselbe Anfrage erneut senden" : "Retry same request"
                  : german ? "Repository analysieren" : "Analyze repository"}
              </Button>
            </div>
          </form>

          <section
            aria-labelledby={`${formId}-review-heading`}
            className="border-t border-border bg-secondary/15 p-5 sm:p-7"
          >
            <div className="mb-4 flex flex-col gap-2.5 sm:flex-row sm:items-start sm:gap-3">
              <span className="inline-flex min-h-7 w-fit items-center rounded-full border border-moss/25 bg-accent/45 px-3 font-mono text-[0.5625rem] font-medium uppercase tracking-[0.14em] text-moss">
                {german ? "Nach der Analyse" : "After analysis"}
              </span>
              <div className="min-w-0">
                <h3 id={`${formId}-review-heading`} className="text-[0.8125rem] font-medium text-foreground">
                  {german ? "Spec prüfen und verknüpfen" : "Review and attach the spec"}
                </h3>
                <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  {german
                    ? "Ergebnisse erscheinen hier im selben Ablauf – mit Coverage, Belegen und einer bewussten Verknüpfung."
                    : "Results appear here in the same workflow, with coverage, evidence, and an explicit attach step."}
                </p>
              </div>
            </div>
            {analysis ? (
              <div className="space-y-4" aria-live="polite">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 text-[0.8125rem] font-medium text-foreground">
                      {MOVING_STATUSES.includes(analysis.status) ? (
                        <Loader2 className="size-3.5 shrink-0 animate-spin text-moss" />
                      ) : analysis.status === "ready" ? (
                        <Check className="size-3.5 shrink-0 text-moss" />
                      ) : (
                        <AlertTriangle className="size-3.5 shrink-0 text-amber-600" />
                      )}
                      <span className="truncate">{copy?.label}</span>
                    </p>
                    <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                      {copy?.detail}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {MOVING_STATUSES.includes(analysis.status) ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        title={german ? "Analyse abbrechen" : "Cancel analysis"}
                        aria-label={german ? "Analyse abbrechen" : "Cancel analysis"}
                        disabled={cancel.isPending}
                        onClick={() => cancel.mutate(analysis.public_id)}
                        className="size-8"
                      >
                        {cancel.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <CircleStop className="size-3.5" />}
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      title={german ? "Analyse löschen" : "Delete analysis"}
                      aria-label={german ? "Analyse löschen" : "Delete analysis"}
                      onClick={() => setDeleteTarget(analysis.public_id)}
                      className="size-8 text-muted-foreground hover:text-destructive"
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>

                <div>
                  <Progress value={statusProgress(analysis)} className="h-1.5" />
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[0.59375rem] uppercase tracking-[0.11em] text-muted-foreground">
                    <span>{analysis.owner}/{analysis.name}</span>
                    {analysis.commit_sha ? <span>{analysis.commit_sha.slice(0, 10)}</span> : null}
                    {analysis.ref ? <span>{analysis.ref}</span> : null}
                    <span>{formatDate(analysis.updated_at)}</span>
                  </div>
                </div>

                {coverageAvailable && analysis.coverage ? (
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className={cn(
                        "rounded-full border px-2.5 py-1 text-[0.625rem]",
                        analysis.coverage.complete === true
                          ? "border-moss/30 bg-accent/60 text-moss"
                          : analysis.coverage.complete === false
                            ? "border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-300"
                            : "border-border bg-card text-muted-foreground",
                      )}>
                        {analysis.coverage.complete === true
                          ? german ? "Coverage vollständig" : "Coverage complete"
                          : analysis.coverage.complete === false
                            ? german ? "Coverage unvollständig" : "Coverage incomplete"
                            : german ? "Coverage gemeldet" : "Coverage reported"}
                      </span>
                      {typeof analysis.coverage.analyzed_bytes === "number" ? (
                        <span className="font-mono text-[0.59375rem] text-muted-foreground">
                          {analysis.coverage.analyzed_bytes.toLocaleString()} {german ? "Bytes analysiert" : "bytes analyzed"}
                          {typeof analysis.coverage.eligible_bytes === "number"
                            ? ` / ${analysis.coverage.eligible_bytes.toLocaleString()} ${german ? "geeignet" : "eligible"}`
                            : ""}
                        </span>
                      ) : null}
                    </div>
                    {coverageItems.length ? (
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {coverageItems.map(([label, value]) => (
                          <div key={label} className="rounded-xl border border-border bg-card p-2.5">
                            <p className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
                            <p className="mt-1 text-[0.9375rem] font-medium text-foreground">{value.toLocaleString()}</p>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {exclusionReasons.length ? (
                      <div className="flex flex-wrap gap-1.5" aria-label={german ? "Ausschlussgründe" : "Exclusion reasons"}>
                        {exclusionReasons.map(([reason, count]) => (
                          <span key={reason} className="rounded-full bg-secondary px-2.5 py-1 text-[0.625rem] text-muted-foreground">
                            {reason.replaceAll("_", " ")} · {count.toLocaleString()}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {parserIds.length ? (
                      <div className="rounded-xl border border-border bg-card p-3">
                        <p className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">
                          {german ? "Parser-Coverage" : "Parser coverage"}
                        </p>
                        <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
                          {german
                            ? "Das sprachneutrale Inventar hält geeignete Text- und Quelldateien im Analyseumfang; die Zähler zeigen, wie viele tatsächlich analysiert wurden. Präzise und weitere statische Adapter ergänzen tiefere, quellbelegte Beziehungen; Ausschlüsse bleiben oben sichtbar."
                            : "The language-neutral inventory keeps eligible text and source files in scope; the counters show how many were actually analyzed. Precise and additional static adapters add deeper source-backed relationships; exclusions remain visible above."}
                        </p>
                        <div className="mt-2 space-y-1.5 text-[0.625rem]">
                          {preciseParserIds.length ? (
                            <p className="flex flex-wrap items-center gap-1.5">
                              <span className="font-medium text-foreground">{german ? "Präzise Adapter" : "Precise adapters"}</span>
                              {preciseParserIds.map((id) => <span key={id} className="rounded-full bg-accent px-2 py-0.5 font-mono text-moss">{id}</span>)}
                            </p>
                          ) : null}
                          {staticParserIds.length ? (
                            <p className="flex flex-wrap items-center gap-1.5">
                              <span className="font-medium text-foreground">{german ? "Statische Adapter" : "Static adapters"}</span>
                              {staticParserIds.map((id) => <span key={id} className="rounded-full bg-secondary px-2 py-0.5 font-mono text-muted-foreground">{id}</span>)}
                            </p>
                          ) : null}
                          {genericParserIds.length ? (
                            <p className="flex flex-wrap items-center gap-1.5">
                              <span className="font-medium text-foreground">{german ? "Generisches Inventar" : "Generic inventory"}</span>
                              {genericParserIds.map((id) => <span key={id} className="rounded-full bg-secondary px-2 py-0.5 font-mono text-muted-foreground">{id}</span>)}
                            </p>
                          ) : null}
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {analysis.status === "error" ? (
                  <div role="alert" className="rounded-xl border border-destructive/25 bg-destructive/5 p-3">
                    <p className="text-[0.71875rem] leading-relaxed text-destructive">
                      {userFacingStoredErrorMessage(
                        analysis.error,
                        german
                          ? "Die Analyse konnte nicht abgeschlossen werden. Versuche es bitte erneut."
                          : "The analysis could not be completed. Please try again.",
                      )}
                    </p>
                  </div>
                ) : null}

                {analysis.status === "needs_scope" ? (
                  <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {needsScopeMessage}
                  </div>
                ) : null}

                {["error", "cancelled", "needs_scope"].includes(analysis.status) ? (
                  <Button type="button" variant="outline" className="w-full" onClick={prepareNewAnalysis}>
                    <RefreshCcw className="size-3.5" />
                    {german ? "Neue Analyse vorbereiten" : "Prepare a new analysis"}
                  </Button>
                ) : null}

                {analysis.status === "ready" && !requestSubmitted ? (
                  <div role="status" className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {german
                      ? "Dieses fertige Ergebnis gehört zu den vorherigen Formulardaten. Starte die geänderte Analyse, bevor du eine Spec als Brief verwendest."
                      : "This ready result belongs to the previous form values. Start the changed analysis before using a spec as the brief."}
                  </div>
                ) : null}

                {analysis.status === "ready" && requestSubmitted ? (
                  <div className="space-y-3">
                    <div className="rounded-2xl border border-moss/25 bg-card p-3.5">
                      <p className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
                        <ShieldCheck className="size-4 text-moss" />
                        {analysis.diagram_spec?.title || (german ? "Kanonische Repository-Spezifikation" : "Canonical repository specification")}
                      </p>
                      {analysis.diagram_spec?.summary || analysis.diagram_spec?.scope_note ? (
                        <p className="mt-1.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                          {analysis.diagram_spec.summary || analysis.diagram_spec.scope_note}
                        </p>
                      ) : null}
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        <span className="rounded-full bg-secondary px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.1em] text-muted-foreground">
                          {nodes.length} {german ? "Knoten" : "nodes"}
                        </span>
                        <span className="rounded-full bg-secondary px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.1em] text-muted-foreground">
                          {edges.length} {german ? "Kanten" : "edges"}
                        </span>
                        {groupLabels.length ? (
                          <span className="rounded-full bg-secondary px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.1em] text-muted-foreground">
                            {groupLabels.length} {german ? "Gruppen" : "groups"}
                          </span>
                        ) : null}
                        {analysisMode ? (
                          <span className="rounded-full bg-secondary px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.1em] text-muted-foreground">
                            {analysisMode.replaceAll("_", " ")}
                          </span>
                        ) : null}
                      </div>
                      {nodes.length ? (
                        <div className="mt-3 max-h-40 space-y-1.5 overflow-y-auto pr-1" aria-label={german ? "Knoten der Spezifikation" : "Specification nodes"}>
                          {nodes.map((node) => (
                            <div key={node.id} className="rounded-lg border border-border bg-background px-2.5 py-2 text-[0.65625rem]">
                              <div className="flex flex-wrap items-center justify-between gap-1.5">
                                <span className="font-medium text-foreground">{node.label}</span>
                                <span className="inline-flex items-center gap-1.5 font-mono text-[0.53125rem] text-muted-foreground">
                                  {node.group ? <span>{node.group}</span> : null}
                                  <span>{node.kind || node.id}</span>
                                  {typeof node.confidence === "number" ? <span>{Math.round(node.confidence * 100)}%</span> : null}
                                </span>
                              </div>
                              {node.description ? <p className="mt-1 leading-relaxed text-muted-foreground">{node.description}</p> : null}
                              {node.evidence_ids?.length ? <div className="mt-1.5">{evidenceReferences(node.evidence_ids)}</div> : null}
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {groupLabels.length ? (
                        <div className="mt-3">
                          <p className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">{german ? "Gruppen" : "Groups"}</p>
                          <div className="mt-1.5 flex max-h-24 flex-wrap gap-1.5 overflow-y-auto">
                            {groupLabels.map((group) => (
                              <span key={group} className="inline-flex items-center rounded-lg border border-border bg-background px-2 py-1 text-[0.625rem] text-foreground">
                                {group}
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      {edges.length ? (
                        <div className="mt-3">
                          <p className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">{german ? "Topologie" : "Topology"}</p>
                          <div className="mt-1.5 max-h-40 space-y-1 overflow-y-auto pr-1">
                            {edges.map((edge, index) => (
                              <div key={edge.id ?? `${edge.source}-${edge.target}-${index}`} className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-2 text-[0.625rem]">
                                <span className="font-medium text-foreground">{nodeLabels.get(edge.source) || edge.source}</span>
                                <ArrowRight className="size-3 shrink-0 text-moss" aria-hidden="true" />
                                {edge.label ? <span className="text-muted-foreground">{edge.label}</span> : null}
                                {edge.label ? <ArrowRight className="size-3 shrink-0 text-moss" aria-hidden="true" /> : null}
                                <span className="font-medium text-foreground">{nodeLabels.get(edge.target) || edge.target}</span>
                                {typeof edge.confidence === "number" ? <span className="font-mono text-[0.53125rem] text-muted-foreground">{Math.round(edge.confidence * 100)}%</span> : null}
                                {edge.evidence_ids?.length ? <span className="ml-auto">{evidenceReferences(edge.evidence_ids)}</span> : null}
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}
                    </div>

                    <div className="rounded-2xl border border-border bg-card p-3.5">
                      <p className="flex items-center justify-between gap-2 text-[0.71875rem] font-medium text-foreground">
                        <span className="flex items-center gap-2"><FileCode2 className="size-3.5 text-moss" /> {german ? "SHA-fixierte Belege" : "SHA-pinned evidence"}</span>
                        <span className="font-mono text-[0.59375rem] text-muted-foreground">
                          {analysis.evidence.length} / {analysis.evidence.length} {german ? "angezeigt" : "shown"}
                        </span>
                      </p>
                      <div className="mt-2 max-h-64 scroll-mt-4 space-y-1.5 overflow-y-auto pr-1">
                        {analysis.evidence.map((evidence, index) => {
                          const url = immutableRepositoryEvidenceUrl(analysis, evidence);
                          const path = evidencePath(evidence);
                          const [start, end] = evidenceLines(evidence);
                          const label = evidence.label || path || `${german ? "Beleg" : "Evidence"} ${index + 1}`;
                          const fact = evidence.fact || evidence.summary;
                          return url ? (
                            <a
                              id={`${formId}-evidence-${index}`}
                              key={evidence.id ?? evidence.evidence_id ?? `${path}-${index}`}
                              href={url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-2 text-[0.65625rem] text-foreground transition-colors hover:border-moss/45"
                            >
                              <span className="min-w-0 flex-1">
                                <span className="block truncate">{label}</span>
                                {fact ? <span className="mt-0.5 block line-clamp-2 text-muted-foreground">{fact}</span> : null}
                                {evidence.parser_id || typeof evidence.confidence === "number" ? (
                                  <span className="mt-0.5 block truncate font-mono text-[0.53125rem] text-muted-foreground">
                                    {evidence.parser_id || (german ? "statische Analyse" : "static analysis")}
                                    {typeof evidence.confidence === "number" ? ` · ${Math.round(evidence.confidence * 100)}%` : ""}
                                  </span>
                                ) : null}
                              </span>
                              {start ? <span className="shrink-0 font-mono text-[0.5625rem] text-muted-foreground">L{start}{end && end !== start ? `–${end}` : ""}</span> : null}
                              <ExternalLink className="size-3 shrink-0 text-muted-foreground" />
                            </a>
                          ) : (
                            <div id={`${formId}-evidence-${index}`} key={evidence.id ?? evidence.evidence_id ?? `${path}-${index}`} className="rounded-lg border border-border px-2.5 py-2 text-[0.65625rem] text-muted-foreground">
                              <span className="block">{label}</span>
                              {fact ? <span className="mt-0.5 block leading-relaxed">{fact}</span> : null}
                            </div>
                          );
                        })}
                        {analysis.evidence.length === 0 ? (
                          <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                            {german ? "Die Spec enthält noch keine anzeigbaren Source-Belege." : "The spec does not expose displayable source evidence yet."}
                          </p>
                        ) : null}
                      </div>
                    </div>

                    {analysis.commit_sha ? (
                      <RepositoryManuscriptPanel
                        key={`${userId}:${analysis.public_id}:${analysis.commit_sha}:${analysis.updated_at}`}
                        analysis={analysis}
                        userId={userId}
                        german={german}
                      />
                    ) : (
                      <div role="alert" className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                        {german
                          ? "Manuskript-Prosa benötigt einen vollständig SHA-fixierten Repository-Stand."
                          : "Manuscript prose requires a fully SHA-pinned repository snapshot."}
                      </div>
                    )}

                    <div className="rounded-2xl border border-border bg-secondary/35 p-3.5">
                      <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                        {german
                          ? "Die Spec und ihre Belege bleiben die Wahrheit. Das gerenderte PNG ist nur eine gestaltete Variante und kann visuell abweichen."
                          : "The spec and its evidence remain canonical. The rendered PNG is only a styled variant and may differ visually."}
                      </p>
                      <Button
                        type="button"
                        className="mt-3 w-full"
                        disabled={connect.isPending}
                        onClick={() => {
                          if (!requestSubmitted || connect.isPending) return;
                          onUseAnalysis(analysis);
                          setOpen(false);
                        }}
                      >
                        {german ? "Verifizierte Analyse verwenden" : "Use verified analysis"}
                        <ArrowRight className="size-4" />
                      </Button>
                      <p className="mt-2 text-center text-[0.625rem] text-muted-foreground">
                        {german ? "Nichts wird automatisch gerendert. Drücke danach im Visual Lab auf Render." : "Nothing renders automatically. Press Render in Visual Lab next."}
                      </p>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : current.isError ? (
              <div role="alert" className="grid min-h-40 place-items-center rounded-2xl border border-destructive/25 bg-destructive/5 px-6 text-center">
                <div>
                  <AlertTriangle className="mx-auto size-5 text-destructive" />
                  <p className="mt-2 text-[0.75rem] text-destructive">
                    {german ? "Diese gespeicherte Analyse ist nicht mehr verfügbar." : "This saved analysis is no longer available."}
                  </p>
                  <Button type="button" variant="outline" size="sm" className="mt-3" onClick={prepareNewAnalysis}>
                    <RefreshCcw className="size-3.5" />
                    {german ? "Neue Analyse vorbereiten" : "Prepare a new analysis"}
                  </Button>
                </div>
              </div>
            ) : current.isLoading || list.isLoading ? (
              <div className="grid min-h-40 place-items-center" role="status">
                <Loader2 className="size-5 animate-spin text-moss" />
                <span className="sr-only">{german ? "Analysen werden geladen" : "Loading analyses"}</span>
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-border bg-card/55 p-4 sm:p-5">
                <div className="grid gap-3 sm:grid-cols-3">
                  {[
                    ["01", german ? "Analysieren" : "Analyze", german ? "Repository-Stand und Ziel senden" : "Submit snapshot and goal"],
                    ["02", german ? "Prüfen" : "Review", german ? "Spec, Coverage und Belege lesen" : "Read spec, coverage, and evidence"],
                    ["03", german ? "Verknüpfen" : "Attach", german ? "Analyse bewusst an den Brief hängen" : "Explicitly attach analysis to the brief"],
                  ].map(([number, title, detail]) => (
                    <div key={number} className="rounded-xl border border-border/80 bg-background/55 p-3">
                      <span className="font-mono text-[0.5625rem] font-medium text-moss">{number}</span>
                      <p className="mt-1.5 text-[0.71875rem] font-medium text-foreground">{title}</p>
                      <p className="mt-0.5 text-[0.65625rem] leading-relaxed text-muted-foreground">{detail}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {(list.data ?? []).length > 0 ? (
              <div className="mt-5 border-t border-border pt-4">
                <p className="font-mono text-[0.5625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  {german ? "Letzte Analysen" : "Recent analyses"}
                </p>
                <div className="mt-2 flex max-h-32 flex-col gap-1 overflow-y-auto">
                  {(list.data ?? []).slice(0, 8).map((item) => (
                    <button
                      key={item.public_id}
                      type="button"
                      onClick={() => {
                        setAnalysisId(item.public_id);
                        setAnalysisRequestId(`selected:${item.public_id}`);
                        setRequestSubmitted(true);
                        setRightsConfirmed(false);
                        create.reset();
                      }}
                      className={cn(
                        "flex min-h-11 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[0.6875rem] transition-colors",
                        item.public_id === analysisId
                          ? "bg-accent text-foreground"
                          : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                      )}
                    >
                      {MOVING_STATUSES.includes(item.status) ? (
                        <Loader2 className="size-3 shrink-0 animate-spin" />
                      ) : item.status === "ready" ? (
                        <Check className="size-3 shrink-0 text-moss" />
                      ) : (
                        <AlertTriangle className="size-3 shrink-0" />
                      )}
                      <span className="min-w-0 flex-1 truncate">{item.owner}/{item.name}</span>
                      <span className="shrink-0 font-mono text-[0.5625rem] uppercase">{item.status.replace("_", " ")}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </section>
        </div>
      </DialogContent>

      <ConfirmDeleteDialog
        target={deleteTarget ? {
          title: german ? "Repository-Analyse löschen?" : "Delete repository analysis?",
          description: german
            ? "Spec, Coverage und Belege werden aus dem Workspace entfernt; aktive Manuskript-Preview- und Review-Anfragen werden gestoppt. Bereits gerenderte Figures, vorhandene Writer-Proposals und schon angewendeter Manuskripttext bleiben mit ihrer gespeicherten Provenienz erhalten."
            : "The spec, coverage, and evidence will be removed from the workspace; active manuscript preview and review requests are stopped. Existing rendered figures, Writer proposals, and already-applied manuscript text remain with their stored provenance.",
          action: german ? "Analyse löschen" : "Delete analysis",
          cancel: german ? "Behalten" : "Keep analysis",
        } : null}
        pending={remove.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && remove.mutate(deleteTarget)}
      />
      <ConfirmDeleteDialog
        target={connectionDeleteTarget ? {
          title: german ? "Private Verbindung entfernen?" : "Remove private connection?",
          description: german
            ? `Der gespeicherte Zugriff für ${connectionDeleteTarget.owner}/${connectionDeleteTarget.name} wird dauerhaft gelöscht. Aktive Analysen werden abgebrochen; bereits fertige abgeleitete Ergebnisse bleiben ohne Repository-Identität erhalten. Der Token bleibt bei GitHub gültig; widerrufe ihn dort, wenn du ihn nicht mehr benötigst.`
            : `Stored access for ${connectionDeleteTarget.owner}/${connectionDeleteTarget.name} will be permanently deleted. Active analyses are cancelled; existing derived results remain without repository identity. The token remains valid on GitHub; revoke it there if you no longer need it.`,
          action: german ? "Verbindung entfernen" : "Remove connection",
          cancel: german ? "Behalten" : "Keep connection",
        } : null}
        pending={removeConnection.isPending}
        onCancel={() => {
          if (!removeConnection.isPending) setConnectionDeleteTarget(null);
        }}
        onConfirm={() => {
          if (connectionDeleteTarget && !removeConnection.isPending) {
            removeConnection.mutate(connectionDeleteTarget.id);
          }
        }}
      />
    </Dialog>
  );
}
