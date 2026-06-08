import { ApiError } from "../../api/client";

export function formatForecastError(error: unknown): string {
  if (error instanceof ApiError) {
    const message = error.code ? `${error.code}: ${error.message}` : error.message;
    if (!error.details || Object.keys(error.details).length === 0) return message;
    return `${message}\nDetails: ${JSON.stringify(error.details)}`;
  }
  if (error instanceof Error) return error.message;
  return "Unknown error";
}

