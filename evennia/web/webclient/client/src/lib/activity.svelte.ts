/** Ephemeral Activity state. No feed or watch data is written to browser storage. */
export interface Reference { kind: "character" | "npc" | "location" | "handset" | "object"; id: number; name: string }
export interface ActivityEvent {
  id: string; seq: number; ts_ms: number; kind: "handset.direct" | "handset.group" | "looc" | "npc.action";
  actor: Reference | null; targets: Reference[]; npc_targets: Reference[]; target_count: number;
  location: Reference | null; body: string; meta: Record<string, string | number>;
}
export interface Watch { id: number; label: string }
export interface Watches { characters: Watch[]; locations: Watch[] }
export interface Role { allowed: boolean; can_puppet: boolean }
export interface Snapshot { stream_id: string; last_seq: number; events: ActivityEvent[]; watches: Watches; role: Role }
export interface Batch { stream_id: string; first_seq: number; last_seq: number; events: ActivityEvent[] }
export type Requester = (ns: string, action: string, data?: unknown) => Promise<any>;
export type Category = "all" | "text" | "looc" | "npc";

export class ActivityFeed {
  allowed = $state(false);
  known = $state(false);
  canPuppet = $state(false);
  events = $state.raw<ActivityEvent[]>([]);
  watches = $state<Watches>({ characters: [], locations: [] });
  results = $state.raw<Reference[]>([]);
  query = $state("");
  loading = $state(false);
  searching = $state(false);
  busy = $state(false);
  error = $state("");
  gap = $state(false);
  paused = $state(false);
  pauseCutoff = $state(0);
  epoch = $state(0);
  lastSeq = $state(0);
  streamId = "";
  private mounted = 0;
  private generation = 0;
  private searchGeneration = 0;
  private subscribing: Promise<void> | null = null;
  private queued: Batch[] = [];
  private request: Requester;
  private manifest: (entries: any[]) => void = () => {};

  constructor(request: Requester = async () => { throw new Error("Activity is not connected."); }) {
    this.request = request;
  }

  connect(request: Requester, manifest: (entries: any[]) => void): void {
    this.request = request;
    this.manifest = manifest;
  }

  setRole(role: Role): void {
    this.known = true;
    this.allowed = role.allowed;
    this.canPuppet = role.allowed && role.can_puppet;
    if (!role.allowed) this.clear();
    else void this.ensureSubscribed();
  }

  open(): void { this.mounted++; void this.ensureSubscribed(); }

  close(): void {
    this.mounted = Math.max(0, this.mounted - 1);
    if (this.mounted) return;
    const generation = this.generation;
    if (this.allowed) void this.request("activity", "activity_unsubscribe").catch((err) => {
      if (generation === this.generation && this.allowed) this.error = String(err.message ?? err);
    });
    this.clear();
  }

  logout(): void {
    this.allowed = false;
    this.canPuppet = false;
    this.known = true;
    this.clear();
  }

  clear(): void {
    this.generation++;
    this.searchGeneration++;
    this.epoch++;
    this.events = [];
    this.watches = { characters: [], locations: [] };
    this.results = [];
    this.query = "";
    this.streamId = "";
    this.lastSeq = 0;
    this.gap = false;
    this.paused = false;
    this.pauseCutoff = 0;
    this.loading = this.searching = this.busy = false;
    this.error = "";
    this.queued = [];
    this.subscribing = null;
  }

  ensureSubscribed(): Promise<void> {
    if (!this.allowed || !this.mounted) return Promise.resolve();
    if (this.subscribing) return this.subscribing;
    const generation = this.generation;
    this.loading = true;
    this.error = "";
    const pending = this.request("activity", "activity_subscribe")
      .then((snapshot: Snapshot) => {
        if (generation !== this.generation || !this.allowed || !this.mounted) return;
        if (!snapshot.role.allowed) { this.setRole(snapshot.role); return; }
        const queued = this.queued;
        this.queued = [];
        if (snapshot.stream_id !== this.streamId) {
          this.events = [];
          this.lastSeq = 0;
          this.gap = false;
          this.results = [];
          this.query = "";
          this.searchGeneration++;
          this.generation++;
          this.epoch++;
          this.busy = this.searching = false;
          this.paused = false;
          this.streamId = snapshot.stream_id;
        } else if (this.lastSeq && snapshot.last_seq > this.lastSeq) {
          const firstFresh = snapshot.events.find((event) => event.seq > this.lastSeq);
          if (!firstFresh || firstFresh.seq > this.lastSeq + 1) this.gap = true;
        }
        this.canPuppet = snapshot.role.can_puppet;
        this.watches = snapshot.watches;
        this.merge(snapshot.events);
        this.lastSeq = Math.max(this.lastSeq, snapshot.last_seq);
        for (const batch of queued) if (batch.stream_id === this.streamId) this.applyBatch(batch);
      })
      .catch((err) => {
        if (generation === this.generation) this.error = String(err.message ?? err);
      })
      .finally(() => {
        if (this.subscribing === pending) { this.subscribing = null; this.loading = false; }
      });
    this.subscribing = pending;
    return pending;
  }

