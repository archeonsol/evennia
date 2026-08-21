/* Which component owns which station.
 *
 * The server decides what stations exist; this decides how each one is drawn.
 * A key the server sends that is not here renders the "no view" notice rather
 * than a blank region, so a panel added to the engine and not yet drawn is a
 * visible gap instead of a silent one.
 */

import type { Component } from "svelte";

import Records from "../panels/Records.svelte";
import Attributes from "../panels/Attributes.svelte";
import Moderation from "../panels/Moderation.svelte";
import Authorization from "../panels/Authorization.svelte";
import Runtime from "../panels/Runtime.svelte";
import Logs from "../panels/Logs.svelte";
import Errors from "../panels/Errors.svelte";
import Objects from "../panels/Objects.svelte";
import Actions from "../panels/Actions.svelte";
import Hooks from "../panels/Hooks.svelte";
import Prototypes from "../panels/Prototypes.svelte";
import Jobs from "../panels/Jobs.svelte";
import Eventbus from "../panels/Eventbus.svelte";
import Sessions from "../panels/Sessions.svelte";
import Server from "../panels/Server.svelte";
import Repl from "../panels/Repl.svelte";
import Sql from "../panels/Sql.svelte";
import Database from "../panels/Database.svelte";
import Views from "../panels/Views.svelte";
import Migrations from "../panels/Migrations.svelte";
import Settings from "../panels/Settings.svelte";
import Audit from "../panels/Audit.svelte";
import Health from "../panels/Health.svelte";

export const PANELS: Record<string, Component<Record<string, never>>> = {
  records: Records,
  attributes: Attributes,
  moderation: Moderation,
  authorization: Authorization,
  runtime: Runtime,
  logs: Logs,
  errors: Errors,
  objects: Objects,
  actions: Actions,
  hooks: Hooks,
  prototypes: Prototypes,
  jobs: Jobs,
  eventbus: Eventbus,
  sessions: Sessions,
  server: Server,
  repl: Repl,
  sql: Sql,
  database: Database,
  views: Views,
  migrations: Migrations,
  settings: Settings,
  audit: Audit,
  health: Health,
};
