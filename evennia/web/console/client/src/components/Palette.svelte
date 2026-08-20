<script lang="ts">
  import { session, select } from "../lib/state.svelte";

  /* Go to a station without the rail.
   *
   * Matches on key, label, and description together, because an operator who
   * remembers what a station *does* rarely remembers what it is called. */

  interface Props {
    onClose: () => void;
  }

  const { onClose }: Props = $props();

  let term = $state("");
  let cursor = $state(0);
  let field: HTMLInputElement | null = $state(null);

  const matches = $derived.by(() => {
    const wanted = term.trim().toLowerCase();
    if (!wanted) return session.panels;
    return session.panels.filter((panel) =>
      `${panel.key} ${panel.label} ${panel.description || ""}`.toLowerCase().includes(wanted),
    );
  });

  const at = $derived(Math.min(cursor, Math.max(0, matches.length - 1)));

  function choose(index: number) {
    const panel = matches[index];
    if (!panel) return;
    onClose();
    select(panel.key);
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
      event.preventDefault();
      cursor = Math.min(at + 1, matches.length - 1);
    } else if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
      event.preventDefault();
      cursor = Math.max(at - 1, 0);
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(at);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  $effect(() => {
    field?.focus();
  });
</script>

<div
  id="palette"
  role="dialog"
  aria-modal="true"
  onclick={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}
  onkeydown={(event) => {
    if (event.key === "Escape") onClose();
  }}
  tabindex="-1"
>
  <div class="palette-box">
    <!-- A combobox over a listbox, which is what this is. The field keeps the
         focus and moves `aria-activedescendant` down the options, so a screen
         reader announces each station as the operator arrows through it while
         the keystrokes still reach the field they are typing into. -->
    <input
      id="palette-input"
      type="text"
      role="combobox"
      autocomplete="off"
      spellcheck="false"
      placeholder="GO TO A STATION"
      aria-label="Go to a station"
      aria-expanded="true"
      aria-controls="palette-list"
      aria-activedescendant={matches[at] ? `palette-option-${matches[at].key}` : undefined}
      bind:this={field}
      bind:value={term}
      onkeydown={onKeydown}
    />
    <ul id="palette-list" role="listbox" aria-label="Stations">
      {#each matches as panel, index (panel.key)}
        <!-- The keyboard path is the field's, not this element's: focus never
             leaves the combobox, and `aria-activedescendant` is what moves. A
             handler here would never fire, so adding one to satisfy the rule
             would be dead code claiming to be an accommodation. -->
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <li
          id="palette-option-{panel.key}"
          role="option"
          aria-selected={index === at}
          class={index === at ? "current" : ""}
          onclick={() => choose(index)}
          onmouseenter={() => (cursor = index)}
        >
          <span class="palette-name">{panel.label.toUpperCase()}</span>
          <span class="palette-hint">{panel.description || ""}</span>
        </li>
      {:else}
        <li class="palette-empty">NO STATION MATCHES.</li>
      {/each}
    </ul>
  </div>
</div>
