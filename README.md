# inktank-ipp

Experimental Python driver for the HP Ink Tank 315 ("Ink Tank 310 series"
family, USB 03F0:1053), plus an IPP Everywhere and eSCL server so any device
on the local network can print and scan without installing a driver.

It runs on Windows 11 today (developed from WSL); a Raspberry Pi is a future
target.

```
[PC / phone] --IPP/eSCL (network)--> [inktank serve] --USB--> Ink Tank 315
                                      ├─ IPP + eSCL server, mDNS advertisement
                                      ├─ PWG Raster / URF / JPEG / PNG -> PCL3GUI
                                      ├─ print: direct USB write (usbprint.sys, MI_01)
                                      └─ scan: HTTP over USB (libusb/WinUSB, MI_02)
```

## What we know about the protocol

- Language: **PCL3GUI** wrapped in PJL (`@PJL ENTER LANGUAGE=PCL3GUI`).
- 24-bit RGB raster, 600 dpi, **Mode 10** (Bert) compression, one row per
  `ESC*b#W`; blank rows are skipped with `ESC*b#Y`.
- Sources: HPLIP (`prnt/hpcups/Pcl3Gui2.cpp`, `Mode10.cpp`, family
  `P15_CISS` in `hpcups.drv`) and the real output of the Windows v4 driver
  (see `tools/win/hp-print-to-file.ps1`), which matches HPLIP except for
  header details (11000 leading zero bytes, paper subtype 1004, raster width
  5030). Captures are kept locally in `captures/`, which is git-ignored.
- The raster origin is the printable area (70 px margins at 600 dpi).
- The Windows queue uses a v4 driver: even RAW jobs go through HP's XPS
  filter and fail (0x8051100F). That is why we write straight to the device
  through `usbprint.sys` (`inktank/transport/usbprint.py`, `tools/win/usbprint.ps1`).
- The USB device has four interfaces: MI_00 HP's scanner driver (WIA),
  MI_01 the printer (class 7), MI_02 and MI_03 vendor class ff/04/01 "REST".
  MI_02 is bound to WinUSB and carries plain HTTP/1.1 to the printer's
  built-in web server, which speaks **eSCL** natively (`/eSCL/*`: platen
  only, 75-600 dpi, JPEG/PDF) and LEDM (`/DevMgmt/*`).

## Code layout

| Module | Role |
|---|---|
| `pcl3gui.py`, `mode10.py`, `raster.py` | Driver: PCL3GUI encoder, Mode 10 compression, PWG/URF decoding |
| `printing.py` | Document -> PCL3GUI job, and sinks (USB or `.prn` files) |
| `scanning.py` | One-page scan straight over USB (`inktank scan`) |
| `transport/usbprint.py` | Printing through `usbprint.sys` (Windows only) |
| `transport/usb_http.py` | HTTP over USB interface 2 via libusb (eSCL, LEDM) |
| `ipp.py`, `ipp_server.py` | IPP encoding and the IPP Everywhere printer (jobs, attributes) |
| `escl_server.py` | eSCL proxy for network scanning |
| `mdns.py` | DNS-SD advertisement |
| `server.py` | `inktank serve`: puts the above on one port |
| `pclparse.py`, `usbpcap.py` | Analysis tools (inspect `.prn` files, USB captures) |

Only `transport/usbprint.py` is tied to Windows; a Linux port (Raspberry Pi)
needs another print transport (e.g. `/dev/usb/lp0`) and nothing else.

## Setup

