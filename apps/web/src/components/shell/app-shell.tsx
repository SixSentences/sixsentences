"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { PanelLeft, Plus } from "lucide-react";
import { toast } from "sonner";

import Analytics from "@/components/analytics";
import OnboardingOverlay from "@/components/onboarding/onboarding-overlay";
import ProductTour from "@/components/tour/product-tour";
import SixMark from "@/components/brand/six-mark";
import SettingsDialog, { type SettingsTab } from "@/components/settings/settings-dialog";
import Sidebar from "@/components/shell/sidebar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import type { EntitlementEventDetail } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const SIDEBAR_KEY = "six_sidebar_open";

const SidebarUiContext = createContext({ open: true });

/** Whether the desktop sidebar is open, for page-level layout decisions. */
export function useSidebarUi() {
  return useContext(SidebarUiContext);
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { me } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  // The welcome overlay remains re-openable from the user menu. First-login
  // guidance is owned by ProductTour so both flows never compete at startup.
  const [showIntro, setShowIntro] = useState(false);
  const [settings, setSettings] = useState<{ open: boolean; tab: SettingsTab }>({
    open: false,
    tab: "account",
  });

  // restore the persisted sidebar preference
  useEffect(() => {
    const stored = window.localStorage.getItem(SIDEBAR_KEY);
    if (stored !== null) setSidebarOpen(stored === "1");
  }, []);

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((open) => {
      window.localStorage.setItem(SIDEBAR_KEY, open ? "0" : "1");
      return !open;
    });
  }, []);

  // The server remains authoritative for unavailable features and capacity.
  // The open-source client reports the reason without directing users to a
  // commercial flow that may not exist on a self-hosted deployment.
  useEffect(() => {
    const onEntitlement = (event: Event) => {
      const detail = (event as CustomEvent<EntitlementEventDetail>).detail;
      toast.error(
        detail?.message
          ?? "This action is currently unavailable. Check the server configuration or contact your workspace operator.",
      );
    };
    window.addEventListener("six:entitlement", onEntitlement);
    return () => window.removeEventListener("six:entitlement", onEntitlement);
  }, []);

  // "Show intro" from the user menu re-opens the welcome overlay; the
  // spotlight tour closes it, the two takeovers never stack
  useEffect(() => {
    const onShow = () => setShowIntro(true);
    const onTour = () => {
      setShowIntro(false);
      setMobileOpen(false);
    };
    window.addEventListener("six:show-onboarding", onShow);
    window.addEventListener("six:start-tour", onTour);
    return () => {
      window.removeEventListener("six:show-onboarding", onShow);
      window.removeEventListener("six:start-tour", onTour);
    };
  }, []);

  // keyboard: ⌘K new search, ⌘B sidebar
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        router.push("/");
      }
      if (event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, toggleSidebar]);

  const openSettings = useCallback(
    (tab: SettingsTab = "account") => setSettings({ open: true, tab }),
    [],
  );

  useEffect(() => {
    const onOpenSettings = (event: Event) => {
      const detail = (
        event as CustomEvent<{ tab?: SettingsTab; provider?: "zotero" | "citavi" }>
      ).detail;
      const tab = detail?.tab ?? "account";
      setMobileOpen(false);
      openSettings(tab);
    };
    window.addEventListener("six:open-settings", onOpenSettings);
    return () => window.removeEventListener("six:open-settings", onOpenSettings);
  }, [openSettings]);

  return (
    <SidebarUiContext.Provider value={{ open: sidebarOpen }}>
    <div data-app-shell className="flex h-dvh overflow-hidden bg-sidebar">
      <a
        href="#app-main-content"
        className="fixed left-3 top-3 z-[100] -translate-y-20 rounded-full bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg transition-transform focus:translate-y-0"
      >
        Skip to main content
      </a>
      {/* Desktop sidebar */}
      <aside
        aria-hidden={!sidebarOpen}
        inert={!sidebarOpen}
        className={cn(
          "hidden shrink-0 overflow-hidden transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] lg:block",
          sidebarOpen ? "w-[17.5rem]" : "w-0",
        )}
      >
        <Sidebar
          onToggle={toggleSidebar}
          onOpenSettings={openSettings}
          onNavigate={() => undefined}
        />
      </aside>

      {/* Mobile sidebar */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent
          side="left"
          className="w-[min(18.75rem,calc(100vw-1rem))] border-sidebar-border p-0"
        >
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Sidebar
            onToggle={() => setMobileOpen(false)}
            onOpenSettings={(tab) => {
              setMobileOpen(false);
              openSettings(tab);
            }}
            onNavigate={() => setMobileOpen(false)}
          />
        </SheetContent>
      </Sheet>

      {/* Main column */}
      <div className="relative flex min-w-0 flex-1 flex-col bg-background lg:my-2 lg:mr-2 lg:rounded-2xl lg:border lg:border-border/70 lg:shadow-[0_1px_12px_rgba(12,29,25,0.04)]">
        {/* Navigation remains reachable without covering page tools when the
            sidebar is collapsed or shown as a mobile sheet. */}
        <header
          className={cn(
            "flex min-h-12 shrink-0 items-center justify-between gap-x-3 border-b border-border/70 bg-background/95 px-2.5 py-1",
            sidebarOpen ? "lg:hidden" : "lg:flex",
          )}
        >
          <div className="flex shrink-0 items-center">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setMobileOpen(true)}
              className="size-10 shrink-0 text-muted-foreground hover:text-foreground lg:hidden"
              aria-label="Open navigation"
            >
              <PanelLeft className="size-4.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleSidebar}
              className="hidden size-10 shrink-0 text-muted-foreground hover:text-foreground lg:inline-flex"
              aria-label="Open sidebar"
            >
              <PanelLeft className="size-4.5" />
            </Button>
            <button
              type="button"
              onClick={() => router.push("/")}
              className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              aria-label="SixSentences home"
            >
              <SixMark className="size-5 shrink-0" title="SixSentences_" />
              <span className="hidden font-mono text-[0.625rem] tracking-[0.2em] sm:inline">
                SIXSENTENCES_
              </span>
            </button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => router.push("/")}
              className="size-8 shrink-0 text-muted-foreground hover:text-foreground"
              aria-label="New search"
            >
              <Plus className="size-4.5" />
            </Button>
          </div>
        </header>

        <main
          id="app-main-content"
          tabIndex={-1}
          className="flex min-h-0 flex-1 flex-col outline-none"
        >
          {children}
        </main>
      </div>

      <SettingsDialog
        open={settings.open}
        tab={settings.tab}
        onOpenChange={(open) => setSettings((s) => ({ ...s, open }))}
        onTabChange={(tab) => setSettings((s) => ({ ...s, tab }))}
      />
      <OnboardingOverlay
        open={showIntro}
        onClose={() => setShowIntro(false)}
        firstName={me?.first_name ?? ""}
      />
      <ProductTour />
      <Analytics />
    </div>
    </SidebarUiContext.Provider>
  );
}
