/**
 * BUG-110: frontend equivalent of the backend's `shared/error_handler.py::
 * sanitize_error` (security.md's "sanitize before showing an upstream/engine
 * error string to a client consumer" rule, applied to the error-boundary
 * fallback UI instead of an API response).
 *
 * An `ApiError` from services/api.ts already carries a message the *backend*
 * sanitized before sending — that's safe to show as-is. Any other thrown
 * value (a raw fetch TypeError with a URL/host, a JSON.parse error embedding
 * response text, a third-party library's internal error) is untrusted and
 * gets replaced with a generic message instead.
 */

interface ApiErrorShape {
  message: string;
  status: number;
}

function isApiError(error: unknown): error is ApiErrorShape {
  return (
    typeof error === 'object' &&
    error !== null &&
    typeof (error as Record<string, unknown>).message === 'string' &&
    typeof (error as Record<string, unknown>).status === 'number'
  );
}

export function sanitizeErrorMessage(
  error: unknown,
  fallback = 'An unexpected error occurred.',
): string {
  if (isApiError(error)) return error.message;
  return fallback;
}
