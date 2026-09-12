import { BookOpenText, Globe2 } from "lucide-react";

import {
  librarySourceIdentity,
  type LibrarySourceIdentity,
} from "@/lib/library-source-identity";

const SOURCE_TONES = [
  "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  "border-sky-500/20 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  "border-violet-500/20 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  "border-rose-500/20 bg-rose-500/10 text-rose-700 dark:text-rose-300",
] as const;

function identityTone(identity: LibrarySourceIdentity): string {
  const seed = identity.hostname ?? identity.label;
  let hash = 0;
  for (const character of seed) hash = (hash * 31 + character.codePointAt(0)!) >>> 0;
  return SOURCE_TONES[hash % SOURCE_TONES.length];
}

export function SourceIdentityMark({
  kind,
  url,
  siteName,
  isGerman,
  className = "size-9 rounded-xl",
}: {
  kind: "paper" | "web";
  url: unknown;
  siteName?: unknown;
  isGerman: boolean;
  className?: string;
}) {
  const identity = librarySourceIdentity({
    url,
    siteName,
    fallbackLabel: kind === "paper" ? "Paper" : isGerman ? "Webquelle" : "Web source",
  });
  const accessibleLabel = identity.specific
    ? `${isGerman ? "Quelle" : "Source"}: ${identity.label}`
    : kind === "paper"
      ? isGerman ? "Paperquelle" : "Paper source"
      : isGerman ? "Webquelle" : "Web source";

  return (
    <span
      role="img"
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className={`grid shrink-0 place-items-center border font-mono text-[0.6875rem] font-semibold tracking-[0.04em] ${identityTone(identity)} ${className}`}
      data-source-identity={identity.specific ? "domain-monogram" : "generic"}
      data-source-hostname={identity.hostname ?? undefined}
    >
      {identity.specific ? identity.monogram : kind === "paper" ? (
        <BookOpenText className="size-4" aria-hidden="true" />
      ) : (
        <Globe2 className="size-4" aria-hidden="true" />
      )}
    </span>
  );
}
