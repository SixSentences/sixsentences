"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  Clipboard,
  ExternalLink,
  FileText,
  Loader2,
  MessageSquareText,
  PenLine,
  ShieldCheck,
  Sparkles,
  Square,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, api } from "@/lib/api";
import type {
  RepositoryAnalysis,
  RepositoryEvidence,
  RepositoryManuscriptClaim,
  RepositoryManuscriptKind,
  RepositoryManuscriptPreview,
  RepositoryManuscriptPreviewRequest,
  RepositoryManuscriptProposal,
  RepositoryManuscriptProposalRequest,
  WriterSummary,
} from "@/lib/types";
import {
  userFacingErrorMessage,
  userFacingStoredErrorMessage,
} from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

const STORAGE_PREFIX = "six:repository-manuscript:";
const COMMIT_SHA = /^[0-9a-f]{40}$/i;
const CONTENT_HASH = /^[0-9a-f]{64}$/i;

type PreviewIntent = {
  requestId: string;
  kind: RepositoryManuscriptKind;
  language: "en" | "de";
  submitted: boolean;
};

type StoredPreviewIntent = PreviewIntent & {
  sourceKey: string;
  proposal?: StoredProposalIntent;
};

type StoredProposalIntent = {
  requestId: string;
  bindingKey: string;
  targetWriterId: string;
  submitted: boolean;
};

type PreviewReceipt = {
  preview: RepositoryManuscriptPreview;
  sourceKey: string;
};

type ProposalReceipt = {
  proposal: RepositoryManuscriptProposal;
  writer: WriterSummary;
  sourceKey: string;
  previewHash: string;
};

type RepositoryManuscriptPanelProps = {
  analysis: RepositoryAnalysis;
  userId: number;
  german: boolean;
};

type RepositoryManuscriptDialogProps = {
  analysisId: string | null;
  userId: number;
  german: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const COPY_KINDS: Array<{
  id: RepositoryManuscriptKind;
  en: string;
  de: string;
  detailEn: string;
  detailDe: string;
}> = [
  {
    id: "caption",
    en: "Caption",
    de: "Caption",
    detailEn: "A concise figure caption. Copy-only in this version.",
    detailDe: "Eine knappe Bildunterschrift. In dieser Version nur kopierbar.",
  },
  {
    id: "description",
    en: "Short description",
    de: "Kurzbeschreibung",
    detailEn: "A manuscript-ready explanatory paragraph.",
    detailDe: "Ein manuskriptfertiger erklärender Absatz.",
  },
  {
    id: "section",
    en: "Section",
    de: "Abschnitt",
    detailEn: "A structured section grounded in the verified repository spec.",
    detailDe: "Ein strukturierter, in der verifizierten Repository-Spec verankerter Abschnitt.",
  },
];

function createRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16);
    return (token === "x" ? value : (value & 0x3) | 0x8).toString(16);
  });
}

function sourceKey(analysis: RepositoryAnalysis): string {
  return `${analysis.public_id}:${analysis.commit_sha ?? "unresolved"}:${analysis.updated_at}`;
}

function identitySourceKey(userId: number, analysis: RepositoryAnalysis): string {
  return `${userId}:${sourceKey(analysis)}`;
}

function storageKey(userId: number, analysisId: string): string {
  return `${STORAGE_PREFIX}${userId}:${analysisId}`;
}

function freshIntent(language: "en" | "de"): PreviewIntent {
  return {
    requestId: createRequestId(),
    kind: "description",
    language,
    submitted: false,
  };
}

function readIntent(
  userId: number,
  analysis: RepositoryAnalysis,
): PreviewIntent {
  const fallback = freshIntent(analysis.language);
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.sessionStorage.getItem(storageKey(userId, analysis.public_id));
    if (!raw) return fallback;
    const stored = JSON.parse(raw) as Partial<StoredPreviewIntent>;
    if (
      stored.sourceKey !== identitySourceKey(userId, analysis)
      || typeof stored.requestId !== "string"
      || !["caption", "description", "section"].includes(String(stored.kind))
      || !["en", "de"].includes(String(stored.language))
      || typeof stored.submitted !== "boolean"
    ) return fallback;
    return {
      requestId: stored.requestId,
      kind: stored.kind as RepositoryManuscriptKind,
      language: stored.language as "en" | "de",
      submitted: stored.submitted,
    };
  } catch {
    window.sessionStorage.removeItem(storageKey(userId, analysis.public_id));
    return fallback;
  }
}

function readProposalIntent(
  userId: number,
  analysis: RepositoryAnalysis,
): StoredProposalIntent | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(storageKey(userId, analysis.public_id));
    if (!raw) return null;
    const stored = JSON.parse(raw) as Partial<StoredPreviewIntent>;
    const proposal = stored.proposal as Partial<StoredProposalIntent> | undefined;
    if (
      stored.sourceKey !== identitySourceKey(userId, analysis)
      || !proposal
      || typeof proposal.requestId !== "string"
      || typeof proposal.bindingKey !== "string"
      || typeof proposal.targetWriterId !== "string"
      || typeof proposal.submitted !== "boolean"
    ) return null;
    return proposal as StoredProposalIntent;
  } catch {
    return null;
  }
}

function evidenceId(evidence: RepositoryEvidence): string | null {
  return evidence.id ?? evidence.evidence_id ?? evidence.hash ?? null;
}

