"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, CircleAlert, Loader2, Pencil, Plus, RefreshCcw, Table as TableIcon, X } from "lucide-react";
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
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { EvidenceCell } from "@/lib/types";
import { cn } from "@/lib/utils";

const DEFAULT_SCHEMA = ["population", "method", "sample_size", "outcomes", "limitations"];

/** The charting schema as editable chips; extraction follows it exactly. */
function FieldSchemaEditor({
  fields,
  onChange,
  disabled = false,
}: {
  fields: string[];
  onChange: (fields: string[]) => void;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const next = draft.trim().toLowerCase().replace(/\s+/g, "_");
    setDraft("");
    if (!next || fields.includes(next)) return;
    if (fields.length >= 12) {
      toast.error("Up to 12 fields.");
      return;
    }
    onChange([...fields, next]);
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {fields.map((field) => (
        <span
          key={field}
          className="inline-flex items-center gap-1 rounded-full border border-border bg-card px-2.5 py-1 text-[0.6875rem] text-foreground"
        >
          {field.replace(/_/g, " ")}
          <button
            type="button"
            disabled={disabled || fields.length <= 1}
            onClick={() => onChange(fields.filter((item) => item !== field))}
            aria-label={`Remove ${field}`}
            className="cursor-pointer text-muted-foreground hover:text-destructive disabled:opacity-40"
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
          placeholder="add field…"
          disabled={disabled}
          aria-label="New extraction field"
          className="w-24 bg-transparent text-[0.6875rem] outline-none placeholder:text-muted-foreground/60"
        />
        <button
          type="button"
          onClick={add}
          disabled={disabled || !draft.trim()}
          aria-label="Add field"
          className="cursor-pointer text-moss hover:text-foreground disabled:opacity-40"
        >
          <Plus className="size-3" />
        </button>
      </span>
    </div>
  );
}

function CellPopover({
  runId,
  workId,
  field,
  cell,
}: {
  runId: number;
  workId: string;
  field: string;
  cell: EvidenceCell | undefined;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const save = useMutation({
    mutationFn: () => api.editExtraction(runId, workId, field, draft.trim()),
    onSuccess: () => {
      toast.success("Cell updated.");
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["extraction", runId] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const value = cell?.value ?? "";

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setDraft(value);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className="group/cell flex w-full cursor-pointer items-start gap-1.5 rounded-lg px-1.5 py-1 text-left transition-colors hover:bg-secondary/60"
        >
          <span className="min-w-0 flex-1 text-[0.78125rem] leading-snug">
            {value || <span className="text-muted-foreground/50">…</span>}
          </span>
          {cell ? (
            cell.verified ? (
              <BadgeCheck className="mt-0.5 size-3 shrink-0 text-moss" />
            ) : (
              <CircleAlert className="mt-0.5 size-3 shrink-0 text-amber-600" />
            )
          ) : null}
          <Pencil className="mt-0.5 size-3 shrink-0 text-muted-foreground/0 transition-colors group-hover/cell:text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-3">
        {cell?.quote ? (
          <blockquote className="mb-2 rounded-lg border-l-2 border-moss/50 bg-secondary/50 px-3 py-2 text-[0.75rem] italic leading-relaxed text-muted-foreground">
            “{cell.quote}”
            <span className="mt-1 block font-mono text-[0.625rem] uppercase not-italic tracking-[0.18em]">
              {cell.verified
                ? `verified${cell.page ? ` · page ${cell.page}` : ""} · ${cell.source}`
                : "quote not located in the text"}
            </span>
          </blockquote>
        ) : cell?.source === "human" ? (
          <p className="mb-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            human entry
          </p>
        ) : null}
        <Textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={3}
          className="rounded-lg text-[0.8125rem]"
          aria-label={`Edit ${field}`}
        />
        <div className="mt-2 flex justify-end">
          <Button
            size="sm"
            disabled={save.isPending || draft.trim() === value}
            onClick={() => save.mutate()}
            className="h-7 rounded-full px-3 text-[0.71875rem]"
          >
            {save.isPending ? <Loader2 className="size-3 animate-spin" /> : "Save"}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** The evidence table: one row per included work, quote-verified cells. */
export default function EvidencePanel({ runId }: { runId: number }) {
  const queryClient = useQueryClient();
  const { data: table, isLoading } = useQuery({
    queryKey: ["extraction", runId],
    queryFn: () => api.extraction(runId),
    refetchInterval: (query) =>
      query.state.data?.is_running === true ? 2500 : false,
  });
  // the charting schema the next extraction runs with; null = as extracted
  const [schema, setSchema] = useState<string[] | null>(null);
  const [reextractOpen, setReextractOpen] = useState(false);
  const isRunning = table?.is_running === true;
  const fields = schema ?? (table && table.fields.length > 0 ? table.fields : DEFAULT_SCHEMA);
  const schemaDirty =
    schema !== null && table !== undefined && table.status !== "none"
      ? schema.join(" ") !== table.fields.join(" ")
      : false;
  const start = useMutation({
    mutationFn: (input: { fields: string[]; force?: boolean }) =>
      api.startExtraction(runId, input.fields, input.force ?? false),
    onSuccess: (result) => {
      toast.success(
        result.scheduled
          ? `Extracting evidence from ${result.scheduled} works…`
          : "Every included study is already extracted.",
      );
      setReextractOpen(false);
      setSchema(null);
      void queryClient.invalidateQueries({ queryKey: ["extraction", runId] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  function requestExtraction(reextractAll = false) {
    if (!table || isRunning || start.isPending) return;
    if (reextractAll) {
      setReextractOpen(true);
      return;
    }
    start.mutate({ fields, force: false });
  }
  function confirmReextraction() {
    if (!reextractOpen || !table || isRunning || start.isPending) return;
    start.mutate({ fields, force: true });
  }

  if (isLoading) {
    return <Loader2 className="mx-auto my-10 size-5 animate-spin text-muted-foreground" />;
  }
  if (!table || table.status === "none") {
    return (
      <div className="rounded-2xl border border-dashed border-border px-6 py-10 text-center">
        <span className="mx-auto grid size-11 place-items-center rounded-full bg-accent">
          <TableIcon className="size-5 text-moss" />
        </span>
        <p className="mt-4 text-[0.9375rem] font-medium text-foreground">
          Chart the evidence
        </p>
        <p className="mx-auto mt-1 max-w-md text-[0.8125rem] text-muted-foreground">
          The model fills your fields for every included work. Each value is
          backed by a verbatim quote, verified against the stored text.
        </p>
        <div className="mx-auto mt-4 flex max-w-lg justify-center">
          <FieldSchemaEditor fields={fields} onChange={setSchema} />
        </div>
        <Button
          size="sm"
          disabled={start.isPending || isRunning}
          onClick={() => requestExtraction()}
          className="mt-4 h-9 rounded-full px-4"
        >
          {start.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            "Extract evidence"
          )}
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {isRunning ? (
        <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          <Loader2 className="size-3 animate-spin" /> extracting…
        </p>
      ) : null}
      {!isRunning && table.unfinished_count > 0 && (
        <p className="text-[0.75rem] text-muted-foreground" role="status">
          {table.unfinished_count} {table.unfinished_count === 1 ? "study is" : "studies are"} unfinished.
          {" "}Completed results are kept when you retry.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-border bg-card px-3 py-2.5">
        <FieldSchemaEditor
          fields={fields}
          onChange={setSchema}
          disabled={isRunning || start.isPending}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            className="h-8 shrink-0 rounded-full px-3 text-[0.71875rem]"
            disabled={start.isPending || isRunning}
            onClick={() => requestExtraction()}
          >
            {start.isPending || isRunning ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <TableIcon className="size-3.5" />
            )}
            {isRunning
              ? "Extracting…"
              : table.failed_count > 0
                ? "Retry unfinished"
                : table.unfinished_count > 0
                  ? "Extract unfinished"
                  : "Check for new studies"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 shrink-0 rounded-full px-3 text-[0.71875rem]"
            disabled={start.isPending || isRunning}
            onClick={() => requestExtraction(true)}
          >
            {start.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <RefreshCcw className="size-3.5" />
            )}
            Re-extract all
          </Button>
        </div>
      </div>
      {schemaDirty && (
        <p className="text-[0.75rem] text-muted-foreground">
          Updated fields apply to new or unfinished studies. To replace completed
          results using these fields, choose Re-extract all and confirm.
        </p>
      )}
      <div className="overflow-x-auto rounded-2xl border border-border">
        <table className="w-full text-left text-[0.8125rem]">
          <thead>
            <tr className="border-b border-border bg-secondary/50 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              <th className="min-w-56 px-3 py-2.5">Study</th>
              {table.fields.map((field) => (
                <th key={field} className="min-w-44 px-2 py-2.5">
                  {field.replace(/_/g, " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row) => (
              <tr key={row.work_id} className="border-b border-border/60 align-top last:border-0">
                <td className="px-3 py-2">
                  <p className="font-medium leading-snug">{row.title}</p>
                  <p className="mt-0.5 font-mono text-[0.625rem] text-muted-foreground">
                    {row.year ?? "n.d."}
                    {row.status === "pending" ? (isRunning ? " · extracting…" : " · unfinished") : ""}
                    {row.status === "failed" ? " · failed" : ""}
                  </p>
                </td>
                {table.fields.map((field) => (
                  <td key={field} className={cn("px-1 py-1.5", row.status !== "done" && "opacity-40")}>
                    <CellPopover
                      runId={runId}
                      workId={row.work_id}
                      field={field}
                      cell={row.payload[field]}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[0.71875rem] text-muted-foreground">
        Green check: the quote sits verbatim in the stored text. Amber: the
        model's quote could not be located, treat the value with care. Click
        any cell to see the quote or correct the value yourself.
      </p>
      <AlertDialog open={reextractOpen} onOpenChange={setReextractOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Re-extract all included studies?</AlertDialogTitle>
            <AlertDialogDescription>
              This starts a new extraction for every included study using the current
              fields, including studies already completed. Each completed new result
              replaces its current table values, including reviewed edits. Export the
              current table from Extraction Studio first
              if you want to keep it. To keep completed results, cancel and use the
              primary extraction button instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep current results</AlertDialogCancel>
            <AlertDialogAction
              disabled={start.isPending || isRunning}
              onClick={confirmReextraction}
            >
              Re-extract all
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
