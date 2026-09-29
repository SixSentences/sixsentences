import type { ChatModelCatalog, ChatModelOption } from "./types";

/** Use only the connected server's explicitly declared workspace catalog. */
export function privateModelOptions(
  catalog: ChatModelCatalog | null | undefined,
): ChatModelOption[] {
  const privateRoute = catalog?.content_scope === "private" && !!catalog.routing_mode?.trim();
  const deploymentRoute = catalog?.content_scope === "deployment_controlled"
    && catalog.routing_mode === "configured";
  // Community operators own their provider/contract policy. This declaration
  // enables selection, not a claim of hosted approval, EU residency or ZDR.
  if (!catalog || (!privateRoute && !deploymentRoute)) {
    return [];
  }
  return catalog.models.filter((model) => !model.locked);
}

/** Resolve removed or unavailable preferences to the current server default. */
export function resolvePrivateModelId(
  requested: string | null | undefined,
  catalog: ChatModelCatalog | null | undefined,
): string {
  const available = privateModelOptions(catalog);
  const selected = available.find((model) => model.id === requested);
  if (selected) return selected.id;
  return (
    available.find((model) => model.id === catalog?.default_id)?.id ??
    available.find((model) => model.default)?.id ??
    available[0]?.id ??
    // This alias is resolved by the server; never send an unvalidated old ID
    // while the current catalog is loading or temporarily unavailable.
    "auto"
  );
}
