"use client";

import { MessageSquareText, PanelRight } from "lucide-react";

import { cn } from "@/lib/utils";

export type MobileWorkspacePane = "agent" | "workspace";

export function MobileWorkspaceSwitch({
  value,
  onChange,
  agentLabel = "Agent",
  workspaceLabel = "Workspace",
}: {
  value: MobileWorkspacePane;
  onChange: (value: MobileWorkspacePane) => void;
  agentLabel?: string;
  workspaceLabel?: string;
}) {
  return (
    <div
      data-workspace-mobile-switch
      className="shrink-0 border-b border-border bg-background px-3 py-2"
    >
      <div className="grid grid-cols-2 rounded-full bg-secondary/70 p-1">
        <button
          type="button"
          onClick={() => onChange("agent")}
          aria-pressed={value === "agent"}
          className={cn(
            "flex h-9 cursor-pointer items-center justify-center gap-2 rounded-full text-[0.75rem] font-medium transition-colors",
            value === "agent"
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground",
          )}
        >
          <MessageSquareText className="size-3.5" />
          {agentLabel}
        </button>
        <button
          type="button"
          onClick={() => onChange("workspace")}
          aria-pressed={value === "workspace"}
          className={cn(
            "flex h-9 cursor-pointer items-center justify-center gap-2 rounded-full text-[0.75rem] font-medium transition-colors",
            value === "workspace"
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground",
          )}
        >
          <PanelRight className="size-3.5" />
          {workspaceLabel}
        </button>
      </div>
    </div>
  );
}
