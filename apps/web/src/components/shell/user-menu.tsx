"use client";

import {
  ChevronsUpDown,
  Map as MapIcon,
  KeyRound,
  Lightbulb,
  LogOut,
  Settings,
  Users,
} from "lucide-react";

import type { SettingsTab } from "@/components/settings/settings-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth";

export default function UserMenu({
  onOpenSettings,
}: {
  onOpenSettings: (tab?: SettingsTab) => void;
}) {
  const { me, signOut } = useAuth();
  if (!me) return null;

  const initial = me.email.slice(0, 1).toUpperCase();
  const isGerman = me.language === "de";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left outline-none transition-colors hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-ring">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-moss-surface to-pine font-mono text-[0.8125rem] text-ivory">
          {initial}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.8125rem] font-medium text-foreground">
            {me.email}
          </span>
          <span className="block truncate text-[0.6875rem] text-muted-foreground">
            {me.role}
          </span>
        </span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-[15.5rem]">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span className="truncate text-[0.8125rem]">{me.email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onOpenSettings("account")}>
          <Settings className="size-4" /> {isGerman ? "Einstellungen" : "Settings"}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onOpenSettings("api-keys")}>
          <KeyRound className="size-4" /> {isGerman ? "API-Schlüssel" : "API keys"}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onOpenSettings("team")}>
          <Users className="size-4" /> Team
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => window.dispatchEvent(new CustomEvent("six:start-tour"))}
        >
          <MapIcon className="size-4" /> {isGerman ? "Produkttour" : "Product tour"}
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a
            href="https://github.com/SixSentences/sixsentences/issues"
            target="_blank"
            rel="noopener noreferrer"
          >
            <Lightbulb className="size-4" /> {isGerman ? "Ideen auf GitHub" : "Ideas on GitHub"}
          </a>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={signOut}>
          <LogOut className="size-4" /> {isGerman ? "Abmelden" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
