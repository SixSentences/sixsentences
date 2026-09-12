"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useMethods } from "@/hooks/queries";

/**
 * The PRISMA-S methods paragraph — citable text for a paper's methods
 * section, generated from the run's real numbers.
 */
export default function MethodsPanel({ runId }: { runId: number }) {
  const { data, isLoading } = useMethods(runId);
  const [copied, setCopied] = useState(false);

  if (isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  if (!data) return null;

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-[0.78125rem] text-muted-foreground">
          Ready for your paper&apos;s methods section. Every number traces back
          to the audit log.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="h-8 rounded-full text-[0.78125rem]"
          onClick={async () => {
            await navigator.clipboard.writeText(data.methods);
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
          }}
        >
          {copied ? <Check className="size-3.5 text-moss" /> : <Copy className="size-3.5" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <div className="rounded-xl border border-border bg-secondary/40 px-5 py-4">
        <p className="whitespace-pre-wrap text-[0.84375rem] leading-relaxed">{data.methods}</p>
      </div>
    </div>
  );
}
