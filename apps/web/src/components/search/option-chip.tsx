"use client";

import { Lock } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

type OptionChipProps = {
  label: string;
  hint: string;
  active: boolean;
  locked?: boolean;
  lockedHint?: string;
  onToggle: () => void;
  icon?: React.ReactNode;
};

/** Pill toggle for a run option with optional server-availability context. */
export default function OptionChip({
  label,
  hint,
  active,
  locked = false,
  lockedHint,
  onToggle,
  icon,
}: OptionChipProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onToggle}
          aria-pressed={active}
          className={cn(
            "inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-[0.78125rem] font-medium transition-all duration-200",
            active && !locked
              ? "border-moss/50 bg-accent text-accent-foreground shadow-[inset_0_0_0_1px_var(--moss)]"
              : "border-border bg-background text-muted-foreground hover:border-input hover:text-foreground",
            locked && "opacity-70",
          )}
        >
          {icon}
          {label}
          {locked && <Lock className="size-3 text-muted-foreground" />}
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-[15rem] text-center">
        {locked ? (lockedHint ?? hint) : hint}
      </TooltipContent>
    </Tooltip>
  );
}
