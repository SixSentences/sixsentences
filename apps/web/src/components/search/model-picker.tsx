"use client";

import { useState } from "react";
import { Check, ChevronDown, Cpu } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useModels } from "@/hooks/queries";
import { useAuth } from "@/lib/auth";
import { privateModelOptions, resolvePrivateModelId } from "@/lib/private-model-selection";
import { cn } from "@/lib/utils";

type ModelPickerProps = {
  value: string;
  onChange: (id: string) => void;
  /** Icon-first trigger for tight spots (the chat composer). */
  compact?: boolean;
  /** Larger trigger for the primary search composer. */
  large?: boolean;
};

/** The model menu renders only capabilities declared by the connected API. */
export default function ModelPicker({
  value,
  onChange,
  compact = false,
  large = false,
}: ModelPickerProps) {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const { data: catalog } = useModels();
  const models = privateModelOptions(catalog);
  const [menuOpen, setMenuOpen] = useState(false);
  const [tooltipOpen, setTooltipOpen] = useState(false);
  if (models.length === 0) return null;

  const selectedId = resolvePrivateModelId(value, catalog);
  const selected = models.find((m) => m.id === selectedId);
  if (!selected) return null;

  return (
    <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
      <Tooltip
        open={!menuOpen && tooltipOpen}
        onOpenChange={setTooltipOpen}
      >
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(
                "group/model inline-flex cursor-pointer items-center rounded-full font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground data-[state=open]:bg-accent data-[state=open]:text-moss",
                large
                  ? "h-10 gap-2 px-3 text-[0.875rem]"
                  : "h-8 gap-1.5 px-2.5 text-[0.78125rem]",
              )}
              aria-label={isGerman ? "KI-Modell auswählen" : "Choose the AI model"}
            >
              <span
                className={cn(
                  "grid place-items-center rounded-md bg-accent text-moss transition-colors group-hover/model:bg-moss-surface group-hover/model:text-ivory",
                  large ? "size-6" : "size-5",
                )}
              >
                <Cpu className={large ? "size-3.5" : "size-3"} />
              </span>
              {!compact && (
                <span className="max-w-[8.75rem] truncate">{selected.label}</span>
              )}
              <ChevronDown
                className={cn(
                  "text-muted-foreground/50 transition-transform group-data-[state=open]/model:rotate-180",
                  large ? "size-3.5" : "size-3",
                )}
              />
            </button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="top">
          {compact
            ? `${isGerman ? "Modell" : "Model"}: ${selected.label}`
            : isGerman
              ? "Welches KI-Modell antwortet"
              : "Which AI model answers"}
        </TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-[20rem] p-1.5">
        <DropdownMenuLabel className="px-2 pb-2 pt-1">
          <span className="block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            {isGerman ? "KI-Modell" : "AI model"}
          </span>
        </DropdownMenuLabel>
        <div className="max-h-[22rem] overflow-y-auto">
          {models.map((model) => (
            <DropdownMenuItem
              key={model.id}
              onSelect={() => onChange(model.id)}
              className="group/item items-start rounded-lg px-2.5 py-2 text-foreground data-[highlighted]:bg-accent data-[highlighted]:text-foreground data-disabled:opacity-60"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[0.8125rem] font-medium">{model.label}</p>
                <p className="mt-0.5 line-clamp-2 text-[0.71875rem] leading-snug !text-muted-foreground group-data-[highlighted]/item:!text-foreground/70">
                  {isGerman ? model.tagline_de : model.tagline}
                </p>
              </div>
              {model.id === selected.id ? (
                <Check className="mt-0.5 size-4 shrink-0 text-moss" />
              ) : null}
            </DropdownMenuItem>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
