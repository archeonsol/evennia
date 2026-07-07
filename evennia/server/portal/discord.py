"""
Implements Discord chat channel integration.

The Discord API uses a mix of websockets and REST API endpoints.

In order for this integration to work, you need to have your own
discord bot set up via https://discord.com/developers/applications
with the MESSAGE CONTENT toggle switched on, and your bot token
added to `server/conf/secret_settings.py` as your  DISCORD_BOT_TOKEN
"""

import json
import os
from random import random

from django.conf import settings
from twisted.internet import protocol

from evennia.server.portal.ws_protocol import (WSClientProtocolBase,
                                               connect_ws, encode_ws_headers)
from evennia.server.session import Session
from evennia.utils import class_from_module, get_evennia_version, http, logger
from evennia.utils.utils import delay

_BASE_SESSION_CLASS = class_from_module(settings.BASE_SESSION_CLASS)

DISCORD_API_VERSION = 10
# include version number to prevent automatically updating to breaking changes
DISCORD_API_BASE_URL = f"https://discord.com/api/v{DISCORD_API_VERSION}"

DISCORD_USER_AGENT = f"Evennia (https://www.evennia.com, {get_evennia_version(mode='short')})"
DISCORD_BOT_TOKEN = settings.DISCORD_BOT_TOKEN
DISCORD_BOT_INTENTS = settings.DISCORD_BOT_INTENTS

# Discord OP codes, alphabetic
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_HEARTBEAT_ACK = 11
OP_HELLO = 10
OP_IDENTIFY = 2
OP_INVALID_SESSION = 9
OP_RECONNECT = 7
OP_RESUME = 6


def should_retry(status_code):
    """
    Helper function to check if the request should be retried later.

    Args:
        status_code (int) - The HTTP status code

    Returns:
        retry (bool) - True if request should be retried False otherwise
    """
    if status_code >= 500 and status_code <= 504:
        # these are common server error codes when the server is temporarily malfunctioning
        # in these cases, we should retry
        return True
    else:
        # handle all other cases; this can be expanded later if needed for special cases
        return False


class _ReactorTimer:
    """Thin adapter: DiscordClient expects ``call_later`` on the factory timer."""

    def call_later(self, delay, func, *args, **kwargs):
        from twisted.internet import reactor

        return reactor.callLater(delay, func, *args, **kwargs)


