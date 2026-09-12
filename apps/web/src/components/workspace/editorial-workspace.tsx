import { useId, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type EditorialMetric = {
  label: ReactNode;
  value: ReactNode;
  className?: string;
};

/** A compact, rule-led metric row that keeps the underlying work in focus. */
export function EditorialMetricStrip({
  items,
  className,
}: {
  items: EditorialMetric[];
  className?: string;
}) {
  return (
    <dl
      className={cn(
        "grid grid-cols-2 divide-x divide-border/70 border-y border-border/70 sm:grid-cols-3",
        className,
      )}
    >
      {items.map((item, index) => (
        <div
          key={index}
          className={cn("flex min-w-0 flex-col px-4 py-4 first:pl-0 sm:px-5", item.className)}
        >
          <dt className="order-2 mt-1 text-xs leading-relaxed text-muted-foreground">
            {item.label}
          </dt>
          <dd className="order-1 truncate font-mono text-xl text-foreground">
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** A small editorial label for record type or context; never a decorative icon bubble. */
export function EditorialKicker({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground",
        className,
      )}
    >
      <span aria-hidden="true" className="h-px w-5 shrink-0 bg-moss/65" />
      {children}
    </span>
  );
}

/**
 * A quiet empty state led by language and hierarchy instead of a large icon.
 * Actions remain explicit children so their native button/link semantics are preserved.
 */
export function EditorialEmptyState({
  eyebrow,
  title,
  description,
  note,
  children,
  align = "center",
  framed = true,
  className,
  titleClassName,
}: {
  eyebrow: ReactNode;
  title: ReactNode;
  description: ReactNode;
  note?: ReactNode;
  children?: ReactNode;
  align?: "start" | "center";
  framed?: boolean;
  className?: string;
  titleClassName?: string;
}) {
  const headingId = useId();
  const centered = align === "center";

  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "flex flex-col",
        centered ? "items-center text-center" : "items-start text-left",
        framed && "rounded-2xl border border-border/70 bg-card/35 px-6 py-12 sm:px-8 sm:py-16",
        className,
      )}
    >
      <span aria-hidden="true" className="h-px w-10 bg-moss/65" />
      <p className="mt-3 font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-moss">
        {eyebrow}
      </p>
      <h2
        id={headingId}
        className={cn("mt-3 font-serif text-2xl leading-tight text-foreground", titleClassName)}
      >
        {title}
      </h2>
      <p className="mt-3 max-w-lg text-[0.8125rem] leading-relaxed text-muted-foreground">
        {description}
      </p>
      {children && (
        <div className={cn("mt-6 flex flex-wrap gap-2", centered && "justify-center")}>
          {children}
        </div>
      )}
      {note && (
        <p className="mt-5 max-w-lg text-[0.6875rem] leading-relaxed text-muted-foreground">
          {note}
        </p>
      )}
    </section>
  );
}
