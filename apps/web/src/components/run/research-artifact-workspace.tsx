"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Globe2,
  Loader2,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Table2,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import UiResourceFrame from "@/components/run/ui-resource-frame";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { PaperPanelState } from "@/components/run/paper-panel";
import { api, type RunRef } from "@/lib/api";
import type { ChatSelection, ToolStepPayload, UiResource } from "@/lib/types";
import { cn } from "@/lib/utils";

const PaperPanel = dynamic(() => import("@/components/run/paper-panel"), {
  ssr: false,
});

export interface TableArtifact {
  title: string;
  columns?: string[];
  rows?: string[][];
  resource?: UiResource;
  messageId?: number;
  revision?: number;
  original?: {
    title: string;
    columns: string[];
    rows: string[][];
  };
}

export interface SourceArtifact {
  title: string;
  url: string;
  domain: string;
  excerpt: string;
  description?: string;
  headings?: string[];
  wordCount?: number;
  quality?: number;
}

export interface ClaimArtifact {
  claim: string;
  verdict: "supported" | "contradicted" | "mixed" | "insufficient";
  confidence: "high" | "medium" | "low";
  rationale: string;
  evidence: Array<{
    work_id: string;
    title: string;
    year?: number | null;
    venue?: string | null;
    stance: "supports" | "contradicts" | "context";
    reason: string;
  }>;
}

export type ResearchArtifact =
  | {
      id: string;
      kind: "paper";
      title: string;
      paper: PaperPanelState;
    }
  | {
      id: string;
      kind: "table";
      title: string;
      table: TableArtifact;
    }
  | {
      id: string;
      kind: "source";
      title: string;
      source: SourceArtifact;
    }
  | {
      id: string;
      kind: "claim";
      title: string;
      claim: ClaimArtifact;
    };

export function isSubstantialTable(table: TableArtifact): boolean {
  const columns = table.columns?.length ?? 0;
  const rows = table.rows?.length ?? 0;
  if (columns === 0 || rows === 0) return Boolean(table.resource);

  // Small comparison tables are faster to scan in the conversation. Move a
  // table into the split workspace only when either axis or the total grid
  // becomes cumbersome enough to interrupt the reading flow.
  // A long two-column list (for example year + publication count) is still
  // easier to scan inline. The side workspace is for genuinely wide or dense
  // evidence matrices, not merely for a high row count.
  return columns >= 7 || rows >= 12 || columns * rows >= 36;
}

export function tableArtifactFromPayload(
  payload: ToolStepPayload,
): TableArtifact | null {
  const structured = payload.table;
  const result = payload.results?.[0];
  const plainCell = (value: string) =>
    String(value)
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .trim();
  const columns = (structured?.columns ?? result?.columns)?.map(plainCell);
  const sourceRows = structured?.rows ?? result?.rows;
  const rows = sourceRows?.map((row) => {
    const cleaned = row.map(plainCell);
    const first = cleaned[0]?.trim() ?? "";
    const reference = first.match(/^\s*\[?(W\d+)\]?/);
    if (!reference) return cleaned;
    const label = first.replace(/^\s*(?:\[?W\d+\]?\s*)+/, "").trim();
    return [`[${reference[1]}]${label ? ` ${label}` : ""}`, ...cleaned.slice(1)];
  });
  const isTable =
    payload.kind === "table" ||
    payload.tool === "make_table" ||
    Boolean(structured);
  if (!isTable) return null;
  return {
    title:
      structured?.title?.trim() ||
      result?.title?.trim() ||
      (payload.tool === "make_table" ? payload.query.trim() : "") ||
      (payload.table_kind === "evidence_comparison"
        ? "Evidence comparison"
        : payload.tool === "extract_data"
          ? "Evidence comparison"
          : "Research table"),
    columns,
    rows,
    resource: payload.resource,
    revision: payload.table_revision,
    original: payload.table_original
      ? {
          title: payload.table_original.title?.trim() || "Research table",
          columns: payload.table_original.columns,
          rows: payload.table_original.rows,
        }
      : undefined,
  };
}

