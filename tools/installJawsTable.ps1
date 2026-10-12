<#
.SYNOPSIS
Installs BrlMultiline's U.S. English grade 2 braille table into JAWS.

.DESCRIPTION
Copies the table and the files it includes into the liblouis tables folder of each installed JAWS
version, and adds it to JAWS's list of braille translation modes (LiblouisTrans.xml) as
"U.S. English Grade 2 BrlMultiline".

The copied table builds on JAWS's own U.S. grade 2 table rather than stock liblouis, so JAWS keeps
its Freedom Scientific symbol definitions; BrlMultiline's rules come first and win for the
characters they name.

A JAWS update usually brings a new liblouis folder, which the table is not in. Run this again after
updating JAWS, and after changing braille substitutions in NVDA, to bring JAWS up to date. Running it
again is safe: files are replaced and the mode is added only once.

Writing under Program Files needs administrator rights; the script asks for them if it does not
have them. Restart JAWS afterwards, then choose the mode in JAWS's braille translation settings.

.PARAMETER Source
The folder holding en-us-g2-brlMultiline.utb and its .cti files. When left out, the first of these
that has them is used: the folder this script is in; the add-on's brailleTables folder in the
repository this script belongs to; the BrlMultiline add-on installed in NVDA for this user.

.PARAMETER JawsVersion
Only install into this JAWS version, such as 2026. By default every installed version is updated.

