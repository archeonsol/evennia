# Client Support Grid

This grid tries to gather info about different MU clients when used with Evennia.
If you want to report a problem, update an entry or add a client, make a
new [documentation issue](github:issue) for it. Everyone's encouraged to report their findings.

## Client Grid

Legend:

 - **Name**: The name of the client. Also note if it's OS-specific.
 - **Version**: Which version or range of client versions were tested.
 - **Comments**: Any quirks on using this client with Evennia should be added here.


| Name | Version tested | Comments |
| --- | --- | --- |
| [Evennia Webclient][1]    | 1.0+      | Evennia-specific |
| [tintin++][2]             |   2.0+    | No MXP support  |
| [tinyfugue][3]            | 5.0+      | No UTF-8 support                                               |
| [MUSHclient][4] (Win)     | 4.94      | NAWS reports full text area                                    |
| [Zmud][5] (Win)           | 7.21      | *UNTESTED*                                                     |
| [Cmud][6] (Win)           | v3        | *UNTESTED*                                                     |
| [Potato][7]              | 2.0.0b16  | No MXP, MCCP support. Win 32bit does not understand            |
|                           |           | "localhost", must use `127.0.0.1`.                             |
| [Mudlet][8]               | 3.4+      | No known issues. Some older versions showed <> as html         |
|                           |           | under MXP.                                                     |
| [SimpleMU][9] (Win)       | full      | Discontinued. NAWS reports pixel size.                         |
| [Atlantis][10] (Mac)      | 0.9.9.4   | No known issues.                                               |
| [GMUD][11]                | 0.0.1     | Can't handle any telnet handshakes. Not recommended.          |
| [BeipMU][12] (Win)        | 3.0.255   | No MXP support. Best to enable "MUD prompt handling", disable  |
|                           |           | "Handle HTML tags".                                            |
| [MudRammer][13] (IOS)     | 1.8.7     | Bad Telnet Protocol compliance: displays spurious characters.  |
| [MUDMaster][14]           | 1.3.1     | *UNTESTED*                                                     |
| [BlowTorch][15] (Andr)    | 1.1.3     | Telnet NOP displays as spurious character.                     |
| [Mukluk][16] (Andr)       | 2015.11.20| Telnet NOP displays as spurious character. Has UTF-8/Emoji     |
|                           |           | support.                                                       |
| [Gnome-MUD][17] (Unix)    | 0.11.2    | Telnet handshake errors. First (only) attempt at logging in    |
|                           |           | fails.                                                         |
| [Spyrit][18]              | 0.4       | No MXP, OOB support.                                           |
| [JamochaMUD][19]          | 5.2       | Does not support ANSI within MXP text.                         |
| [DuckClient][20] (Chrome) | 4.2       | No MXP support. Displays Telnet Go-Ahead and                   |
|                           |           | WILL SUPPRESS-GO-AHEAD as ù character. Also seems to run       |
|                           |           | the `version` command on connection, which will not work in    |
|                           |           | `MULTISESSION_MODES` above 1.                                  |
| [KildClient][21]          | 2.11.1    | No known issues.                                               |


[1]: ../Components/Webclient
[2]: http://tintin.sourceforge.net/
[3]: http://tinyfugue.sourceforge.net/
[4]: https://mushclient.com/
[5]: http://forums.zuggsoft.com/index.php?page=4&action=file&file_id=65
[6]: http://forums.zuggsoft.com/index.php?page=4&action=category&cat_id=11
[7]: https://www.potatomushclient.com/
[8]: https://www.mudlet.org/
[9]: https://archive.org/details/tucows_196173_SimpleMU_MU_Client
[10]: https://www.riverdark.net/atlantis/
[11]: https://sourceforge.net/projects/g-mud/
[12]: http://www.beipmu.com/
[13]: https://itunes.apple.com/us/app/mudrammer-a-modern-mud-client/id597157072
[14]: https://itunes.apple.com/us/app/mudmaster/id341160033
[15]: https://bt.happygoatstudios.com/
[16]: https://play.google.com/store/apps/details?id=com.crap.mukluk
[17]: https://github.com/GNOME/gnome-mud
[18]: https://spyrit.ierne.eu.org/
[19]: https://jamochamud.org/
[20]: http://duckclient.com/
[21]: https://www.kildclient.org/

## Workarounds for client issues:

### Issue: Telnet NOP displays as spurious character.

Known clients:

* BlowTorch (Andr)
* Mukluk (Andr)

Workaround:

* In-game: Use `@option NOPKEEPALIVE=off` for the session, or use the `/save`
parameter to disable it for that Evennia account permanently.
* Client-side: Set a gag-type trigger on the NOP character to make it invisible to the client.

### Issue: Box-drawing lines, bars and arrows show as `?` diamonds or garbage.

Game text is Unicode and goes out as UTF-8. A client decoding in ASCII or
Latin-1 shows each multi-byte character as replacement diamonds (Mudlet) or
mojibake such as `â”€` (most others).

What the server does about it:

* On connect the portal offers UTF-8 through the telnet CHARSET option
  (RFC 2066). A client that accepts switches its own decoding and gets full
  Unicode; Mudlet and tintin++ do this with no setup. A client that reports the
  UTF-8 bit in MTTS is treated the same way.
* A client that never confirms UTF-8 gets text folded to plain characters, one
  for one so tables stay aligned: `─` is `-`, `│` is `|`, corners are `+`,
  `█` is `#`, `→` is `>`, `é` is `e`, and anything with no equivalent is `?`
  (`evennia.utils.textfold`). A player who chose a legacy encoding with
  `@option ENCODING` (such as `cp437`) keeps every character it can carry.
  Turn this off with `TELNET_ASCII_FALLBACK = False`.

Workaround for a client that decodes UTF-8 but does not say so:

* In-game: `@option UTF-8=on` for the session, or `@option/save UTF-8=on` to
  keep it for the account.
* Client-side: set the client's encoding to UTF-8 (MUSHclient: "UTF-8
  (Unicode)" in the world's output settings).
