import { cn } from "@/lib/utils";

/** A calm CSS-only backdrop for the focused live-session surface. */
export default function HeroField({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("hero-field overflow-hidden bg-pine", className)}
    >
      <span className="hero-field__glow hero-field__glow--one" />
      <span className="hero-field__glow hero-field__glow--two" />
      <span className="hero-field__grid" />
      <span className="hero-field__grain grain" />
    </div>
  );
}
