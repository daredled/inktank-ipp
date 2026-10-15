# Direct printer access through usbprint.sys, bypassing the spooler.
# (v4-driver queues push everything through the XPS filter, even RAW jobs.)
#
#   powershell -File usbprint.ps1 -List
#   powershell -File usbprint.ps1 -File C:\path\job.prn [-Match "vid_03f0&pid_1053"]
#   powershell -File usbprint.ps1 -DeviceId      # read the IEEE 1284 Device ID
param(
    [string]$File,
    [string]$Match = "vid_03f0&pid_1053",
    [switch]$List,
    [switch]$DeviceId
)

Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class UsbPrint {
    static Guid GUID_DEVINTERFACE_USBPRINT = new Guid("28d78fad-5a12-11d1-ae5b-0000f803a8c2");
    const int DIGCF_PRESENT = 0x2, DIGCF_DEVICEINTERFACE = 0x10;
    const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000;
    const uint FILE_SHARE_READ = 1, FILE_SHARE_WRITE = 2, OPEN_EXISTING = 3;
    // IOCTL_USBPRINT_GET_1284_ID = CTL_CODE(FILE_DEVICE_UNKNOWN, 13, METHOD_BUFFERED, FILE_ANY_ACCESS)
    const uint IOCTL_USBPRINT_GET_1284_ID = 0x220034;

    [StructLayout(LayoutKind.Sequential)]
    struct SP_DEVICE_INTERFACE_DATA { public int cbSize; public Guid cls; public int flags; public IntPtr reserved; }

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern IntPtr SetupDiGetClassDevs(ref Guid g, IntPtr e, IntPtr w, int flags);
    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiEnumDeviceInterfaces(IntPtr s, IntPtr d, ref Guid g, int i, ref SP_DEVICE_INTERFACE_DATA data);
    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetupDiGetDeviceInterfaceDetail(IntPtr s, ref SP_DEVICE_INTERFACE_DATA data, IntPtr detail, int size, out int req, IntPtr info);
    [DllImport("setupapi.dll")]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr s);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tmpl);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool DeviceIoControl(SafeFileHandle h, uint code, IntPtr inBuf, int inLen, byte[] outBuf, int outLen, out int ret, IntPtr ov);

    public static List<string> Devices() {
        var list = new List<string>();
        IntPtr set = SetupDiGetClassDevs(ref GUID_DEVINTERFACE_USBPRINT, IntPtr.Zero, IntPtr.Zero, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
        try {
            for (int i = 0; ; i++) {
                var d = new SP_DEVICE_INTERFACE_DATA();
                d.cbSize = Marshal.SizeOf(d);
                if (!SetupDiEnumDeviceInterfaces(set, IntPtr.Zero, ref GUID_DEVINTERFACE_USBPRINT, i, ref d)) break;
                int req;
                SetupDiGetDeviceInterfaceDetail(set, ref d, IntPtr.Zero, 0, out req, IntPtr.Zero);
                IntPtr buf = Marshal.AllocHGlobal(req);
                try {
                    Marshal.WriteInt32(buf, IntPtr.Size == 8 ? 8 : 6);
                    if (SetupDiGetDeviceInterfaceDetail(set, ref d, buf, req, out req, IntPtr.Zero))
                        list.Add(Marshal.PtrToStringUni(buf + 4));
                } finally { Marshal.FreeHGlobal(buf); }
            }
        } finally { SetupDiDestroyDeviceInfoList(set); }
        return list;
    }

    static SafeFileHandle Open(string path) {
        var h = CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
        if (h.IsInvalid) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "CreateFile " + path);
        return h;
    }

    public static string DeviceId(string path) {
        using (var h = Open(path)) {
            var buf = new byte[1024];
            int ret;
            if (!DeviceIoControl(h, IOCTL_USBPRINT_GET_1284_ID, IntPtr.Zero, 0, buf, buf.Length, out ret, IntPtr.Zero))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "GET_1284_ID");
            // The first two bytes are the big-endian length.
            return System.Text.Encoding.ASCII.GetString(buf, 2, Math.Max(0, ret - 2));
        }
    }

    public static void Send(string path, byte[] data) {
        using (var h = Open(path))
        using (var fs = new FileStream(h, FileAccess.ReadWrite, 1)) {
            const int chunk = 16384;
            for (int off = 0; off < data.Length; off += chunk)
                fs.Write(data, off, Math.Min(chunk, data.Length - off));
            fs.Flush();
        }
    }
}
"@

$devices = [UsbPrint]::Devices()
if ($List) { $devices; return }
$path = $devices | Where-Object { $_ -like "*$Match*" } | Select-Object -First 1
if (-not $path) { throw "no usbprint device matching '$Match'. Devices: $($devices -join ', ')" }
if ($DeviceId) { [UsbPrint]::DeviceId($path); return }
$data = [System.IO.File]::ReadAllBytes($File)
[UsbPrint]::Send($path, $data)
Write-Output "sent $($data.Length) bytes to $path"
