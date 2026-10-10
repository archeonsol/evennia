// Panel components by dockview component name. Shared by the docked workspace
// and the screen-reader layout so both mount the same views.

import GameLog from "../components/GameLog.svelte";
import RoomPanel from "../components/RoomPanel.svelte";
import ChatPanel from "../components/ChatPanel.svelte";
import ChannelView from "../components/ChannelView.svelte";
import AssistPanel from "../components/AssistPanel.svelte";
import IFramePanel from "../components/IFramePanel.svelte";
import MediaPanel from "../components/MediaPanel.svelte";
import SpawnsPanel from "../components/SpawnsPanel.svelte";
import PuppetsPanel from "../components/PuppetsPanel.svelte";
import HelpPanel from "../components/HelpPanel.svelte";
import ActivityPanel from "../components/ActivityPanel.svelte";

export const PANELS: Record<string, any> = {
  log: GameLog,
  scene: RoomPanel,
  chat: ChatPanel,
  channel: ChannelView,
  assist: AssistPanel,
  // Saved layouts from before the Assist panel name these; a restore with an
  // unknown component fails and resets the whole layout.
  tickets: AssistPanel,
  mytickets: AssistPanel,
  iframe: IFramePanel,
  media: MediaPanel,
  spawns: SpawnsPanel,
  puppets: PuppetsPanel,
  help: HelpPanel,
  activity: ActivityPanel,
};