export function sourceArtifactFromPayload(
  payload: ToolStepPayload,
): SourceArtifact | null {
  if (payload.tool !== "read_webpage" || payload.status === "failed") return null;
  const result = payload.results?.[0];
  if (!result?.url || !result.excerpt) return null;
  let fallbackDomain = result.url;
  try {
    fallbackDomain = new URL(result.url).hostname;
  } catch {
    // The backend normally returns an absolute URL. Keep the visible value
    // usable if an older persisted tool result does not.
  }
  const excerpt = result.excerpt
    // Some documentation sites expose utility-class fragments as visible
    // accessibility text. Keep the source reader focused on the article,
    // without mutating the persisted evidence captured by the agent.
    .replace(/(?:\[[^\]\s]+\]|[a-z]+])(?::[a-z0-9./[\]-]+)+/gi, " ")
    .replace(
      /\b(?:dark:)?(?:bg|border|text|font|leading|tracking|rounded|px|py|p|m|w|h)-[a-z0-9./[\]-]+\b/gi,
      " ",
    )
    .replace(/\\?"+\s*(?:role|aria-hidden)=[^>\s]+>*/gi, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
  return {
    title: result.title?.trim() || result.domain?.trim() || "Source",
    url: result.url,
    domain: result.domain?.trim() || fallbackDomain,
    excerpt,
    description: result.description,
    headings: result.headings,
    wordCount: result.word_count,
    quality: result.quality,
  };
}

export function claimArtifactFromPayload(
  payload: ToolStepPayload,
): ClaimArtifact | null {
  if (payload.kind !== "claim_verification") return null;
  const result = payload.results?.[0];
  if (!result?.claim || !result.verdict || !result.confidence) return null;
  return {
    claim: result.claim,
    verdict: result.verdict,
    confidence: result.confidence,
    rationale: result.rationale ?? "",
    evidence: result.evidence ?? [],
  };
}

