import { mount } from "svelte";

import App from "./App.svelte";

const target = document.getElementById("console-root");
if (!target) throw new Error("console: no #console-root to mount into");

target.replaceChildren();
mount(App, { target });
