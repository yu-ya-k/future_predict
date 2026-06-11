import { ApiError } from "../../api/client";
import { FRAMING_ROUGH_QUESTION_MAX_LENGTH } from "./constants";

const UNEXPECTED_ERROR_MESSAGE =
  "予期しないエラーが発生しました。時間をおいて再読み込みしてください。問題が続く場合は管理者にお問い合わせください。";

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
    )}文字以内にしてください。長い資料やプロンプトは、Forecastに必要な前提や解決条件に絞って貼り付けてください。`;
  }

  return issues
    .map((issue) => (typeof issue.msg === "string" ? issue.msg : "入力内容を確認してください。"))
    .join("\n");
}

export function formatForecastError(error: unknown): string {
  if (error instanceof ApiError) {
    const validationMessage = formatValidationIssues(parseValidationIssues(error));
    if (validationMessage) return validationMessage;

    if (error.code === "prompt_stale") {
      return "Promptが古くなっています。Promptを再取得してから取り込んでください。";
    }
    if (error.code === "research_pack_manual_recovery_not_allowed") {
      return "この状態では手動収集に切り替えられません。最新状態を確認してください。";
    }
    if (error.code === "research_pack_already_exists") {
      return "すでに公開情報パックがあります。最新状態を再読み込みしてください。";
    }

    // Typed, actionable errors (e.g. forecast command 409s such as
    // policy_requires_revision) carry a code and often structured details the
    // operator needs to act on, so surface them. Errors with no code fall back
    // to a friendly sentence rather than dumping an opaque payload.
    if (error.code === "approval_required") {
      return "入力データが変更されています。最新状態を確認して再度実行してください。";
    }

    const serverMessage = error.message.trim();
    if (error.code) {
      const lines = [
        serverMessage && serverMessage !== error.code
          ? `${error.code}: ${serverMessage}`
          : error.code,
      ];
      if (error.details && Object.keys(error.details).length > 0) {
        // Trim details to safe human-readable key=value pairs; avoid dumping raw JSON.
        const summary = Object.entries(error.details)
          .map(([k, v]) => `${k}: ${typeof v === "string" || typeof v === "number" ? v : JSON.stringify(v)}`)
          .join(", ");
        lines.push(`詳細: ${summary}`);
      }
      return lines.join("\n");
    }
    // Without a code we cannot translate, so we surface the server-provided
    // message (e.g. a 4xx/5xx `detail` such as "Draft model unavailable."),
    // which operators rely on. The one exception is a network failure
    // (status 0), whose message is the client-side English "Network error"
    // string and must not leak to users.
    if (serverMessage && error.status !== 0) {
      return serverMessage;
    }
    return UNEXPECTED_ERROR_MESSAGE;
  }
  if (error instanceof Error) {
    return UNEXPECTED_ERROR_MESSAGE;
  }
  return UNEXPECTED_ERROR_MESSAGE;
}
