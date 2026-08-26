<script lang="ts">
  import { tick } from "svelte";
  import { cancelDialog, dialogState, finishDialog } from "../lib/dialog.svelte";

  let value = $state("");
  let error = $state("");
  let field: HTMLInputElement | HTMLTextAreaElement | null = $state(null);

  const request = $derived(dialogState.request);

  $effect(() => {
    if (!request) return;
    value = request.initial || "";
    error = "";
    tick().then(() => field?.focus());
  });

  function submit() {
    if (!request) return;
    if (request.input && request.required && !value.trim()) {
      error = `${request.label || "This value"} is required.`;
      field?.focus();
      return;
    }
    finishDialog(request.input ? value.trim() : true);
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelDialog();
      return;
    }
    if (event.key !== "Tab") return;
    const dialog = event.currentTarget as HTMLElement;
    const controls = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled)',
      ),
    );
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
</script>

{#if request}
  <div class="dialog-backdrop">
    <div
      class="console-dialog"
      data-danger={request.danger || undefined}
      role="dialog"
      tabindex="-1"
      aria-modal="true"
      aria-labelledby="console-dialog-title"
      aria-describedby={request.description ? "console-dialog-description" : undefined}
      onkeydown={onKeydown}
    >
      <form
        onsubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <header class="dialog-head">
          <h2 id="console-dialog-title">{request.title}</h2>
        </header>
        <div class="dialog-body">
          {#if request.description}
            <p id="console-dialog-description">{request.description}</p>
          {/if}
          {#if request.input}
            <label class="legend" for="console-dialog-value">{request.label || "Value"}</label>
            {#if request.input === "textarea"}
              <textarea
                id="console-dialog-value"
                rows="5"
                placeholder={request.placeholder || ""}
                aria-invalid={error ? "true" : undefined}
                aria-describedby={error ? "console-dialog-error" : undefined}
                bind:this={field}
                bind:value
              ></textarea>
            {:else}
              <input
                id="console-dialog-value"
                type={request.input}
                placeholder={request.placeholder || ""}
                aria-invalid={error ? "true" : undefined}
                aria-describedby={error ? "console-dialog-error" : undefined}
                bind:this={field}
                bind:value
              />
            {/if}
            {#if error}<p id="console-dialog-error" class="field-error" role="alert">{error}</p>{/if}
          {/if}
        </div>
        <footer class="dialog-actions">
          <button type="button" onclick={cancelDialog}>{request.cancelLabel || "CANCEL"}</button>
          <button type="submit" class:danger-action={request.danger}>
            {request.confirmLabel || "CONTINUE"}
          </button>
        </footer>
      </form>
    </div>
  </div>
{/if}
