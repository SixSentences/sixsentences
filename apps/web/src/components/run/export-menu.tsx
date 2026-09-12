"use client";

import { useState } from "react";
import { Download, Library } from "lucide-react";
import { toast } from "sonner";

import ZoteroDialog from "@/components/run/zotero-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { track } from "@/lib/analytics";
import { downloadExport, downloadPrismaDiagram, downloadRunAsset } from "@/lib/api";
import type { ExportFormat } from "@/lib/types";

const FORMATS: { format: ExportFormat; label: string; hint: string }[] = [
  { format: "bibtex", label: "BibTeX", hint: ".bib" },
  { format: "ris", label: "RIS", hint: ".ris" },
  { format: "csl", label: "CSL-JSON", hint: ".json" },
];

/** Citation exports + Zotero push. Every entry keeps its OpenAlex id. */
export default function ExportMenu({ runId }: { runId: number }) {
  const [includedOnly, setIncludedOnly] = useState(true);
  const [zoteroOpen, setZoteroOpen] = useState(false);

  async function handleExport(format: ExportFormat) {
    try {
      await downloadExport(runId, format, includedOnly);
      track("run_exported", { kind: format, included_only: includedOnly });
      toast.success(`Export ready. Check your downloads.`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Export failed.");
    }
  }

  async function handleDiagram(format: "svg" | "png") {
    try {
      await downloadPrismaDiagram(runId, format);
      track("run_exported", { kind: `prisma.${format}` });
      toast.success("Diagram ready. Check your downloads.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Export failed.");
    }
  }

  async function handleAsset(path: string, fallback: string) {
    try {
      await downloadRunAsset(runId, path, fallback);
      track("run_exported", { kind: fallback });
      toast.success("Export ready. Check your downloads.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Export failed.");
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-8 rounded-full text-[0.78125rem]">
            <Download className="size-3.5" />
            Export
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-[14.375rem]">
          <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            Citation file
          </DropdownMenuLabel>
          {FORMATS.map(({ format, label, hint }) => (
            <DropdownMenuItem key={format} onSelect={() => void handleExport(format)}>
              {label}
              <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">{hint}</span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuCheckboxItem
            checked={includedOnly}
            onCheckedChange={(checked) => setIncludedOnly(checked === true)}
            onSelect={(event) => event.preventDefault()}
          >
            Included works only
          </DropdownMenuCheckboxItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            PRISMA 2020 flow
          </DropdownMenuLabel>
          <DropdownMenuItem onSelect={() => void handleDiagram("svg")}>
            Diagram
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.svg</span>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void handleDiagram("png")}>
            Diagram
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.png</span>
          </DropdownMenuItem>
          <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            For Word
          </DropdownMenuLabel>
          <DropdownMenuItem
            onSelect={() =>
              void handleAsset(
                "bibliography?style=apa&format=docx",
                "references-apa.docx",
              )
            }
          >
            References, APA 7
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.docx</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() =>
              void handleAsset(
                "bibliography?style=ieee&format=docx",
                "references-ieee.docx",
              )
            }
          >
            References, IEEE
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.docx</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => void handleAsset("methods.docx", "methods.docx")}
          >
            Methods paragraph
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.docx</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => void handleAsset("extraction.docx", "evidence-table.docx")}
          >
            Evidence table
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.docx</span>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            Submission compliance
          </DropdownMenuLabel>
          <DropdownMenuItem
            onSelect={() =>
              void handleAsset("prisma-checklist?format=docx", "prisma-checklist.docx")
            }
          >
            PRISMA 2020 checklist
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.docx</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => void handleAsset("search-appendix", "search-appendix.txt")}
          >
            Search appendix (PRISMA-S)
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.txt</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() =>
              void handleAsset("preregistration?format=docx", "preregistration.docx")
            }
          >
            Preregistration
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.docx</span>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            For the paper
          </DropdownMenuLabel>
          <DropdownMenuItem
            onSelect={() => void handleAsset("extraction.csv", "evidence-table.csv")}
          >
            Evidence table
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.csv</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => void handleAsset("extraction.tex", "evidence-table.tex")}
          >
            Evidence table
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.tex</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() =>
              void handleAsset("bundle.zip", "reproducibility-bundle.zip")
            }
          >
            Reproducibility bundle
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">.zip</span>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setZoteroOpen(true)}>
            <Library className="size-4" />
            Reference manager…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <ZoteroDialog
        runId={runId}
        open={zoteroOpen}
        onOpenChange={setZoteroOpen}
        defaultIncludedOnly={includedOnly}
      />
    </>
  );
}
