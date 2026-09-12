import { cn } from "@/lib/utils";
import { publicLegalUrl } from "@/lib/public-links";

const LEGAL_LINKS = [
  { href: publicLegalUrl("imprint"), label: "Imprint" },
  { href: publicLegalUrl("privacy"), label: "Privacy" },
  { href: publicLegalUrl("terms"), label: "Terms" },
  {
    href: publicLegalUrl("report-illegal-content"),
    label: "Report illegal content",
  },
].filter((link): link is { href: string; label: string } => link.href !== null);

export default function PublicLegalFooter({
  className,
  inverse = false,
}: {
  className?: string;
  inverse?: boolean;
}) {
  if (LEGAL_LINKS.length === 0) return null;

  return (
    <footer
      aria-label="Legal information"
      className={cn(
        "flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-center font-mono text-[0.5625rem] uppercase tracking-[0.12em]",
        inverse ? "text-ivory/55" : "text-muted-foreground",
        className,
      )}
    >
      {LEGAL_LINKS.map((link) => (
        <a
          key={link.href}
          href={link.href}
          target="_blank"
          rel="noreferrer"
          className={cn(
            "rounded-sm underline-offset-4 transition-colors hover:underline focus-visible:outline-none focus-visible:ring-2",
            inverse
              ? "hover:text-ivory focus-visible:ring-ivory/70"
              : "hover:text-foreground focus-visible:ring-ring",
          )}
        >
          {link.label}
        </a>
      ))}
    </footer>
  );
}
