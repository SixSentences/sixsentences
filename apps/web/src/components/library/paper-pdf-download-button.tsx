"use client";

import { useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import { Download, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { fetchDocumentBytes } from "@/lib/api";
import { downloadPdfBytes, paperPdfFilename } from "@/lib/paper-download";
import { cn } from "@/lib/utils";

export function PaperPdfDownloadButton({
  documentId,
  title,
  isGerman,
  className,
}: {
  documentId: number;
  title: string | null;
  isGerman: boolean;
  className?: string;
}) {
  const pendingRef = useRef(false);
  const label = isGerman ? "PDF herunterladen" : "Download PDF";
  const download = useMutation({
    mutationFn: async () => {
      const bytes = await fetchDocumentBytes(documentId);
      downloadPdfBytes(bytes, paperPdfFilename(title));
    },
    onSuccess: () => {
      toast.success(isGerman ? "PDF-Download gestartet." : "PDF download started.");
    },
    onError: () => {
      toast.error(
        isGerman
          ? "Die PDF konnte nicht heruntergeladen werden. Bitte versuche es erneut."
          : "The PDF could not be downloaded. Please try again.",
      );
    },
    onSettled: () => {
      pendingRef.current = false;
    },
  });

  const startDownload = () => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    download.mutate();
  };

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={cn("rounded-full text-[0.75rem]", className)}
      disabled={download.isPending}
      aria-label={`${label}: ${title || (isGerman ? "Paper" : "paper")}`}
      aria-busy={download.isPending}
      onClick={startDownload}
    >
      {download.isPending ? (
        <Loader2 className="size-3 animate-spin" aria-hidden="true" />
      ) : (
        <Download className="size-3" aria-hidden="true" />
      )}
      {download.isPending
        ? isGerman ? "Wird geladen …" : "Downloading …"
        : label}
    </Button>
  );
}
