/** UI labels for the server-owned completeness check; not a legal assessment. */
const GAP_LABELS: Readonly<Record<string, string>> = {
  "controller name": "Responsible institution / controller",
  "controller address": "Controller postal address",
  purpose: "Purpose of this study",
  "data categories": "What data and answers are collected",
  "legal basis": "Research legal basis",
  "legal basis details": "Explanation of the legal basis",
  "retention period": "Deletion deadline or retention criteria",
  "additional recipients": "Additional data recipients (state explicitly if none)",
  "additional transfers": "Additional international transfers (state explicitly if none)",
  "supervisory authority": "Supervisory authority and complaint contact",
  "reachable study contact email": "Valid study contact email",
  "HTTPS privacy notice URL": "A valid HTTPS privacy notice link, or leave this optional field empty",
  "researcher review of the legal basis and participant information": "Your explicit review confirmation after completing the fields",
  "Review the participant information fields.": "Review the participant information fields",
};

export const SURVEY_PARTICIPANT_INFORMATION_REQUIRED =
  "Before publishing, complete and save the participant information in Share, including your review confirmation. The survey has not been published.";

/** Display only known field labels, never arbitrary provider/server error text. */
export function participantInformationGapLabels(gaps: readonly string[]): string[] {
  return [...new Set(gaps.map((gap) => Object.prototype.hasOwnProperty.call(GAP_LABELS, gap)
    ? GAP_LABELS[gap]
    : "Review the participant information fields"))];
}

/** Recognize the existing API guard without treating every conflict as this guard. */
export function isParticipantInformationRequired(detail: unknown): boolean {
  if (detail === "Complete and review the study's participant information before accepting responses.") return true;
  return typeof detail === "object" && detail !== null && !Array.isArray(detail) && "code" in detail
    && detail.code === "participant_information_required";
}