Uses [uv](https://docs.astral.sh/uv/) and Python 3.14. On WSL/Linux just run
`uv sync`. On Windows, keep the virtual environment outside the WSL tree:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\inktank\venv"
cd \\wsl.localhost\<distro>\home\<user>\code\impresora   # or wherever the repo lives
uv sync
uv run inktank device-id
```

## Network printing (IPP Everywhere)

```powershell
uv run inktank serve            # port 8631, advertised over mDNS
uv run inktank serve --to-dir C:\Users\Public\inktank-jobs   # save .prn files instead of printing
```

The first time, run `tools/win/setup-admin.ps1` from an elevated PowerShell
or cmd. It removes the firewall block rules for uv's `python.exe`, opens TCP
8631 and UDP 5353 on private networks, and with `-AddLocalPrinter` adds the
printer on the same PC:

```powershell
powershell -ExecutionPolicy Bypass -File tools\win\setup-admin.ps1 -AddLocalPrinter
```

On other devices the printer shows up as **HP Ink Tank 315 (inktank)**. If it
does not, add it by hand as an IPP device at `http://<server-ip>:8631/ipp/print`.
A status page with recent jobs is at `http://<server-ip>:8631/`.

Supported formats: `image/pwg-raster` (Windows, Android/Mopria), `image/urf`
(AirPrint), `image/jpeg`, `image/png`. Paper: Letter and A4.

## Network scanning (eSCL)

`inktank serve` also shares the scanner: it forwards every `/eSCL/*` request
to the printer over USB (`inktank/escl_server.py`) and advertises `_uscan._tcp` over
mDNS on the same port. Windows 11, macOS, iOS, Android and Linux
(sane-airscan) find it as **HP Ink Tank 315 (inktank)** with no driver; by
hand, the eSCL root is `http://<server-ip>:8631/eSCL`. Use `--no-scanner` to
turn it off.

Only one page at a time from the glass (no feeder or duplex), Letter or A4,
up to 600 dpi, JPEG or PDF.

## Windows service

`tools/win/install-service.ps1` (elevated) deploys the code to
`C:\Program Files\inktank` with its own Python and virtual environment and
registers the `inktank` service through [WinSW](https://github.com/winsw/winsw)
v2.12.0 (pinned by SHA-256). The service runs as LocalSystem, starts
automatically and restarts on failure.

```powershell
powershell -ExecutionPolicy Bypass -File tools\win\install-service.ps1             # install or update
powershell -ExecutionPolicy Bypass -File tools\win\install-service.ps1 -Uninstall  # remove
```

Logs live in `C:\Program Files\inktank\logs` and roll at 10 MB (5 files kept):

- `inktank-service.out.log`: server log (startup, requests, jobs, errors)
- `inktank-service.err.log`: only uncaught crashes
- `inktank-service.wrapper.log`: WinSW events (start, stop, restarts)

```powershell
Get-Content 'C:\Program Files\inktank\logs\inktank-service.out.log' -Tail 20 -Wait
```

## Command-line tools

```bash
uv run inktank testpage out/testpage.prn          # test page
uv run inktank image photo.png out/photo.prn      # image -> PCL3GUI
uv run inktank inspect job.prn --png out/job      # list commands and render pages
uv run inktank usbpcap capture.pcapng --out out/c # extract bulk data from USBPcap
uv run inktank print job.prn|photo.png            # (Windows) straight over USB
uv run inktank device-id                          # (Windows) IEEE 1284 Device ID
uv run inktank scan page.pdf --dpi 300 --mode gray # (Windows/Linux) scan straight over USB
```

Reference output from the official driver (prints to a file, not to paper):

```bash
powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w tools/win/hp-print-to-file.ps1)" -Output 'C:\Users\Public\hp-ref.prn'
```

## Tests

```bash
uv run pytest
# Against HPLIP's original encoder (Mode10.cpp built with -DAPDK_LITTLE_ENDIAN):
uv run python tests/check_against_hplip.py /path/to/harness
```

## License

MIT, see [LICENSE](LICENSE). `inktank/mode10.py` and `inktank/pcl3gui.py`
are adapted from HPLIP's hpcups (BSD 3-Clause, HP); see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), which also covers the
libusb DLL that the `libusb-package` dependency installs.

## To do

- Printer status (ink, paper, errors): LEDM answers over the same USB HTTP
  channel (`/DevMgmt/ProductStatusDyn.xml`, `ConsumableConfigDyn.xml`).
- Run on a Raspberry Pi (systemd).
- PDF input.
