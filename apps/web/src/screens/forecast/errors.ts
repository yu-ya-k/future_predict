import { ApiError } from "../../api/client";
import { FRAMING_ROUGH_QUESTION_MAX_LENGTH } from "./constants";

interface ValidationIssue {
  type?: unknown;
  loc?: unknown;
  msg?: unknown;
  ctx?: unknown;
}

function parseValidationIssues(error: ApiError): ValidationIssue[] {
  const rawDetail = error.detail ?? error.message;
  try {
    const parsed = JSON.parse(rawDetail);
    return Array.isArray(parsed) ? (parsed as ValidationIssue[]) : [];
  } catch {
    return [];
  }
}

function locIncludes(issue: ValidationIssue, value: string): boolean {
  return Array.isArray(issue.loc) && issue.loc.some((item) => item === value);
}

function maxLengthFrom(issue: ValidationIssue): number {
  const ctx = issue.ctx;
  if (ctx && typeof ctx === "object" && "max_length" in ctx) {
    const maxLength = (ctx as { max_length?: unknown }).max_length;
    if (typeof maxLength === "number") return maxLength;
  }
  return FRAMING_ROUGH_QUESTION_MAX_LENGTH;
}

function formatValidationIssues(issues: ValidationIssue[]): string | null {
  if (issues.length === 0) return null;

  const roughQuestionTooLong = issues.find(
    (issue) => issue.type === "string_too_long" && locIncludes(issue, "rough_question"),
  );
  if (roughQuestionTooLong) {
    return `入力が長すぎます。予測したいことは${maxLengthFrom(roughQuestionTooLong).toLocaleString(
      "ja-JP",
    )}文字以内にしてください。長い資料やプロンプトは、Forecastに必要な前提や判定条件に絞って貼り付けてください。`;
  }

  return issues
    .map((issue) => (typeof issue.msg === "string" ? issue.msg : "入力内容を確認してください。"))
    .join("\n");
}

export function formatForecastError(error: unknown): string {
  if (error instanceof ApiError) {
    const validationMessage = formatValidationIssues(parseValidationIssues(error));
    if (validationMessage) return validationMessage;

    const message = error.code ? `${error.code}: ${error.message}` : error.message;
    if (!error.details || Object.keys(error.details).length === 0) return message;
    return `${message}\nDetails: ${JSON.stringify(error.details)}`;
  }
  if (error instanceof Error) return error.message;
  return "Unknown error";
}
