# Print a test page with the official HP driver, but to a file: the result is
# exactly the PCL stream the driver would send over USB. Nothing reaches the
# printer. Without -Output it prints normally to the given queue.
#
#   powershell -File hp-print-to-file.ps1 -Output C:\Users\Public\hp-ref.prn [-Grayscale]
#   powershell -File hp-print-to-file.ps1 -Printer "HP Ink Tank 315 (inktank)"
param(
    [string]$Printer = "HP Ink Tank 310 series",
    [string]$Output,
    [switch]$Grayscale
)
Add-Type -AssemblyName System.Drawing

$doc = New-Object System.Drawing.Printing.PrintDocument
$doc.PrinterSettings.PrinterName = $Printer
if ($Output) {
    $doc.PrinterSettings.PrintToFile = $true
    $doc.PrinterSettings.PrintFileName = $Output
}
$doc.DocumentName = "inktank reference"
$doc.DefaultPageSettings.Color = -not $Grayscale
$doc.PrintController = New-Object System.Drawing.Printing.StandardPrintController

$doc.add_PrintPage({
    param($sender, $e)
    $g = $e.Graphics
    $g.PageUnit = [System.Drawing.GraphicsUnit]::Inch
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::Black), 0.02
    $b = $e.MarginBounds
    $g.DrawRectangle($pen, 0.25, 0.25, 8.0, 10.5)
    $colors = "Black", "Cyan", "Magenta", "Yellow", "Red", "Lime", "Blue", "Gray"
    for ($i = 0; $i -lt $colors.Count; $i++) {
        $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromName($colors[$i]))
        $g.FillRectangle($brush, 1.0 + $i * 0.8, 1.0, 0.75, 1.0)
    }
    $font = New-Object System.Drawing.Font "Arial", 24
    $g.DrawString("inktank - HP reference", $font, [System.Drawing.Brushes]::Black, 1.0, 3.0)
    $e.HasMorePages = $false
})
$doc.Print()
Write-Output "done: $(if ($Output) { $Output } else { $Printer })"
