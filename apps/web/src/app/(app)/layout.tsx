"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import AppShell from "@/components/shell/app-shell";
import SixMark from "@/components/brand/six-mark";
import { Button } from "@/components/ui/button";
import { LegalReacceptance, PrivacyUpdateNotice } from "@/components/legal-reacceptance";
import { useAuth } from "@/lib/auth";
import { ProjectProvider } from "@/lib/project-context";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { status, isResolving, me, refresh, signOut } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "signed-out") {
      const destination = `${window.location.pathname}${window.location.search}`;
      router.replace(`/login?next=${encodeURIComponent(destination)}`);
    }
  }, [status, router]);

  const connectionNotice = status === "unavailable" ? (
    <section role="status" className="max-w-lg space-y-3 rounded-2xl border border-border bg-background p-5 text-sm shadow-sm">
      <p>{me?.language === "de"
        ? "Deine Anmeldung konnte gerade nicht geprüft werden. Versuche es erneut, ohne dich neu anzumelden."
        : "We could not check your sign-in right now. Try again without signing in again."}</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={isResolving} onClick={() => void refresh()}>
          {isResolving ? (me?.language === "de" ? "Wird verbunden…" : "Reconnecting…") : (me?.language === "de" ? "Erneut versuchen" : "Try again")}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={signOut}>
          {me?.language === "de" ? "Abmelden" : "Sign out"}
        </Button>
      </div>
    </section>
  ) : null;

  if (status === "unavailable" && !me) {
    return <main className="grid min-h-dvh place-items-center bg-background p-6">{connectionNotice}</main>;
  }
  if (status !== "signed-in" && !(status === "unavailable" && me)) {
    return (
      <div className="grid min-h-dvh place-items-center bg-background">
        <SixMark animated className="h-10 w-10 text-foreground/80" title="Loading" />
      </div>
    );
  }

  if (me?.legal_reaccept_required) {
    return (
      <>
        {connectionNotice && <div className="fixed inset-x-4 top-4 z-50 mx-auto w-fit">{connectionNotice}</div>}
        <LegalReacceptance
          me={me}
          onAccepted={refresh}
          onSignOut={signOut}
        />
      </>
    );
  }

  return (
    <ProjectProvider>
      <AppShell>{children}</AppShell>
      {connectionNotice && <div className="fixed inset-x-4 top-4 z-50 mx-auto w-fit">{connectionNotice}</div>}
      {me && <PrivacyUpdateNotice me={me} onPresented={refresh} />}
    </ProjectProvider>
  );
}
