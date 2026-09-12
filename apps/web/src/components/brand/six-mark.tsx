type SixMarkProps = {
  className?: string;
  /** Type-in entrance for the bars + infinitely blinking cursor. */
  animated?: boolean;
  title?: string;
};

/**
 * The SixSentences_ mark: six text-line bars and a terminal block cursor.
 * Filled rects only, so it stays crisp from favicon size up to hero size.
 * With `animated`, the lines type themselves in and the cursor blinks
 * (see .mark-animated rules in globals.css; disabled under reduced motion).
 */
export default function SixMark({ className, animated = false, title }: SixMarkProps) {
  return (
    <svg
      viewBox="0 0 120 120"
      xmlns="http://www.w3.org/2000/svg"
      className={`${animated ? "mark-animated " : ""}${className ?? ""}`}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      fill="currentColor"
    >
      {title ? <title>{title}</title> : null}
      <g>
        <rect className="mark-bar" x="18" y="15" width="84" height="10.5" rx="5.25" />
        <rect className="mark-bar" x="18" y="30.9" width="66.36" height="10.5" rx="5.25" />
        <rect className="mark-bar" x="18" y="46.8" width="78.12" height="10.5" rx="5.25" />
        <rect className="mark-bar" x="18" y="62.7" width="53.76" height="10.5" rx="5.25" />
        <rect className="mark-bar" x="18" y="78.6" width="72.24" height="10.5" rx="5.25" />
        <rect className="mark-bar" x="18" y="94.5" width="30.24" height="10.5" rx="5.25" />
      </g>
      <rect className="mark-cursor" x="55.24" y="94.5" width="15" height="10.5" rx="2.5" />
    </svg>
  );
}
