// Panel components by dockview component name. Shared by the docked workspace
// and the screen-reader layout so both mount the same views.

import GameLog from "../components/GameLog.svelte";
import RoomPanel from "../components/RoomPanel.svelte";
import ChatPanel from "../components/ChatPanel.svelte";
import ChannelView from "../components/ChannelView.svelte";
import AssistPanel from "../components/AssistPanel.svelte";
import TicketsPanel from "../components/TicketsPanel.svelte";
import IFramePanel from "../components/IFramePanel.svelte";
import MediaPanel from "../components/MediaPanel.svelte";
import SpawnsPanel from "../components/SpawnsPanel.svelte";
import MyTicketsPanel from "../components/MyTicketsPanel.svelte";
import PuppetsPanel from "../components/PuppetsPanel.svelte";

export const PANELS: Record<string, any> = {
  log: GameLog,
  scene: RoomPanel,
  chat: ChatPanel,
  channel: ChannelView,
  assist: AssistPanel,
  tickets: TicketsPanel,
  iframe: IFramePanel,
  media: MediaPanel,
  spawns: SpawnsPanel,
  mytickets: MyTicketsPanel,
  puppets: PuppetsPanel,
};
