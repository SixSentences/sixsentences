"use client";

import Link from "next/link";
import { AlertTriangle, ArrowUpRight, GitBranch, ShieldCheck } from "lucide-react";

import type {
  WriterRepositoryProseClaim,
  WriterRepositoryProseProvenance,
} from "@/lib/types";
import { REPOSITORY_VISUAL_SOURCE_ENABLED } from "@/lib/launch-features";

const COMMIT_SHA = /^[0-9a-f]{40}$/i;
const CONTENT_HASH = /^[0-9a-f]{64}$/i;

type RepositoryProseProvenanceProps = {
  provenance?: WriterRepositoryProseProvenance;
  sourceKind?: "repository_prose";
  requiresManualReview?: true;
};

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value)
    && value.every((item) => typeof item === "string" && item.length > 0);
}

function validClaim(
  value: unknown,
  privateSource: boolean,
): value is WriterRepositoryProseClaim {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const claim = value as Partial<WriterRepositoryProseClaim>;
  if (
    typeof claim.text !== "string"
    || claim.text.trim().length === 0
    || (claim.support !== "topology" && claim.support !== "coverage")
  ) return false;
  if (privateSource) {
    return claim.node_ids === undefined
      && claim.edge_ids === undefined
      && claim.evidence_ids === undefined;
  }
  if (!(typeof claim.text === "string"
    && claim.text.trim().length > 0
    && stringArray(claim.node_ids)
    && stringArray(claim.edge_ids)
    && Array.isArray(claim.evidence_ids)
    && claim.evidence_ids.every((id) => typeof id === "string" && id.length > 0)
    && (claim.support === "topology" || claim.support === "coverage"))) return false;
  if (claim.support === "topology") {
    return (claim.node_ids.length > 0 || claim.edge_ids.length > 0)
      && claim.evidence_ids.length > 0;
  }
  return claim.node_ids.length === 0 && claim.edge_ids.length === 0;
}

function validProvenance(
  value: WriterRepositoryProseProvenance | undefined,
): value is WriterRepositoryProseProvenance {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (
    value.repository_access !== undefined
    && value.repository_access !== "public"
    && value.repository_access !== "private"
  ) {
    return false;
  }
  // Private repository proposals are new with this contract and always carry
  // an explicit access marker. A missing marker can only be a legacy public
  // proposal, which must still pass every full public-provenance check below.
  const privateSource = value.repository_access === "private";
  const privatePayloadRedacted = value.analysis_id === undefined
    && value.repository_url === undefined
    && value.subpath === undefined
    && value.commit_sha === undefined
    && value.grounding_sha256 === undefined
    && value.preview_request_id === undefined
    && value.preview_sha256 === undefined
    && value.compile_input_sha256 === undefined;
  const publicIdentityValid = typeof value.repository_url === "string"
    && value.repository_url.length > 0
    && repositoryIdentity(value.repository_url) !== null;
  const commitValid = typeof value.commit_sha === "string"
    && COMMIT_SHA.test(value.commit_sha);
  const publicIdsValid = typeof value.analysis_id === "string"
    && value.analysis_id.length > 0
    && typeof value.grounding_sha256 === "string"
    && CONTENT_HASH.test(value.grounding_sha256)
    && typeof value.preview_request_id === "string"
    && value.preview_request_id.length > 0
    && typeof value.preview_sha256 === "string"
    && CONTENT_HASH.test(value.preview_sha256)
    && typeof value.compile_input_sha256 === "string"
    && CONTENT_HASH.test(value.compile_input_sha256);
  return (!privateSource || privatePayloadRedacted)
    && (privateSource || publicIdsValid)
    && (privateSource || publicIdentityValid)
    && (value.subpath === undefined || value.subpath === null || typeof value.subpath === "string")
    && (privateSource || commitValid)
    && ["description", "section"].includes(value.kind)
    && (value.language === "en" || value.language === "de")
    && Array.isArray(value.claims)
    && value.claims.length > 0
    && value.claims.every((claim) => validClaim(claim, privateSource))
    && typeof value.scope_note === "string"
    && value.scope_note.trim().length > 0;
}

