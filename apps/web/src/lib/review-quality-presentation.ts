/** Display statistical evidence without promoting placeholder values to claims. */
type QualityPayload = Record<string, unknown>;

export interface QualityEstimatePresentation {
  value: number | null;
  detail: string;
  description: string;
}

const ratio = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? value : null;
const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
// Preserve the estimator's four-decimal ratios at decision boundaries.
const percent = (value: number): string => `${(value * 100).toFixed(2).replace(/(\.\d)0$/, "$1")}%`;

function interval(payload: QualityPayload, estimate: number) {
  const low = ratio(payload.ci_low);
  const high = ratio(payload.ci_high);
  return low !== null && high !== null && low <= estimate && estimate <= high
    ? { low, text: `95% CI ${percent(low)}–${percent(high)}` }
    : null;
}

/** An undetermined method has no usable completeness point estimate. */
export function coveragePresentation(payload: QualityPayload): QualityEstimatePresentation {
  const value = ratio(payload.completeness);
  if (payload.method !== "chao2" || value === null) {
    return {
      value: null,
      detail: "insufficient search evidence",
      description: "Search coverage is undetermined: insufficient evidence to estimate completeness",
    };
  }
  const detail = interval(payload, value)?.text ?? "confidence interval unavailable";
  return {
    value,
    detail,
    description: `Estimated search coverage ${percent(value)} (${detail})`,
  };
}

/** Certification requires the server flag, minimum sample and conservative bound. */
export function recallPresentation(payload: QualityPayload): QualityEstimatePresentation {
  const value = ratio(payload.estimated_recall);
  if (payload.method !== "chao2" || value === null) {
    return {
      value: null,
      detail: "not certified: insufficient reviewer evidence",
      description: "Screening recall is undetermined; not certified (insufficient reviewer evidence)",
    };
  }
  const ci = interval(payload, value);
  const target = ratio(payload.target_recall);
  const screened = count(payload.screened);
  const minimum = count(payload.min_sample);
  const reviewers = count(payload.reviewers);
  let status = "not certified: insufficient certification evidence";
  if (screened !== null && minimum !== null && minimum > 0 && screened < minimum) {
    status = `not certified: ${screened} screened; minimum ${minimum}`;
  } else if (ci && target !== null && ci.low < target) {
    status = `not certified: lower 95% bound ${percent(ci.low)} is below target ${percent(target)}`;
  } else if (
    payload.certified === true && ci && target !== null && ci.low >= target
    && screened !== null && minimum !== null && minimum > 0 && screened >= minimum
    && reviewers !== null && reviewers >= 2
  ) {
    status = `certified: lower 95% bound ${percent(ci.low)} meets target ${percent(target)}`;
  }
  const detail = `${ci?.text ?? "confidence interval unavailable"}; ${status}`;
  return { value, detail, description: `Estimated screening recall ${percent(value)} (${detail})` };
}
