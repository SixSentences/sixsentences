import type {
  SpecialistAgentEvent,
  Survey,
  SurveyQuestion,
} from "@/lib/types";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

const QUESTION_TYPES = new Set([
  "short_text",
  "long_text",
  "single_choice",
  "multiple_choice",
  "rating",
  "scale",
]);

function isSurveyQuestion(item: unknown): item is SurveyQuestion {
  const record = asRecord(item);
  return Boolean(
    typeof record?.id === "string" &&
    typeof record.title === "string" &&
    typeof record.description === "string" &&
    typeof record.type === "string" &&
    QUESTION_TYPES.has(record.type) &&
    typeof record.required === "boolean" &&
    Array.isArray(record.options) &&
    record.options.every((option) => typeof option === "string") &&
    (record.min === null || typeof record.min === "number") &&
    (record.max === null || typeof record.max === "number"),
  );
}

function afterField(event: SpecialistAgentEvent, field: string): unknown {
  const after = asRecord(event.after);
  const survey = asRecord(after?.survey);
  return survey?.[field] ?? after?.[field] ?? after?.value ?? event.after;
}

function questionList(value: unknown): SurveyQuestion[] | null {
  if (Array.isArray(value)) return value.every(isSurveyQuestion) ? value : null;
  const record = asRecord(value);
  const survey = asRecord(record?.survey);
  const questions = survey?.questions ?? record?.questions;
  return Array.isArray(questions) && questions.every(isSurveyQuestion) ? questions : null;
}

function questionOrder(value: unknown): string[] | null {
  const record = asRecord(value);
  const candidate = Array.isArray(value)
    ? value
    : record?.question_ids ?? record?.order ?? record?.questions;
  return Array.isArray(candidate) && candidate.every((item) => typeof item === "string")
    ? candidate
    : null;
}

function questionValue(value: unknown): SurveyQuestion | null {
  const record = asRecord(value);
  const candidate = asRecord(record?.question) ?? record;
  return isSurveyQuestion(candidate) ? candidate : null;
}

function questionId(event: SpecialistAgentEvent): string {
  const input = asRecord(event.input);
  const before = asRecord(event.before);
  const after = asRecord(event.after);
  return String(
    input?.question_id ?? input?.id ?? before?.id ?? after?.id ?? "",
  );
}

/**
 * Project only a server-confirmed, auto-applied survey change into the editor.
 * The terminal response remains authoritative and reconciles the full survey.
 */
export function projectConfirmedSurveyChange(
  survey: Survey,
  event: SpecialistAgentEvent,
): Survey {
  if (event.event !== "change.completed" || event.applied !== true) return survey;

  const after = asRecord(event.after);
  const completeSurvey = asRecord(after?.survey) ?? after;
  if (
    typeof completeSurvey?.title === "string" &&
    typeof completeSurvey.description === "string" &&
    Array.isArray(completeSurvey.questions)
  ) {
    return { ...survey, ...completeSurvey } as Survey;
  }

  const operation = event.operation?.toLowerCase() ?? "";
  if (operation === "set_title") {
    const title = afterField(event, "title");
    return typeof title === "string" ? { ...survey, title } : survey;
  }
  if (operation === "set_description" || operation === "set_introduction") {
    const description = afterField(event, "description");
    return typeof description === "string" ? { ...survey, description } : survey;
  }
  if (operation === "set_confirmation") {
    const confirmation = afterField(event, "confirmation");
    return typeof confirmation === "string"
      ? { ...survey, settings: { ...survey.settings, confirmation } }
      : survey;
  }
  if (operation === "set_collect_identity") {
    const collectIdentity = afterField(event, "collect_identity");
    return typeof collectIdentity === "boolean"
      ? { ...survey, settings: { ...survey.settings, collect_identity: collectIdentity } }
      : survey;
  }

  if (operation.includes("question")) {
    const replacement = questionList(event.after);
    if (replacement) return { ...survey, questions: replacement };

    if (operation.startsWith("reorder_")) {
      const order = questionOrder(event.after);
      if (!order) return survey;
      const byId = new Map(survey.questions.map((question) => [question.id, question]));
      if (
        order.length !== survey.questions.length ||
        new Set(order).size !== order.length ||
        order.some((id) => !byId.has(id))
      ) {
        return survey;
      }
      const ordered = order.flatMap((id) => byId.get(id) ? [byId.get(id)!] : []);
      return { ...survey, questions: ordered };
    }

    const changedQuestion = questionValue(event.after);
    if (operation.startsWith("add_") && changedQuestion) {
      const existing = survey.questions.some((item) => item.id === changedQuestion.id);
      const input = asRecord(event.input);
      const requestedPosition = typeof input?.position === "number"
        ? Math.trunc(input.position)
        : survey.questions.length;
      const position = Math.max(0, Math.min(requestedPosition, survey.questions.length));
      const questions = existing
        ? survey.questions.map((item) => item.id === changedQuestion.id ? changedQuestion : item)
        : [
            ...survey.questions.slice(0, position),
            changedQuestion,
            ...survey.questions.slice(position),
          ];
      return { ...survey, questions };
    }
    if (operation.startsWith("update_") && changedQuestion) {
      return {
        ...survey,
        questions: survey.questions.map((item) =>
          item.id === changedQuestion.id ? changedQuestion : item,
        ),
      };
    }
    if (operation.startsWith("delete_")) {
      const id = questionId(event);
      return id
        ? { ...survey, questions: survey.questions.filter((item) => item.id !== id) }
        : survey;
    }
  }

  return survey;
}
