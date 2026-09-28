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
  <!-- The panel's own command line: a prompt and a field, then its commands. -->
  <div class="hd">
    <label class="line">
      <span class="prompt" aria-hidden="true">HELP&gt;</span>
      <input
        class="search sh-field sh-placeholder"
        bind:value={query}
        onkeydown={onSearchKey}
        placeholder="Search or type a topic"
        aria-label="Search help"
      />
    </label>
    <span class="cmds">
      <button class="nav sh-cmd" onclick={() => help.back()} disabled={!help.canBack} aria-label="Back">Back</button>
      <button class="nav sh-cmd" onclick={() => help.forward()} disabled={!help.canForward} aria-label="Forward">Forward</button>
      <button class="nav sh-cmd" onclick={() => go("")} aria-label="All topics">Topics</button>
    </span>
  </div>
  {#if help.error}<p class="err" role="alert">{help.error}</p>{/if}

  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="body" bind:this={body} onclick={onBodyClick} aria-live="polite" aria-busy={help.loading}>
    {#if !view}
      <p class="empty">{help.loading ? "Loading help" : "Search above, or choose Topics for every topic."}</p>
    {:else if view.kind === "index"}
      <p class="lead">
        Start with <button class="ref" onclick={() => go("newbie")}>help newbie</button>. How to read a syntax
        line: <button class="ref" onclick={() => go("syntax")}>help syntax</button>.
      </p>
      {#each view.categories ?? [] as cat (cat.name)}
        <section class="cat">
          <button class="catname" onclick={() => go(`category ${cat.name.toLowerCase()}`)}>{cat.name}</button>
          <div class="topics">
            {#each cat.topics as t (t.key)}
              <button class="topic" title={helpPlain(t.summary)} onclick={() => go(t.key)}>{t.key}</button>
            {/each}
          </div>
        </section>
      {/each}
    {:else if view.kind === "category"}
      <div class="crumbs"><button class="crumb" onclick={() => go("")}>Help</button><span class="sep" aria-hidden="true">/</span><span>{view.category}</span></div>
      <h2 class="title">{view.category}</h2>
      <div class="rows">
        {#each view.topics ?? [] as t (t.key)}
          <button class="row sh-row" onclick={() => go(t.key)}>
            <span class="k">{t.key}</span><span class="s">{helpPlain(t.summary)}</span>
          </button>
        {/each}
      </div>
    {:else if (view.kind === "topic" || view.kind === "section") && topic}
      <div class="crumbs">
        <button class="crumb" onclick={() => go("")}>Help</button><span class="sep" aria-hidden="true">/</span>
        <button class="crumb" onclick={() => go(`category ${topic.category.toLowerCase()}`)}>{topic.category}</button><span class="sep" aria-hidden="true">/</span>
        <span>{cap(topic.key)}</span>
      </div>
      <h2 class="title">{cap(topic.key)}</h2>
      <p class="summary">{@html helpTextToHtml(topic.summary)}</p>
      {#if topic.sections.length >= 4}
        <nav class="toc" aria-label="Sections">
          <span class="toclbl" aria-hidden="true">Sections</span>
          {#each topic.sections as s (s.title)}
            <button class="topic" class:on={s.title === view.section} onclick={() => jump(s.title)}>{s.title}</button>
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
        <p class="lead">{plural(view.hits?.length ?? 0, "match")} for “{view.query}”. Enter opens the first.</p>
      {/if}
      {#if view.suggestions?.length}
        <div class="suggest">
          <span class="toclbl">Did you mean</span>
          {#each view.suggestions as s (s.query)}
            <button class="topic" onclick={() => go(s.query)}>{s.label}</button>
          {/each}
        </div>
      {/if}
      <div class="rows">
        {#each view.hits ?? [] as h (h.query)}
          <button class="row sh-row" onclick={() => go(h.query)}>
            <span class="k">{h.query}</span><span class="s">{h.snippet}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  /* The console grammar the rest of the shell uses (styles/shell.css): a
     prompt on a ruled line, bracketed commands, ruled rows, section heads on a
     rule. No boxes, chips or cards: help is text, so it reads as text. */
  .helpp { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd {
    display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px;
    padding: 6px 10px 5px; border-bottom: 1px solid var(--border-bright); flex: 0 0 auto;
  }
  .line { flex: 1 1 18ch; min-width: 0; display: flex; align-items: center; gap: 0.8ch; }
  .prompt { color: var(--accent-bright); font-size: 0.78rem; letter-spacing: 0.08em; flex: none; }
  .search { flex: 1; min-width: 0; }
  .search:focus { outline: none; }
  .cmds { display: flex; flex: none; margin-left: auto; }
  .err { margin: 0; padding: 4px 10px; font-size: 0.74rem; color: var(--alert); border-bottom: 1px solid var(--border); }
  .body { flex: 1; min-height: 0; overflow-y: auto; padding: 8px 12px 16px; line-height: 1.5; font-size: 0.86rem; color: var(--fg); }
  .empty { color: var(--fg-faint); letter-spacing: 0.08em; }
  .lead { color: var(--fg-dim); margin: 0 0 10px; }
  .ref, .crumb {
    background: none; border: 0; padding: 0; font: inherit; color: var(--accent-bright); cursor: pointer;
    text-decoration: underline dotted; text-underline-offset: 3px;
  }
  .ref:hover, .ref:focus-visible, .crumb:hover, .crumb:focus-visible { background: var(--accent); color: var(--bg-deep); text-decoration: none; }
  .crumbs { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0 0.8ch; color: var(--fg-faint); font-size: 0.66rem; letter-spacing: 0.14em; text-transform: uppercase; margin-bottom: 2px; }
  .crumbs .crumb { color: var(--fg-dim); text-decoration: none; letter-spacing: inherit; text-transform: inherit; }
  .sep { color: var(--border-bright); }
  .title { margin: 2px 0 4px; color: var(--gold); font-size: 0.95rem; letter-spacing: 0.16em; font-weight: normal; text-transform: uppercase; }
  .summary { margin: 0 0 8px; color: var(--fg-dim); }

  /* The index is a help listing, as a MUD prints one: each category a head on
     a rule, its topics set in columns underneath. */
  .cat { padding: 4px 0 8px; }
  .catname {
    display: flex; align-items: center; gap: 1ch; width: 100%; margin: 4px 0 3px; padding: 0;
    background: none; border: 0; font: inherit; font-size: 0.66rem; letter-spacing: 0.24em; text-transform: uppercase;
    color: var(--gold); text-align: left; cursor: pointer; white-space: nowrap;
  }
  .catname::after { content: ""; flex: 1; height: 1px; background: linear-gradient(to right, var(--border-bright), transparent); }
  .catname:hover, .catname:focus-visible { color: var(--accent-bright); }
  .topics { display: grid; grid-template-columns: repeat(auto-fill, minmax(21ch, 1fr)); gap: 0 1ch; }
  .topic {
    background: none; border: 0; padding: 1px 0.6ch; min-height: 22px; font: inherit; font-size: 0.8rem;
    color: var(--fg-dim); text-align: left; cursor: pointer; overflow-wrap: anywhere;
  }
  .topic:hover, .topic:focus-visible, .topic.on { background: var(--accent); color: var(--bg-deep); }
  .toc, .suggest { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0 0.4ch; margin: 4px 0 10px; }
  .toclbl { color: var(--fg-faint); font-size: 0.62rem; letter-spacing: 0.18em; text-transform: uppercase; margin-right: 0.8ch; }

  .rows { display: flex; flex-direction: column; margin-top: 6px; border-top: 1px solid var(--border); }
  .rows:empty { display: none; }
  .k { color: var(--accent-bright); font-size: 0.8rem; }
  .s { color: var(--fg-dim); font-size: 0.76rem; }

  /* A section is a head on a rule and its text. The section a page was opened
     at carries the list marker; arriving at it lights its head for a moment. */
  .sec { margin-top: 14px; scroll-margin-top: 6px; }
  .sec h3 {
    position: relative; display: flex; align-items: center; gap: 1ch; margin: 0 0 4px; padding: 0 0.5ch 0 2.2ch;
    color: var(--accent-bright); font-size: 0.7rem; font-weight: normal; letter-spacing: 0.2em; text-transform: uppercase;
  }
  .sec h3::before { content: "\25B8"; content: "\25B8" / ""; position: absolute; left: 0.3ch; color: transparent; }
  .sec h3::after { content: ""; flex: 1; height: 1px; background: linear-gradient(to right, var(--border-bright), transparent); }
  .sec.focus h3::before { color: var(--accent-bright); }
  .sec:global(.flash) h3 { background: var(--accent); color: var(--bg-deep); }
  .sec:global(.flash) h3::before { color: var(--bg-deep); }
  .text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .body :global(a.help-link) { color: var(--accent-bright); text-decoration: underline dotted; text-underline-offset: 3px; cursor: pointer; }
  .body :global(a.help-link:hover), .body :global(a.help-link:focus-visible) { background: var(--accent); color: var(--bg-deep); text-decoration: none; }
</style>
