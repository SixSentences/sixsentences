import type { ChatModelCatalog, ChatModelOption } from "./types";

/** Only the server's explicitly private catalog may populate workspace choices. */
export function privateModelOptions(
  catalog: ChatModelCatalog | null | undefined,
): ChatModelOption[] {
  if (catalog?.routing_mode !== "gemini_private" || catalog.content_scope !== "private") {
    return [];
  }
  return catalog.models.filter(
    (model) => model.provider === "gemini" && !model.locked,
  );
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
