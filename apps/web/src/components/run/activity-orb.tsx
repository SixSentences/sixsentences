import { cn } from "@/lib/utils";

/** Decorative CSS-only activity indicator with a reduced-motion fallback. */
export default function ActivityOrb({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("activity-orb relative block", className)}
    >
      <span className="activity-orb__ring" />
      <span className="activity-orb__ring activity-orb__ring--offset" />
      <span className="activity-orb__core" />
    </span>
  );
}
