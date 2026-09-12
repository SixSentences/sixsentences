"use client";

import { useCallback, useEffect, useState } from "react";

import { useModels } from "@/hooks/queries";
import { privateModelOptions, resolvePrivateModelId } from "@/lib/private-model-selection";

/** Keep workspace preferences usable when the allowed provider/catalog changes. */
export function usePrivateModelPreference(storageKey?: string) {
  const { data: catalog } = useModels();
  const [preference, setPreference] = useState<{
    key: string | undefined;
    requested: string;
    hydrated: boolean;
  }>({ key: storageKey, requested: "auto", hydrated: !storageKey });

  useEffect(() => {
    let requested = "auto";
    if (storageKey) {
      try {
        requested = window.localStorage.getItem(storageKey) || "auto";
      } catch {
        // Disabled browser storage must not prevent an AI request.
      }
    }
    setPreference({ key: storageKey, requested, hydrated: true });
  }, [storageKey]);

  const model = resolvePrivateModelId(
    preference.key === storageKey ? preference.requested : "auto",
    catalog,
  );

  useEffect(() => {
    if (
      !storageKey ||
      preference.key !== storageKey ||
      !preference.hydrated ||
      privateModelOptions(catalog).length === 0
    ) return;
    try {
      window.localStorage.setItem(storageKey, model);
    } catch {
      // The selected model remains valid even when preferences cannot persist.
    }
    if (preference.requested !== model) {
      setPreference({ key: storageKey, requested: model, hydrated: true });
    }
  }, [catalog, model, preference.hydrated, preference.key, preference.requested, storageKey]);

  const pickModel = useCallback((requested: string) => {
    setPreference({
      key: storageKey,
      requested: resolvePrivateModelId(requested, catalog),
      hydrated: true,
    });
  }, [catalog, storageKey]);

  return [model, pickModel] as const;
}
