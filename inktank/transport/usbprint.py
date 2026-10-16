"""Direct printer access through usbprint.sys on Windows (ctypes, no spooler).

The Windows queue uses a v4 driver that pushes everything through HP's XPS
filter, even RAW jobs, so we write straight to the device instead.
"""

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("usbprint only works on Windows")

HP_INK_TANK_310 = "vid_03f0&pid_1053"

_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


GUID_DEVINTERFACE_USBPRINT = GUID(0x28D78FAD, 0x5A12, 0x11D1,
                                  (ctypes.c_ubyte * 8)(0xAE, 0x5B, 0x00, 0x00, 0xF8, 0x03, 0xA8, 0xC2))
DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
IOCTL_USBPRINT_GET_1284_ID = 0x220034

_setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
_setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
_setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                  wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
_setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                       ctypes.c_void_p, wintypes.DWORD,
                                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                      ctypes.c_void_p]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _error(what: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{what}: {ctypes.FormatError(code)}")


def list_devices() -> list[str]:
    """Paths of every usbprint device present."""
    hset = _setupapi.SetupDiGetClassDevsW(ctypes.byref(GUID_DEVINTERFACE_USBPRINT), None, None,
                                          DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hset == INVALID_HANDLE_VALUE:
        raise _error("SetupDiGetClassDevs")
    paths = []
    try:
        i = 0
        while True:
            data = SP_DEVICE_INTERFACE_DATA(cbSize=ctypes.sizeof(SP_DEVICE_INTERFACE_DATA))
            if not _setupapi.SetupDiEnumDeviceInterfaces(hset, None, ctypes.byref(GUID_DEVINTERFACE_USBPRINT),
                                                         i, ctypes.byref(data)):
                break
            needed = wintypes.DWORD()
            _setupapi.SetupDiGetDeviceInterfaceDetailW(hset, ctypes.byref(data), None, 0,
                                                       ctypes.byref(needed), None)
            buf = ctypes.create_string_buffer(needed.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 on 64-bit, 6 on 32-bit.
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if _setupapi.SetupDiGetDeviceInterfaceDetailW(hset, ctypes.byref(data), buf, needed,
                                                          ctypes.byref(needed), None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
            i += 1
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(hset)
    return paths


def find_device(match: str = HP_INK_TANK_310) -> str:
    for path in list_devices():
        if match in path.lower():
            return path
    raise FileNotFoundError(f"no usbprint device matching {match!r}")


class UsbPrinter:
    def __init__(self, path: str | None = None):
        self.path = path or find_device()
        self.handle = _kernel32.CreateFileW(self.path, GENERIC_READ | GENERIC_WRITE,
                                            FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                            OPEN_EXISTING, 0, None)
        if self.handle == INVALID_HANDLE_VALUE:
            raise _error(f"CreateFile {self.path}")

    def close(self):
        if self.handle:
            _kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def write(self, data: bytes, chunk: int = 64 * 1024) -> None:
        view = memoryview(data)
        written = wintypes.DWORD()
        for off in range(0, len(view), chunk):
            part = bytes(view[off:off + chunk])
            if not _kernel32.WriteFile(self.handle, part, len(part), ctypes.byref(written), None):
                raise _error("WriteFile")
            if written.value != len(part):
                raise OSError(f"WriteFile wrote {written.value} of {len(part)} bytes")

    def device_id(self) -> str:
        """IEEE 1284 Device ID (includes the S: status field)."""
        buf = ctypes.create_string_buffer(1024)
        ret = wintypes.DWORD()
        if not _kernel32.DeviceIoControl(self.handle, IOCTL_USBPRINT_GET_1284_ID, None, 0,
                                         buf, len(buf), ctypes.byref(ret), None):
            raise _error("IOCTL_USBPRINT_GET_1284_ID")
        # The first two bytes are the big-endian length.
        return buf.raw[2:ret.value].decode("ascii", "replace")