class DiscordWebsocketServerFactory(protocol.ReconnectingClientFactory):
    """
    A customized websocket client factory that navigates the Discord gateway process.

    """

    initialDelay = 1
    factor = 1.5
    maxDelay = 60
    noisy = False
    gateway = None
    resume_url = None
    is_connecting = False

    # read by connect_ws to build the outbound handshake (set in websocket_init)
    ws_url = None
    ws_headers = ()
    ws_subprotocols = ()

    def __init__(self, sessionhandler, *args, **kwargs):
        self.uid = kwargs.get("uid")
        self.sessionhandler = sessionhandler
        self.port = None
        self.bot = None
        self._batched_timer = _ReactorTimer()

    def get_gateway_url(self, *args, **kwargs):
        # get the websocket gateway URL from Discord
        headers = {
            "User-Agent": DISCORD_USER_AGENT,
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }

        def cbResponse(response):
            if response.code == 200:
                self.websocket_init(response.content, *args, **kwargs)
            else:
                logger.log_warn(f"Discord gateway request failed (HTTP {response.code}).")
                # release the connect lock so ReconnectingClientFactory can retry
                self.is_connecting = False

        def ebFailed(failure):
            logger.log_err(f"Discord gateway request errored: {failure.getErrorMessage()}")
            # release the connect lock so ReconnectingClientFactory can retry
            self.is_connecting = False

        http.request("GET", f"{DISCORD_API_BASE_URL}/gateway", headers=headers).addCallbacks(
            cbResponse, ebFailed
        )

    def websocket_init(self, payload, *args, **kwargs):
        """
        callback for when the URL is gotten
        """
        data = json.loads(str(payload, "utf-8"))
        self.is_connecting = False
        if url := data.get("url"):
            self.gateway = f"{url}/?v={DISCORD_API_VERSION}&encoding=json"
            useragent = kwargs.pop("useragent", DISCORD_USER_AGENT)
            headers = kwargs.pop(
                "headers",
                {
                    "Authorization": [f"Bot {DISCORD_BOT_TOKEN}"],
                    "Content-Type": ["application/json"],
                },
            )
            headers.setdefault("User-Agent", [useragent])

            logger.log_info("Connecting to Discord Gateway...")
            self.ws_url = self.gateway
            self.ws_headers = encode_ws_headers(headers)
            self.ws_subprotocols = ()
            self.start()
        else:
            logger.log_err("Discord did not return a websocket URL; connection cancelled.")

    def buildProtocol(self, addr):
        """
        Build new instance of protocol

        Args:
            addr (str): Not used, using factory/settings data

        """
        if hasattr(settings, "DISCORD_SESSION_CLASS"):
            protocol_class = class_from_module(
                settings.DISCORD_SESSION_CLASS, fallback=DiscordClient
            )
            protocol = protocol_class()
        else:
            protocol = DiscordClient()

        protocol.factory = self
        protocol.sessionhandler = self.sessionhandler
        return protocol

    def startedConnecting(self, connector):
        """
        Tracks reconnections for debugging.

        Args:
            connector (Connector): Represents the connection.

        """
        logger.log_info("Connecting to Discord...")

    def reconnect(self):
        """
        Force a reconnection of the bot protocol. This requires
        de-registering the session and then reattaching a new one.

        """
        # set up the reconnection target (Discord hands out a resume_gateway_url)
        if self.resume_url:
            self.ws_url = self.resume_url
        elif self.gateway:
            self.ws_url = self.gateway
        # reset the internal delay, since this is a deliberate disconnect
        self.delay = self.initialDelay
        # disconnect to allow the reconnection process to kick in
        self.bot.sendClose()
        self.sessionhandler.server_disconnect(self.bot)

    def start(self):
        "Connect protocol to remote server"

        if not self.gateway:
            # we don't know where to connect to
            # get the gateway URL from Discord
            self.is_connecting = True
            self.get_gateway_url()
        elif not self.is_connecting:
            # everything is good, connect
            from evennia.server.portal.asyncio_transport import (
                asyncio_servers_enabled, get_asyncio_loop)

            if asyncio_servers_enabled():
                from evennia.server.portal.ws_protocol import connect_ws_asyncio

                get_asyncio_loop().create_task(connect_ws_asyncio(self))
                return
            connect_ws(self)