.PARAMETER Uninstall
Removes the table's files and its mode instead.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File installJawsTable.ps1
#>
[CmdletBinding()]
param(
	[string]$Source,
	[string]$JawsVersion,
	[switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$TableFile = 'en-us-g2-brlMultiline.utb'
$IncludedFiles = @(
	'en-us-g2-brlMultiline.cti',
	'en-us-g2-brlMultiline-sequences.cti',
	'brlMultiline-user.cti'
)
$DisplayName = 'U.S. English Grade 2 BrlMultiline'
# JAWS's own U.S. English Grade 2 mode: the new mode copies its settings and goes after it.
$BaseModeId = '20'
$StockInclude = 'include en-us-g2.ctb'
$FirstModeId = 900

function Test-Administrator {
	$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
	(New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
		[Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
	Write-Host 'Administrator rights are needed to change JAWS. Asking for them now.'
	$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
	foreach ($name in $PSBoundParameters.Keys) {
		$value = $PSBoundParameters[$name]
		if ($value -is [switch]) {
			if ($value) { $arguments += "-$name" }
		} else {
			$arguments += "-$name", "`"$value`""
		}
	}
	$shell = (Get-Process -Id $PID).Path
	try {
		$process = Start-Process -FilePath $shell -ArgumentList $arguments -Verb RunAs -Wait -PassThru
	} catch {
		Write-Host 'Administrator rights were not given, so JAWS was not changed.'
		exit 1
	}
	exit $process.ExitCode
}

# Everything after this runs elevated, in a window that closes when the script ends, so keep it open
# long enough to read what happened.
function Exit-Script([int]$code) {
	Write-Host ''
	Read-Host 'Press Enter to close'
	exit $code
}

function Find-Source {
	if ($Source) { return $Source }
	$candidates = @(
		$PSScriptRoot,
		(Join-Path $PSScriptRoot '..\addon\brailleTables'),
		(Join-Path $env:APPDATA 'nvda\addons\BrlMultiline\brailleTables')
	)
	foreach ($candidate in $candidates) {
		if (Test-Path (Join-Path $candidate $TableFile)) {
			return (Resolve-Path $candidate).Path
		}
	}
	return $null
}

function Write-TextFile([string]$path, [string]$text, [bool]$bom) {
	[IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $bom))
}

function Test-Bom([string]$path) {
	$bytes = [IO.File]::ReadAllBytes($path)
	$bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
}

# The newest liblouis folder of each JAWS version, which is the one its translator uses.
function Get-JawsTargets {
	$jawsRoot = Join-Path $env:ProgramFiles 'Freedom Scientific\JAWS'
	if (-not (Test-Path $jawsRoot)) { return @() }
	$versions = Get-ChildItem $jawsRoot -Directory
	if ($JawsVersion) { $versions = $versions | Where-Object Name -EQ $JawsVersion }
	foreach ($version in $versions) {
		$liblouis = Join-Path $version.FullName 'Liblouis'
		if (-not (Test-Path $liblouis)) { continue }
		$newest = Get-ChildItem $liblouis -Directory |
			Where-Object { $_.Name -as [version] -and (Test-Path (Join-Path $_.FullName 'LiblouisTrans.xml')) } |
			Sort-Object { [version]$_.Name } -Descending |
			Select-Object -First 1
		if ($newest) {
			[pscustomobject]@{
				Jaws = $version.Name
				Liblouis = $newest.Name
				Xml = Join-Path $newest.FullName 'LiblouisTrans.xml'
				Tables = Join-Path $newest.FullName 'tables'
			}
		}
	}
}

# The <Mode .../> element whose attribute has the value, or $null.
function Find-Mode([string]$xml, [string]$attribute, [string]$value) {
	$pattern = '<Mode\b[^>]*?\b' + $attribute + '\s*=\s*"' + [regex]::Escape($value) + '"[^>]*/>'
	$match = [regex]::Match($xml, $pattern, 'IgnoreCase')
	if ($match.Success) { return $match }
	return $null
}

function Get-Attribute([string]$element, [string]$name) {
	$match = [regex]::Match($element, '\b' + $name + '\s*=\s*"([^"]*)"')
	if ($match.Success) { return $match.Groups[1].Value }
	return $null
}

function Install-Into($target, [string]$sourceFolder) {
	$xml = [IO.File]::ReadAllText($target.Xml)
	$base = Find-Mode $xml 'Id' $BaseModeId

	# Build on the table JAWS's own grade 2 mode uses, so its symbol definitions stay.
	$baseTable = if ($base) { Get-Attribute $base.Value 'Table' }
	$table = [IO.File]::ReadAllText((Join-Path $sourceFolder $TableFile))
	if ($baseTable -and (Test-Path (Join-Path $target.Tables $baseTable))) {
		$table = $table -replace [regex]::Escape($StockInclude), "include $baseTable"
	}
	Write-TextFile (Join-Path $target.Tables $TableFile) $table $false
	foreach ($file in $IncludedFiles) {
		$from = Join-Path $sourceFolder $file
		$to = Join-Path $target.Tables $file
		if (Test-Path $from) {
			Copy-Item $from $to -Force
		} elseif ($file -eq 'brlMultiline-user.cti') {
			# Written by the add-on as NVDA runs; the table does not compile without it.
			Write-TextFile $to "# No braille substitutions were found when this was installed.`r`n" $false
			Write-Warning "$file was not in $sourceFolder, so JAWS gets no braille substitutions."
		} else {
			throw "$file is missing from $sourceFolder."
		}
	}

	if (Find-Mode $xml 'Table' $TableFile) {
		Write-Host "  The mode was already listed; files updated."
		return
	}
	if (-not $base) {
		throw "JAWS's U.S. English Grade 2 mode (Id $BaseModeId) was not found in $($target.Xml)."
	}
	$id = $FirstModeId
	while (Find-Mode $xml 'Id' "$id") { $id++ }
	$mode = $base.Value
	$mode = $mode -replace '\bId\s*=\s*"[^"]*"', "Id=`"$id`""
	$mode = $mode -replace '\bDisplayName\s*=\s*"[^"]*"', "DisplayName=`"$DisplayName`""
	$mode = $mode -replace '\bTable\s*=\s*"[^"]*"', "Table=`"$TableFile`""
	$indent = [regex]::Match($xml.Substring(0, $base.Index), '[ \t]*$').Value
	$updated = $xml.Insert($base.Index + $base.Length, "`r`n$indent<!-- Added by BrlMultiline's installJawsTable.ps1 -->`r`n$indent$mode")
	[void][xml]$updated  # Refuse to write a file JAWS could not read.
	$backup = "$($target.Xml).brlMultiline-backup"
	Copy-Item $target.Xml $backup -Force
	Write-TextFile $target.Xml $updated (Test-Bom $target.Xml)
	Write-Host "  Added the mode with Id $id. The previous file is saved as $(Split-Path $backup -Leaf)."
}

function Uninstall-From($target) {
	foreach ($file in @($TableFile) + $IncludedFiles) {
		$path = Join-Path $target.Tables $file
		if (Test-Path $path) { Remove-Item $path -Force }
	}
	$xml = [IO.File]::ReadAllText($target.Xml)
	$mode = Find-Mode $xml 'Table' $TableFile
	if (-not $mode) {
		Write-Host '  Files removed; the mode was not listed.'
		return
	}
	$pattern = '\r?\n[ \t]*<!-- Added by BrlMultiline''s installJawsTable\.ps1 -->(?=\r?\n[ \t]*' + [regex]::Escape($mode.Value) + ')'
	$updated = [regex]::Replace($xml, $pattern, '')
	$mode = Find-Mode $updated 'Table' $TableFile
	$start = $mode.Index
	while ($start -gt 0 -and " `t".Contains($updated[$start - 1])) { $start-- }
	if ($start -gt 0 -and $updated[$start - 1] -eq "`n") { $start-- }
	if ($start -gt 0 -and $updated[$start - 1] -eq "`r") { $start-- }
	$updated = $updated.Remove($start, $mode.Index + $mode.Length - $start)
	[void][xml]$updated
	Write-TextFile $target.Xml $updated (Test-Bom $target.Xml)
	Write-Host '  Files and mode removed.'
}

$targets = @(Get-JawsTargets)
if (-not $targets) {
	$which = if ($JawsVersion) { "JAWS $JawsVersion" } else { 'JAWS' }
	Write-Host "$which with liblouis was not found under $env:ProgramFiles\Freedom Scientific\JAWS."
	Exit-Script 1
}

$sourceFolder = $null
if (-not $Uninstall) {
	$sourceFolder = Find-Source
	if (-not $sourceFolder) {
		Write-Host "$TableFile was not found. Put this script next to the table files, install the"
		Write-Host 'BrlMultiline add-on in NVDA, or name the folder with -Source.'
		Exit-Script 1
	}
	Write-Host "Table files from $sourceFolder"
}

$failed = $false
foreach ($target in $targets) {
	Write-Host "JAWS $($target.Jaws), liblouis $($target.Liblouis):"
	try {
		if ($Uninstall) { Uninstall-From $target } else { Install-Into $target $sourceFolder }
	} catch {
		Write-Host "  Failed: $($_.Exception.Message)"
		$failed = $true
	}
}

if (-not $failed) {
	Write-Host ''
	if ($Uninstall) {
		Write-Host 'Done. Restart JAWS.'
	} else {
		Write-Host "Done. Restart JAWS, then choose `"$DisplayName`" as the braille translation."
	}
}
Exit-Script ([int]$failed)