  batch(batch: Batch): void {
    if (!this.allowed || !this.mounted) return;
    if (this.subscribing) {
      this.queued = [...this.queued, batch].slice(-64);
      return;
    }
    if (batch.stream_id !== this.streamId) { void this.ensureSubscribed(); return; }
    this.applyBatch(batch);
  }

  private applyBatch(batch: Batch): void {
    const fresh = batch.events.filter((event) => event.seq > this.lastSeq);
    if (!fresh.length) return;
    if (fresh[0].seq > this.lastSeq + 1) this.gap = true;
    this.merge(fresh);
    this.lastSeq = Math.max(this.lastSeq, batch.last_seq);
  }

  private merge(events: ActivityEvent[]): void {
    const byId = new Map(this.events.map((event) => [event.id, event]));
    for (const event of events) byId.set(event.id, event);
    this.events = [...byId.values()].sort((a, b) => a.seq - b.seq).slice(-1000);
  }

  togglePause(): void {
    this.paused = !this.paused;
    this.pauseCutoff = this.lastSeq;
  }

  watched(event: ActivityEvent): boolean {
    const ids = [event.actor, ...event.targets, ...event.npc_targets].filter((ref) => ref?.kind === "character" || ref?.kind === "npc").map((ref) => ref!.id);
    return this.watches.characters.some((watch) => ids.includes(watch.id))
      || this.watches.locations.some((watch) => watch.id === event.location?.id);
  }

  filtered(category: Category, watched: boolean, query: string): ActivityEvent[] {
    const needle = query.trim().toLocaleLowerCase();
    return this.events.filter((event) => !(this.paused && event.seq > this.pauseCutoff) && this.matches(event, category, watched, needle));
  }

  /** Events after `seq` that match the filters, counted past the pause cutoff. */
  countAfter(seq: number, category: Category, watched: boolean, query: string): number {
    const needle = query.trim().toLocaleLowerCase();
    return this.events.filter((event) => event.seq > seq && this.matches(event, category, watched, needle)).length;
  }

  private matches(event: ActivityEvent, category: Category, watched: boolean, needle: string): boolean {
    if (category === "text" && !event.kind.startsWith("handset.")) return false;
    if (category === "looc" && event.kind !== "looc") return false;
    if (category === "npc" && event.kind !== "npc.action") return false;
    if (watched && !this.watched(event)) return false;
    return !needle || [event.body, event.actor?.name, event.location?.name, ...event.targets.map((ref) => ref.name), ...event.npc_targets.map((ref) => ref.name), event.meta.group_name].some((value) => String(value ?? "").toLocaleLowerCase().includes(needle));
  }

  async search(query: string): Promise<void> {
    this.query = query;
    const serial = ++this.searchGeneration;
    const generation = this.generation;
    this.results = [];
    this.searching = query.trim().length >= 2;
    if (!this.searching) return;
    try {
      const reply = await this.request("activity", "activity_search", { query });
      if (serial === this.searchGeneration && generation === this.generation) this.results = reply.results;
    } catch (err: any) {
      if (serial === this.searchGeneration && generation === this.generation) this.error = String(err.message ?? err);
    } finally {
      if (serial === this.searchGeneration && generation === this.generation) this.searching = false;
    }
  }

  async mutate(action: "watch" | "unwatch" | "puppet_add", data: Record<string, unknown>): Promise<void> {
    if (!this.allowed || this.busy) return;
    const generation = this.generation;
    this.busy = true;
    this.error = "";
    try {
      const reply = await this.request(action === "puppet_add" ? "puppets" : "activity", action === "puppet_add" ? action : `activity_${action}`, data);
      if (generation !== this.generation) return;
      if (action === "puppet_add") this.manifest(reply.puppets);
      else this.watches = reply.watches;
    } catch (err: any) {
      if (generation === this.generation) this.error = String(err.message ?? err);
    } finally {
      if (generation === this.generation) this.busy = false;
    }
  }
}

export const activity = new ActivityFeed();
