<script lang="ts">
  // The help panel: every help topic, browsable beside the game instead of
  // scrolled through the terminal.
  //
  // A typed `help` arrives here as a `help_view` push. Links, the search box,
  // and back/forward ask the server over the `help` RPCs (see help.svelte.ts),
  // so browsing never prints into the terminal.
  import { tick } from "svelte";
  import { help } from "../lib/help.svelte";
  import { helpPlain, helpTextToHtml, sectionAnchor } from "../lib/helpText";
  import { connection } from "../lib/evennia.svelte";

  let query = $state("");
  let body: HTMLElement | undefined = $state();
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  const view = $derived(help.results ?? help.page);
  const topic = $derived(view?.topic);

  // Opened with nothing to show: load the list of topics once connected.
  $effect(() => {
    if (!help.page && !help.loading && connection.state === "open") void help.open("");
  });

  // Search as you type; Enter opens the best match instead.
  $effect(() => {
    const q = query.trim();
    if (searchTimer) clearTimeout(searchTimer);
    if (!q) {
      help.results = null;
      return;
    }
    searchTimer = setTimeout(() => void help.search(q), 180);
    return () => {
      if (searchTimer) clearTimeout(searchTimer);
    };
  });

  // After a page shows, bring its section into view, or go to the top.
  $effect(() => {
    void help.shown;
    const section = help.page?.section;
    void tick().then(() => {
      if (!body) return;
      const el = section ? body.querySelector<HTMLElement>(`#${sectionAnchor(section)}`) : null;
      if (el) {
        el.scrollIntoView({ block: "start" });
        el.classList.add("flash");
        setTimeout(() => el.classList.remove("flash"), 1400);
      } else {
        body.scrollTop = 0;
      }
    });
  });

  function go(q: string) {
    query = "";
    help.results = null;
    void help.open(q);
  }

  function onSearchKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      const q = query.trim();
      if (q) go(q);
    } else if (e.key === "Escape" && query) {
      e.preventDefault();
      query = "";
    }
  }

  // Help references inside rendered text are anchors with `data-help`.
  function onBodyClick(e: MouseEvent) {
    const a = (e.target as HTMLElement)?.closest?.("a.help-link") as HTMLElement | null;
    if (!a) return;
    e.preventDefault();
    go(a.dataset.help ?? "");
  }

  function jump(title: string) {
    body?.querySelector(`#${sectionAnchor(title)}`)?.scrollIntoView({ block: "start" });
  }

  const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "es"}`;
</script>

<div class="helpp" role="region" aria-label="Help">
  <div class="hd">
    <button class="nav" onclick={() => help.back()} disabled={!help.canBack} aria-label="Back" title="Back">‹</button>
    <button class="nav" onclick={() => help.forward()} disabled={!help.canForward} aria-label="Forward" title="Forward">›</button>
    <button class="nav" onclick={() => go("")} aria-label="All topics" title="All topics">≡</button>
    <input
      class="search"
      bind:value={query}
      onkeydown={onSearchKey}
      placeholder="Search help, or type a topic…"
      aria-label="Search help"
    />
  </div>
  {#if help.error}<p class="err" role="alert">{help.error}</p>{/if}

  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="body" bind:this={body} onclick={onBodyClick} aria-live="polite" aria-busy={help.loading}>
    {#if !view}
      <p class="empty">{help.loading ? "Loading help…" : "Type a topic above, or press ≡ for every topic."}</p>
    {:else if view.kind === "index"}
      <p class="lead">
        New here? Start with <button class="crumb" onclick={() => go("newbie")}>help newbie</button>. To read a
        syntax line, see <button class="crumb" onclick={() => go("syntax")}>help syntax</button>.
      </p>
      {#each view.categories ?? [] as cat (cat.name)}
        <section class="cat">
          <button class="catname" onclick={() => go(`category ${cat.name.toLowerCase()}`)}>{cat.name}</button>
          <div class="chips">
            {#each cat.topics as t (t.key)}
              <button class="chip" title={helpPlain(t.summary)} onclick={() => go(t.key)}>{t.key}</button>
            {/each}
          </div>
        </section>
      {/each}
    {:else if view.kind === "category"}
      <div class="crumbs"><button class="crumb" onclick={() => go("")}>Help</button> › <span>{view.category}</span></div>
      <h2 class="title">{view.category}</h2>
      <div class="rows">
        {#each view.topics ?? [] as t (t.key)}
          <button class="row" onclick={() => go(t.key)}>
            <span class="k">{t.key}</span><span class="s">{helpPlain(t.summary)}</span>
          </button>
        {/each}
      </div>
    {:else if (view.kind === "topic" || view.kind === "section") && topic}
      <div class="crumbs">
        <button class="crumb" onclick={() => go("")}>Help</button> ›
        <button class="crumb" onclick={() => go(`category ${topic.category.toLowerCase()}`)}>{topic.category}</button> ›
        <span>{cap(topic.key)}</span>
      </div>
      <h2 class="title">{cap(topic.key)}</h2>
      <p class="summary">{@html helpTextToHtml(topic.summary)}</p>
      {#if topic.sections.length >= 4}
        <nav class="toc chips" aria-label="Sections">
          {#each topic.sections as s (s.title)}
            <button class="chip" class:on={s.title === view.section} onclick={() => jump(s.title)}>{s.title}</button>
          {/each}
        </nav>
      {/if}
      {#if topic.intro}<div class="text">{@html helpTextToHtml(topic.intro)}</div>{/if}
      {#each topic.sections as s (s.title)}
        <section class="sec" id={sectionAnchor(s.title)} class:focus={s.title === view.section}>
          <h3>{cap(s.title)}</h3>
          <div class="text">{@html helpTextToHtml(s.body)}</div>
        </section>
      {/each}
    {:else}
      {#if view.kind === "not_found"}
        <p class="lead">No help topic matches “{view.query}”.</p>
      {:else}
        <p class="lead">{plural(view.hits?.length ?? 0, "match")} for “{view.query}”. Press Enter to open the best one.</p>
      {/if}
      {#if view.suggestions?.length}
        <div class="chips">
          <span class="dim">Did you mean</span>
          {#each view.suggestions as s (s.query)}
            <button class="chip" onclick={() => go(s.query)}>{s.label}</button>
          {/each}
        </div>
      {/if}
      <div class="rows">
        {#each view.hits ?? [] as h (h.query)}
          <button class="row" onclick={() => go(h.query)}>
            <span class="k">{h.query}</span><span class="s">{h.snippet}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .helpp { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd { display: flex; align-items: center; gap: 4px; padding: 6px 8px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; }
  .nav { background: none; border: 1px solid var(--border-bright); color: var(--accent-bright); font-family: inherit; font-size: 0.85rem; min-width: 26px; min-height: 26px; cursor: pointer; }
  .nav:disabled { opacity: 0.35; cursor: default; }
  .search { flex: 1; min-width: 0; background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg); font-family: inherit; font-size: 0.8rem; padding: 4px 7px; min-height: 26px; }
  .search:focus { outline: none; border-color: var(--accent); }
  .err { margin: 0; padding: 4px 10px; font-size: 0.74rem; color: var(--alert); border-bottom: 1px solid var(--border); }
  .body { flex: 1; min-height: 0; overflow-y: auto; padding: 8px 12px 16px; line-height: 1.5; font-size: 0.86rem; color: var(--fg); }
  .empty, .dim { color: var(--fg-faint); font-style: italic; }
  .lead { color: var(--fg-dim); margin: 0 0 10px; }
  .crumbs { color: var(--fg-faint); font-size: 0.7rem; letter-spacing: 0.06em; margin-bottom: 2px; }
  .crumb { background: none; border: none; padding: 0; color: var(--accent-bright); font-family: inherit; font-size: inherit; cursor: pointer; text-decoration: underline; }
  .title { margin: 2px 0 4px; color: var(--gold); font-size: 1.05rem; letter-spacing: 0.06em; font-weight: 600; }
  .summary { margin: 0 0 8px; color: var(--fg-dim); }
  .cat { display: flex; flex-direction: column; gap: 4px; padding: 6px 0; border-top: 1px solid var(--border); }
  .catname { align-self: flex-start; background: none; border: none; padding: 0; color: var(--accent-bright); font-family: inherit; text-transform: uppercase; letter-spacing: 0.16em; font-size: 0.72rem; cursor: pointer; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .toc { margin: 4px 0 10px; }
  .chip { background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; font-size: 0.72rem; padding: 1px 8px; min-height: 24px; cursor: pointer; }
  .chip:hover, .chip.on { color: var(--accent-bright); border-color: var(--accent); }
  .rows { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
  .row { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 6px 9px; background: var(--bg); border: 1px solid var(--border); border-left: 3px solid var(--border-bright); color: var(--fg); font-family: inherit; cursor: pointer; }
  .row:hover { border-color: var(--accent); }
  .k { color: var(--accent-bright); font-size: 0.78rem; }
  .s { color: var(--fg-dim); font-size: 0.76rem; }
  .sec { margin-top: 12px; padding-left: 8px; border-left: 2px solid var(--border); scroll-margin-top: 6px; transition: border-color 0.4s, background 0.4s; }
  .sec.focus { border-left-color: var(--accent); }
  .sec:global(.flash) { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .sec h3 { margin: 0 0 4px; color: var(--accent-bright); font-size: 0.8rem; letter-spacing: 0.12em; text-transform: uppercase; }
  .text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .body :global(a.help-link) { color: var(--accent-bright); text-decoration: underline; cursor: pointer; }
  :global([data-calm]) .sec { transition: none; }
</style>