function repositoryIdentity(repositoryUrl: string): string | null {
  try {
    const url = new URL(repositoryUrl);
    if (
      url.protocol !== "https:"
      || url.hostname.toLowerCase() !== "github.com"
      || url.port
      || url.username
      || url.password
      || url.search
      || url.hash
    ) return null;
    const match = /^\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/.exec(url.pathname);
    if (!match || match[2].toLowerCase().endsWith(".git")) return null;
    return `${match[1]}/${match[2]}`;
  } catch {
    return null;
  }
}

function safeSubpath(subpath: string | null | undefined): string | null {
  if (!subpath) return null;
  const normalized = subpath.replace(/^\/+|\/+$/g, "");
  if (
    !normalized
    || normalized.includes("\\")
    || normalized.split("/").includes("..")
    || normalized.includes("\0")
  ) return null;
  return normalized;
}

function repositoryCommitUrl(provenance: WriterRepositoryProseProvenance): string | null {
  if (provenance.repository_access === "private") return null;
  const identity = repositoryIdentity(provenance.repository_url ?? "");
  const commitSha = provenance.commit_sha ?? "";
  if (!identity || !COMMIT_SHA.test(commitSha)) return null;
  const subpath = safeSubpath(provenance.subpath);
  const encodedSubpath = subpath
    ? `/${subpath.split("/").map(encodeURIComponent).join("/")}`
    : "";
  return `https://github.com/${identity}/tree/${commitSha}${encodedSubpath}`;
}

