import React from "react";
import { ApiError } from "@/api/client";
import { useSession } from "@/features/auth/session";
import Button from "@/shared/ui/Button";
import Notice from "@/shared/ui/Notice";

const HTTP_UNAUTHORIZED = 401;

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === HTTP_UNAUTHORIZED) return "Your session has ended. Sign in again.";
    return error.message;
  }
  return "Could not reach the machine unit. Check the connection and try again.";
}

interface Props {
  error: unknown;
  onRetry?: () => void;
}

export default function ErrorNotice({ error, onRetry }: Props): React.ReactElement {
  const { signOut } = useSession();
  const sessionEnded = error instanceof ApiError && error.status === HTTP_UNAUTHORIZED;

  return (
    <Notice tone="critical" role="alert">
      <p>{errorMessage(error)}</p>
      {sessionEnded ? (
        <Button onClick={signOut}>
          Sign in
        </Button>
      ) : (
        onRetry && (
          <Button onClick={onRetry}>
            Try again
          </Button>
        )
      )}
    </Notice>
  );
}