function evidencePath(evidence: RepositoryEvidence): string {
  return evidence.path ?? evidence.source_path ?? "";
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

function safeEvidencePath(path: string): boolean {
  return Boolean(path)
    && !path.startsWith("/")
    && !path.includes("\\")
    && !path.split("/").includes("..")
    && !path.includes("\0");
}

function evidenceUrl(
  analysis: RepositoryAnalysis,
  evidence: RepositoryEvidence,
): string | null {
  const path = evidencePath(evidence);
  if (!analysis.commit_sha || !COMMIT_SHA.test(analysis.commit_sha) || !safeEvidencePath(path)) {
    return null;
  }
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  const [start, end] = evidenceLines(evidence);
  const lines = start ? `#L${start}${end && end !== start ? `-L${end}` : ""}` : "";
  return `https://github.com/${encodeURIComponent(analysis.owner)}/${encodeURIComponent(
    analysis.name,
  )}/blob/${analysis.commit_sha}/${encodedPath}${lines}`;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

function canonicalJson(value: unknown): string {
  const normalize = (candidate: unknown): unknown => {
    if (Array.isArray(candidate)) return candidate.map(normalize);
    if (!candidate || typeof candidate !== "object") return candidate;
    return Object.fromEntries(
      Object.entries(candidate as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, normalize(item)]),
    );
  };
  return JSON.stringify(normalize(value));
}

function validClaim(
  value: unknown,
  nodeIds: Set<string>,
  edgeIds: Set<string>,
  evidenceIds: Set<string>,
): value is RepositoryManuscriptClaim {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const claim = value as Partial<RepositoryManuscriptClaim>;
  if (
    typeof claim.text !== "string"
    || claim.text.trim().length === 0
    || !isStringArray(claim.node_ids)
    || !isStringArray(claim.edge_ids)
    || !Array.isArray(claim.evidence_ids)
    || !claim.evidence_ids.every((id) => typeof id === "string" && id.length > 0)
    || !["topology", "coverage"].includes(String(claim.support))
  ) return false;
  if (!claim.node_ids.every((id) => nodeIds.has(id))) return false;
  if (!claim.edge_ids.every((id) => edgeIds.has(id))) return false;
  if (!claim.evidence_ids.every((id) => evidenceIds.has(id))) return false;
  if (claim.support === "topology") {
    return (claim.node_ids.length > 0 || claim.edge_ids.length > 0)
      && claim.evidence_ids.length > 0;
  }
  return claim.node_ids.length === 0 && claim.edge_ids.length === 0;
}

function validatePreview(
  value: unknown,
  analysis: RepositoryAnalysis,
  request: RepositoryManuscriptPreviewRequest,
): RepositoryManuscriptPreview {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("The prose preview response was malformed. Nothing was sent to a manuscript.");
  }
  const preview = value as Partial<RepositoryManuscriptPreview>;
  const nodes = analysis.diagram_spec?.nodes ?? [];
  const edges = analysis.diagram_spec?.edges ?? [];
  if (
    nodes.length === 0
    || nodes.some((node) => !node.id)
    || new Set(nodes.map((node) => node.id)).size !== nodes.length
    || edges.some((edge) => !edge.id)
    || new Set(edges.map((edge) => edge.id)).size !== edges.length
  ) {
    throw new Error(
      "The canonical repository specification no longer exposes stable node and relationship IDs. Nothing was sent to a manuscript.",
    );
  }
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edgeIds = new Set(edges.map((edge) => edge.id as string));
  const localEvidenceIds = new Set(
    analysis.evidence.flatMap((item) => [item.id, item.evidence_id, item.hash])
      .filter((id): id is string => typeof id === "string" && id.length > 0),
  );
  const validSelectedNodes = isStringArray(preview.selected_node_ids)
    && preview.selected_node_ids.length > 0
    && new Set(preview.selected_node_ids).size === preview.selected_node_ids.length
    && preview.selected_node_ids.every((id) => nodeIds.has(id));
  const validSelectedEdges = isStringArray(preview.selected_edge_ids)
    && new Set(preview.selected_edge_ids).size === preview.selected_edge_ids.length
    && preview.selected_edge_ids.every((id) => edgeIds.has(id));
  const validEvidenceUnion = isStringArray(preview.evidence_ids)
    && preview.evidence_ids.length > 0
    && preview.evidence_ids.every((id) => localEvidenceIds.has(id));
  const validCounts = Number.isInteger(preview.described_node_count)
    && Number(preview.described_node_count) > 0
    && preview.described_node_count === preview.selected_node_ids?.length
    && Number.isInteger(preview.spec_node_count)
    && preview.spec_node_count === nodes.length
    && Number.isInteger(preview.described_edge_count)
    && Number(preview.described_edge_count) >= 0
    && preview.described_edge_count === preview.selected_edge_ids?.length
    && Number.isInteger(preview.spec_edge_count)
    && preview.spec_edge_count === edges.length;
  const selectedNodeIds = new Set(preview.selected_node_ids ?? []);
  const selectedEdgeIds = new Set(preview.selected_edge_ids ?? []);
  const selectedEvidenceIds = new Set(preview.evidence_ids ?? []);
  const claimsMatchSelection = Array.isArray(preview.claims)
    && preview.claims.every((claim) =>
      claim.node_ids.every((id) => selectedNodeIds.has(id))
      && claim.edge_ids.every((id) => selectedEdgeIds.has(id))
      && claim.evidence_ids.every((id) => selectedEvidenceIds.has(id)));
  const orderedClaimEvidence = Array.from(new Set(
    (preview.claims ?? []).flatMap((claim) => claim.evidence_ids),
  ));
  const selectedEdgesHaveSelectedEndpoints = (preview.selected_edge_ids ?? []).every((id) => {
    const edge = edges.find((candidate) => candidate.id === id);
    return Boolean(edge && selectedNodeIds.has(edge.source) && selectedNodeIds.has(edge.target));
  });
  const kindSelectionIsValid = request.kind === "caption"
    ? Number(preview.described_node_count) <= 6 && Number(preview.described_edge_count) <= 1
    : request.kind === "description"
      ? Number(preview.described_node_count) <= 12 && Number(preview.described_edge_count) <= 3
      : selectedNodeIds.size === nodes.length && selectedEdgeIds.size === edges.length;
  if (
    preview.request_id !== request.request_id
    || preview.analysis_id !== analysis.public_id
    || preview.analysis_updated_at !== analysis.updated_at
    || preview.commit_sha !== analysis.commit_sha
    || typeof preview.grounding_sha256 !== "string"
    || !CONTENT_HASH.test(preview.grounding_sha256)
    || preview.kind !== request.kind
    || preview.language !== request.language
    || typeof preview.text !== "string"
    || preview.text.trim().length === 0
    || typeof preview.preview_sha256 !== "string"
    || !CONTENT_HASH.test(preview.preview_sha256)
    || !Array.isArray(preview.claims)
    || preview.claims.length === 0
    || !preview.claims.every((claim) => validClaim(claim, nodeIds, edgeIds, localEvidenceIds))
    || !validSelectedNodes
    || !validSelectedEdges
    || !validEvidenceUnion
    || !validCounts
    || !claimsMatchSelection
    || JSON.stringify(orderedClaimEvidence) !== JSON.stringify(preview.evidence_ids)
    || !selectedEdgesHaveSelectedEndpoints
    || !kindSelectionIsValid
    || typeof preview.scope_note !== "string"
    || preview.scope_note.trim().length === 0
    || !preview.provenance
    || preview.provenance.grounding !== "repository_spec"
    || preview.provenance.commit_sha !== analysis.commit_sha
    || preview.provenance.repository_url !== analysis.repository_url
    || preview.provenance.grounding_sha256 !== preview.grounding_sha256
    || !isStringArray(preview.provenance.evidence_ids)
    || !preview.provenance.evidence_ids.every((id) => localEvidenceIds.has(id))
    || JSON.stringify(preview.provenance.evidence_ids) !== JSON.stringify(preview.evidence_ids)
    || preview.provenance.scope_note !== preview.scope_note
    || !preview.provenance.coverage
    || typeof preview.provenance.coverage !== "object"
    || !analysis.coverage
    || canonicalJson(preview.provenance.coverage) !== canonicalJson(analysis.coverage)
    || !preview.consent
    || preview.consent.ai_generation_confirmed !== true
    || preview.consent.purpose_version !== "repository-manuscript-prose-v1"
    || typeof preview.consent.confirmed_at !== "string"
    || typeof preview.generated_at !== "string"
  ) {
    throw new Error(
      "The prose preview did not match the pinned repository analysis or its claim evidence. Nothing was sent to a manuscript.",
    );
  }
  const joinedClaims = preview.claims.map((claim) => claim.text.trim()).join(" ");
  if (preview.text.trim() !== joinedClaims) {
    throw new Error(
      "The prose preview text did not match its ordered grounded claims. Nothing was sent to a manuscript.",
    );
  }
  return preview as RepositoryManuscriptPreview;
}

