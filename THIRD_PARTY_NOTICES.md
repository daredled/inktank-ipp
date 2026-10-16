# Third-party notices

## HPLIP (hpcups)

`inktank/mode10.py` (Mode 10 / Bert compression) and `inktank/pcl3gui.py`
(PCL3GUI job and page headers) are adapted from HPLIP's hpcups filter,
in particular `prnt/hpcups/Mode10.cpp`, `Pcl3Gui2.cpp`, `Encapsulator.cpp`
and `PrinterCommands.h`. Those files carry the following license:

```
Copyright (c) 1996 - 2015, HP Co.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:
1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.
3. Neither the name of HP nor the names of its
   contributors may be used to endorse or promote products derived
   from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR "AS IS" AND ANY EXPRESS OR IMPLIED
WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN
NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PATENT INFRINGEMENT; PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## libusb, pyusb

Scanning uses pyusb (BSD 3-Clause) and the `libusb-package` wheel, which ships
a prebuilt libusb 1.0 shared library (LGPL-2.1-or-later,
https://libusb.info). They are installed as dependencies, loaded dynamically
and not included in this repository.

## WinSW

`tools/win/install-service.ps1` downloads WinSW v2.12.0
(https://github.com/winsw/winsw), MIT License. It is not included in this
repository.