export function RepositoryProseProvenance({
  provenance,
  sourceKind,
  requiresManualReview,
}: RepositoryProseProvenanceProps) {
  if (sourceKind !== "repository_prose") return null;
  const german = provenance?.language === "de";
  if (requiresManualReview !== true || !validProvenance(provenance)) {
    return (
      <div
        role="alert"
        className="mt-2 flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[0.6875rem] leading-relaxed text-amber-800 dark:text-amber-200"
      >
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
        <span>
          {german
            ? "Die Repository-Provenienz dieses Proposals ist unvollständig. Nicht anwenden, bevor die Quelle geprüft wurde."
            : "This proposal has incomplete repository provenance. Do not apply it before reviewing the source."}
        </span>
      </div>
    );
  }

  const privateSource = provenance.repository_access === "private";
  const repository = privateSource
    ? null
    : repositoryIdentity(provenance.repository_url ?? "");
  const commitUrl = repositoryCommitUrl(provenance);
  const evidenceCount = new Set(
    provenance.claims.flatMap((claim) => claim.evidence_ids ?? []),
  ).size;
  const kind = german
    ? provenance.kind === "description" ? "Kurzbeschreibung" : "Abschnitt"
    : provenance.kind === "description" ? "Short description" : "Section";

  return (
    <section
      aria-label={german ? "Repository-Provenienz" : "Repository provenance"}
      className="mt-2 rounded-xl border border-moss/25 bg-accent/35 p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[0.6875rem] font-medium text-foreground">
          <ShieldCheck className="size-3.5 shrink-0 text-moss" />
          {privateSource
            ? german ? "Privates GitHub-Repository" : "Private GitHub repository"
            : german ? "Kanonische Repository-Spec" : "Canonical repository spec"}
        </p>
        <span className="rounded-full border border-moss/25 bg-card px-2 py-0.5 font-mono text-[0.5rem] uppercase tracking-[0.1em] text-moss">
          {german ? "Manuelles Review" : "Manual review"}
        </span>
      </div>

      <div className="mt-2 grid gap-1.5 text-[0.59375rem] leading-relaxed text-muted-foreground sm:grid-cols-2">
        <p className="min-w-0">
          <span className="block font-mono text-[0.5rem] uppercase tracking-[0.1em]">Repository</span>
          <span
            className="block truncate text-foreground"
            title={privateSource ? undefined : repository ?? provenance.repository_url}
          >
            {privateSource
              ? german ? "Privates GitHub-Repository" : "Private GitHub repository"
              : repository ?? provenance.repository_url}
          </span>
        </p>
        {!privateSource && provenance.commit_sha ? <p>
          <span className="block font-mono text-[0.5rem] uppercase tracking-[0.1em]">Commit</span>
          <span className="font-mono text-foreground" title={provenance.commit_sha}>
            {provenance.commit_sha?.slice(0, 10)}
          </span>
        </p> : null}
        {!privateSource && provenance.grounding_sha256 ? <p>
          <span className="block font-mono text-[0.5rem] uppercase tracking-[0.1em]">Grounding</span>
          <span className="font-mono text-foreground" title={provenance.grounding_sha256}>
            {provenance.grounding_sha256.slice(0, 10)}
          </span>
        </p> : null}
        <p>
          <span className="block font-mono text-[0.5rem] uppercase tracking-[0.1em]">
            {german ? "Umfang" : "Review scope"}
          </span>
          <span className="text-foreground">
            {kind} · {provenance.language.toUpperCase()} · {provenance.claims.length} {german ? "Aussagen" : "claims"}
            {!privateSource ? ` · ${evidenceCount} E#` : ""}
          </span>
        </p>
      </div>

      <p className="mt-2 text-[0.625rem] leading-relaxed text-muted-foreground">
        <span className="font-medium text-foreground">{german ? "Scope:" : "Scope:"}</span>{" "}
        {provenance.scope_note}
      </p>
      <p className="mt-1 text-[0.5625rem] leading-relaxed text-muted-foreground">
        {privateSource
          ? german
            ? "Dieses manuell zu prüfende Proposal nutzt ausschließlich abgeleiteten Repository-Inhalt. Identität, Commit, rohe Belegdatensätze, Quelllinks und Rohcode bleiben verborgen; geprüfte Komponenten- oder Verzeichnislabels können in der Prosa pfadähnlich sein."
            : "This manually reviewed proposal uses derived repository content only. Identity, commit, raw evidence records, source links, and raw code remain hidden; screened component or directory labels may be path-like in the prose."
          : german
          ? "Dieses Proposal basiert auf der verifizierten Spec am festen Commit, nicht auf dem Styling der Grafik. Prüfe die E#-Belege bei Analysezugriff im Visual Lab; prüfe sonst das feste Repository und wende den Text erst nach Quellenbestätigung an."
          : "This proposal is grounded in the verified spec at the pinned commit, not the visual styling. Review its E# evidence in Visual Lab when you have analysis access; otherwise inspect the pinned repository and apply only after confirming the source."}
      </p>

      <div className="mt-2 flex flex-wrap gap-2">
        {commitUrl ? (
          <a
            href={commitUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[0.59375rem] font-medium text-moss hover:underline"
          >
            <GitBranch className="size-3" />
            {german ? "Repository am Commit" : "Repository at commit"}
            <ArrowUpRight className="size-3" />
          </a>
        ) : null}
        {!privateSource && provenance.analysis_id ? (
          REPOSITORY_VISUAL_SOURCE_ENABLED ? (
            <Link
              href={`/figures?analysis=${encodeURIComponent(provenance.analysis_id)}`}
              className="inline-flex items-center gap-1 text-[0.59375rem] font-medium text-moss hover:underline"
            >
              {german ? "Analyse im Visual Lab prüfen (falls zugänglich)" : "Review analysis in Visual Lab (if accessible)"}
              <ArrowUpRight className="size-3" />
            </Link>
          ) : (
            <span
              aria-disabled="true"
              title={german
                ? "Die Repository-Analyse wird noch getestet."
                : "Repository analysis is still being tested."}
              data-launch-feature="repository-visual-source"
              className="inline-flex cursor-not-allowed items-center gap-1 rounded-full border border-dashed border-border/70 px-2 py-1 text-[0.59375rem] font-medium text-muted-foreground opacity-60"
            >
              {german ? "Visual-Lab-Analyse · In Erprobung" : "Visual Lab analysis · In testing"}
            </span>
          )
        ) : null}
      </div>
    </section>
  );
}
