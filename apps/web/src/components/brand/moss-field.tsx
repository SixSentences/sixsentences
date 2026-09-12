import { cn } from "@/lib/utils";

/**
 * Lightweight brand backdrop built entirely from CSS gradients.
 *
 * It intentionally has no canvas, WebGL, image or animation-file dependency,
 * so authentication and empty-state screens also work in constrained browsers.
 */
export default function MossField({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("moss-field overflow-hidden bg-pine", className)}
    >
      <span className="moss-field__layer moss-field__layer--one" />
      <span className="moss-field__layer moss-field__layer--two" />
      <span className="moss-field__layer moss-field__layer--three" />
      <span className="moss-field__grain grain" />
    </div>
  );
}
