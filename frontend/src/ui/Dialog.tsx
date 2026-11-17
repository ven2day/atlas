// Radix dialogs with a focus trap, and ConfirmTyped: a destructive control that only arms once
// the operator has typed the exact phrase (RESUME, FLATTEN) and given a reason.

import * as RadixDialog from "@radix-ui/react-dialog";
import { useId, useState, type ReactNode } from "react";

import { cx } from "../lib/cx";
import { Button } from "./Button";
import { TextInput } from "./Input";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  wide = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-overlay animate-fade-in" />
        <RadixDialog.Content
          className={cx(
            "fixed left-1/2 z-50 -translate-x-1/2 rounded-xl border border-line-strong bg-bg-1 shadow-popover animate-rise-in",
            wide
              ? "top-[5%] flex max-h-[90vh] w-[960px] max-w-[94vw] flex-col"
              : "top-[16%] w-[460px] max-w-[92vw]",
          )}
        >
          <div className="flex shrink-0 items-start justify-between gap-4 px-5 pb-3 pt-5">
            <div className="min-w-0">
              <RadixDialog.Title className="text-lg font-semibold tracking-tight text-fg-0">
                {title}
              </RadixDialog.Title>
              <RadixDialog.Description className="mt-1.5 text-sm leading-relaxed text-fg-1">
                {description ?? " "}
              </RadixDialog.Description>
            </div>
            <RadixDialog.Close
              aria-label="Close"
              className="-mr-1 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-fg-2 transition-colors duration-150 hover:bg-bg-2 hover:text-fg-0"
            >
              <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 2l10 10M12 2 2 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </RadixDialog.Close>
          </div>
          <div className={wide ? "min-h-0 flex-1 overflow-auto px-5 pb-5" : "px-5 pb-5"}>{children}</div>
          {footer && (
            <div className="flex shrink-0 items-center justify-end gap-2 border-t border-line px-5 py-3.5">
              {footer}
            </div>
          )}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

export function ConfirmTyped({
  open,
  onOpenChange,
  title,
  description,
  phrase,
  confirmLabel,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  phrase: "RESUME" | "FLATTEN";
  confirmLabel: string;
  /** Resolves on success; a rejection's message is shown in the dialog. */
  onConfirm: (reason: string) => Promise<unknown>;
}) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ids = useId();
  const armed = typed === phrase && reason.trim().length >= 3 && !busy;

  const close = (next: boolean) => {
    if (!next) {
      setTyped("");
      setReason("");
      setError(null);
    }
    onOpenChange(next);
  };

  const submit = async () => {
    if (!armed) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      close(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "the request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={close} title={title} description={description}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${ids}-reason`} className="rq-label">
            Reason (recorded with the action)
          </label>
          <TextInput id={`${ids}-reason`} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${ids}-phrase`} className="rq-label">
            Type <span className="font-mono normal-case tracking-normal text-fg-0">{phrase}</span> to confirm
          </label>
          <TextInput
            id={`${ids}-phrase`}
            className="font-mono"
            value={typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setTyped(e.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="rounded-md border border-down bg-down-soft px-3 py-2 text-sm text-down">
            {error}
          </p>
        )}
        <div className="mt-1 flex justify-end gap-2">
          <Button variant="ghost" size="md" onClick={() => close(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="danger" size="md" disabled={!armed}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
