// One component that turns a query result into loading / error / empty / stale / content, so
// every screen fails the same way and always tells the reader what to do next. Without it each
// screen invents its own ordering of those four states, and the ones that forget "empty" end up
// rendering a blank table that reads as "nothing is wrong".

import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import { EmptyState, ErrorState, Skeleton, StaleNotice } from "./Panel";

/** The server's own words where there are any, so an error is diagnosable from the screen. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.fields.length > 0 ? `${error.message} (${error.fields.join(", ")})` : error.message;
  }
  if (error instanceof Error) return error.message;
  return String(error ?? "unknown error");
}

export function QueryGate<T>({
  isLoading,
  isError,
  error,
  data,
  isEmpty,
  children,
  emptyTitle,
  emptyHint,
  emptyAction,
  stale,
  skeletonRows = 3,
  className,
}: {
  isLoading?: boolean;
  isError?: boolean;
  error?: unknown;
  data: T | undefined;
  /** True when the payload arrived but holds nothing worth rendering. */
  isEmpty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
  emptyTitle: string;
  /** What the reader can do next. An empty state without one is a dead end. */
  emptyHint?: ReactNode;
  emptyAction?: ReactNode;
  /** Present-but-out-of-date caveat, shown above the content rather than replacing it. */
  stale?: ReactNode;
  skeletonRows?: number;
  className?: string;
}) {
  if (isLoading) {
    return (
      <div className={className ?? "px-4 py-4"} aria-busy="true">
        <Skeleton rows={skeletonRows} />
      </div>
    );
  }
  if (isError) {
    return (
      <div className={className ?? "p-4"}>
        <ErrorState message={describeError(error)} />
      </div>
    );
  }
  if (data === undefined || (isEmpty !== undefined && isEmpty(data))) {
    return <EmptyState title={emptyTitle} hint={emptyHint} action={emptyAction} />;
  }
  return (
    <>
      {stale && <StaleNotice className="mx-4 mt-3">{stale}</StaleNotice>}
      {children(data)}
    </>
  );
}