function validateProposal(
  value: unknown,
  analysisId: string,
  repositoryAccess: "public" | "private",
  writer: WriterSummary,
  request: RepositoryManuscriptProposalRequest,
): RepositoryManuscriptProposal {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("The manuscript proposal response was malformed. The manuscript was not changed.");
  }
  const proposal = value as Partial<RepositoryManuscriptProposal>;
  if (
    proposal.request_id !== request.request_id
    || proposal.analysis_id !== analysisId
    || proposal.repository_access !== repositoryAccess
    || !Number.isInteger(proposal.message_id)
    || proposal.writer_document_id !== request.writer_document_id
    || proposal.writer_document_id !== writer.public_id
    || proposal.expected_writer_revision !== request.expected_writer_revision
    || !Array.isArray(proposal.edits)
    || proposal.edits.length === 0
    || !proposal.edits.every((edit) =>
      edit
      && typeof edit.path === "string"
      && typeof edit.find === "string"
      && typeof edit.replace === "string"
      && edit.applicable === true)
    || !proposal.verification
    || proposal.verification.status !== "passed"
    || proposal.requires_manual_review !== true
    || typeof proposal.idempotent !== "boolean"
  ) {
    throw new Error(
      "The verified Writer proposal could not be validated. The manuscript was not changed.",
    );
  }
  return proposal as RepositoryManuscriptProposal;
}

function errorCopy(error: unknown, german: boolean, action: "preview" | "proposal"): string {
  if (error instanceof ApiError) {
    if (error.status === 404) {
      return german
        ? "Die Repository-Analyse oder das Zielmanuskript ist nicht mehr verfügbar. Lade die Ansicht neu; es wurde nichts geändert."
        : "The repository analysis or target manuscript is no longer available. Reload this view; nothing was changed.";
    }
    if (error.status === 403) {
      return german
        ? "Deine Berechtigung für diese Analyse oder das Zielmanuskript hat sich geändert. Es wurde nichts geändert."
        : "Your permission for this analysis or target manuscript changed. Nothing was changed.";
    }
    if (error.status === 409) {
      return german
        ? "Analyse, Preview oder Manuskript hat sich seit deiner Auswahl geändert. Aktualisiere die Daten und starte den Schritt erneut; es wurde nichts geändert."
        : "The analysis, preview, or manuscript changed after you selected it. Refresh and start this step again; nothing was changed.";
    }
    if (error.status === 422) {
      return german
        ? "Der Server hat die gebundene Preview-Anfrage abgelehnt. Prüfe Auswahl und Einwilligung; es wurde nichts geändert."
        : "The server rejected the bound preview request. Check the selection and consent; nothing was changed.";
    }
    if (error.status === 0) {
      return german
        ? "Die Verbindung wurde unterbrochen. Derselbe Request kann sicher erneut geprüft werden; ein Manuskript wurde nicht verändert."
        : "The connection was interrupted. The same request can be checked safely again; no manuscript was changed.";
    }
    if (error.message) {
      return userFacingErrorMessage(
        error,
        action === "preview"
          ? german
            ? "Die Preview konnte nicht erstellt werden."
            : "The preview could not be generated."
          : german
            ? "Das Review-Proposal konnte nicht erstellt werden."
            : "The review proposal could not be created.",
      );
    }
  }
  if (error instanceof Error && error.message) {
    return userFacingErrorMessage(
      error,
      action === "preview"
        ? german
          ? "Die Preview konnte nicht erstellt werden."
          : "The preview could not be generated."
        : german
          ? "Das Review-Proposal konnte nicht erstellt werden."
          : "The review proposal could not be created.",
    );
  }
  return action === "preview"
    ? german ? "Die Preview konnte nicht erstellt werden." : "The preview could not be generated."
    : german ? "Das Review-Proposal konnte nicht erstellt werden." : "The review proposal could not be created.";
}

function editableWriter(writer: WriterSummary): boolean {
  return writer.access_role === "owner" || writer.access_role === "editor";
}

function coverageCopy(preview: RepositoryManuscriptPreview, german: boolean): string {
  const coverage = preview.provenance.coverage;
  if (!coverage) return german ? "Coverage nicht gemeldet" : "Coverage not reported";
  const analyzed = typeof coverage.analyzed_files === "number" ? coverage.analyzed_files : null;
  const eligible = typeof coverage.eligible_files === "number" ? coverage.eligible_files : null;
  const status = coverage.complete === true
    ? german ? "vollständig" : "complete"
    : coverage.complete === false
      ? german ? "unvollständig" : "incomplete"
      : german ? "gemeldet" : "reported";
  if (analyzed !== null && eligible !== null) {
    return german
      ? `Coverage ${status}: ${analyzed.toLocaleString()} von ${eligible.toLocaleString()} geeigneten Dateien analysiert`
      : `Coverage ${status}: ${analyzed.toLocaleString()} of ${eligible.toLocaleString()} eligible files analyzed`;
  }
  return german ? `Coverage ${status}` : `Coverage ${status}`;
}

function clipboardText(
  preview: RepositoryManuscriptPreview,
  analysis: RepositoryAnalysis,
  german: boolean,
): string {
  const byId = new Map(
    analysis.evidence.map((item, index) => [evidenceId(item), { item, index }]),
  );
  const evidence = preview.provenance.evidence_ids.map((id) => {
    const match = byId.get(id);
    if (!match) return `- ${id}`;
    const path = evidencePath(match.item) || id;
    const [start, end] = evidenceLines(match.item);
    const lines = start ? `:${start}${end && end !== start ? `-${end}` : ""}` : "";
    return `- E${match.index + 1} ${path}${lines}`;
  });
  const subpath = analysis.subpath || (german ? "Repository-Root" : "repository root");
  return [
    preview.text.trim(),
    "",
    german ? "Provenienz (mitkopiert)" : "Provenance (copied with text)",
    `${analysis.owner}/${analysis.name}@${preview.commit_sha}`,
    `${german ? "Analysierter Pfad" : "Analyzed path"}: ${subpath}`,
    `${german ? "Aussageumfang" : "Claim scope"}: ${preview.scope_note}`,
    `${preview.described_node_count}/${preview.spec_node_count} ${german ? "Komponenten beschrieben" : "components described"}; ${preview.described_edge_count}/${preview.spec_edge_count} ${german ? "Beziehungen beschrieben" : "relationships described"}`,
    coverageCopy(preview, german),
    german
      ? "Grundlage: kanonische, verifizierte Repository-Spec – nicht das Styling der gerenderten Grafik."
      : "Grounding: canonical verified repository spec — not the styling of the rendered visual.",
    ...(evidence.length ? [german ? "Belege:" : "Evidence:", ...evidence] : []),
  ].join("\n");
}

