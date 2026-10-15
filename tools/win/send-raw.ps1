# Send a file as-is (RAW datatype) to a Windows print queue, bypassing the
# driver's rendering.
# NOTE: this does not work with v4-driver queues (like HP's): they push even
# RAW jobs through the XPS filter. Use usbprint.ps1 instead.
#
#   powershell -File send-raw.ps1 -File C:\path\job.prn [-Printer "HP Ink Tank 310 series"]
param(
    [string]$Printer = "HP Ink Tank 310 series",
    [Parameter(Mandatory = $true)][string]$File,
    [string]$DocName = "inktank raw"
)

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class RawPrinter {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public class DOCINFO {
        public string pDocName;
        public string pOutputFile;
        public string pDataType;
    }

    [DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool OpenPrinter(string name, out IntPtr h, IntPtr defaults);
    [DllImport("winspool.drv", SetLastError = true)]
    static extern bool ClosePrinter(IntPtr h);
    [DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern int StartDocPrinter(IntPtr h, int level, DOCINFO di);
    [DllImport("winspool.drv", SetLastError = true)]
    static extern bool EndDocPrinter(IntPtr h);
    [DllImport("winspool.drv", SetLastError = true)]
    static extern bool StartPagePrinter(IntPtr h);
    [DllImport("winspool.drv", SetLastError = true)]
    static extern bool EndPagePrinter(IntPtr h);
    [DllImport("winspool.drv", SetLastError = true)]
    static extern bool WritePrinter(IntPtr h, byte[] buf, int count, out int written);

    public static int Send(string printer, string docName, byte[] data) {
        IntPtr h;
        if (!OpenPrinter(printer, out h, IntPtr.Zero))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "OpenPrinter");
        try {
            var di = new DOCINFO { pDocName = docName, pDataType = "RAW" };
            int job = StartDocPrinter(h, 1, di);
            if (job == 0)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "StartDocPrinter");
            StartPagePrinter(h);
            int written;
            if (!WritePrinter(h, data, data.Length, out written) || written != data.Length)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "WritePrinter");
            EndPagePrinter(h);
            EndDocPrinter(h);
            return job;
        } finally {
            ClosePrinter(h);
        }
    }
}
"@

$data = [System.IO.File]::ReadAllBytes($File)
$job = [RawPrinter]::Send($Printer, $DocName, $data)
Write-Output "job $job sent to '$Printer' ($($data.Length) bytes)"
