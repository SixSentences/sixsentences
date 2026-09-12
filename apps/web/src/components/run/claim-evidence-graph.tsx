"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BadgeCheck,
  BookOpenCheck,
  Check,
  CircleDot,
  FileText,
  Link2,
  Loader2,
  Plus,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type {
  EvidenceGraph,
  EvidenceGraphEdge,
  EvidenceRelationship,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const RELATIONSHIPS: Array<{
  id: EvidenceRelationship;
  label: string;
  description: string;
}> = [
  { id: "supports", label: "Supports", description: "Directly backs the claim" },
  { id: "contradicts", label: "Contradicts", description: "Provides opposing evidence" },
  { id: "qualifies", label: "Qualifies", description: "Narrows the claim or adds conditions" },
  { id: "mixed", label: "Mixed", description: "Contains supporting and opposing results" },
  { id: "indirect", label: "Indirect", description: "Relevant through a proxy or adjacent outcome" },
  { id: "outdated", label: "Outdated", description: "Superseded by a newer source version" },
  { id: "retracted", label: "Retracted", description: "Source is no longer valid evidence" },
  { id: "corrected", label: "Corrected", description: "Use a corrected source version" },
];

const EDGE_COLORS: Record<EvidenceRelationship, string> = {
  supports: "#567b6b",
  contradicts: "#b4534d",
  qualifies: "#b1843a",
  mixed: "#8c6f9c",
  indirect: "#7a8580",
  outdated: "#9a7b62",
  retracted: "#9d302c",
  corrected: "#35726a",
};

type ClaimForm = {
  text: string;
  section: string;
  confidence: string;
  writer_document_id: string;
};

type EvidenceForm = {
  target_id: string;
  relationship: EvidenceRelationship;
  locator: string;
  quote: string;
  note: string;
  verified: boolean;
};

export default function ClaimEvidenceGraph({ runId }: { runId: number }) {
  const queryClient = useQueryClient();
  const { data: graph, isLoading } = useQuery({
    queryKey: ["evidence-graph", runId],
    queryFn: () => api.evidenceGraph(runId),
  });
  const [selectedClaimId, setSelectedClaimId] = useState<number | null>(null);
  const [claimOpen, setClaimOpen] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [deleteEdgeTarget, setDeleteEdgeTarget] = useState<{
    edge: EvidenceGraphEdge;
    sourceTitle: string;
    claimText: string;
  } | null>(null);
  const [claimForm, setClaimForm] = useState<ClaimForm>({
    text: "",
    section: "",
    confidence: "unrated",
    writer_document_id: "none",
  });
  const [evidenceForm, setEvidenceForm] = useState<EvidenceForm>({
    target_id: "",
    relationship: "supports",
    locator: "",
    quote: "",
    note: "",
    verified: false,
  });

  const selectedClaim =
    graph?.claims.find((claim) => claim.id === selectedClaimId) ?? graph?.claims[0];
  const selectedEdges = graph?.edges.filter(
    (edge) => edge.claim_id === selectedClaim?.id,
  ) ?? [];
  const evidenceById = useMemo(
    () => new Map(graph?.evidence.map((node) => [node.id, node]) ?? []),
    [graph],
  );

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["evidence-graph", runId] });
  const createClaim = useMutation({
    mutationFn: () =>
      api.createRunClaim(runId, {
        text: claimForm.text.trim(),
        section: claimForm.section.trim(),
        confidence: claimForm.confidence,
        writer_document_id:
          claimForm.writer_document_id === "none"
            ? null
            : Number(claimForm.writer_document_id),
      }),
    onSuccess: (claim) => {
      setClaimOpen(false);
      setSelectedClaimId(claim.id);
      setClaimForm({
        text: "",
        section: "",
        confidence: "unrated",
        writer_document_id: "none",
      });
      toast.success("Claim added to the evidence graph.");
      void invalidate();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Claim could not be saved."),
  });
  const linkEvidence = useMutation({
    mutationFn: () => {
      if (!selectedClaim) throw new Error("Select a claim first.");
      return api.linkRunClaimEvidence(runId, selectedClaim.id, {
        target_type: "work",
        target_id: evidenceForm.target_id,
        relationship: evidenceForm.relationship,
        locator: evidenceForm.locator,
        quote: evidenceForm.quote,
        note: evidenceForm.note,
        verified: evidenceForm.verified,
      });
    },
    onSuccess: () => {
      setLinkOpen(false);
      setEvidenceForm({
        target_id: "",
        relationship: "supports",
        locator: "",
        quote: "",
        note: "",
        verified: false,
      });
      toast.success("Evidence linked.");
      void invalidate();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Evidence could not be linked."),
  });
  const updateEdge = useMutation({
    mutationFn: ({
      edge,
      body,
    }: {
      edge: EvidenceGraphEdge;
      body: Partial<{ relationship: EvidenceRelationship; verified: boolean }>;
    }) =>
      api.updateRunClaimEvidence(runId, edge.claim_id, edge.id, body),
    onSuccess: () => void invalidate(),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Evidence could not be updated."),
  });
  const deleteEdge = useMutation({
    mutationFn: (edge: EvidenceGraphEdge) =>
      api.deleteRunClaimEvidence(runId, edge.claim_id, edge.id),
    onSuccess: (_, edge) => {
      setDeleteEdgeTarget((current) =>
        current?.edge.id === edge.id ? null : current,
      );
      toast.success("Evidence link removed.");
      void invalidate();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Link could not be removed."),
  });

  if (isLoading || !graph) {
    return (
      <div className="grid h-full place-items-center">
        <Loader2 className="size-5 animate-spin text-moss" />
      </div>
    );
  }
  if (graph.project_id === null) {
    return (
      <div className="grid h-full place-items-center p-6">
        <div className="max-w-md rounded-3xl border border-dashed border-border p-8 text-center">
          <Link2 className="mx-auto size-6 text-moss" />
          <h2 className="mt-4 font-display text-2xl text-foreground">
            A project keeps the graph durable
          </h2>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
            Assign this search to a project first. Claims can then stay connected to
            studies, datasets and manuscript sections across every search version.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <GraphMetric label="Claims" value={graph.summary.claims} />
        <GraphMetric label="Evidence nodes" value={graph.summary.evidence} />
        <GraphMetric
          label="Unsupported"
          value={graph.summary.unsupported}
          warning={graph.summary.unsupported > 0}
        />
        <GraphMetric
          label="Needs verification"
          value={graph.summary.unverified}
          warning={graph.summary.unverified > 0}
        />
        <GraphMetric
          label="Contradictions"
          value={graph.summary.contradictions}
          critical={graph.summary.contradictions > 0}
        />
      </div>

      <section className="mt-4 rounded-3xl border border-border bg-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-muted-foreground">
              Claim to evidence map
            </p>
            <p className="mt-1 text-[0.75rem] text-muted-foreground">
              {graph.project_name} · select a claim to inspect every source edge
            </p>
          </div>
          <Button size="sm" className="h-8 rounded-full" onClick={() => setClaimOpen(true)}>
            <Plus className="size-3.5" /> New claim
          </Button>
        </div>

        {graph.claims.length ? (
          <GraphCanvas
            graph={graph}
            selectedClaimId={selectedClaim?.id ?? null}
            onSelectClaim={setSelectedClaimId}
          />
        ) : (
          <div className="mt-5 grid min-h-72 place-items-center rounded-2xl border border-dashed border-border bg-secondary/15">
            <div className="max-w-sm text-center">
              <CircleDot className="mx-auto size-5 text-moss" />
              <h3 className="mt-3 font-display text-2xl text-foreground">
                Make the conclusions explicit
              </h3>
              <p className="mt-2 text-[0.75rem] leading-relaxed text-muted-foreground">
                Add one precise claim, then connect the passages, studies and analyses
                that support, contradict or qualify it.
              </p>
            </div>
          </div>
        )}
      </section>

      {selectedClaim && (
        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(18rem,0.7fr)_minmax(28rem,1.3fr)]">
          <section className="rounded-3xl border border-border bg-card p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                  Selected claim
                </p>
                <ImpactBadge impact={selectedClaim.impact} />
              </div>
              <Badge variant="outline" className="rounded-full font-mono text-[0.5625rem]">
                {selectedClaim.confidence}
              </Badge>
            </div>
            <blockquote className="mt-4 font-display text-2xl leading-snug text-foreground">
              “{selectedClaim.text}”
            </blockquote>
            <div className="mt-5 grid grid-cols-2 gap-2">
              <Detail label="Section" value={selectedClaim.section || "Not assigned"} />
              <Detail
                label="Manuscript"
                value={selectedClaim.writer_title || "Not linked"}
              />
              <Detail label="Supporting" value={String(selectedClaim.support_count)} />
              <Detail
                label="Contradicting"
                value={String(selectedClaim.contradiction_count)}
              />
            </div>
            <div className="mt-5 space-y-1.5">
              {graph.claims.map((claim) => (
                <button
                  key={claim.id}
                  type="button"
                  onClick={() => setSelectedClaimId(claim.id)}
                  className={cn(
                    "w-full cursor-pointer rounded-xl border px-3 py-2.5 text-left transition-colors",
                    claim.id === selectedClaim.id
                      ? "border-moss/35 bg-accent/45"
                      : "border-border/70 hover:border-moss/30",
                  )}
                >
                  <p className="line-clamp-2 text-[0.71875rem] leading-snug">{claim.text}</p>
                </button>
              ))}
            </div>
          </section>

          <section className="rounded-3xl border border-border bg-card p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                  Evidence edges
                </p>
                <p className="mt-1 text-[0.71875rem] text-muted-foreground">
                  Relationship, passage and human verification remain separate.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-8 rounded-full"
                onClick={() => setLinkOpen(true)}
              >
                <Link2 className="size-3.5" /> Link evidence
              </Button>
            </div>
            <div className="mt-4 space-y-2">
              {selectedEdges.map((edge) => {
                const node = evidenceById.get(edge.evidence_id);
                return (
                  <div
                    key={edge.id}
                    className="rounded-2xl border border-border/70 p-4"
                  >
                    <div className="flex items-start gap-3">
                      <span
                        className="mt-0.5 size-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: EDGE_COLORS[edge.relationship] }}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="min-w-0 flex-1 truncate text-[0.8125rem] font-medium text-foreground">
                            {node?.title ?? edge.target_id}
                          </p>
                          {edge.verified ? (
                            <span className="inline-flex items-center gap-1 text-[0.625rem] text-moss">
                              <BadgeCheck className="size-3" /> verified
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[0.625rem] text-amber-700">
                              <AlertTriangle className="size-3" /> review
                            </span>
                          )}
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <Select
                            value={edge.relationship}
                            onValueChange={(value: EvidenceRelationship) =>
                              updateEdge.mutate({
                                edge,
                                body: { relationship: value },
                              })
                            }
                          >
                            <SelectTrigger className="h-7 w-36 rounded-full text-[0.65625rem]">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {RELATIONSHIPS.map((relationship) => (
                                <SelectItem
                                  key={relationship.id}
                                  value={relationship.id}
                                >
                                  {relationship.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {edge.locator && (
                            <span className="font-mono text-[0.59375rem] text-muted-foreground">
                              {edge.locator}
                            </span>
                          )}
                          {node?.year && (
                            <span className="font-mono text-[0.59375rem] text-muted-foreground">
                              {node.year}
                            </span>
                          )}
                        </div>
                        {edge.quote && (
                          <blockquote className="mt-3 rounded-xl bg-secondary/45 px-3 py-2 text-[0.71875rem] italic leading-relaxed text-muted-foreground">
                            “{edge.quote}”
                          </blockquote>
                        )}
                        {edge.note && (
                          <p className="mt-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                            {edge.note}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        {!edge.verified && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-7 rounded-full text-moss"
                            onClick={() =>
                              updateEdge.mutate({ edge, body: { verified: true } })
                            }
                            aria-label="Verify evidence"
                          >
                            <Check className="size-3.5" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7 rounded-full text-muted-foreground hover:text-destructive"
                          disabled={deleteEdge.isPending}
                          onClick={() =>
                            setDeleteEdgeTarget({
                              edge,
                              sourceTitle: node?.title ?? edge.target_id,
                              claimText: selectedClaim.text,
                            })
                          }
                          aria-label="Remove evidence link"
                        >
                          {deleteEdge.isPending && deleteEdge.variables?.id === edge.id ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="size-3.5" />
                          )}
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
              {!selectedEdges.length && (
                <div className="rounded-2xl border border-dashed border-border px-5 py-8 text-center">
                  <ShieldAlert className="mx-auto size-5 text-amber-700" />
                  <p className="mt-3 text-[0.8125rem] font-medium">
                    This claim is unsupported
                  </p>
                  <p className="mt-1 text-[0.71875rem] text-muted-foreground">
                    Link at least one source and verify the exact relationship.
                  </p>
                </div>
              )}
            </div>
          </section>
        </div>
      )}

      <Dialog open={claimOpen} onOpenChange={setClaimOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="font-display text-2xl text-foreground">
              Add a research claim
            </DialogTitle>
            <DialogDescription>
              State one testable conclusion. Evidence is connected separately so support
              can change without rewriting the claim.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <Field label="Claim">
              <Textarea
                autoFocus
                className="min-h-28"
                value={claimForm.text}
                onChange={(event) =>
                  setClaimForm({ ...claimForm, text: event.target.value })
                }
                placeholder="State one precise result, interpretation or methodological assertion."
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Manuscript section">
                <Input
                  value={claimForm.section}
                  onChange={(event) =>
                    setClaimForm({ ...claimForm, section: event.target.value })
                  }
                  placeholder="Results, Discussion…"
                />
              </Field>
              <Field label="Current confidence">
                <Select
                  value={claimForm.confidence}
                  onValueChange={(value) =>
                    setClaimForm({ ...claimForm, confidence: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="unrated">Unrated</SelectItem>
                    <SelectItem value="low">Low</SelectItem>
                    <SelectItem value="moderate">Moderate</SelectItem>
                    <SelectItem value="high">High</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>
            {graph.writers.length > 0 && (
              <Field label="Connected manuscript">
                <Select
                  value={claimForm.writer_document_id}
                  onValueChange={(value) =>
                    setClaimForm({ ...claimForm, writer_document_id: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Link later</SelectItem>
                    {graph.writers.map((writer) => (
                      <SelectItem key={writer.id} value={String(writer.id)}>
                        {writer.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" className="rounded-full" onClick={() => setClaimOpen(false)}>
              Cancel
            </Button>
            <Button
              className="rounded-full"
              disabled={!claimForm.text.trim() || createClaim.isPending}
              onClick={() => createClaim.mutate()}
            >
              {createClaim.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Add claim
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={linkOpen} onOpenChange={setLinkOpen}>
        <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-display text-2xl text-foreground">
              Link evidence
            </DialogTitle>
            <DialogDescription>
              Connect a source to “{selectedClaim?.text.slice(0, 110)}
              {(selectedClaim?.text.length ?? 0) > 110 ? "…" : ""}”
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <Field label="Source from this review">
              <Select
                value={evidenceForm.target_id}
                onValueChange={(value) =>
                  setEvidenceForm({ ...evidenceForm, target_id: value })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Choose a publication" />
                </SelectTrigger>
                <SelectContent className="max-w-xl">
                  {graph.available_evidence.map((source) => (
                    <SelectItem key={source.target_id} value={source.target_id}>
                      {source.title} {source.year ? `(${source.year})` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Relationship">
              <div className="grid gap-2 sm:grid-cols-2">
                {RELATIONSHIPS.map((relationship) => (
                  <button
                    key={relationship.id}
                    type="button"
                    onClick={() =>
                      setEvidenceForm({
                        ...evidenceForm,
                        relationship: relationship.id,
                      })
                    }
                    className={cn(
                      "cursor-pointer rounded-xl border px-3 py-2.5 text-left transition-colors",
                      evidenceForm.relationship === relationship.id
                        ? "border-moss/40 bg-accent/45"
                        : "border-border hover:border-moss/25",
                    )}
                  >
                    <span className="text-[0.75rem] font-medium">
                      {relationship.label}
                    </span>
                    <span className="mt-0.5 block text-[0.65625rem] text-muted-foreground">
                      {relationship.description}
                    </span>
                  </button>
                ))}
              </div>
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Page, table or locator">
                <Input
                  value={evidenceForm.locator}
                  onChange={(event) =>
                    setEvidenceForm({ ...evidenceForm, locator: event.target.value })
                  }
                  placeholder="p. 12, Table 3"
                />
              </Field>
              <div className="flex items-end">
                <div className="flex h-10 w-full items-center justify-between rounded-lg border border-border px-3">
                  <span className="text-[0.75rem]">I checked this source</span>
                  <Switch
                    checked={evidenceForm.verified}
                    onCheckedChange={(verified) =>
                      setEvidenceForm({ ...evidenceForm, verified })
                    }
                  />
                </div>
              </div>
            </div>
            <Field label="Exact source passage">
              <Textarea
                value={evidenceForm.quote}
                onChange={(event) =>
                  setEvidenceForm({ ...evidenceForm, quote: event.target.value })
                }
                placeholder="Paste the exact passage that supports this relationship."
              />
            </Field>
            <Field label="Interpretation note">
              <Textarea
                value={evidenceForm.note}
                onChange={(event) =>
                  setEvidenceForm({ ...evidenceForm, note: event.target.value })
                }
                placeholder="Why this evidence supports, contradicts or qualifies the claim."
              />
            </Field>
          </div>
          <DialogFooter>
            <Button variant="ghost" className="rounded-full" onClick={() => setLinkOpen(false)}>
              Cancel
            </Button>
            <Button
              className="rounded-full"
              disabled={!evidenceForm.target_id || linkEvidence.isPending}
              onClick={() => linkEvidence.mutate()}
            >
              {linkEvidence.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Link evidence
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDeleteDialog
        target={
          deleteEdgeTarget
            ? {
                title: "Remove this evidence link?",
                description: `“${deleteEdgeTarget.sourceTitle}” will be disconnected from the claim “${deleteEdgeTarget.claimText}”. The source itself stays in the review.`,
                action: "Remove link",
                cancel: "Keep link",
              }
            : null
        }
        pending={deleteEdge.isPending}
        onCancel={() => {
          if (!deleteEdge.isPending) setDeleteEdgeTarget(null);
        }}
        onConfirm={() => {
          const target = deleteEdgeTarget;
          if (!target || deleteEdge.isPending) return;
          deleteEdge.mutate(target.edge);
        }}
      />
    </div>
  );
}

function GraphCanvas({
  graph,
  selectedClaimId,
  onSelectClaim,
}: {
  graph: EvidenceGraph;
  selectedClaimId: number | null;
  onSelectClaim: (id: number) => void;
}) {
  const claims = graph.claims.slice(0, 5);
  const evidence = graph.evidence.slice(0, 7);
  const evidenceIndex = new Map(evidence.map((node, index) => [node.id, index]));
  const claimIndex = new Map(claims.map((claim, index) => [claim.id, index]));
  const rowCount = Math.max(claims.length, evidence.length, 1);
  const canvasHeight = Math.min(384, Math.max(216, rowCount * 62 + 72));
  const y = (index: number, count: number) =>
    count <= 1 ? 50 : 10 + (index / (count - 1)) * 80;
  const yPixels = (index: number, count: number) =>
    (y(index, count) / 100) * canvasHeight;

  return (
    <div
      className="relative mt-5 overflow-hidden rounded-2xl border border-border/70 bg-secondary/10"
      style={{ height: canvasHeight }}
    >
      <svg
        className="pointer-events-none absolute inset-0 size-full"
        viewBox={`0 0 1000 ${canvasHeight}`}
        preserveAspectRatio="none"
        aria-hidden
      >
        {graph.edges.map((edge) => {
          const ci = claimIndex.get(edge.claim_id);
          const ei = evidenceIndex.get(edge.evidence_id);
          if (ci === undefined || ei === undefined) return null;
          return (
            <path
              key={edge.id}
              d={`M 285 ${yPixels(ci, claims.length)} C 430 ${yPixels(ci, claims.length)}, 570 ${yPixels(ei, evidence.length)}, 715 ${yPixels(ei, evidence.length)}`}
              fill="none"
              stroke={EDGE_COLORS[edge.relationship]}
              strokeWidth={selectedClaimId === edge.claim_id ? 2.4 : 1.2}
              strokeOpacity={selectedClaimId === edge.claim_id ? 0.9 : 0.35}
              strokeDasharray={
                edge.relationship === "indirect" || edge.relationship === "outdated"
                  ? "7 5"
                  : undefined
              }
            />
          );
        })}
      </svg>
      <div className="absolute inset-y-0 left-4 w-[27%]">
        {claims.map((claim, index) => (
          <button
            key={claim.id}
            type="button"
            onClick={() => onSelectClaim(claim.id)}
            style={{
              top: `${y(index, claims.length)}%`,
              transform: "translateY(-50%)",
            }}
            className={cn(
              "absolute left-0 w-full cursor-pointer rounded-xl border px-3 py-2.5 text-left shadow-sm transition-all",
              claim.id === selectedClaimId
                ? "border-moss/45 bg-primary text-primary-foreground"
                : "border-border bg-card hover:border-moss/30",
            )}
          >
            <span className="line-clamp-2 text-[0.6875rem] font-medium leading-snug">
              {claim.text}
            </span>
            <span
              className={cn(
                "mt-1 block font-mono text-[0.53125rem] uppercase tracking-[0.14em]",
                claim.id === selectedClaimId
                  ? "text-ivory/55"
                  : "text-muted-foreground",
              )}
            >
              {claim.impact}
            </span>
          </button>
        ))}
      </div>
      <div className="absolute inset-y-0 right-4 w-[27%]">
        {evidence.map((node, index) => (
          <div
            key={node.id}
            style={{
              top: `${y(index, evidence.length)}%`,
              transform: "translateY(-50%)",
            }}
            className={cn(
              "absolute right-0 w-full rounded-xl border bg-card px-3 py-2 shadow-sm",
              node.retracted ? "border-red-300/70" : "border-border",
            )}
          >
            <div className="flex items-start gap-2">
              {node.target_type === "work" || node.target_type === "quote" ? (
                <BookOpenCheck className="mt-0.5 size-3 shrink-0 text-moss" />
              ) : (
                <FileText className="mt-0.5 size-3 shrink-0 text-moss" />
              )}
              <div className="min-w-0">
                <p className="line-clamp-2 text-[0.65625rem] font-medium leading-snug">
                  {node.title}
                </p>
                <p className="mt-1 font-mono text-[0.5rem] uppercase tracking-[0.12em] text-muted-foreground">
                  {node.target_type} {node.year ? `· ${node.year}` : ""}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>
      {graph.claims.length > claims.length && (
        <span className="absolute bottom-3 left-4 text-[0.625rem] text-muted-foreground">
          +{graph.claims.length - claims.length} more claims below
        </span>
      )}
      {graph.evidence.length > evidence.length && (
        <span className="absolute bottom-3 right-4 text-[0.625rem] text-muted-foreground">
          +{graph.evidence.length - evidence.length} more sources below
        </span>
      )}
    </div>
  );
}

function GraphMetric({
  label,
  value,
  warning = false,
  critical = false,
}: {
  label: string;
  value: number;
  warning?: boolean;
  critical?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-2xl border bg-card px-4 py-3",
        critical
          ? "border-red-300/60"
          : warning
            ? "border-amber-300/60"
            : "border-border",
      )}
    >
      <p
        className={cn(
          "font-display text-3xl",
          critical ? "text-red-800" : warning ? "text-amber-800" : "text-foreground",
        )}
      >
        {value}
      </p>
      <p className="mt-0.5 text-[0.6875rem] text-muted-foreground">{label}</p>
    </div>
  );
}

function ImpactBadge({ impact }: { impact: EvidenceGraph["claims"][number]["impact"] }) {
  return (
    <span
      className={cn(
        "mt-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[0.65625rem]",
        impact === "critical"
          ? "bg-red-50 text-red-800"
          : impact === "attention"
            ? "bg-amber-50 text-amber-800 dark:bg-amber-300/10 dark:text-amber-200"
            : impact === "stable"
              ? "bg-accent text-moss"
              : "bg-secondary text-muted-foreground",
      )}
    >
      {impact === "stable" ? (
        <BadgeCheck className="size-3" />
      ) : impact === "critical" ? (
        <ShieldAlert className="size-3" />
      ) : (
        <AlertTriangle className="size-3" />
      )}
      {impact}
    </span>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-secondary/45 px-3 py-2.5">
      <p className="font-mono text-[0.53125rem] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 truncate text-[0.6875rem] font-medium">{value}</p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
