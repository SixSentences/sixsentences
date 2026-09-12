"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpen, DownloadCloud, ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDocuments } from "@/hooks/queries";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { DocumentEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

function openInReader(doc: DocumentEntry) {
  window.dispatchEvent(
    new CustomEvent("six:open-paper", {
      detail: {
        documentId: doc.id,
        title: doc.title ?? doc.work_id,
        highlights: [],
        legalBasis: doc.legal_basis,
        license: doc.license,
      },
    }),
  );
}

/** One-click open-access collection for a completed run's included works. */
function CollectButton({ runId }: { runId: number }) {
  const queryClient = useQueryClient();
  const collect = useMutation({
    mutationFn: () => api.collectPdfs(runId),
    onSuccess: () => {
      toast.success(
        "Collecting open-access PDFs in the background. Paywalled papers stay linked, never downloaded.",
        { duration: 8000 },
      );
      // the ledger fills up as the background task lands documents
      setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ["documents", runId] });
      }, 12_000);
    },
    onError: (error) => {
      if (!(error instanceof Error && error.message.includes("include"))) {
        toast.error(error instanceof Error ? error.message : "Collection failed.");
      }
    },
  });
  return (
    <Button
      variant="outline"
      size="sm"
      className="h-8 rounded-full text-[0.78125rem]"
      onClick={() => collect.mutate()}
      disabled={collect.isPending}
    >
      {collect.isPending ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <DownloadCloud className="size-3.5" />
      )}
      Save open-access PDFs
    </Button>
  );
}

/**
 * The run's library: uploaded papers and acquired full texts, readable in the
 * split-view reader. The ledger stays honest — "not retrieved" rows show why,
 * and every stored file records the legal basis it was fetched under.
 */
export default function DocumentsPanel({
  runId,
  canCollect = false,
}: {
  runId: number;
  /** Completed runs can collect open-access PDFs after the fact. */
  canCollect?: boolean;
}) {
  const { data: documents, isLoading } = useDocuments(runId);

  if (isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;

  if (!documents || documents.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border px-4 py-8 text-center">
        <p className="text-[0.8125rem] text-muted-foreground">
          No PDFs in this run&apos;s library yet. Attach one in the chat, or
          collect the open-access copies of the included works.
        </p>
        {canCollect && (
          <div className="mt-3 flex justify-center">
            <CollectButton runId={runId} />
          </div>
        )}
      </div>
    );
  }

  const retrieved = documents.filter((doc) => doc.status === "retrieved").length;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <p className="text-[0.78125rem] text-muted-foreground">
          <span className="font-medium text-foreground">{retrieved}</span> of{" "}
          {documents.length} full texts stored. Open-access sources and your own
          uploads only; every file records its legal basis.
        </p>
        {canCollect && (
          <span className="ml-auto">
            <CollectButton runId={runId} />
          </span>
        )}
      </div>
      <div className="overflow-hidden rounded-xl border border-border">
        <Table>
          <TableHeader>
            <TableRow className="bg-secondary/50 hover:bg-secondary/50">
              <TableHead>Paper</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Legal basis</TableHead>
              <TableHead className="text-right">Size</TableHead>
              <TableHead className="w-[6.5rem] text-right">Read</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {documents.map((doc) => (
              <TableRow key={doc.id}>
                <TableCell className="max-w-[22rem]">
                  <span className="block truncate text-[0.8125rem] font-medium">
                    {doc.title ?? doc.work_id}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 font-mono text-[0.65625rem] text-muted-foreground">
                    {doc.work_id}
                    {doc.url && (
                      <a href={doc.url} target="_blank" rel="noreferrer" aria-label="Open source URL">
                        <ExternalLink className="size-3 hover:text-moss" />
                      </a>
                    )}
                  </span>
                </TableCell>
                <TableCell>
                  <Badge
                    variant="outline"
                    className={cn(
                      "rounded-full text-[0.65625rem]",
                      doc.status === "retrieved"
                        ? "border-moss/30 bg-accent text-moss"
                        : "border-border bg-secondary text-muted-foreground",
                    )}
                  >
                    {doc.status === "retrieved" ? "retrieved" : "not retrieved"}
                  </Badge>
                </TableCell>
                <TableCell className="text-[0.78125rem] text-muted-foreground">
                  {doc.source ?? "—"}
                </TableCell>
                <TableCell className="text-[0.78125rem] text-muted-foreground">
                  {doc.legal_basis ? (
                    <span>
                      {doc.legal_basis.replaceAll("_", " ")}
                      {doc.license ? ` · ${doc.license}` : ""}
                    </span>
                  ) : (
                    <span title={doc.reason ?? undefined}>{doc.reason ?? "—"}</span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono text-[0.71875rem] tabular-nums text-muted-foreground">
                  {doc.byte_size > 0 ? formatBytes(doc.byte_size) : "—"}
                </TableCell>
                <TableCell className="text-right">
                  {doc.has_file && (doc.content_type ?? "").includes("pdf") ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 rounded-full px-2.5 text-[0.71875rem]"
                      onClick={() => openInReader(doc)}
                    >
                      <BookOpen className="size-3" />
                      Read
                    </Button>
                  ) : (
                    <span className="text-[0.71875rem] text-muted-foreground/60">—</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
