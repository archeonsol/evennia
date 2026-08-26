/* One protected, keyboard-complete replacement for browser prompt/confirm.
 *
 * Consequential console actions need their context, consequence, validation,
 * and recovery in the same visual language as the rest of the station. Native
 * dialogs cannot carry that information and do not restore focus reliably.
 */

export interface DialogOptions {
  title: string;
  description?: string;
  label?: string;
  initial?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  input?: "text" | "password" | "textarea";
  required?: boolean;
  danger?: boolean;
}

export interface DialogRequest extends DialogOptions {
  id: number;
  resolve: (value: string | boolean | null) => void;
  returnFocus: HTMLElement | null;
}

export const dialogState = $state<{ request: DialogRequest | null }>({ request: null });

let serial = 0;
const queue: DialogRequest[] = [];

function enqueue(options: DialogOptions): Promise<string | boolean | null> {
  return new Promise((resolve) => {
    queue.push({
      ...options,
      id: ++serial,
      resolve,
      returnFocus:
        typeof document === "undefined" ? null : (document.activeElement as HTMLElement | null),
    });
    showNext();
  });
}

function showNext(): void {
  if (!dialogState.request && queue.length) dialogState.request = queue.shift() || null;
}

export async function askText(options: DialogOptions): Promise<string | null> {
  const result = await enqueue({ input: "text", required: true, ...options });
  return typeof result === "string" ? result : null;
}

export async function askConfirm(options: DialogOptions): Promise<boolean> {
  return (await enqueue({ ...options, input: undefined })) === true;
}

export function finishDialog(value: string | boolean): void {
  const request = dialogState.request;
  if (!request) return;
  dialogState.request = null;
  request.resolve(value);
  queueMicrotask(() => {
    request.returnFocus?.focus();
    showNext();
  });
}

export function cancelDialog(): void {
  const request = dialogState.request;
  if (!request) return;
  dialogState.request = null;
  request.resolve(null);
  queueMicrotask(() => {
    request.returnFocus?.focus();
    showNext();
  });
}