export function RepositoryManuscriptPanel({
  analysis,
  userId,
  german,
}: RepositoryManuscriptPanelProps) {
  const queryClient = useQueryClient();
  const analysisSourceKey = identitySourceKey(userId, analysis);
  const [intent, setIntent] = useState<PreviewIntent>(() => readIntent(userId, analysis));
  const [storedProposalAtMount] = useState<StoredProposalIntent | null>(
    () => readProposalIntent(userId, analysis),
  );
  const [aiConfirmed, setAiConfirmed] = useState(false);
  const [previewReceipt, setPreviewReceipt] = useState<PreviewReceipt | null>(null);
  const [selectedWriterId, setSelectedWriterId] = useState(
    storedProposalAtMount?.targetWriterId ?? "",
  );
  const [proposalIntent, setProposalIntent] = useState<StoredProposalIntent>(
    storedProposalAtMount ?? {
      requestId: createRequestId(),
      bindingKey: "",
      targetWriterId: "",
      submitted: false,
    },
  );
  const [proposalReceipt, setProposalReceipt] = useState<ProposalReceipt | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const [proposalCancelRequested, setProposalCancelRequested] = useState(false);
  const [proposalRecoveryError, setProposalRecoveryError] = useState<string | null>(null);
  const sourceKeyRef = useRef(analysisSourceKey);
  const resetSourceKeyRef = useRef(analysisSourceKey);
  const intentRef = useRef(intent);
  const previewReceiptRef = useRef<PreviewReceipt | null>(previewReceipt);
  const selectedTargetRef = useRef("");
  const previewInFlightRef = useRef(false);
  const proposalInFlightRef = useRef(false);

  sourceKeyRef.current = analysisSourceKey;
  intentRef.current = intent;
  previewReceiptRef.current = previewReceipt;
  selectedTargetRef.current = selectedWriterId;

  const writers = useQuery({
    queryKey: ["writer-docs", userId],
    queryFn: api.writerList,
    enabled: Boolean(previewReceipt),
  });
  const editableWriters = useMemo(
    () => (writers.data ?? []).filter(editableWriter),
    [writers.data],
  );
  const selectedWriter = editableWriters.find(
    (writer) => writer.public_id === selectedWriterId,
  ) ?? null;
  const proposalBindingKey = selectedWriter && previewReceipt
    ? `${analysisSourceKey}:${previewReceipt.preview.preview_sha256}:${selectedWriter.public_id}:${selectedWriter.revision}:${selectedWriter.access_role}`
    : "";

  useEffect(() => {
    try {
      window.sessionStorage.setItem(
        storageKey(userId, analysis.public_id),
        JSON.stringify({
          ...intent,
          sourceKey: analysisSourceKey,
          ...(proposalIntent.bindingKey ? { proposal: proposalIntent } : {}),
        } satisfies StoredPreviewIntent),
      );
    } catch {
      // The UI remains usable when hardened browsers disable session storage.
    }
  }, [analysis.public_id, analysisSourceKey, intent, proposalIntent, userId]);

  const preview = useMutation({
    mutationFn: async (variables: {
      sourceKey: string;
      request: RepositoryManuscriptPreviewRequest;
    }) => validatePreview(
      await api.repositoryManuscriptPreview(analysis.public_id, variables.request),
      analysis,
      variables.request,
    ),
    onSuccess: (result, variables) => {
      if (
        sourceKeyRef.current !== variables.sourceKey
        || intentRef.current.requestId !== variables.request.request_id
      ) return;
      setPreviewReceipt({ preview: result, sourceKey: variables.sourceKey });
      setRecoveryError(null);
      setCancelRequested(false);
      setProposalIntent({
        requestId: createRequestId(),
        bindingKey: "",
        targetWriterId: "",
        submitted: false,
      });
      setProposalReceipt(null);
      setProposalCancelRequested(false);
      setProposalRecoveryError(null);
    },
    onSettled: () => {
      previewInFlightRef.current = false;
    },
  });

  const recoveredTurn = useQuery({
    queryKey: ["repository-manuscript-turn", userId, analysis.public_id, intent.requestId],
    queryFn: () => api.agentTurnStatus(intent.requestId),
    enabled: intent.submitted && !preview.isPending && !previewReceipt,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" || status === "cancel_requested"
        ? 1_500
        : false;
    },
  });

  const stop = useMutation({
    mutationFn: (turnId: string) => api.agentTurnStop(turnId),
    onSuccess: () => {
      setCancelRequested(true);
      void recoveredTurn.refetch();
    },
  });

  const proposal = useMutation({
    mutationFn: async (variables: {
      sourceKey: string;
      previewHash: string;
      writer: WriterSummary;
      request: RepositoryManuscriptProposalRequest;
    }) => validateProposal(
      await api.repositoryManuscriptProposal(analysis.public_id, variables.request),
      analysis.public_id,
      analysis.repository_access ?? "public",
      variables.writer,
      variables.request,
    ),
    onSuccess: (result, variables) => {
      if (
        sourceKeyRef.current !== variables.sourceKey
        || selectedTargetRef.current !== variables.writer.public_id
        || previewReceiptRef.current?.preview.preview_sha256 !== variables.previewHash
      ) return;
      setProposalReceipt({
        proposal: result,
        writer: variables.writer,
        sourceKey: variables.sourceKey,
        previewHash: variables.previewHash,
      });
      setProposalCancelRequested(false);
      setProposalRecoveryError(null);
      void queryClient.invalidateQueries({ queryKey: ["writer-chat", variables.writer.public_id] });
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
    },
    onSettled: () => {
      proposalInFlightRef.current = false;
    },
  });

  const recoveredProposalTurn = useQuery({
    queryKey: [
      "repository-manuscript-proposal-turn",
      userId,
      analysis.public_id,
      proposalIntent.requestId,
    ],
    queryFn: () => api.agentTurnStatus(proposalIntent.requestId),
    enabled: Boolean(
      proposalIntent.submitted
      && proposalIntent.bindingKey
      && proposalIntent.bindingKey === proposalBindingKey
      && previewReceipt
      && selectedWriter
      && !proposal.isPending
      && !proposalReceipt,
    ),
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" || status === "cancel_requested"
        ? 1_500
        : false;
    },
  });

  const stopProposal = useMutation({
    mutationFn: (turnId: string) => api.agentTurnStop(turnId),
    onSuccess: () => {
      setProposalCancelRequested(true);
      void recoveredProposalTurn.refetch();
    },
  });

  useEffect(() => {
    if (resetSourceKeyRef.current === analysisSourceKey) return;
    resetSourceKeyRef.current = analysisSourceKey;
    setIntent(freshIntent(analysis.language));
    setAiConfirmed(false);
    setPreviewReceipt(null);
    setSelectedWriterId("");
    setProposalIntent({
      requestId: createRequestId(),
      bindingKey: "",
      targetWriterId: "",
      submitted: false,
    });
    setProposalReceipt(null);
    setCancelRequested(false);
    setRecoveryError(null);
    setProposalCancelRequested(false);
    setProposalRecoveryError(null);
    preview.reset();
    proposal.reset();
  // The source key deliberately includes commit and updated_at. A changed
  // analysis invalidates every local preview and proposal fence.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisSourceKey]);

  useEffect(() => {
    if (!selectedWriterId) return;
    if (writers.isLoading) return;
    if (editableWriters.some((writer) => writer.public_id === selectedWriterId)) return;
    setSelectedWriterId("");
    setProposalIntent({
      requestId: createRequestId(),
      bindingKey: "",
      targetWriterId: "",
      submitted: false,
    });
    setProposalReceipt(null);
    setProposalCancelRequested(false);
    setProposalRecoveryError(null);
    proposal.reset();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editableWriters, selectedWriterId, writers.isLoading]);

  useEffect(() => {
    if (!previewReceipt || writers.isLoading) return;
    if (proposalIntent.bindingKey === proposalBindingKey) return;
    setProposalIntent({
      requestId: createRequestId(),
      bindingKey: proposalBindingKey,
      targetWriterId: selectedWriter?.public_id ?? "",
      submitted: false,
    });
    setProposalReceipt(null);
    setProposalCancelRequested(false);
    setProposalRecoveryError(null);
    proposal.reset();
  // The selected target's exact revision and permission are part of the fence.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewReceipt, proposalBindingKey, proposalIntent.bindingKey, writers.isLoading]);

  useEffect(() => {
    const turn = recoveredTurn.data;
    if (!turn || previewReceipt) return;
    if (
      turn.turn_id !== intent.requestId
      || turn.resource_kind !== "repository-prose"
      || turn.resource_id !== analysis.public_id
    ) {
      setRecoveryError(
        german
          ? "Der gespeicherte Preview-Status gehört nicht zur ausgewählten Analyse. Es wurde nichts an ein Manuskript gesendet."
          : "The saved preview status does not belong to the selected analysis. Nothing was sent to a manuscript.",
      );
      return;
    }
    if (turn.status === "completed") {
      try {
        const request: RepositoryManuscriptPreviewRequest = {
          request_id: intent.requestId,
          kind: intent.kind,
          language: intent.language,
          ai_generation_confirmed: true,
        };
        const result = validatePreview(turn.result, analysis, request);
        setPreviewReceipt({ preview: result, sourceKey: analysisSourceKey });
        setRecoveryError(null);
      } catch (error) {
        setRecoveryError(errorCopy(error, german, "preview"));
      }
    } else if (turn.status === "failed" || turn.status === "cancelled") {
      setRecoveryError(
        userFacingStoredErrorMessage(
          turn.error_message,
          german
            ? "Die gespeicherte Preview-Anfrage wurde beendet. Starte eine neue Preview."
            : "The saved preview request ended. Start a new preview.",
        ),
      );
      setIntent((current) => ({ ...current, requestId: createRequestId(), submitted: false }));
      setAiConfirmed(false);
      setCancelRequested(false);
      preview.reset();
    }
  }, [analysis, analysisSourceKey, german, intent, previewReceipt, recoveredTurn.data]);

  useEffect(() => {
    const turn = recoveredProposalTurn.data;
    if (
      !turn
      || proposalReceipt
      || !previewReceipt
      || !selectedWriter
      || proposalIntent.bindingKey !== proposalBindingKey
    ) return;
    if (
      turn.turn_id !== proposalIntent.requestId
      || turn.resource_kind !== "repository-proposal"
      || turn.resource_id !== analysis.public_id
    ) {
      setProposalRecoveryError(
        german
          ? "Der gespeicherte Proposal-Status gehört nicht zu dieser Analyse. Das Manuskript blieb unverändert."
          : "The saved proposal status does not belong to this analysis. The manuscript stayed unchanged.",
      );
      return;
    }
    if (turn.status === "completed") {
      try {
        const request: RepositoryManuscriptProposalRequest = {
          request_id: proposalIntent.requestId,
          preview_request_id: previewReceipt.preview.request_id,
          preview_sha256: previewReceipt.preview.preview_sha256,
          writer_document_id: selectedWriter.public_id,
          expected_writer_revision: selectedWriter.revision,
        };
        const result = validateProposal(
          turn.result,
          analysis.public_id,
          analysis.repository_access ?? "public",
          selectedWriter,
          request,
        );
        setProposalReceipt({
          proposal: result,
          writer: selectedWriter,
          sourceKey: analysisSourceKey,
          previewHash: previewReceipt.preview.preview_sha256,
        });
        setProposalRecoveryError(null);
        setProposalCancelRequested(false);
        void queryClient.invalidateQueries({
          queryKey: ["writer-chat", selectedWriter.public_id],
        });
        void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
      } catch (error) {
        setProposalRecoveryError(errorCopy(error, german, "proposal"));
      }
    } else if (turn.status === "failed" || turn.status === "cancelled") {
      setProposalRecoveryError(
        userFacingStoredErrorMessage(
          turn.error_message,
          german
            ? "Die gespeicherte Proposal-Anfrage wurde beendet. Du kannst eine neue Review-Anfrage starten; das Manuskript blieb unverändert."
            : "The saved proposal request ended. You can start a new review request; the manuscript stayed unchanged.",
        ),
      );
      setProposalIntent({
        requestId: createRequestId(),
        bindingKey: proposalBindingKey,
        targetWriterId: selectedWriter.public_id,
        submitted: false,
      });
      setProposalCancelRequested(false);
      proposalInFlightRef.current = false;
      proposal.reset();
    }
  }, [
    analysis.public_id,
    analysisSourceKey,
    german,
    previewReceipt,
    proposalBindingKey,
    proposalIntent,
    proposalReceipt,
    queryClient,
    recoveredProposalTurn.data,
    selectedWriter,
  ]);

  const movingTurn = preview.isPending
    || recoveredTurn.data?.status === "queued"
    || recoveredTurn.data?.status === "running"
    || recoveredTurn.data?.status === "cancel_requested";
  const movingProposal = proposal.isPending
    || recoveredProposalTurn.data?.status === "queued"
    || recoveredProposalTurn.data?.status === "running"
    || recoveredProposalTurn.data?.status === "cancel_requested";

  const resetPreviewIntent = (patch: Partial<Pick<PreviewIntent, "kind" | "language">>) => {
    if (movingProposal) return;
    setIntent((current) => ({
      ...current,
      ...patch,
      requestId: createRequestId(),
      submitted: false,
    }));
    setAiConfirmed(false);
    setPreviewReceipt(null);
    setProposalIntent({
      requestId: createRequestId(),
      bindingKey: "",
      targetWriterId: "",
      submitted: false,
    });
    setProposalReceipt(null);
    setCancelRequested(false);
    setRecoveryError(null);
    setProposalCancelRequested(false);
    setProposalRecoveryError(null);
    preview.reset();
    proposal.reset();
  };

  const generatePreview = () => {
    if (!aiConfirmed || movingTurn || movingProposal || previewInFlightRef.current) return;
    if (previewReceipt) {
      resetPreviewIntent({});
      return;
    }
    previewInFlightRef.current = true;
    const nextIntent = {
      ...intent,
      submitted: true,
    };
    setIntent(nextIntent);
    setPreviewReceipt(null);
    setProposalReceipt(null);
    setRecoveryError(null);
    const request: RepositoryManuscriptPreviewRequest = {
      request_id: nextIntent.requestId,
      kind: nextIntent.kind,
      language: nextIntent.language,
      ai_generation_confirmed: true,
    };
    preview.mutate({ sourceKey: analysisSourceKey, request });
  };

  const sendToReview = () => {
    if (
      intent.kind === "caption"
      || !previewReceipt
      || previewReceipt.sourceKey !== analysisSourceKey
      || !selectedWriter
      || !proposalBindingKey
      || proposalIntent.bindingKey !== proposalBindingKey
      || movingProposal
      || proposalInFlightRef.current
    ) return;
    proposalInFlightRef.current = true;
    const submittedIntent = { ...proposalIntent, submitted: true };
    setProposalIntent(submittedIntent);
    setProposalRecoveryError(null);
    setProposalCancelRequested(false);
    const request: RepositoryManuscriptProposalRequest = {
      request_id: submittedIntent.requestId,
      preview_request_id: previewReceipt.preview.request_id,
      preview_sha256: previewReceipt.preview.preview_sha256,
      writer_document_id: selectedWriter.public_id,
      expected_writer_revision: selectedWriter.revision,
    };
    proposal.mutate({
      sourceKey: analysisSourceKey,
      previewHash: previewReceipt.preview.preview_sha256,
      writer: selectedWriter,
      request,
    });
  };

  const evidenceById = useMemo(() => {
    const map = new Map<string, { evidence: RepositoryEvidence; index: number }>();
    analysis.evidence.forEach((item, index) => {
      [item.id, item.evidence_id, item.hash].forEach((id) => {
        if (id) map.set(id, { evidence: item, index });
      });
    });
    return map;
  }, [analysis.evidence]);

  const selectedKind = COPY_KINDS.find((item) => item.id === intent.kind) ?? COPY_KINDS[1];
  const previewError = preview.isError
    ? errorCopy(preview.error, german, "preview")
    : stop.isError
      ? errorCopy(stop.error, german, "preview")
    : recoveredTurn.isError && intent.submitted
      ? errorCopy(recoveredTurn.error, german, "preview")
      : recoveryError;
  const proposalError = !movingProposal && proposal.isError
    ? errorCopy(proposal.error, german, "proposal")
    : stopProposal.isError
      ? errorCopy(stopProposal.error, german, "proposal")
      : recoveredProposalTurn.isError && proposalIntent.submitted
        ? errorCopy(recoveredProposalTurn.error, german, "proposal")
        : proposalRecoveryError;

  return (
    <section
      className="rounded-2xl border border-moss/25 bg-card p-3.5"
      aria-labelledby={`repository-manuscript-${analysis.public_id}`}
    >
      <div className="flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-accent text-moss">
          <PenLine className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <p id={`repository-manuscript-${analysis.public_id}`} className="text-[0.75rem] font-medium text-foreground">
            {german ? "Repository-Wissen im Manuskript nutzen" : "Use repository knowledge in a manuscript"}
          </p>
          <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
            {german
              ? "Erzeuge prüfbare Prosa aus derselben kanonischen Spec. Ziel, Style-Brief und gerendertes PNG sind keine Faktenquelle."
              : "Generate reviewable prose from the same canonical spec. The user goal, style brief, and rendered PNG are not factual sources."}
          </p>
        </div>
      </div>

      <div className="mt-3" role="radiogroup" aria-label={german ? "Textart" : "Copy type"}>
        <div className="grid grid-cols-3 gap-1 rounded-xl bg-secondary/60 p-1">
          {COPY_KINDS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={intent.kind === item.id}
              disabled={movingTurn || movingProposal}
              onClick={() => resetPreviewIntent({ kind: item.id })}
              className={cn(
                "min-w-0 rounded-lg px-2 py-1.5 text-[0.625rem] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                intent.kind === item.id
                  ? "bg-card font-medium text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <span className="block truncate">{german ? item.de : item.en}</span>
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-[0.625rem] leading-relaxed text-muted-foreground">
          {german ? selectedKind.detailDe : selectedKind.detailEn}
        </p>
      </div>

      <div className="mt-3 space-y-1.5">
        <Label htmlFor={`repository-manuscript-language-${analysis.public_id}`} className="text-[0.65625rem]">
          {german ? "Ausgabesprache" : "Output language"}
        </Label>
        <Select
          value={intent.language}
          disabled={movingTurn || movingProposal}
          onValueChange={(value) => resetPreviewIntent({ language: value as "en" | "de" })}
        >
          <SelectTrigger id={`repository-manuscript-language-${analysis.public_id}`} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="en">English</SelectItem>
            <SelectItem value="de">Deutsch</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <label
        htmlFor={`repository-manuscript-consent-${analysis.public_id}`}
        className="mt-3 flex cursor-pointer items-start gap-2.5 rounded-xl border border-border bg-secondary/35 p-3"
      >
        <Checkbox
          id={`repository-manuscript-consent-${analysis.public_id}`}
          checked={aiConfirmed}
          disabled={movingTurn}
          onCheckedChange={(checked) => setAiConfirmed(checked === true)}
          className="mt-0.5"
        />
        <span className="text-[0.625rem] leading-relaxed text-muted-foreground">
          <span className="block font-medium text-foreground">
            {german ? "Zusätzliche KI-Verarbeitung bestätigen" : "Confirm the additional AI processing"}
          </span>
          {german
            ? "Der konfigurierte KI-Anbieter wählt und ordnet ausschließlich opake, validierte Komponenten- und Beziehungs-IDs aus einer reduzierten Topologie; übertragen werden nur erlaubte Strukturkategorien, Beziehungen und Konfidenzen. Keine Komponentenlabels, kein Rohcode, keine Dateipfade, Auszüge, Scope-/Zieltexte oder Belegpositionen."
            : "The configured AI provider only selects and orders opaque, validated component and relationship IDs from a reduced topology; only allowlisted structural categories, relationships, and confidence values are sent. No component labels, raw code, file paths, excerpts, scope or goal text, or evidence locations."}
        </span>
      </label>

      {previewError ? (
        <div role="alert" className="mt-3 rounded-xl border border-destructive/25 bg-destructive/5 p-3 text-[0.65625rem] leading-relaxed text-destructive">
          {previewError}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          disabled={!aiConfirmed || movingTurn || movingProposal}
          onClick={generatePreview}
          className="min-w-0 flex-1"
        >
          {movingTurn ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
          {movingTurn
            ? cancelRequested
              ? german ? "Abbruch angefordert…" : "Stop requested…"
              : german ? "Preview wird erstellt…" : "Generating preview…"
            : preview.isError || previewError
              ? german ? "Dieselbe Anfrage erneut prüfen" : "Retry the same request"
              : previewReceipt
                ? german ? "Neue Preview erzeugen" : "Generate a new preview"
                : german ? "Preview erzeugen" : "Generate preview"}
        </Button>
        {movingTurn ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={stop.isPending || cancelRequested}
            onClick={() => stop.mutate(intent.requestId)}
          >
            {stop.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Square className="size-3.5" />}
            {german ? "Stoppen" : "Stop"}
          </Button>
        ) : null}
      </div>

      {previewReceipt?.sourceKey === analysisSourceKey ? (
        <div className="mt-4 space-y-3" aria-live="polite">
          <div className="rounded-xl border border-moss/25 bg-accent/25 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="flex items-center gap-1.5 text-[0.6875rem] font-medium text-foreground">
                <ShieldCheck className="size-3.5 text-moss" />
                {german ? "Grounded Preview" : "Grounded preview"}
              </p>
              <span className="font-mono text-[0.53125rem] text-muted-foreground">
                {analysis.owner}/{analysis.name}@{previewReceipt.preview.commit_sha.slice(0, 10)}
              </span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-[0.71875rem] leading-relaxed text-foreground">
              {previewReceipt.preview.text}
            </p>
            <div className="mt-3 space-y-2 border-t border-border/70 pt-2">
              {previewReceipt.preview.claims.map((claim, claimIndex) => (
                <div key={`${claim.support}-${claimIndex}`} className="rounded-lg bg-card/75 p-2.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-[0.53125rem] uppercase tracking-[0.1em] text-muted-foreground">
                      {german ? `Aussage ${claimIndex + 1}` : `Claim ${claimIndex + 1}`}
                    </span>
                    <span className={cn(
                      "rounded-full px-1.5 py-0.5 font-mono text-[0.5rem] uppercase tracking-[0.08em]",
                      claim.support === "coverage"
                        ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
                        : "bg-accent text-moss",
                    )}>
                      {claim.support === "coverage"
                        ? german ? "Coverage" : "Coverage"
                        : german ? "Topologie" : "Topology"}
                    </span>
                  </div>
                  <p className="mt-1 text-[0.625rem] leading-relaxed text-muted-foreground">
                    {claim.text}
                  </p>
                  {claim.node_ids.length || claim.edge_ids.length ? (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {claim.node_ids.map((id) => <span key={`n-${id}`} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[0.5rem] text-muted-foreground">N · {id}</span>)}
                      {claim.edge_ids.map((id) => <span key={`e-${id}`} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[0.5rem] text-muted-foreground">R · {id}</span>)}
                    </div>
                  ) : null}
                  {claim.evidence_ids.length ? (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {claim.evidence_ids.map((id) => {
                        const match = evidenceById.get(id);
                        if (!match) return null;
                        const url = evidenceUrl(analysis, match.evidence);
                        const path = evidencePath(match.evidence);
                        const [start, end] = evidenceLines(match.evidence);
                        const label = `E${match.index + 1} · ${path}${start ? `:${start}${end && end !== start ? `–${end}` : ""}` : ""}`;
                        return url ? (
                          <a
                            key={id}
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={label}
                            className="inline-flex max-w-full items-center gap-1 rounded bg-accent px-1.5 py-0.5 font-mono text-[0.5rem] text-moss hover:underline"
                          >
                            <span className="truncate">{label}</span>
                            <ExternalLink className="size-2.5 shrink-0" />
                          </a>
                        ) : (
                          <span key={id} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[0.5rem] text-muted-foreground">{label}</span>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="mt-1.5 text-[0.5625rem] text-amber-700 dark:text-amber-300">
                      {german ? "Coverage-/Scope-Aussage aus den serverseitigen Analysezählern." : "Coverage/scope statement from server-side analysis counts."}
                    </p>
                  )}
                </div>
              ))}
            </div>
            <div className="mt-3 rounded-lg border border-border/80 bg-card/75 p-2.5 text-[0.59375rem] leading-relaxed text-muted-foreground">
              <p>{coverageCopy(previewReceipt.preview, german)}</p>
              <p className="mt-1">
                {german ? "Analysierter Pfad" : "Analyzed path"}: {analysis.subpath || (german ? "Repository-Root" : "repository root")}
              </p>
              <p className="mt-1">
                {german ? "Aussageumfang" : "Claim scope"}: {previewReceipt.preview.scope_note}
              </p>
              <p className="mt-1 font-mono text-[0.53125rem] uppercase tracking-[0.08em]">
                {previewReceipt.preview.described_node_count}/{previewReceipt.preview.spec_node_count} {german ? "Komponenten beschrieben" : "components described"} · {previewReceipt.preview.described_edge_count}/{previewReceipt.preview.spec_edge_count} {german ? "Beziehungen beschrieben" : "relationships described"}
              </p>
              <p className="mt-1">
                {german
                  ? "Diese Prosa beschreibt die kanonische Spec, nicht das visuelle Styling des PNGs."
                  : "This prose describes the canonical spec, not the visual styling of the PNG."}
              </p>
            </div>
            <p className="mt-3 text-[0.5625rem] leading-relaxed text-muted-foreground">
              {german
                ? "„Nur Text“ kopiert manuskriptfertige Prosa ohne Belegblock. Die sichere Alternative nimmt Commit, Scope, Coverage und E# mit."
                : "“Text only” copies manuscript-ready prose without the evidence block. The safer alternative keeps the commit, scope, coverage, and E# references."}
            </p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(previewReceipt.preview.text.trim());
                    toast.success(german ? "Nur Text kopiert." : "Text only copied.");
                  } catch {
                    toast.error(german ? "Kopieren wurde vom Browser blockiert." : "The browser blocked clipboard access.");
                  }
                }}
              >
                <Clipboard className="size-3.5" />
                {german ? "Nur Text kopieren" : "Copy text"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(
                      clipboardText(previewReceipt.preview, analysis, german),
                    );
                    toast.success(german ? "Text mit Provenienz kopiert." : "Text copied with provenance.");
                  } catch {
                    toast.error(german ? "Kopieren wurde vom Browser blockiert." : "The browser blocked clipboard access.");
                  }
                }}
              >
                <ShieldCheck className="size-3.5" />
                {german ? "Mit Provenienz" : "With provenance"}
              </Button>
            </div>
          </div>

          {intent.kind === "caption" ? (
            <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-[0.625rem] leading-relaxed text-muted-foreground">
              <p className="font-medium text-foreground">
                {german ? "Caption ist in dieser Version copy-only" : "Caption is copy-only in this version"}
              </p>
              <p className="mt-1">
                {german
                  ? "Kopiere den Caption-Text und füge ihn im Writer selbst zu der passenden, bereits angehängten Grafik hinzu. Dieser Flow erstellt grundsätzlich kein Caption-Proposal."
                  : "Copy the caption text and add it yourself to the matching, already attached figure in Writer. This flow does not create caption proposals."}
              </p>
            </div>
          ) : (
            <div className="rounded-xl border border-border bg-secondary/35 p-3">
              <p className="flex items-center gap-1.5 text-[0.6875rem] font-medium text-foreground">
                <MessageSquareText className="size-3.5 text-moss" />
                {german ? "2. Im Writer prüfen" : "2. Review in Writer"}
              </p>
              <p className="mt-1 text-[0.625rem] leading-relaxed text-muted-foreground">
                {german
                  ? "Wähle ein Manuskript mit Schreibrechten. Es wird nur ein compile-geprüftes Edit-Proposal angelegt; das Manuskript bleibt unverändert, bis du es im Writer annimmst."
                  : "Choose a manuscript you can edit. This creates only a compile-checked edit proposal; the manuscript stays unchanged until you approve it in Writer."}
              </p>
              <p className="mt-1 text-[0.625rem] leading-relaxed text-muted-foreground">
                {german
                  ? "Das Proposal wird unmittelbar vor dem Dokumentende vorbereitet. Nimm es dort an oder nutze „Nur Text kopieren“ für eine manuelle Platzierung."
                  : "The proposal is prepared immediately before the document end. Accept it there, or use “Copy text” for manual placement."}
              </p>
              <div className="mt-2">
                <Label htmlFor={`repository-manuscript-target-${analysis.public_id}`} className="sr-only">
                  {german ? "Zielmanuskript" : "Target manuscript"}
                </Label>
                <Select
                  value={selectedWriterId}
                  disabled={writers.isLoading || movingProposal}
                  onValueChange={(value) => setSelectedWriterId(value)}
                >
                  <SelectTrigger id={`repository-manuscript-target-${analysis.public_id}`} className="w-full">
                    <SelectValue placeholder={german ? "Manuskript wählen…" : "Choose a manuscript…"} />
                  </SelectTrigger>
                  <SelectContent>
                    {editableWriters.map((writer) => (
                      <SelectItem key={writer.public_id} value={writer.public_id}>
                        {writer.title} · r{writer.revision}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {writers.isError ? (
                <p role="alert" className="mt-2 text-[0.625rem] text-destructive">
                  {errorCopy(writers.error, german, "proposal")}
                </p>
              ) : !writers.isLoading && editableWriters.length === 0 ? (
                <p className="mt-2 text-[0.625rem] leading-relaxed text-muted-foreground">
                  {german ? "Kein Manuskript mit Schreibrechten verfügbar." : "No manuscript with edit permission is available."}{" "}
                  <Link href="/writer" className="text-moss hover:underline">
                    {german ? "Manuscripts öffnen" : "Open Manuscripts"}
                  </Link>
                </p>
              ) : selectedWriter ? (
                <p className="mt-2 font-mono text-[0.53125rem] uppercase tracking-[0.1em] text-muted-foreground">
                  {selectedWriter.access_role} · revision {selectedWriter.revision}
                </p>
              ) : null}

              {proposalError ? (
                <div role="alert" className="mt-2 rounded-lg border border-destructive/25 bg-destructive/5 p-2.5 text-[0.625rem] leading-relaxed text-destructive">
                  {proposalError}
                </div>
              ) : null}

              {proposalReceipt
                && proposalReceipt.sourceKey === analysisSourceKey
                && proposalReceipt.previewHash === previewReceipt.preview.preview_sha256
                && proposalReceipt.writer.public_id === selectedWriterId ? (
                  <div role="status" className="mt-2 rounded-lg border border-moss/25 bg-accent/50 p-2.5">
                    <p className="flex items-center gap-1.5 text-[0.65625rem] font-medium text-foreground">
                      <Check className="size-3.5 text-moss" />
                      {german ? "Review-Proposal bereit" : "Review proposal ready"}
                    </p>
                    <p className="mt-1 text-[0.59375rem] leading-relaxed text-muted-foreground">
                      {german
                        ? "Das Manuskript ist weiterhin unverändert. Öffne den Writer und nimm das Proposal dort an oder lehne es ab."
                        : "The manuscript is still unchanged. Open Writer and approve or reject the proposal there."}
                    </p>
                    <Button asChild size="sm" className="mt-2 w-full">
                      <Link href={`/writer/${proposalReceipt.writer.public_id}`}>
                        {german ? "Proposal im Writer öffnen" : "Open proposal in Writer"}
                        <ArrowUpRight className="size-3.5" />
                      </Link>
                    </Button>
                  </div>
                ) : (
                  <div className="mt-2 flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      className="min-w-0 flex-1"
                      disabled={
                        !selectedWriter
                        || !proposalBindingKey
                        || proposalIntent.bindingKey !== proposalBindingKey
                        || movingProposal
                      }
                      onClick={sendToReview}
                    >
                      {movingProposal ? <Loader2 className="size-3.5 animate-spin" /> : <MessageSquareText className="size-3.5" />}
                      {movingProposal
                        ? proposalCancelRequested
                          ? german ? "Abbruch angefordert…" : "Stop requested…"
                          : german ? "Proposal wird geprüft…" : "Checking proposal…"
                        : proposalRecoveryError
                          ? german ? "Neue Review-Anfrage starten" : "Start a new review request"
                          : proposal.isError || recoveredProposalTurn.isError
                            ? german ? "Dieselbe Anfrage erneut prüfen" : "Retry the same request"
                            : german ? "Zum Manuskript-Review senden" : "Send to manuscript review"}
                    </Button>
                    {movingProposal ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={stopProposal.isPending || proposalCancelRequested}
                        onClick={() => stopProposal.mutate(proposalIntent.requestId)}
                      >
                        {stopProposal.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Square className="size-3.5" />}
                        {german ? "Stoppen" : "Stop"}
                      </Button>
                    ) : null}
                  </div>
                )}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

export function RepositoryManuscriptDialog({
  analysisId,
  userId,
  german,
  open,
  onOpenChange,
}: RepositoryManuscriptDialogProps) {
  const analysis = useQuery({
    queryKey: ["repository-analysis", userId, analysisId],
    queryFn: () => api.repositoryAnalysis(analysisId as string),
    enabled: open && Boolean(analysisId),
    retry: false,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <DialogHeader className="border-b border-border px-5 py-4 pr-12">
          <DialogTitle className="flex items-center gap-2 text-[1rem]">
            <FileText className="size-4 text-moss" />
            {german ? "Repository-Text fürs Manuskript" : "Repository copy for a manuscript"}
          </DialogTitle>
          <DialogDescription className="text-[0.75rem] leading-relaxed">
            {german
              ? "Preview und Writer-Proposal bleiben getrennt. Keine Aktion in diesem Dialog ändert automatisch ein Manuskript."
              : "Preview and Writer proposal stay separate. No action in this dialog changes a manuscript automatically."}
          </DialogDescription>
        </DialogHeader>
        <div className="p-5">
          {analysis.isLoading ? (
            <div className="grid min-h-40 place-items-center" role="status">
              <Loader2 className="size-5 animate-spin text-moss" />
              <span className="sr-only">{german ? "Repository-Analyse wird geladen" : "Loading repository analysis"}</span>
            </div>
          ) : analysis.isError ? (
            <div role="alert" className="rounded-xl border border-destructive/25 bg-destructive/5 p-4 text-[0.71875rem] leading-relaxed text-destructive">
              <AlertTriangle className="mb-2 size-4" />
              {errorCopy(analysis.error, german, "preview")}
            </div>
          ) : analysis.data?.status !== "ready" || !analysis.data.commit_sha ? (
            <div role="status" className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-4 text-[0.71875rem] leading-relaxed text-muted-foreground">
              {german
                ? "Die verknüpfte Repository-Analyse ist nicht mehr als SHA-fixierte, fertige Spec verfügbar. Es wurde nichts geändert."
                : "The linked repository analysis is no longer available as a SHA-pinned ready spec. Nothing was changed."}
            </div>
          ) : (
            <RepositoryManuscriptPanel analysis={analysis.data} userId={userId} german={german} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
