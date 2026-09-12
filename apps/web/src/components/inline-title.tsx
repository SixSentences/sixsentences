"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Manuscript-style in-place title editing: click, type, blur saves.
 * Enter commits, Escape reverts. Commits only trimmed, changed, non-empty values.
 */
export function InlineTitle({
  value,
  onCommit,
  ariaLabel,
  placeholder,
  className,
  maxLength = 240,
}: {
  value: string;
  onCommit: (next: string) => void;
  ariaLabel: string;
  placeholder?: string;
  className?: string;
  maxLength?: number;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    setDraft(value);
  }, [value]);

  const commit = () => {
    const next = draft.trim();
    if (!next || next === value) {
      setDraft(value);
      return;
    }
    onCommit(next);
  };

  return (
    <input
      value={draft}
      maxLength={maxLength}
      aria-label={ariaLabel}
      placeholder={placeholder}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") {
          setDraft(value);
          event.currentTarget.blur();
        }
      }}
      className={cn(
        "w-full min-w-0 bg-transparent text-[0.8125rem] font-medium text-foreground outline-none placeholder:text-muted-foreground/50",
        className,
      )}
    />
  );
}
