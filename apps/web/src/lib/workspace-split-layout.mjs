/**
 * Return whether both panes and the divider fit without violating their
 * minimum widths.
 *
 * @param {{
 *   containerWidth: number;
 *   primaryMinPx: number;
 *   secondaryMinPx: number;
 *   dividerPx: number;
 *   minPercent: number;
 *   maxPercent: number;
 * }} options
 */
export function isWorkspaceSplitFeasible({
  containerWidth,
  primaryMinPx,
  secondaryMinPx,
  dividerPx,
  minPercent,
  maxPercent,
}) {
  if (containerWidth <= dividerPx) return false;
  const minimum = Math.max(minPercent, (primaryMinPx / containerWidth) * 100);
  const maximum = Math.min(
    maxPercent,
    ((containerWidth - dividerPx - secondaryMinPx) / containerWidth) * 100,
  );
  return minimum <= maximum;
}

/**
 * Clamp a preferred split to the limits that are currently renderable.
 * The caller owns the preferred value; this function never mutates it, so a
 * temporary narrow container cannot overwrite the user's wider-layout choice.
 *
 * @param {number} candidate
 * @param {{
 *   containerWidth: number;
 *   primaryMinPx: number;
 *   secondaryMinPx: number;
 *   dividerPx: number;
 *   minPercent: number;
 *   maxPercent: number;
 * }} options
 */
export function clampWorkspaceSplitPercent(candidate, {
  containerWidth,
  primaryMinPx,
  secondaryMinPx,
  dividerPx,
  minPercent,
  maxPercent,
}) {
  const guardedCandidate = Math.min(maxPercent, Math.max(minPercent, candidate));
  if (containerWidth <= dividerPx) return guardedCandidate;

  const minimum = Math.max(minPercent, (primaryMinPx / containerWidth) * 100);
  const maximum = Math.min(
    maxPercent,
    ((containerWidth - dividerPx - secondaryMinPx) / containerWidth) * 100,
  );
  if (minimum <= maximum) {
    return Math.min(maximum, Math.max(minimum, candidate));
  }

  const available = containerWidth - dividerPx;
  const proportional =
    ((available * primaryMinPx) / (primaryMinPx + secondaryMinPx) / containerWidth)
    * 100;
  return Math.min(maxPercent, Math.max(minPercent, proportional));
}
