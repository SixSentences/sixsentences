"use client";

import { useQuery } from "@tanstack/react-query";
import { API_URL } from "@/lib/api";

export type SignupControlState = {
  enabled: boolean;
  release_approved: boolean;
  override_enabled: boolean | null;
  revision: string;
  updated_at: string | null;
  updated_by: string | null;
  history: { enabled: boolean; at: string; by: string | null }[];
};

export async function fetchSignupStatus(): Promise<{ enabled: boolean }> {
  const response = await fetch(`${API_URL}/auth/signup-status`, {
    cache: "no-store",
    credentials: "omit",
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) throw new Error("Registration status is unavailable.");
  const payload: unknown = await response.json();
  if (!payload || typeof payload !== "object" || !("enabled" in payload)
      || typeof payload.enabled !== "boolean") {
    throw new Error("Registration status is unavailable.");
  }
  return { enabled: payload.enabled };
}

/** A failed or malformed status response never leaves a stale signup form open. */
export function useSignupStatus() {
  const query = useQuery({
    queryKey: ["public-signup-status"],
    queryFn: fetchSignupStatus,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchInterval: 5_000,
    refetchOnWindowFocus: "always",
    refetchOnReconnect: "always",
  });
  return { ...query, enabled: query.isSuccess && query.data.enabled === true };
}
