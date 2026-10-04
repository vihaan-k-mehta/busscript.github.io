# Busscript

A local CAN bus tool you can script: REST and an MCP server alongside a live UI. Live trace, DBC signal decoding, graphing, bus statistics, logging (ASC/BLF), replay, transmit, filters. Python core (python-can, cantools) with a React UI, all on `127.0.0.1`.

## Run it

Busscript is for **Windows**. You need **Python 3.11 or newer** from python.org (tick "Add python.exe to PATH"). Tested on Windows 11 with Python 3.12.

**Easiest:** download `Busscript.exe` from the Releases page and double-click it. No Python needed. Windows may warn that the publisher is unknown because the program is not code-signed (More info, then Run anyway).

**From source (for developers):** the steps below. They need Python.

1. Download the zip and use *Extract All* (do not run it from inside the zip).
2. Double-click `start.bat`.
3. The first run sets up a private environment and installs the libraries (about a minute, needs internet, once). Then your browser opens on a demo bus with synthetic traffic, so you can try everything without hardware.

To use your own adapter press **Use my own adapter** in the demo banner and add a channel on the Setup tab. After you have added a channel, Busscript starts on your channels instead of the demo. `start.bat live` never shows the demo, `start.bat demo` always does.

Build the exe yourself with `pip install pyinstaller` then `python scripts/make_exe.py` (needs the built UI). Node.js is not needed: the UI is already built (`ui/dist`).

Open the link that is printed in the window; it contains your access token.

The first run downloads about 150 MB of libraries, mostly the ones that read MF4 files, so give it a few minutes.

## Open a recording

Press **Open file** (or drag a file onto the window). Busscript opens MF4 (MDF4), ASC, BLF, candump `.log`, PEAK `.trc`, CSV and SQLite logs, and database files (DBC, KCD, SYM, ARXML, CDD). Drop a DBC in as well and every signal in the recording gets its name. In the file view you can filter by message, jump to a time, and plot a signal across the whole recording. Try the included samples in `samples/` (`demo_drive.mf4`, `.asc`, `.blf`, with `demo.dbc`). Pressing Start closes the file and goes back to live data.

MF4 files must contain recorded CAN frames. Files that hold only already-decoded signals are not supported. MF4 was tested with files Busscript itself wrote, not with files from other loggers. Recordings are loaded into memory, up to one million frames.

## Check my setup and diagnostics

The Setup tab starts with **Check my setup**: it lists the libraries Busscript needs (installed for you by `start.bat`) and which adapter drivers are on this PC (PEAK PCAN, Vector, Kvaser, IXXAT, National Instruments, Intrepid, SYS TEC). Driver packages come from the adapter maker and cannot be bundled; the card says which one to install. Serial adapters (SLCAN, CANable, Arduino) need no driver from us.

The **Diagnostics** tab turns car and truck protocols into plain words: OBD-II (engine speed, coolant temperature, stored fault codes), UDS (read fault codes with their status, refusals with the reason) over ISO-TP including multi-frame messages, and J1939 message names. Selecting a frame in the Trace also shows what it means. Try `samples/demo_diag.asc`. Not included: flashing, security-key calculation, LIN, XCP/A2L, FIBEX.

## Analyze: statistics, search and save

The **Analyze** tab has three tools. The **statistics report** shows, for every message, how many frames there were and how regular they are (average spacing, spread, shortest, longest), for the open file or the live bus. **Find a frame** searches an open recording with a plain condition such as `id > 0x100 and d0 == 5` (id, dlc, time, dir, error, d0 to d63; join with `and` or `or`). **Save or convert** writes the data as CSV, ASC, BLF or MF4, only the frames you want, or only the time around frames that match a condition (a few seconds before and after each one, or from one condition to another). It can also write chosen signals to a spreadsheet table. Saved files can be downloaded from the same page. The Data pane also shows the lowest and highest value each signal reached, so a very short spike is not missed.

## Scripts

The **Scripts** tab runs a short list of steps for you, one per line, with no programming needed. Pick an example, press Run, and read the results beside it:

```
every 100ms for 5s
  send VehicleSpeed Speed=42.5
end
print Done
```

Commands: `send`, `sendraw`, `wait`, `wait until`, `repeat`, `every`, `wave` (a signal that rises and falls by itself), `log start` / `log stop`, `print`, `start`, `stop`. Mistakes are explained with their line number. Scripts cannot run arbitrary code, they obey the same listen-only rule as everything else, and stop on their own after 30 minutes.

Panes are resizable: drag a divider, or focus it and use the arrow keys (Shift for bigger steps, Home and End for the limits, Enter or double-click to reset). Sizes are remembered in the browser; the toolbar has Reset layout.

## MCP

Settings -> MCP: switch it on, copy the client config. Endpoint `http://127.0.0.1:8765/mcp` with `Authorization: Bearer <token>`, or `python -m core --mcp-stdio`.

Tools: `list_channels`, `load_dbc`, `start_measurement`, `stop_measurement`, `get_recent_frames`, `read_signal`, `get_statistics`, `start_logging`, `stop_logging`, `start_replay`, `stop_replay`, `send_frame`, `send_signal`, and for recordings `list_files`, `open_file`, `file_overview`, `file_signal`.

Safety: off until you enable it; each tool can be switched off; `send_frame` and `send_signal` are off by default (MCP transmit is a separate switch, rate-limited to 20 frames per second) and channels are listen-only by default; files must live in the data directory (`~/busscript-data`, or `BUSSCRIPT_DATA`); every call shows in the activity log. Bus data is returned as structured JSON and is untrusted: other devices control it.

## Automate it

Everything the UI does is a REST call. `examples/automate.py` starts a measurement, reads a signal by name, sends a signal by name, records a log and stops (standard library only). The same from curl:

```bash
TOKEN=$(cat ~/busscript-data/token)       # or $BUSSCRIPT_DATA/token
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/measurement/start
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"   -d '{"channel":"demo","message":"EngineData","signals":{"Gear":"Second","ThrottlePos":20}}'   http://127.0.0.1:8765/api/transmit/signal
```

Signals not named go out at their initial value (or 0); values outside the database range or unknown choice names are refused. The channel must not be listen-only.

## Tests

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt   # once
.venv/Scripts/python -m pytest -q tests
```

Released under the MIT licence (see `LICENSE`). Third-party libraries keep their own licences; python-can and asammdf (the MF4 reader) are LGPL-3.0.
