"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BadgeCheck,
  BookOpen,
  Check,
  ChevronRight,
  FileDown,
  Loader2,
  PencilLine,
  Plus,
  RefreshCcw,
  Search,
  SlidersHorizontal,
  Table2,
  UserCheck,
  Users,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, downloadRunAsset } from "@/lib/api";
import type { EvidenceCell, EvidenceRow, EvidenceTable } from "@/lib/types";
import { cn } from "@/lib/utils";

const PRESETS = [
  {
    id: "general",
    label: "General",
    fields: ["population", "method", "sample_size", "outcomes", "limitations"],
  },
  {
    id: "pico",
    label: "PICO",
    fields: ["population", "intervention", "comparator", "outcomes", "effect_size"],
  },
  {
    id: "diagnostic",
    label: "Diagnostic",
    fields: ["population", "index_test", "reference_standard", "sensitivity", "specificity"],
  },
  {
    id: "qualitative",
    label: "Qualitative",
    fields: ["setting", "participants", "data_collection", "themes", "researcher_reflexivity"],
  },
  {
    id: "software",
    label: "Software",
    fields: ["task", "dataset", "baseline", "metric", "result", "limitations"],
  },
] as const;

type SchemaDraft = EvidenceTable["schema"];

function fieldLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function cleanTitle(value: string): string {
  return value.replace(/[<>]/g, "").replace(/\s+/g, " ").trim();
}

function withReviewedCell(
  table: EvidenceTable,
  workId: string,
  field: string,
  payload: EvidenceCell,
): EvidenceTable {
  const rows = table.rows.map((row) =>
    row.work_id === workId
      ? { ...row, payload: { ...row.payload, [field]: payload } }
      : row,
  );
  const cells = rows.flatMap((row) => Object.values(row.payload));
  return {
    ...table,
    rows,
    summary: {
      ...table.summary,
      reviewed_cells: cells.filter((cell) =>
        ["confirmed", "corrected"].includes(cell.review_status ?? ""),
      ).length,
      conflicts: cells.filter((cell) => cell.review_status === "conflict").length,
    },
  };
}

function cellTone(cell: EvidenceCell | undefined): string {
  if (!cell) return "border-border bg-background";
  if (cell.review_status === "conflict") return "border-red-300/70 bg-red-50/45";
  if (cell.review_status === "needs_attention" || !cell.verified) {
    return "border-amber-300/70 bg-amber-50/45 dark:border-amber-300/30 dark:bg-amber-300/10";
  }
  if (cell.review_status === "confirmed" || cell.review_status === "corrected") {
    return "border-moss/35 bg-accent/45";
  }
  return "border-border bg-background";
}

