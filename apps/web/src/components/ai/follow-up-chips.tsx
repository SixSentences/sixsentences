"use client";

import { CornerDownRight } from "lucide-react";

import type { FollowUpSuggestion } from "@/lib/follow-ups";
import { cn } from "@/lib/utils";

export default function FollowUpChips({
  suggestions,
  onSelect,
  disabled = false,
  className,
}: {
  suggestions: FollowUpSuggestion[];
  onSelect: (prompt: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div
      className={cn("flex max-w-full flex-wrap items-center gap-1.5", className)}
      data-testid="follow-up-chips"
    >
      <span className="mr-0.5 inline-flex items-center gap-1 text-[0.625rem] text-muted-foreground/70">
        <CornerDownRight className="size-3" /> Try next
      </span>
      {suggestions.map((suggestion) => (
        <button
          key={suggestion.label}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(suggestion.prompt)}
          className="cursor-pointer rounded-full border border-border/80 bg-background/65 px-2.5 py-1 text-[0.6875rem] text-muted-foreground transition-colors hover:border-moss/40 hover:bg-accent/60 hover:text-moss disabled:cursor-not-allowed disabled:opacity-50"
        >
          {suggestion.label}
        </button>
      ))}
    </div>
  );
}
