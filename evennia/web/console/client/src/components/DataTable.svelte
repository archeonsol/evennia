<script lang="ts" generics="Row">
  import type { Snippet } from "svelte";

  /* One table, virtualized, for every listing in the console.
   *
   * The vanilla client hand-rolled a table per panel and rendered every row it
   * was given. That is fine at forty rows and is not fine at forty thousand:
   * the Records lens reads `SessionRecord`, which grows by one row per
   * connection, and an operator who raises the page size should get a slow
   * query rather than a browser that stops responding.
   *
   * Only the visible window plus an overscan is in the DOM. Two spacer rows
   * carry the height of everything above and below, so the scrollbar still
   * describes the whole listing and the operator cannot tell the difference.
   *
   * Below `VIRTUALIZE_ABOVE` rows nothing is virtualized at all. Measuring and
   * offsetting a table of twelve rows costs more than it saves, and a plain
   * table is easier to be sure is correct.
   */

  interface Column {
    key: string;
    label: string;
    /** Right-aligned and tabular. Set for counts and identifiers. */
    numeric?: boolean;
  }

  interface Props {
    columns: Column[];
    rows: Row[];
    /** Renders one row's cells. Receives the row and its index. */
    row: Snippet<[Row, number]>;
    /** Replaces the whole header row, for headings that are also controls.
     *  Records needs this: its headings sort, and one of them selects a page. */
    head?: Snippet;
    /** Stable identity per row, so a scroll does not re-create every node. */
    key?: (row: Row, index: number) => string | number;
    /** Height of one rendered row, in pixels. Rows are single-line by design. */
    rowHeight?: number;
    /** Rows rendered beyond the window on each side. */
    overscan?: number;
    /** Maximum height of the scrolling region. */
    maxHeight?: string;
    label?: string;
  }

  const VIRTUALIZE_ABOVE = 120;

  const {
    columns,
    rows,
    row,
    head,
    key,
    rowHeight = 26,
    overscan = 8,
    maxHeight = "62vh",
    label = "",
  }: Props = $props();

  let scroller: HTMLDivElement | null = $state(null);
  let scrollTop = $state(0);
  let viewport = $state(0);

  const virtual = $derived(rows.length > VIRTUALIZE_ABOVE);

  const first = $derived(
    virtual ? Math.max(0, Math.floor(scrollTop / rowHeight) - overscan) : 0,
  );
  const last = $derived(
    virtual
      ? Math.min(rows.length, Math.ceil((scrollTop + viewport) / rowHeight) + overscan)
      : rows.length,
  );

  const visible = $derived(rows.slice(first, last));
  const above = $derived(first * rowHeight);
  const below = $derived(Math.max(0, (rows.length - last) * rowHeight));

  function measure(node: HTMLDivElement) {
    viewport = node.clientHeight;
    const observer = new ResizeObserver(() => {
      viewport = node.clientHeight;
    });
    observer.observe(node);
    return { destroy: () => observer.disconnect() };
  }

  function onScroll(event: Event) {
    scrollTop = (event.currentTarget as HTMLDivElement).scrollTop;
  }
</script>

<div
  class="table-scroll"
  style:max-height={maxHeight}
  bind:this={scroller}
  use:measure
  onscroll={onScroll}
>
  <table aria-label={label || undefined} aria-rowcount={rows.length}>
    <thead>
      {#if head}
        {@render head()}
      {:else}
        <tr>
          {#each columns as column (column.key)}
            <th scope="col" class={column.numeric ? "num" : undefined}>{column.label}</th>
          {/each}
        </tr>
      {/if}
    </thead>
    <tbody>
      {#if above > 0}
        <!-- A `tr` with no cells collapses to nothing, whatever height it is
             given, so the spacer needs a real cell to hold the space open. -->
        <tr class="spacer" aria-hidden="true">
          <td colspan={columns.length} style:height="{above}px"></td>
        </tr>
      {/if}
      {#each visible as item, index (key ? key(item, first + index) : first + index)}
        {@render row(item, first + index)}
      {/each}
      {#if below > 0}
        <tr class="spacer" aria-hidden="true">
          <td colspan={columns.length} style:height="{below}px"></td>
        </tr>
      {/if}
    </tbody>
  </table>
</div>