function csvCell(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function tableText(columns: string[], rows: string[][], separator: string): string {
  return [columns, ...rows]
    .map((row) => row.map((value) => value.replace(/\s+/g, " ").trim()).join(separator))
    .join("\n");
}

const WORK_REFERENCE = /(\[W\d+\]|\bW\d{4,}\b)/g;

function TableCell({
  value,
  onPrompt,
}: {
  value: string;
  onPrompt: (prompt: string) => void;
}) {
  const parts = value.split(WORK_REFERENCE);
  return (
    <>
      {parts.map((part, index) => {
        const workId = part.replace(/[\[\]]/g, "");
        if (!/^W\d+$/.test(workId)) return <span key={index}>{part}</span>;
        return (
          <button
            key={`${workId}-${index}`}
            type="button"
            className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full border border-moss/25 bg-accent px-1.5 font-mono text-[0.625rem] font-semibold text-moss transition-colors hover:bg-moss-surface hover:text-ivory"
            title={`Ask about ${workId}`}
            onClick={() => onPrompt(`Tell me more about ${workId} in this comparison.`)}
          >
            {workId}
          </button>
        );
      })}
    </>
  );
}

type EditableTable = {
  title: string;
  columns: string[];
  rows: string[][];
};

function tableSnapshot(table: TableArtifact): EditableTable {
  return {
    title: table.title || "Research table",
    columns: [...(table.columns ?? [])],
    rows: (table.rows ?? []).map((row) => [...row]),
  };
}

function sameTable(left: EditableTable, right: EditableTable): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function TableWorkspace({
  table,
  runId,
  onPrompt,
}: {
  table: TableArtifact;
  runId: RunRef;
  onPrompt: (prompt: string) => void;
}) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState<EditableTable>(() => tableSnapshot(table));
  const [draft, setDraft] = useState<EditableTable>(() => tableSnapshot(table));
  const [undoStack, setUndoStack] = useState<EditableTable[]>([]);

  useEffect(() => {
    const next = tableSnapshot(table);
    setSaved(next);
    if (!editing) setDraft(next);
  }, [editing, table]);

  const columns = draft.columns;
  const rows = draft.rows;
  const normalizedFilter = filter.trim().toLocaleLowerCase();
  const visibleRows = useMemo(
    () =>
      rows
        .map((row, index) => ({ row, index }))
        .filter(
          ({ row }) =>
            !normalizedFilter ||
            row.some((cell) =>
              cell.toLocaleLowerCase().includes(normalizedFilter),
            ),
        ),
    [normalizedFilter, rows],
  );
  const dirty = !sameTable(draft, saved);

  function changeDraft(mutator: (next: EditableTable) => void) {
    setDraft((current) => {
      const next = {
        title: current.title,
        columns: [...current.columns],
        rows: current.rows.map((row) => [...row]),
      };
      mutator(next);
      setUndoStack((history) => [...history.slice(-29), current]);
      return next;
    });
  }

  const saveMutation = useMutation({
    mutationFn: (value: EditableTable) => {
      if (!table.messageId) throw new Error("This legacy table cannot be saved.");
      return api.updateChatTable(runId, table.messageId, value);
    },
    onSuccess: (result) => {
      const next = {
        title: result.title,
        columns: result.columns,
        rows: result.rows,
      };
      setSaved(next);
      setDraft(next);
      setUndoStack([]);
      setEditing(false);
      void queryClient.invalidateQueries({ queryKey: ["chat", String(runId)] });
      toast.success("Table changes saved");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not save the table"),
  });
  const resetMutation = useMutation({
    mutationFn: () => {
      if (!table.messageId) throw new Error("This legacy table cannot be restored.");
      return api.resetChatTable(runId, table.messageId);
    },
    onSuccess: (result) => {
      const next = {
        title: result.title,
        columns: result.columns,
        rows: result.rows,
      };
      setSaved(next);
      setDraft(next);
      setUndoStack([]);
      setEditing(false);
      void queryClient.invalidateQueries({ queryKey: ["chat", String(runId)] });
      toast.success("Generated table restored");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not restore the table",
      ),
  });

  function downloadCsv() {
    if (columns.length === 0) return;
    const safeForSpreadsheet = (value: string) =>
      /^[=+\-@]/.test(value.trimStart()) ? `'${value}` : value;
    const csv = [columns, ...rows]
      .map((row) => row.map((value) => csvCell(safeForSpreadsheet(value))).join(","))
      .join("\r\n");
    const url = URL.createObjectURL(
      new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    const fileStem =
      (draft.title || "research-table")
        .toLocaleLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "") || "research-table";
    anchor.download = `${fileStem}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function copyTable() {
    if (columns.length === 0) return;
    void navigator.clipboard
      .writeText(tableText(columns, rows, "\t"))
      .then(() => toast.success("Table copied"))
      .catch(() => toast.error("Could not copy the table"));
  }

  if (columns.length === 0 && table.resource && !editing) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto bg-background p-3 sm:p-4">
        <UiResourceFrame resource={table.resource} onPrompt={onPrompt} />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background">
      <div className="shrink-0 border-b border-border px-3 py-2.5 sm:px-4">
        <div className="flex min-w-0 items-center gap-2">
          {editing ? (
            <Input
              value={draft.title}
              maxLength={240}
              onChange={(event) =>
                changeDraft((next) => {
                  next.title = event.target.value;
                })
              }
              aria-label="Table title"
              className="h-8 min-w-0 flex-1 rounded-lg bg-transparent text-[0.8125rem] font-medium"
            />
          ) : (
            <p className="min-w-0 flex-1 truncate text-[0.8125rem] font-medium text-foreground">
              {draft.title}
            </p>
          )}
          <p className="shrink-0 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
            {rows.length} × {columns.length}
          </p>
          {editing ? (
            <>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 rounded-full"
                aria-label="Undo table change"
                disabled={undoStack.length === 0}
                onClick={() => {
                  const previous = undoStack.at(-1);
                  if (!previous) return;
                  setDraft(previous);
                  setUndoStack((history) => history.slice(0, -1));
                }}
              >
                <Undo2 className="size-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 rounded-full"
                onClick={() => {
                  setDraft(saved);
                  setUndoStack([]);
                  setEditing(false);
                }}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                className="h-8 rounded-full"
                disabled={
                  !dirty ||
                  !draft.title.trim() ||
                  draft.columns.some((column) => !column.trim()) ||
                  saveMutation.isPending
                }
                onClick={() => saveMutation.mutate(draft)}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Check className="size-3.5" />
                )}
                Save
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full"
              disabled={!table.messageId}
              onClick={() => setEditing(true)}
            >
              <Pencil className="size-3.5" />
              Edit
            </Button>
          )}
        </div>
        <div className="mt-2 flex min-w-0 items-center gap-1.5">
          {rows.length >= 5 ? (
            <label className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="Filter this table"
                aria-label="Filter research table"
                className="h-8 rounded-full bg-transparent pl-8 text-[0.75rem]"
              />
            </label>
          ) : (
            <span className="flex-1" />
          )}
          {!editing && table.original ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
              disabled={resetMutation.isPending}
              onClick={() => resetMutation.mutate()}
            >
              {resetMutation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RotateCcw className="size-3.5" />
              )}
              Restore
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            className="h-8 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
            onClick={copyTable}
            disabled={columns.length === 0}
          >
            <Copy className="size-3.5" />
            Copy
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
            onClick={downloadCsv}
            disabled={columns.length === 0}
          >
            <Download className="size-3.5" />
            CSV
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {columns.length === 0 ? (
          <div className="grid h-full min-h-52 place-items-center p-8 text-center">
            <div>
              <Table2 className="mx-auto size-5 text-muted-foreground" />
              <p className="mt-3 text-[0.8125rem] font-medium">The table is empty</p>
              <p className="mt-1 text-[0.71875rem] text-muted-foreground">
                Ask the agent to add the fields or papers you need.
              </p>
            </div>
          </div>
        ) : visibleRows.length === 0 ? (
          <div className="grid h-full min-h-52 place-items-center p-8 text-center">
            <div>
              <Search className="mx-auto size-5 text-muted-foreground" />
              <p className="mt-3 text-[0.8125rem] font-medium">No matching rows</p>
              <button
                type="button"
                className="mt-1 cursor-pointer text-[0.71875rem] text-moss hover:underline"
                onClick={() => setFilter("")}
              >
                Clear the filter
              </button>
            </div>
          </div>
        ) : (
          <table className="w-max min-w-full border-separate border-spacing-0 text-left text-[0.75rem]">
            <thead className="sticky top-0 z-20">
              <tr>
                {columns.map((column, columnIndex) => (
                  <th
                    key={columnIndex}
                    className={cn(
                      "min-w-[10rem] max-w-[22rem] border-b border-r border-border bg-secondary p-2 align-bottom font-mono text-[0.625rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground last:border-r-0",
                      columnIndex === 0 && "sticky left-0 z-30 min-w-[13rem]",
                    )}
                  >
                    {editing ? (
                      <div className="flex items-center gap-1">
                        <Input
                          value={column}
                          maxLength={240}
                          aria-label={`Column ${columnIndex + 1} name`}
                          className="h-8 min-w-0 bg-card px-2 font-mono text-[0.625rem] uppercase tracking-[0.08em]"
                          onChange={(event) =>
                            changeDraft((next) => {
                              next.columns[columnIndex] = event.target.value;
                            })
                          }
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7 shrink-0 rounded-full text-muted-foreground hover:text-destructive"
                          aria-label={`Delete column ${column || columnIndex + 1}`}
                          disabled={columns.length <= 1}
                          onClick={() =>
                            changeDraft((next) => {
                              next.columns.splice(columnIndex, 1);
                              next.rows = next.rows.map((row) => {
                                const copy = [...row];
                                copy.splice(columnIndex, 1);
                                return copy;
                              });
                            })
                          }
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      </div>
                    ) : (
                      column || `Column ${columnIndex + 1}`
                    )}
                  </th>
                ))}
                {editing ? (
                  <th className="sticky right-0 z-30 w-12 border-b border-border bg-secondary p-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-8 rounded-full"
                      aria-label="Add column"
                      disabled={columns.length >= 40}
                      onClick={() =>
                        changeDraft((next) => {
                          next.columns.push(`Column ${next.columns.length + 1}`);
                          next.rows = next.rows.map((row) => [...row, ""]);
                        })
                      }
                    >
                      <Plus className="size-3.5" />
                    </Button>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map(({ row, index: rowIndex }) => (
                <tr
                  key={rowIndex}
                  className="group/row even:bg-secondary/25 hover:bg-accent/45"
                >
                  {columns.map((_, columnIndex) => {
                    const value = String(row[columnIndex] ?? "");
                    return (
                      <td
                        key={columnIndex}
                        className={cn(
                          "max-w-[22rem] border-b border-r border-border/65 bg-inherit align-top leading-relaxed text-foreground last:border-r-0",
                          editing ? "p-1.5" : "px-3 py-2.5",
                          columnIndex === 0 &&
                            "sticky left-0 z-10 bg-card font-medium group-even/row:bg-secondary group-hover/row:bg-accent",
                          !value && !editing && "text-muted-foreground/55",
                        )}
                      >
                        {editing ? (
                          <Textarea
                            value={value}
                            maxLength={4000}
                            rows={2}
                            aria-label={`Row ${rowIndex + 1}, ${columns[columnIndex]}`}
                            className="min-h-16 min-w-[9rem] resize-y bg-card px-2 py-1.5 text-[0.71875rem] leading-relaxed"
                            onChange={(event) =>
                              changeDraft((next) => {
                                next.rows[rowIndex][columnIndex] = event.target.value;
                              })
                            }
                          />
                        ) : value ? (
                          <TableCell value={value} onPrompt={onPrompt} />
                        ) : (
                          "Not reported"
                        )}
                      </td>
                    );
                  })}
                  {editing ? (
                    <td className="sticky right-0 z-10 w-12 border-b border-border/65 bg-card p-1.5 text-center group-even/row:bg-secondary group-hover/row:bg-accent">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-8 rounded-full text-muted-foreground hover:text-destructive"
                        aria-label={`Delete row ${rowIndex + 1}`}
                        onClick={() =>
                          changeDraft((next) => {
                            next.rows.splice(rowIndex, 1);
                          })
                        }
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {editing ? (
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border px-3 py-2">
          <p className="text-[0.6875rem] text-muted-foreground">
            Changes stay in this research record and survive a reload.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="h-8 shrink-0 rounded-full"
            disabled={rows.length >= 500}
            onClick={() =>
              changeDraft((next) => {
                next.rows.push(next.columns.map(() => ""));
              })
            }
          >
            <Plus className="size-3.5" />
            Add row
          </Button>
        </div>
      ) : normalizedFilter && rows.length > 0 ? (
        <p className="shrink-0 border-t border-border px-4 py-2 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
          Showing {visibleRows.length} of {rows.length} rows
        </p>
      ) : null}
    </div>
  );
}

function SourceWorkspace({
  source,
  onPrompt,
}: {
  source: SourceArtifact;
  onPrompt: (prompt: string) => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-background">
      <div className="sticky top-0 z-10 border-b border-border bg-background/95 px-4 py-3 backdrop-blur">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-moss">
            <Globe2 className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-[0.875rem] font-medium leading-snug">{source.title}</h3>
            <p className="mt-0.5 truncate font-mono text-[0.625rem] uppercase tracking-[0.12em] text-muted-foreground">
              {source.domain}
              {source.wordCount ? ` · ${source.wordCount.toLocaleString()} words` : ""}
            </p>
          </div>
          <Button asChild variant="outline" size="sm" className="h-8 rounded-full">
            <a href={source.url} target="_blank" rel="noreferrer">
              <ExternalLink className="size-3.5" />
              Original
            </a>
          </Button>
        </div>
      </div>
      <article className="mx-auto max-w-3xl px-5 py-6">
        {source.description ? (
          <p className="mb-5 border-l-2 border-moss/40 pl-4 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {source.description}
          </p>
        ) : null}
        {source.headings?.length ? (
          <div className="mb-5 rounded-2xl border border-border bg-secondary/35 p-4">
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.16em] text-muted-foreground">
              Sections read
            </p>
            <p className="mt-2 text-[0.75rem] leading-relaxed">
              {source.headings.slice(0, 8).join(" · ")}
            </p>
          </div>
        ) : null}
        <p className="whitespace-pre-wrap text-[0.8125rem] leading-[1.75] text-foreground/90">
          {source.excerpt}
        </p>
      </article>
      <div className="sticky bottom-0 flex items-center justify-end gap-2 border-t border-border bg-background/95 px-4 py-2.5 backdrop-blur">
        <Button
          variant="outline"
          size="sm"
          className="rounded-full"
          onClick={() =>
            onPrompt(`Summarize the key claims from ${source.url} and separate facts from interpretation.`)
          }
        >
          Summarize claims
        </Button>
        <Button
          size="sm"
          className="rounded-full"
          onClick={() =>
            onPrompt(`Check the most important claim from ${source.url} against scholarly sources.`)
          }
        >
          Verify against research
        </Button>
      </div>
    </div>
  );
}

function ClaimWorkspace({
  claim,
  onPrompt,
}: {
  claim: ClaimArtifact;
  onPrompt: (prompt: string) => void;
}) {
  const verdictStyle = {
    supported: "bg-moss-surface/15 text-moss",
    contradicted: "bg-destructive/10 text-destructive",
    mixed: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
    insufficient: "bg-secondary text-muted-foreground",
  }[claim.verdict];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-background p-4 sm:p-5">
      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center justify-between gap-3">
          <span className={cn("rounded-full px-2.5 py-1 font-mono text-[0.625rem] uppercase tracking-[0.12em]", verdictStyle)}>
            {claim.verdict}
          </span>
          <span className="font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
            {claim.confidence} confidence
          </span>
        </div>
        <h3 className="mt-4 font-serif text-xl leading-snug">{claim.claim}</h3>
        {claim.rationale ? (
          <p className="mt-3 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {claim.rationale}
          </p>
        ) : null}
      </div>
      <div className="mt-4 space-y-2">
        <p className="px-1 font-mono text-[0.59375rem] uppercase tracking-[0.16em] text-muted-foreground">
          Evidence trail · {claim.evidence.length} source{claim.evidence.length === 1 ? "" : "s"}
        </p>
        {claim.evidence.length ? (
          claim.evidence.map((item, index) => (
            <button
              key={`${item.work_id}-${index}`}
              type="button"
              onClick={() => onPrompt(`Open ${item.work_id} and show the passage behind this evidence assessment.`)}
              className="w-full cursor-pointer rounded-2xl border border-border bg-card p-3.5 text-left transition-colors hover:border-moss/35 hover:bg-accent/30"
            >
              <span className="flex items-center justify-between gap-2">
                <span className="font-mono text-[0.625rem] text-moss">{item.work_id}</span>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.1em]",
                    item.stance === "supports"
                      ? "bg-moss-surface/15 text-moss"
                      : item.stance === "contradicts"
                        ? "bg-destructive/10 text-destructive"
                        : "bg-secondary text-muted-foreground",
                  )}
                >
                  {item.stance}
                </span>
              </span>
              <p className="mt-1.5 text-[0.78125rem] font-medium leading-snug">
                {item.title}
              </p>
              <p className="mt-1 text-[0.71875rem] leading-relaxed text-muted-foreground">
                {item.reason}
              </p>
            </button>
          ))
        ) : (
          <div className="rounded-2xl border border-dashed border-border p-6 text-center">
            <AlertTriangle className="mx-auto size-4 text-muted-foreground" />
            <p className="mt-2 text-[0.75rem] text-muted-foreground">
              No source clearly settled this claim.
            </p>
          </div>
        )}
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          className="rounded-full"
          onClick={() => onPrompt(`Find stronger evidence that could challenge this claim: "${claim.claim}"`)}
        >
          Stress-test
        </Button>
        <Button
          size="sm"
          className="rounded-full"
          onClick={() => onPrompt(`Turn this claim audit into a concise, citable paragraph with appropriate uncertainty: "${claim.claim}"`)}
        >
          Draft cited paragraph
        </Button>
      </div>
    </div>
  );
}

export default function ResearchArtifactWorkspace({
  artifacts,
  activeId,
  runId,
  onSelect,
  onClose,
  onDiscuss,
  onPrompt,
}: {
  artifacts: ResearchArtifact[];
  activeId: string;
  runId: RunRef;
  onSelect: (id: string) => void;
  onClose: () => void;
  onDiscuss: (selection: ChatSelection) => void;
  onPrompt: (prompt: string) => void;
}) {
  const active =
    artifacts.find((artifact) => artifact.id === activeId) ?? artifacts.at(-1);
  if (!active) return null;

  return (
    <aside
      aria-label="Research workspace"
      className="fixed inset-x-0 bottom-0 top-12 z-40 flex min-h-0 flex-col overflow-hidden border-l border-border bg-card lg:static lg:z-auto lg:w-[42%] lg:min-w-[26rem] lg:max-w-[48rem] lg:shrink-0 lg:rounded-tr-[1.5rem] 2xl:w-[46%] 2xl:max-w-[58rem]"
    >
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border bg-card px-2 py-2">
        <div
          role="tablist"
          aria-label="Open research artifacts"
          className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
        >
          {artifacts.map((artifact) => {
            const selected = artifact.id === active.id;
            const Icon =
              artifact.kind === "paper"
                ? FileText
                : artifact.kind === "table"
                  ? Table2
                  : artifact.kind === "source"
                    ? Globe2
                    : ShieldCheck;
            return (
              <button
                key={artifact.id}
                type="button"
                role="tab"
                aria-selected={selected}
                title={artifact.title}
                onClick={() => onSelect(artifact.id)}
                onKeyDown={(event) => {
                  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
                    return;
                  }
                  event.preventDefault();
                  const buttons = Array.from(
                    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
                      '[role="tab"]',
                    ) ?? [],
                  );
                  const current = buttons.indexOf(event.currentTarget);
                  const target =
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? buttons.length - 1
                        : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) %
                          buttons.length;
                  const next = buttons[target];
                  if (!next) return;
                  next.focus();
                  onSelect(artifacts[target].id);
                }}
                className={cn(
                  "flex h-8 max-w-[14rem] shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-[0.71875rem] font-medium transition-colors",
                  selected
                    ? "border-moss/30 bg-accent text-foreground shadow-sm"
                    : "border-transparent text-muted-foreground hover:border-border hover:bg-secondary hover:text-foreground",
                )}
              >
                <Icon className="size-3.5 shrink-0" />
                <span className="truncate">{artifact.title}</span>
              </button>
            );
          })}
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 shrink-0 rounded-full text-muted-foreground"
          onClick={onClose}
          aria-label="Close research workspace"
        >
          <X className="size-4" />
        </Button>
      </div>

      <div
        role="tabpanel"
        aria-label={active.title}
        className="flex min-h-0 flex-1 flex-col"
      >
        {active.kind === "paper" ? (
          <PaperPanel
            paper={active.paper}
            onClose={onClose}
            showClose={false}
            onDiscuss={onDiscuss}
          />
        ) : active.kind === "table" ? (
          <TableWorkspace table={active.table} runId={runId} onPrompt={onPrompt} />
        ) : active.kind === "source" ? (
          <SourceWorkspace source={active.source} onPrompt={onPrompt} />
        ) : (
          <ClaimWorkspace claim={active.claim} onPrompt={onPrompt} />
        )}
      </div>
    </aside>
  );
}
