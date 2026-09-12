"use client";

import Link from "next/link";
import { TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { userFacingErrorMessage } from "@/lib/user-facing-error";

/** Detail-query failure with an optional read-only retry and a way back. */
export function DetailError({
  title,
  error,
  fallback,
  backHref,
  backLabel,
  onRetry,
  retrying = false,
}: {
  title: string;
  error: unknown;
  fallback: string;
  backHref?: string;
  backLabel?: string;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  return (
    <div className="grid min-h-0 flex-1 place-items-center p-8 text-center">
      <div role="alert">
        <TriangleAlert className="mx-auto size-7 text-muted-foreground" />
        <h1 className="mt-4 font-display text-3xl text-foreground">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {error ? userFacingErrorMessage(error, fallback) : fallback}
        </p>
        {onRetry && (
          <Button variant="outline" size="sm" className="mt-5 mr-2 rounded-full" disabled={retrying} onClick={onRetry}>
            {retrying ? "Retrying…" : "Try again"}
          </Button>
        )}
        {backHref ? (
          <Button asChild variant="outline" size="sm" className="mt-5 rounded-full">
            <Link href={backHref}>{backLabel ?? "Back to the list"}</Link>
          </Button>
        ) : null}
      </div>
    </div>
  );
}
