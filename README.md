# inktank-ipp

Experimental Python driver for the HP Ink Tank 315 ("Ink Tank 310 series"
family, USB 03F0:1053), plus an IPP Everywhere server so any device on the
local network can print to it without installing a driver.

It runs on Windows 11 today (developed from WSL); a Raspberry Pi is a future
target.

```
[PC / phone] --IPP (network)--> [inktank serve] --USB--> Ink Tank 315
                                 ├─ IPP server + mDNS advertisement
                                 ├─ PWG Raster / URF / JPEG / PNG -> PCL3GUI
                                 └─ direct USB write (usbprint.sys)
```

## What we know about the protocol

- Language: **PCL3GUI** wrapped in PJL (`@PJL ENTER LANGUAGE=PCL3GUI`).
- 24-bit RGB raster, 600 dpi, **Mode 10** (Bert) compression, one row per
  `ESC*b#W`; blank rows are skipped with `ESC*b#Y`.
- Sources: HPLIP (`prnt/hpcups/Pcl3Gui2.cpp`, `Mode10.cpp`, family
  `P15_CISS` in `hpcups.drv`) and the real output of the Windows v4 driver
  (`captures/hp-ref-color.prn`), which matches HPLIP except for header
  details (11000 leading zero bytes, paper subtype 1004, raster width 5030).
- The raster origin is the printable area (70 px margins at 600 dpi).
- The Windows queue uses a v4 driver: even RAW jobs go through HP's XPS
  filter and fail (0x8051100F). That is why we write straight to the device
  through `usbprint.sys` (`inktank/usb_windows.py`, `tools/win/usbprint.ps1`).

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
uv run inktank inspect captures/x.prn --png out/x # list commands and render pages
uv run inktank usbpcap capture.pcapng --out out/c # extract bulk data from USBPcap
uv run inktank print job.prn|photo.png            # (Windows) straight over USB
uv run inktank device-id                          # (Windows) IEEE 1284 Device ID
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

## To do

- Printer status (ink, paper, errors): the driver uses
  `hpgid25v4-bidiUSB.js`; USB interface MI_03 "(REST)" looks like LEDM.
- Run on a Raspberry Pi (systemd).
- PDF input.