export default function ExtractionStudio({ runId }: { runId: number }) {
  const queryClient = useQueryClient();
  const { data: table, isLoading } = useQuery({
    queryKey: ["extraction", runId],
    queryFn: () => api.extraction(runId),
    refetchInterval: (query) =>
      query.state.data?.is_running === true ? 2_000 : false,
  });
  const [selectedWorkId, setSelectedWorkId] = useState("");
  const [selectedField, setSelectedField] = useState("");
  const [studyFilter, setStudyFilter] = useState("");
  const [schemaDraft, setSchemaDraft] = useState<SchemaDraft | null>(null);
  const [newField, setNewField] = useState("");
  const [reviewValue, setReviewValue] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [reviewRound, setReviewRound] = useState<1 | 2>(1);
  const [contractOpen, setContractOpen] = useState(false);
  const [reextractOpen, setReextractOpen] = useState(false);
  const isRunning = table?.is_running === true;

  const schema = schemaDraft ?? table?.schema ?? {
    name: "Review extraction",
    fields: ["population", "method", "sample_size", "outcomes", "limitations"],
    reviewer_mode: "single" as const,
    instructions: "",
  };
  const visibleRows = useMemo(() => {
    const query = studyFilter.trim().toLowerCase();
    if (!table || !query) return table?.rows ?? [];
    return table.rows.filter(
      (row) =>
        row.title.toLowerCase().includes(query) ||
        row.work_id.toLowerCase().includes(query),
    );
  }, [studyFilter, table]);
  const selectedRow =
    table?.rows.find((row) => row.work_id === selectedWorkId) ??
    visibleRows[0] ??
    table?.rows[0];
  const activeField = schema.fields.includes(selectedField)
    ? selectedField
    : schema.fields[0];
  const activeCell = selectedRow?.payload[activeField];
  const reviewProgress = table?.summary.total_cells
    ? (table.summary.reviewed_cells / table.summary.total_cells) * 100
    : 0;

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["extraction", runId] });

  const saveSchema = useMutation({
    mutationFn: () => api.updateExtractionSchema(runId, schema),
    onSuccess: (savedSchema) => {
      queryClient.setQueryData<EvidenceTable>(
        ["extraction", runId],
        (current) => current && { ...current, schema: savedSchema, fields: savedSchema.fields },
      );
      toast.success("Extraction contract saved.");
      setSchemaDraft(null);
      setContractOpen(false);
      void invalidate();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "The schema could not be saved."),
  });
  const extract = useMutation({
    mutationFn: (force: boolean) => api.startExtraction(runId, schema.fields, force),
    onSuccess: (result) => {
      setReextractOpen(false);
      toast.success(
        result.scheduled
          ? `Extraction started for ${result.scheduled} studies.`
          : "Every included study is already extracted.",
      );
      void invalidate();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Extraction could not start."),
  });
  function requestExtraction(reextractAll = false) {
    if (!table || isRunning || extract.isPending) return;
    if (reextractAll) {
      setReextractOpen(true);
      return;
    }
    extract.mutate(false);
  }
  function confirmReextraction() {
    if (!reextractOpen || !table || isRunning || extract.isPending) return;
    extract.mutate(true);
  }
  const review = useMutation({
    mutationFn: (verdict: "confirmed" | "corrected" | "needs_attention" | "conflict") => {
      if (!selectedRow || !activeField) throw new Error("Select a cell first.");
      return api.reviewExtraction(runId, selectedRow.work_id, {
        field: activeField,
        round: reviewRound,
        verdict,
        value: reviewValue || activeCell?.value || "",
        note: reviewNote,
      });
    },
    onSuccess: (result) => {
      queryClient.setQueryData<EvidenceTable>(
        ["extraction", runId],
        (current) =>
          current
            ? withReviewedCell(current, result.work_id, result.field, result.payload)
            : current,
      );
      toast.success(`Review round ${reviewRound} saved.`);
      setReviewNote("");
      setReviewValue(result.payload.value);
      void invalidate();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Review could not be saved."),
  });

  const selectCell = (row: EvidenceRow, field: string) => {
    setSelectedWorkId(row.work_id);
    setSelectedField(field);
    setReviewValue(row.payload[field]?.value ?? "");
    setReviewNote("");
  };
  const updateSchema = (patch: Partial<SchemaDraft>) =>
    setSchemaDraft({ ...schema, ...patch });
  const addField = () => {
    const field = newField.trim().toLowerCase().replace(/\s+/g, "_");
    if (!field || schema.fields.includes(field)) return;
    if (schema.fields.length >= 12) {
      toast.error("A schema can contain up to 12 fields.");
      return;
    }
    updateSchema({ fields: [...schema.fields, field] });
    setNewField("");
  };
  const openPassage = () => {
    if (!selectedRow?.document_id || !activeCell?.page) return;
    window.dispatchEvent(
      new CustomEvent("six:open-paper", {
        detail: {
          documentId: selectedRow.document_id,
          title: selectedRow.title,
          page: activeCell.page,
          flash: activeCell.quote,
          highlights: activeCell.quote
            ? [
                {
                  page: activeCell.page,
                  quote: activeCell.quote,
                  note: `${fieldLabel(activeField)} extraction`,
                },
              ]
            : [],
        },
      }),
    );
  };

  if (isLoading || !table) {
    return (
      <div className="grid h-full place-items-center">
        <Loader2 className="size-5 animate-spin text-moss" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border px-5 py-3">
        <div className="min-w-0 flex-1 basis-56">
          <div className="flex items-center gap-2">
            <p className="text-[0.8125rem] font-medium text-foreground">{schema.name}</p>
            <span className="font-mono text-[0.5625rem] uppercase tracking-[0.16em] text-muted-foreground">
              {schema.reviewer_mode.replace("_", " ")} review
            </span>
          </div>
          <div className="mt-1.5 flex max-w-xl items-center gap-2">
            <Progress value={reviewProgress} className="h-1.5" />
            <span className="shrink-0 font-mono text-[0.59375rem] text-muted-foreground">
              {table.summary.reviewed_cells}/{table.summary.total_cells} checked
            </span>
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8 rounded-full"
            disabled={isRunning || extract.isPending}
            onClick={() => setContractOpen(true)}
          >
            <SlidersHorizontal className="size-3.5" />
            Edit contract
          </Button>
          {table.summary.conflicts > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-1 text-[0.6875rem] text-red-800">
              <AlertCircle className="size-3" />
              {table.summary.conflicts} conflict{table.summary.conflicts === 1 ? "" : "s"}
            </span>
          )}
          {table.status !== "none" && (
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full"
              onClick={() => {
                void downloadRunAsset(runId, "extraction.csv", "evidence-table.csv").catch(
                  (error) =>
                    toast.error(
                      error instanceof Error ? error.message : "Export failed.",
                    ),
                );
              }}
            >
              <FileDown className="size-3.5" /> CSV
            </Button>
          )}
          <Button
            size="sm"
            className="h-8 rounded-full"
            disabled={extract.isPending || isRunning}
            onClick={() => requestExtraction()}
          >
            {extract.isPending || isRunning ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : table.status === "none" ? (
              <Table2 className="size-3.5" />
            ) : (
              <RefreshCcw className="size-3.5" />
            )}
            {isRunning
              ? "Extracting…"
              : table.status === "none"
                ? "Extract studies"
                : table.failed_count > 0
                  ? "Retry unfinished"
                  : table.unfinished_count > 0
                    ? "Extract unfinished"
                    : "Check for new studies"}
          </Button>
          {table.status !== "none" && (
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full"
              disabled={extract.isPending || isRunning}
              onClick={() => requestExtraction(true)}
            >
              <RefreshCcw className="size-3.5" /> Re-extract all
            </Button>
          )}
        </div>
      </div>
      {!isRunning && table.unfinished_count > 0 && (
        <p className="shrink-0 border-b border-border px-5 py-2 text-[0.75rem] text-muted-foreground" role="status">
          {table.unfinished_count} {table.unfinished_count === 1 ? "study is" : "studies are"} unfinished.
          {" "}Completed results are kept when you retry.
        </p>
      )}

      <div className="block min-h-0 flex-1 overflow-y-auto xl:grid xl:grid-cols-[250px_minmax(360px,1fr)_310px] xl:overflow-hidden">
        <aside className="min-h-fit overflow-visible border-b border-border bg-secondary/15 p-4 xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
          <div className="mb-2 flex items-center justify-between">
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-muted-foreground">
              Studies
            </p>
            <span className="font-mono text-[0.5625rem] text-muted-foreground">
              {visibleRows.length}
            </span>
          </div>
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" />
            <Input
              value={studyFilter}
              onChange={(event) => setStudyFilter(event.target.value)}
              placeholder="Find a study"
              className="h-8 pl-8 text-[0.6875rem]"
            />
          </div>
          <div className="mt-2 space-y-1">
            {visibleRows.map((row) => {
              const reviewed = schema.fields.filter((field) =>
                ["confirmed", "corrected"].includes(
                  row.payload[field]?.review_status ?? "",
                ),
              ).length;
              const active = selectedRow?.work_id === row.work_id;
              return (
                <button
                  key={row.work_id}
                  type="button"
                  onClick={() => selectCell(row, activeField)}
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2 rounded-xl px-2.5 py-2.5 text-left transition-colors",
                    active ? "bg-primary text-primary-foreground" : "hover:bg-secondary",
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <p className="line-clamp-2 text-[0.71875rem] font-medium leading-snug">
                      {cleanTitle(row.title)}
                    </p>
                    <p
                      className={cn(
                        "mt-1 font-mono text-[0.5625rem]",
                        active ? "text-ivory/55" : "text-muted-foreground",
                      )}
                    >
                      {row.year ?? "n.d."} · {reviewed}/{schema.fields.length} reviewed
                    </p>
                  </div>
                  <ChevronRight className="size-3.5 shrink-0 opacity-45" />
                </button>
              );
            })}
          </div>
        </aside>

        <main className="min-h-fit overflow-visible border-b border-border p-5 xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
          {selectedRow ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                    {selectedRow.work_id} · {selectedRow.year ?? "n.d."}
                  </p>
                  <h2 className="mt-1 max-w-3xl font-display text-2xl leading-tight text-foreground">
                    {cleanTitle(selectedRow.title)}
                  </h2>
                </div>
                <span
                  className={cn(
                    "rounded-full px-2.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.14em]",
                    selectedRow.status === "done"
                      ? "bg-accent text-moss"
                      : selectedRow.status === "failed"
                        ? "bg-red-50 text-red-800"
                        : "bg-secondary text-muted-foreground",
                  )}
                >
                  {selectedRow.status === "pending" && !isRunning
                    ? "unfinished"
                    : selectedRow.status}
                </span>
              </div>
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                {schema.fields.map((field) => {
                  const cell = selectedRow.payload[field];
                  const active = activeField === field;
                  return (
                    <button
                      key={field}
                      type="button"
                      onClick={() => selectCell(selectedRow, field)}
                      className={cn(
                        "group min-h-32 cursor-pointer rounded-2xl border p-4 text-left transition-all",
                        cellTone(cell),
                        active && "ring-2 ring-moss/25",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="font-mono text-[0.59375rem] uppercase tracking-[0.16em] text-muted-foreground">
                          {fieldLabel(field)}
                        </p>
                        {cell?.review_status === "confirmed" ||
                        cell?.review_status === "corrected" ? (
                          <BadgeCheck className="size-3.5 text-moss" />
                        ) : !cell?.verified && cell ? (
                          <AlertCircle className="size-3.5 text-amber-700" />
                        ) : (
                          <PencilLine className="size-3.5 text-muted-foreground/60" />
                        )}
                      </div>
                      <p className="mt-3 line-clamp-3 text-[0.8125rem] leading-relaxed text-foreground/85">
                        {cell?.value || (
                          <span className="text-muted-foreground">
                            {selectedRow.status === "pending" && isRunning
                              ? "Waiting for extraction…"
                              : "No value extracted"}
                          </span>
                        )}
                      </p>
                      {cell?.quote && (
                        <p className="mt-3 line-clamp-1 text-[0.65625rem] italic text-muted-foreground">
                          “{cell.quote}”
                        </p>
                      )}
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <EmptyExtraction />
          )}
        </main>

        <aside className="min-h-fit overflow-visible bg-card p-5 xl:min-h-0 xl:overflow-y-auto">
          {selectedRow && activeField ? (
            <>
              <p className="font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-muted-foreground">
                Cell inspector
              </p>
              <h3 className="mt-1 font-display text-2xl text-foreground">
                {fieldLabel(activeField)}
              </h3>
              <div className="mt-4 flex flex-wrap gap-1.5">
                <span
                  className={cn(
                    "rounded-full px-2.5 py-1 text-[0.65625rem]",
                    activeCell?.verified
                      ? "bg-accent text-moss"
                      : "bg-amber-50 text-amber-800 dark:bg-amber-300/10 dark:text-amber-200",
                  )}
                >
                  {activeCell?.verified ? "Quote located" : "Needs source check"}
                </span>
                {activeCell?.source && (
                  <span className="rounded-full bg-secondary px-2.5 py-1 text-[0.65625rem] text-muted-foreground">
                    {activeCell.source}
                  </span>
                )}
                {activeCell?.page && (
                  <span className="rounded-full bg-secondary px-2.5 py-1 text-[0.65625rem] text-muted-foreground">
                    page {activeCell.page}
                  </span>
                )}
              </div>
              {activeCell?.quote ? (
                <button
                  type="button"
                  onClick={openPassage}
                  disabled={!selectedRow.document_id || !activeCell.page}
                  className="mt-4 w-full cursor-pointer rounded-2xl border-l-2 border-moss bg-secondary/45 px-4 py-3 text-left disabled:cursor-default"
                >
                  <p className="text-[0.75rem] italic leading-relaxed text-muted-foreground">
                    “{activeCell.quote}”
                  </p>
                  {selectedRow.document_id && activeCell.page && (
                    <span className="mt-2 inline-flex items-center gap-1 text-[0.65625rem] font-medium text-moss">
                      <BookOpen className="size-3" /> Open source passage
                    </span>
                  )}
                </button>
              ) : (
                <div className="mt-4 rounded-2xl border border-dashed border-border p-4 text-[0.71875rem] leading-relaxed text-muted-foreground">
                  No supporting passage was extracted. Verify this value in the full text
                  before confirming it.
                </div>
              )}

              <label className="mt-5 block">
                <span className="text-[0.6875rem] font-medium">Reviewed value</span>
                <Textarea
                  value={reviewValue}
                  onChange={(event) => setReviewValue(event.target.value)}
                  placeholder={activeCell?.value || "Enter the verified value"}
                  className="mt-1.5 min-h-24 text-[0.75rem]"
                />
              </label>
              <label className="mt-3 block">
                <span className="text-[0.6875rem] font-medium">Reviewer note</span>
                <Textarea
                  value={reviewNote}
                  onChange={(event) => setReviewNote(event.target.value)}
                  placeholder="Decision rationale, unit conversion, unresolved ambiguity…"
                  className="mt-1.5 min-h-20 text-[0.75rem]"
                />
              </label>
              {schema.reviewer_mode !== "single" && (
                <div className="mt-4 rounded-2xl bg-secondary/45 p-3">
                  <p className="text-[0.6875rem] font-medium">Independent pass</p>
                  <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
                    {schema.reviewer_mode === "blinded"
                      ? "The second reviewer records a value without seeing the first decision."
                      : "A second decision is compared with round one and disagreements become conflicts."}
                  </p>
                  <div className="mt-2 grid grid-cols-2 gap-1.5">
                    {[1, 2].map((round) => (
                      <button
                        key={round}
                        type="button"
                        onClick={() => setReviewRound(round as 1 | 2)}
                        className={cn(
                          "cursor-pointer rounded-lg px-2 py-1.5 text-[0.6875rem]",
                          reviewRound === round
                            ? "bg-primary text-primary-foreground"
                            : "border border-border bg-card text-muted-foreground",
                        )}
                      >
                        Review {round}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="mt-4 grid gap-2">
                <Button
                  variant="outline"
                  className="rounded-full"
                  disabled={review.isPending}
                  onClick={() => review.mutate("needs_attention")}
                >
                  <AlertCircle className="size-3.5" /> Needs attention
                </Button>
                <Button
                  className="rounded-full"
                  disabled={review.isPending}
                  onClick={() =>
                    review.mutate(
                      reviewValue &&
                        activeCell?.value &&
                        reviewValue !== activeCell.value
                        ? "corrected"
                        : "confirmed",
                    )
                  }
                >
                  {review.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Check className="size-3.5" />
                  )}
                  Confirm
                </Button>
              </div>
              {activeCell?.reviews?.length ? (
                <div className="mt-6">
                  <p className="font-mono text-[0.5625rem] uppercase tracking-[0.16em] text-muted-foreground">
                    Review trail
                  </p>
                  <div className="mt-2 space-y-2">
                    {activeCell.reviews.map((item) => (
                      <div
                        key={`${item.round}-${item.reviewed_at}`}
                        className="rounded-xl border border-border/70 px-3 py-2.5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-[0.6875rem] font-medium">
                            Round {item.round}
                          </p>
                          <span className="font-mono text-[0.5625rem] uppercase text-muted-foreground">
                            {item.verdict.replace("_", " ")}
                          </span>
                        </div>
                        {item.note && (
                          <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
                            {item.note}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="grid min-h-52 place-items-center text-center xl:min-h-72">
              <div>
                <PencilLine className="mx-auto size-5 text-muted-foreground" />
                <p className="mt-3 text-[0.8125rem] font-medium">Select an extraction cell</p>
                <p className="mt-1 text-[0.6875rem] text-muted-foreground">
                  Its source passage and review controls will appear here.
                </p>
              </div>
            </div>
          )}
        </aside>
      </div>

      <AlertDialog open={reextractOpen} onOpenChange={setReextractOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Re-extract all included studies?</AlertDialogTitle>
            <AlertDialogDescription>
              This starts a new extraction for every included study using the current
              contract, including studies already completed. Each completed new result
              replaces its current table values, including reviewed edits. Download the
              CSV first if you want to keep the current table.
              To keep completed results, cancel and use the primary extraction button instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep current results</AlertDialogCancel>
            <AlertDialogAction
              disabled={extract.isPending || isRunning}
              onClick={confirmReextraction}
            >
              Re-extract all
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <Dialog
        open={contractOpen}
        onOpenChange={(open) => {
          setContractOpen(open);
          if (!open) {
            setSchemaDraft(null);
            setNewField("");
          }
        }}
      >
        <DialogContent className="max-h-[calc(100vh-3rem)] overflow-y-auto p-0 sm:max-w-3xl">
          <DialogHeader className="border-b border-border px-6 py-5 pr-14">
            <DialogTitle className="font-display text-2xl font-normal text-foreground">
              Extraction contract
            </DialogTitle>
            <DialogDescription className="text-[0.75rem]">
              Define exactly what should be charted from every included study.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-6 px-6 py-2 md:grid-cols-[minmax(0,1.15fr)_minmax(240px,.85fr)]">
            <div>
              <label className="block">
                <span className="text-[0.6875rem] font-medium">Contract name</span>
                <Input
                  value={schema.name}
                  onChange={(event) => updateSchema({ name: event.target.value })}
                  className="mt-1.5"
                  aria-label="Extraction schema name"
                />
              </label>
              <div className="mt-5">
                <p className="text-[0.6875rem] font-medium">Fields</p>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {schema.fields.map((field) => (
                    <div
                      key={field}
                      className="flex items-center gap-2 rounded-xl border border-border/70 bg-card px-3 py-2.5"
                    >
                      <span className="min-w-0 flex-1 truncate text-[0.75rem]">
                        {fieldLabel(field)}
                      </span>
                      <button
                        type="button"
                        disabled={schema.fields.length === 1}
                        onClick={() =>
                          updateSchema({
                            fields: schema.fields.filter(
                              (candidate) => candidate !== field,
                            ),
                          })
                        }
                        className="cursor-pointer text-muted-foreground hover:text-destructive disabled:opacity-30"
                        aria-label={`Remove ${fieldLabel(field)}`}
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <Input
                    value={newField}
                    onChange={(event) => setNewField(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") addField();
                    }}
                    placeholder="Add another field"
                  />
                  <Button
                    variant="outline"
                    size="icon"
                    className="shrink-0"
                    disabled={!newField.trim()}
                    onClick={addField}
                  >
                    <Plus className="size-4" />
                  </Button>
                </div>
              </div>
              <label className="mt-5 block">
                <span className="text-[0.6875rem] font-medium">
                  Reviewer instructions
                </span>
                <Textarea
                  value={schema.instructions}
                  onChange={(event) =>
                    updateSchema({ instructions: event.target.value })
                  }
                  placeholder="Endpoint hierarchy, units, time points and ambiguity rules…"
                  className="mt-1.5 min-h-28 text-[0.75rem]"
                />
              </label>
            </div>
            <div>
              <p className="text-[0.6875rem] font-medium">Start from a framework</p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() =>
                      updateSchema({
                        name: `${preset.label} extraction`,
                        fields: [...preset.fields],
                      })
                    }
                    className="cursor-pointer rounded-xl border border-border bg-card px-3 py-2.5 text-left text-[0.71875rem] text-muted-foreground transition-colors hover:border-moss/40 hover:text-foreground"
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
              <label className="mt-5 block">
                <span className="text-[0.6875rem] font-medium">Review method</span>
                <Select
                  value={schema.reviewer_mode}
                  onValueChange={(value: SchemaDraft["reviewer_mode"]) =>
                    updateSchema({ reviewer_mode: value })
                  }
                >
                  <SelectTrigger className="mt-1.5">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="single">
                      <span className="inline-flex items-center gap-2">
                        <UserCheck className="size-3.5" /> Single verification
                      </span>
                    </SelectItem>
                    <SelectItem value="double">
                      <span className="inline-flex items-center gap-2">
                        <Users className="size-3.5" /> Double extraction
                      </span>
                    </SelectItem>
                    <SelectItem value="blinded">
                      <span className="inline-flex items-center gap-2">
                        <Users className="size-3.5" /> Blinded double extraction
                      </span>
                    </SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <div className="mt-5 rounded-2xl bg-secondary/45 p-4">
                <p className="text-[0.71875rem] font-medium text-foreground">
                  {schema.fields.length} charting fields
                </p>
                <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  Changes affect new extractions. Existing reviewed values remain in
                  the audit trail.
                </p>
              </div>
            </div>
          </div>
          <DialogFooter className="mx-0 mb-0 rounded-none rounded-b-2xl px-6">
            <Button
              variant="ghost"
              className="rounded-full"
              onClick={() => {
                setContractOpen(false);
                setSchemaDraft(null);
                setNewField("");
              }}
            >
              Cancel
            </Button>
            <Button
              className="rounded-full"
              disabled={!schemaDraft || saveSchema.isPending}
              onClick={() => saveSchema.mutate()}
            >
              {saveSchema.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Save contract
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function EmptyExtraction() {
  return (
    <div className="grid min-h-64 place-items-center rounded-3xl border border-dashed border-border md:min-h-72 xl:min-h-[28rem]">
      <div className="max-w-sm text-center">
        <span className="mx-auto grid size-11 place-items-center rounded-full bg-accent">
          <Table2 className="size-5 text-moss" />
        </span>
        <h2 className="mt-4 font-display text-2xl text-foreground">No extracted studies yet</h2>
        <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
          Set the charting contract, then extract every included study into a
          quote-backed, reviewable evidence table.
        </p>
      </div>
    </div>
  );
}
