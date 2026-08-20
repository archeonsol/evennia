/* Payload shapes shared between a panel and the components it renders with.
 *
 * These live here rather than in a component because a Svelte `<script>` block
 * cannot export a type, and duplicating a shape in two files is how the two
 * quietly stop describing the same thing.
 *
 * Each one describes what the *server* sends. Fields are optional wherever the
 * panel that produces them can omit them, so a missing key is a compile-time
 * question rather than a runtime `undefined` in the middle of a table.
 */

export interface Frame {
  line: number;
  function: string;
  file: string;
}

export interface FaultGroup {
  signature: string;
  exception: string;
  message: string;
  count: number;
  state: string;
  last_seen: string;
  frames?: Frame[];
  note?: string;
  reviewed_by?: string;
}

export interface Signature {
  field: string;
  label: string;
  value: string;
  present: boolean;
}

export interface SessionRow {
  id: number;
  account: string;
  protocol: string;
  connected: string;
  disconnected: string;
  ip: string;
  address_state: string;
  address_state_note: string;
  cidr: string;
  network: string;
  country: string;
  client_fp: string;
  device_token: string;
  signatures: Signature[];
  address_trustworthy: boolean;
  address_warning: string;
}

export interface FlagRow {
  id: number;
  kind: string;
  severity: number;
  account: string;
  summary: string;
  seen_count: number;
  state: string;
  first_seen: string;
  last_seen: string;
}

export interface SanctionRow {
  id: number;
  subject_type: string;
  subject: string;
  level: string;
  reason: string;
  actor: string;
  created: string;
  expires: string;
  silent: boolean;
}

export interface SignalRow {
  field: string;
  label: string;
  seen: number;
  of: number;
  state: string;
  advice: string;
}

export interface DossierKey {
  kind: string;
  label: string;
  value: string;
  sessions: number;
  first_seen: string;
  last_seen: string;
  shared_with: string[];
  shared_count: number;
  sanctioned: boolean;
  bannable: boolean;
  subject_type: string;
}
