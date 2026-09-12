"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { retryTransientApiQuery, transientApiRetryDelay } from "@/lib/api";

function ThemeColorSync() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    meta?.setAttribute("content", resolvedTheme === "dark" ? "#101211" : "#f4f1ea");
  }, [resolvedTheme]);

  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: retryTransientApiQuery,
            retryDelay: transientApiRetryDelay,
            refetchOnWindowFocus: false,
            staleTime: 5_000,
          },
        },
      }),
  );

  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="light"
      enableSystem
      storageKey="sixsentences-theme"
      disableTransitionOnChange
    >
      <ThemeColorSync />
      <QueryClientProvider client={queryClient}>
        <AuthProvider queryClient={queryClient}>
          <TooltipProvider delayDuration={250}>{children}</TooltipProvider>
          <Toaster position="bottom-right" />
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
