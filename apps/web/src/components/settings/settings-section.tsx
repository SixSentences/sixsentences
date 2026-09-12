"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function SettingsSection({
  title,
  description,
  icon,
  children,
  className,
}: {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-xl border border-border p-4", className)}>
      <div className="mb-3">
        <h3 className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          {icon}
          {title}
        </h3>
        {description ? (
          <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

export function SecretReveal({
  secret,
  note,
}: {
  secret: string;
  note: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1_600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <div className="rounded-xl border border-moss/40 bg-accent/60 p-3">
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 break-all font-mono text-[0.75rem]">
          {secret}
        </code>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-8 shrink-0 rounded-full"
          aria-label="Copy secret"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(secret);
              setCopied(true);
            } catch {
              setCopied(false);
            }
          }}
        >
          {copied ? (
            <Check className="size-3.5 text-moss" />
          ) : (
            <Copy className="size-3.5" />
          )}
        </Button>
      </div>
      <p className="mt-1.5 text-[0.71875rem] text-muted-foreground">{note}</p>
    </div>
  );
}