class DiscordClient(WSClientProtocolBase, _BASE_SESSION_CLASS):
    """
    Implements the Discord client
    """

    nextHeartbeatCall = None
    pending_heartbeat = False
    heartbeat_interval = None
    last_sequence = 0
    session_id = None
    discord_id = None

    def __init__(self):
        super().__init__()

    def at_login(self):
        pass

    def onOpen(self):
        """
        Called when connection is established.

        """
        logger.log_msg("Discord connection established.")
        self.factory.bot = self

        self.init_session("discord", "discord.gg", self.factory.sessionhandler)
        self.uid = int(self.factory.uid)
        self.logged_in = True
        self.sessionhandler.connect(self)

    def onMessage(self, payload, isBinary):
        """
        Callback fired when a complete WebSocket message was received.

        Args:
            payload (bytes): The WebSocket message received.
            isBinary (bool): Flag indicating whether payload is binary or
                             UTF-8 encoded text.

        """
        if isBinary:
            logger.log_info("DISCORD: got a binary payload for some reason")
            return
        data = json.loads(str(payload, "utf-8"))
        if seqid := data.get("s"):
            self.last_sequence = seqid

        # not sure if that error json format is for websockets, so
        # check for it just in case
        if "errors" in data:
            self.handle_error(data)
            return

        # check for discord gateway API op codes first
        if data["op"] == OP_HELLO:
            self.interval = data["d"]["heartbeat_interval"] / 1000  # convert millisec to seconds
            if self.nextHeartbeatCall:
                try:
                    if self.nextHeartbeatCall.active():
                        self.nextHeartbeatCall.cancel()
                except Exception:
                    pass
                self.nextHeartbeatCall = None
            self.nextHeartbeatCall = self.factory._batched_timer.call_later(
                self.interval * random(),
                self.doHeartbeat,
            )
            if self.session_id:
                # we already have a session; try to resume instead
                self.resume()
            else:
                self.identify()
        elif data["op"] == OP_HEARTBEAT_ACK:
            # our last heartbeat was acknowledged, so reset the "pending" flag
            self.pending_heartbeat = False
        elif data["op"] == OP_HEARTBEAT:
            # Discord wants us to send a heartbeat immediately
            self.doHeartbeat(force=True)
        elif data["op"] == OP_INVALID_SESSION:
            # Discord doesn't like our current session; reconnect for a new one
            logger.log_msg("Discord: received 'Invalid Session' opcode. Reconnecting.")
            if data["d"] == False:
                # can't resume, clear existing resume data
                self.session_id = None
                self.factory.resume_url = None
            self.factory.reconnect()
        elif data["op"] == OP_RECONNECT:
            # reconnect as requested; Discord does this regularly for server load balancing
            logger.log_msg("Discord: received 'Reconnect' opcode. Reconnecting.")
            self.factory.reconnect()
        elif data["op"] == OP_DISPATCH:
            # handle the general dispatch opcode events by type
            if data["t"] == "READY":
                # our recent identification is valid; process new session info
                self.connection_ready(data["d"])
            else:
                # general message, pass on to data_in
                self.data_in(data=data)

    def onClose(self, wasClean, code=None, reason=None):
        """
        This is executed when the connection is lost for whatever
        reason. it can also be called directly, from the disconnect
        method.

        Args:
            wasClean (bool): ``True`` if the WebSocket was closed cleanly.
            code (int or None): Close status as sent by the WebSocket peer.
            reason (str or None): Close reason as sent by the WebSocket peer.

        """
        self.sessionhandler.disconnect(self)
        if self.nextHeartbeatCall:
            try:
                if self.nextHeartbeatCall.active():
                    self.nextHeartbeatCall.cancel()
            except Exception:
                pass
            self.nextHeartbeatCall = None
        if wasClean:
            logger.log_info(f"Discord connection closed ({code}) reason: {reason}")
        else:
            logger.log_info(f"Discord connection lost.")

    def _send_json(self, data):
        """
        Post JSON data to the websocket

        Args:
            data (dict): content to send.

        """
        return self.sendMessage(json.dumps(data).encode("utf-8"))

    def _post_json(self, url, data, **kwargs):
        """
        Post JSON data to a REST API endpoint

        Args:
            url (str) - The API path which is being posted to
            data (dict) - Content to be sent
        """
        url = f"{DISCORD_API_BASE_URL}/{url}"
        body = json.dumps(data).encode("utf-8")
        request_type = kwargs.pop("type", "POST")
        headers = {
            "User-Agent": DISCORD_USER_AGENT,
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }

        def cbResponse(response):
            if response.code == 200 or response.code == 204:
                self.post_response(response.content)
            elif should_retry(response.code):
                delay(300, self._post_json, url, data, **kwargs)

        http.request(request_type, url, headers=headers, data=body).addCallback(cbResponse)

    def post_response(self, body, **kwargs):
        """
        Process the response from sending a POST request

        Args:
            body (bytes) - The post response body
        """
        data = json.loads(body)
        if "errors" in data:
            self.handle_error(data)

    def handle_error(self, data, **kwargs):
        """
        General hook for processing errors.

        Args:
            data (dict) - The received error data

        """
        logger.log_err(str(data))

    def resume(self):
        """
        Called after a reconnection to re-identify and replay missed events

        """
        if not self.last_sequence or not self.session_id:
            # we have no known state to resume from, identify normally
            self.identify()
            return

        # build a RESUME request for Discord and send it
        data = {
            "op": OP_RESUME,
            "d": {
                "token": DISCORD_BOT_TOKEN,
                "session_id": self.session_id,
                "s": self.last_sequence,
            },
        }
        self._send_json(data)

    def disconnect(self, reason=None):
        """
        Generic hook for the engine to call in order to
        disconnect this protocol.

        Args:
            reason (str or None): Motivation for the disconnection.

        """
        self.sendClose(self.CLOSE_STATUS_CODE_NORMAL, reason)

    def identify(self, *args, **kwargs):
        """
        Send Discord authentication. This should be sent once heartbeats begin.

        """
        data = {
            "op": 2,
            "d": {
                "token": DISCORD_BOT_TOKEN,
                "intents": DISCORD_BOT_INTENTS,
                "properties": {
                    "os": os.name,
                    "browser": DISCORD_USER_AGENT,
                    "device": DISCORD_USER_AGENT,
                },
            },
        }
        self._send_json(data)

    def connection_ready(self, data):
        """
        Process READY data for relevant bot info.
        """
        self.factory.resume_url = data["resume_gateway_url"]
        self.session_id = data["session_id"]
        self.discord_id = data["user"]["id"]

    def doHeartbeat(self, *args, **kwargs):
        """
        Send heartbeat to Discord.

        """
        if not self.pending_heartbeat or kwargs.get("force"):
            if self.nextHeartbeatCall:
                try:
                    if self.nextHeartbeatCall.active():
                        self.nextHeartbeatCall.cancel()
                except Exception:
                    pass
                self.nextHeartbeatCall = None
            # send the heartbeat
            data = {"op": 1, "d": self.last_sequence}
            self._send_json(data)
            # track that we sent a heartbeat, in case we don't receive an ACK
            self.pending_heartbeat = True
            self.nextHeartbeatCall = self.factory._batched_timer.call_later(
                self.interval,
                self.doHeartbeat,
            )
        else:
            # we didn't get a response since the last heartbeat; reconnect
            self.factory.reconnect()

    def send_channel(self, text, channel_id, **kwargs):
        """
        Send a message from an Evennia channel to a Discord channel.

        Use with session.msg(channel=(message, channel, sender))

        """

        data = {"content": text}
        data.update(kwargs)
        self._post_json(f"channels/{channel_id}/messages", data)

    def send_nickname(self, text, guild_id, user_id, **kwargs):
        """
        Changes a user's nickname on a Discord server.

        Use with session.msg(nickname=(new_nickname, guild_id, user_id))
        """

        data = {"nick": text}
        data.update(kwargs)
        self._post_json(f"guilds/{guild_id}/members/{user_id}", data, type="PATCH")

    def send_role(self, role_id, guild_id, user_id, **kwargs):
        data = kwargs
        self._post_json(f"guilds/{guild_id}/members/{user_id}/roles/{role_id}", data, type="PUT")

    def send_remove_role(self, role_id, guild_id, user_id, **kwargs):
        """
        Remove a role from a guild member via REST DELETE.

        Use with session.msg(remove_role=(role_id, guild_id, user_id))
        """
        self._post_json(f"guilds/{guild_id}/members/{user_id}/roles/{role_id}", {}, type="DELETE")

    def send_interaction_reply(self, content, interaction_id, token, **kwargs):
        """
        Respond to a Discord interaction (slash command or button) via REST.

        Use with session.msg(interaction_reply=((content, interaction_id, token), opts))
        Sends an CHANNEL_MESSAGE_WITH_SOURCE (type 4) response by default.
        Pass response_type=6 for DEFERRED_UPDATE_MESSAGE on component clicks.
        """
        data_payload = {}
        if content:
            data_payload["content"] = str(content)[:2000]
        if kwargs.get("embeds"):
            data_payload["embeds"] = kwargs["embeds"]
        if kwargs.get("components") is not None:
            data_payload["components"] = kwargs["components"]
        response_type = int(kwargs.get("response_type", 4))
        data = {"type": response_type, "data": data_payload}
        if kwargs.get("flags"):
            data["data"]["flags"] = int(kwargs["flags"])
        self._post_json(f"interactions/{interaction_id}/{token}/callback", data)

    def send_register_commands(self, commands, app_id, guild_id, **kwargs):
        """
        Bulk-overwrite guild slash commands via REST PUT.

        Use with session.msg(register_commands=(commands_list, app_id, guild_id))
        ``commands`` is a list of application command dicts.
        """
        self._post_json(
            f"applications/{app_id}/guilds/{guild_id}/commands",
            commands,
            type="PUT",
        )

    def send_create_thread(self, name, channel_id, job_id, **kwargs):
        """
        Create a Discord thread under a parent channel, then report its id back.

        Use with session.msg(create_thread=(name, channel_id, job_id)). On
        success the portal feeds a THREAD_CREATED event to the server carrying
        ``job_id`` and the new ``thread_id`` so game code can bind them.

        Optional kwargs (forum parent or rich opener):
          applied_tags (list[str]) — forum tag snowflakes
          message (dict) — initial thread/post body (embeds/content)
          forum (bool) — omit type 11 (required for forum channel parents)
        """
        url = f"{DISCORD_API_BASE_URL}/channels/{channel_id}/threads"
        forum = kwargs.pop("forum", False)
        data = {"name": str(name)[:100], "auto_archive_duration": 1440}
        if not forum and "message" not in kwargs:
            # type 11 = GUILD_PUBLIC_THREAD in a text channel.
            data["type"] = 11
        data.update(kwargs)
        body = json.dumps(data).encode("utf-8")
        headers = {
            "User-Agent": DISCORD_USER_AGENT,
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }

        def cbResponse(response):
            if response.code in (200, 201):
                try:
                    payload = json.loads(response.content)
                except Exception:
                    logger.log_err(
                        f"Discord thread create: invalid JSON for job={job_id}"
                    )
                    return
                thread_id = payload.get("id")
                if thread_id:
                    self.sessionhandler.data_in(
                        self,
                        bot_data_in=(
                            "",
                            {
                                "type": "THREAD_CREATED",
                                "job_id": job_id,
                                "thread_id": thread_id,
                            },
                        ),
                    )
                else:
                    logger.log_err(
                        f"Discord thread create: no thread id in response job={job_id}"
                    )
            elif should_retry(response.code):
                delay(300, self.send_create_thread, name, channel_id, job_id, **kwargs)
            else:
                err_body = ""
                try:
                    err_body = response.content.decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                logger.log_err(
                    f"Discord thread create failed job={job_id} HTTP {response.code}: "
                    f"{err_body}"
                )
                self.sessionhandler.data_in(
                    self,
                    bot_data_in=(
                        "",
                        {
                            "type": "THREAD_CREATE_FAILED",
                            "job_id": job_id,
                            "code": response.code,
                            "error": err_body,
                        },
                    ),
                )

        http.request("POST", url, headers=headers, data=body).addCallback(cbResponse)

    def send_thread_message(self, thread_id, **kwargs):
        """
        Post a message (plain content, embeds, and/or components) into a thread.

        Use with session.msg(thread_message=(thread_id,), embeds=[...]).
        """
        data = {}
        if kwargs.get("content"):
            data["content"] = str(kwargs["content"])[:2000]
        if kwargs.get("embeds"):
            data["embeds"] = kwargs["embeds"]
        if kwargs.get("components") is not None:
            data["components"] = kwargs["components"]
        if data:
            self._post_json(f"channels/{thread_id}/messages", data)

    def send_thread_update(self, thread_id, **kwargs):
        """
        PATCH a thread (rename, forum tags) without archiving.

        Use with session.msg(thread_update=(thread_id,), name=..., applied_tags=[...]).
        """
        data = {}
        if kwargs.get("name"):
            data["name"] = str(kwargs["name"])[:100]
        if kwargs.get("applied_tags") is not None:
            data["applied_tags"] = kwargs["applied_tags"]
        if data:
            self._post_json(f"channels/{thread_id}", data, type="PATCH")

    def send_thread_archive(self, thread_id, archived, **kwargs):
        """
        Archive or unarchive a thread via REST PATCH.

        Use with session.msg(thread_archive=(thread_id, True/False)).
        Optional kwargs: applied_tags (list of forum tag snowflakes).
        """
        data = {"archived": bool(archived)}
        if kwargs.get("applied_tags") is not None:
            data["applied_tags"] = kwargs["applied_tags"]
        self._post_json(
            f"channels/{thread_id}",
            data,
            type="PATCH",
        )

    def send_dm(self, user_id, text, **kwargs):
        """
        Send a direct message to a user: open (or reuse) their DM channel, then
        post. Use with session.msg(dm=(user_id, text)).
        """
        url = f"{DISCORD_API_BASE_URL}/users/@me/channels"
        body = json.dumps({"recipient_id": str(user_id)}).encode("utf-8")
        headers = {
            "User-Agent": DISCORD_USER_AGENT,
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }

        def cbResponse(response):
            if response.code in (200, 201):
                try:
                    channel_id = json.loads(response.content).get("id")
                except Exception:
                    return
                if channel_id:
                    self._post_json(
                        f"channels/{channel_id}/messages",
                        {"content": str(text)[:2000]},
                    )
            elif should_retry(response.code):
                delay(300, self.send_dm, user_id, text, **kwargs)

        http.request("POST", url, headers=headers, data=body).addCallback(cbResponse)

        d.addCallback(cbResponse)

    def send_default(self, *args, **kwargs):
        """
        Ignore other outputfuncs

        """
        pass

    def data_in(self, data, **kwargs):
        """
        Process incoming data from Discord and sent to the Evennia server

        Args:
            data (dict): Converted json data.

        """
        action_type = data.get("t", "UNKNOWN")

        if action_type == "MESSAGE_CREATE":
            # someone posted a message on Discord that the bot can see
            data = data["d"]
            if data["author"]["id"] == self.discord_id:
                # it's by the bot itself! disregard
                return
            if data.get("webhook_id"):
                # Webhook posts (including our own channel webhook fallback) must not
                # re-enter the game via BUS.emit — they are not player chat.
                return
            message = data["content"]
            channel_id = data["channel_id"]
            keywords = {"channel_id": channel_id}
            if "guild_id" in data:
                # message received to a Discord channel
                keywords["type"] = "channel"
                member = data.get("member") or {}
                author = member.get("nick") or data["author"]["username"]
                author_id = data["author"]["id"]
                keywords["sender"] = (author_id, author)
                keywords["guild_id"] = data["guild_id"]
                if member.get("roles"):
                    keywords["discord_member_role_ids"] = member["roles"]

            else:
                # message sent directly to the bot account via DM
                keywords["type"] = "direct"
                author = data["author"]["username"]
                author_id = data["author"]["id"]
                keywords["sender"] = (author_id, author)

            # pass the processed data to the server
            self.sessionhandler.data_in(self, bot_data_in=(message, keywords))

        elif action_type in ("GUILD_CREATE", "GUILD_UPDATE"):
            # we received the current status of a guild the bot is on; process relevant info
            data = data["d"]
            keywords = {"type": "guild", "guild_id": data["id"], "guild_name": data["name"]}
            keywords["channels"] = {
                chan["id"]: {"name": chan["name"], "guild": data["name"]}
                for chan in data["channels"]
                if chan["type"] == 0
            }
            # send the possibly-updated guild and channel data to the server
            self.sessionhandler.data_in(self, bot_data_in=("", keywords))

        elif "DELETE" in action_type:
            # deletes should possibly be handled separately to check for channel removal
            # for now, just ignore
            pass

        else:
            # send the data for any other action types on to the bot as-is for optional server-side handling
            keywords = {"type": action_type}
            inner = data.get("d")
            if isinstance(inner, dict):
                inner = dict(inner)
                # Discord payloads often have their own "type" integer key. Preserve
                # our routing string in keywords["type"] and expose the Discord payload
                # integer separately as keywords["interaction_type"].
                if "type" in inner:
                    keywords["interaction_type"] = inner.pop("type")
                keywords.update(inner)
            self.sessionhandler.data_in(self, bot_data_in=("", keywords))
