# Build the Windows one-folder distribution: dist\DataConverterTool\DataConverterTool.exe
#
# WHY EVERY NATIVE CALL GOES THROUGH Invoke-Native
#
#   1) CONDITIONAL defect: Windows PowerShell 5.1 turns a native command's stderr
#      output into a NativeCommandError error record. With $ErrorActionPreference
#      = "Stop" that record becomes a *terminating* error, so the first stderr line
#      printed by PyInstaller aborts the whole build. Measured on PS 5.1.26100.9444:
#        - a command that writes to stderr and exits 0, called as
#          '& cmd 2>&1 | Tee-Object' under Stop, TERMINATES (RemoteException) while
#          $LASTEXITCODE is still 0;
#        - the same form with a FAILING command (exit 5) terminates AND leaves
#          $LASTEXITCODE = -1, i.e. the real exit code is destroyed.
#      Invoke-Native lowers ErrorActionPreference for the duration of the native
#      call, tees the output into a log file, and then re-checks $LASTEXITCODE
#      explicitly -- so stderr is captured, the REAL exit code survives, and
#      "fail fast" is preserved. It only bites when a command actually writes to
#      stderr, hence conditional.
#
#   2) UNCONDITIONAL defect: the original script never inspected $LASTEXITCODE at
#      all, so ANY PyInstaller failure still printed "Build finished." and exited 0
#      while dist\DataConverterTool silently kept the PREVIOUS build's exe -- a
#      false green that no amount of stderr handling would have caught.
#
#   3) The build is STAGED. PyInstaller writes into dist\.staging, the result is
#      verified, and only then is dist\DataConverterTool swapped in. The old build
#      is moved aside to dist\.prev-DataConverterTool and, if the swap fails, the
#      script rolls it back. When promotion fails, any half-written
#      dist\DataConverterTool is first moved aside to DataConverterTool.partial so
#      the restore stays a plain directory rename; that .partial path may survive a
#      failed build (a stale one is cleared on the next failed promotion). A failed
#      build must never destroy a previously good distribution: deleting the old
#      output up front turns "build failed" into "user lost their only working exe".
#
# Logs go to build\logs\ (build\ and dist\ are gitignored, so the .staging,
# .prev and .partial paths referenced below are ignored too).
#
# NOTE: keep this file PURE ASCII. Windows PowerShell 5.1 reads a BOM-less .ps1 as
# the ANSI codepage, and a non-ASCII comment has been observed to corrupt parsing
# badly enough that param() segments stopped being recognised.

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "build\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    $display = "$FilePath $($Arguments -join ' ')"
    # Tee-Object has no -Encoding parameter on Windows PowerShell 5.1 and writes
    # UTF-16LE: reading build\logs\pyinstaller.log as UTF-8 yields " I N F O :"
    # style garbage, which breaks grep/tail/CI parsing. So tee manually -- keep
    # streaming to the console, and write the log through one no-BOM UTF-8 writer.
    # append = $false: one log file per command, rewritten on every build. Appending
    # across builds made files grow without bound AND mix encodings -- measured: an old
    # Tee-Object build left UTF-16 in the middle of pyinstaller.log (14835 NUL bytes),
    # which breaks any line-based parser. Rewriting keeps every log single-encoding.
    if (Test-Path $LogPath) {
        Remove-Item -Path $LogPath -Force
    }
    $writer = New-Object System.IO.StreamWriter($LogPath, $false, (New-Object System.Text.UTF8Encoding($false)))

    # Lower ErrorActionPreference only for the native call itself: stderr lines must
    # be logged, not promoted into terminating NativeCommandError.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $writer.WriteLine("")
        $writer.WriteLine("===== $display =====")
        & $FilePath @Arguments 2>&1 | ForEach-Object {
            $line = "$_"
            $writer.WriteLine($line)
            Write-Output $line
        }
    }
    finally {
        $writer.Flush()
        $writer.Dispose()
        $ErrorActionPreference = $previous
    }

    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Command failed with exit code ${code}: $display`nFull log: $LogPath"
    }
}

Write-Host "Build logs: $LogDir"

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (!(Test-Path $VenvPython)) {
    Invoke-Native -FilePath "python" -Arguments @("-m", "venv", ".venv") -LogPath (Join-Path $LogDir "venv.log")
}

Invoke-Native -FilePath $VenvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip") -LogPath (Join-Path $LogDir "pip-upgrade.log")
Invoke-Native -FilePath $VenvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt") -LogPath (Join-Path $LogDir "pip-requirements.log")
Invoke-Native -FilePath $VenvPython -Arguments @("-m", "pip", "install", "-r", "requirements-packaging.txt") -LogPath (Join-Path $LogDir "pip-packaging.log")

$DistDir = Join-Path $ProjectRoot "dist"
$AppDir = Join-Path $DistDir "DataConverterTool"
$StagingRoot = Join-Path $DistDir ".staging"
$StagingApp = Join-Path $StagingRoot "DataConverterTool"
$PrevApp = Join-Path $DistDir ".prev-DataConverterTool"
$ExeName = "DataConverterTool.exe"

# Build into a staging directory first. dist\DataConverterTool is NOT touched here:
# if anything below fails, the previous distribution is still on disk and runnable.
if (Test-Path $StagingRoot) {
    Remove-Item -Path $StagingRoot -Recurse -Force -ErrorAction Stop
}
New-Item -ItemType Directory -Force -Path $StagingRoot | Out-Null

Invoke-Native -FilePath $VenvPython `
    -Arguments @("-m", "PyInstaller", "--clean", "--noconfirm", "--distpath", $StagingRoot, "DataConverterTool.spec") `
    -LogPath (Join-Path $LogDir "pyinstaller.log")

# Verify the staged result BEFORE promoting it over the current build.
$StagedExe = Join-Path $StagingApp $ExeName
if (!(Test-Path -Path $StagedExe -PathType Leaf)) {
    throw "PyInstaller reported success but the staged executable is missing: $StagedExe (current dist\DataConverterTool left untouched)"
}
$StagedItem = Get-Item -Path $StagedExe
$StagedBytes = $StagedItem.Length
if ($StagedBytes -lt 1MB) {
    throw "Staged executable looks wrong: $StagedExe is only $StagedBytes bytes (current dist\DataConverterTool left untouched)"
}

New-Item -ItemType Directory -Force -Path (Join-Path $StagingApp "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StagingApp "uploads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StagingApp "exports") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StagingApp "logs") | Out-Null

# Promote the staged build. The old directory is MOVED aside rather than deleted, so
# a failure in the middle of the swap can be rolled back instead of losing the build.
if (Test-Path $PrevApp) {
    Remove-Item -Path $PrevApp -Recurse -Force -ErrorAction Stop
}
$movedAside = $false
try {
    if (Test-Path $AppDir) {
        Move-Item -Path $AppDir -Destination $PrevApp -ErrorAction Stop
        $movedAside = $true
    }
    Move-Item -Path $StagingApp -Destination $AppDir -ErrorAction Stop
}
catch {
    $failure = $_
    # A rollback is possible whenever .prev exists: it is cleared just before the swap
    # (above), so at this point .prev exists if and only if THIS run moved the old build
    # aside. $movedAside tells us which phase failed.
    if (Test-Path $PrevApp) {
        $restored = $false
        # Promotion phase may have re-created $AppDir as an empty/half-filled directory.
        # Move it aside (best effort) so the restore below can be a plain rename --
        # but NEVER Remove-Item it: it can hold fragments of the NEW build.
        if ($movedAside -and (Test-Path $AppDir)) {
            if (Test-Path "$AppDir.partial") {
                Remove-Item -Path "$AppDir.partial" -Recurse -Force -ErrorAction SilentlyContinue
            }
            Move-Item -Path $AppDir -Destination "$AppDir.partial" -ErrorAction SilentlyContinue
        }
        # Only a RENAME is safe when $AppDir is really gone: Move-Item into an EXISTING
        # directory nests the source inside it and still reports success, which would
        # turn a failed restore into a false "restored" message. Re-check, do not assume.
        if (!(Test-Path $AppDir)) {
            try {
                Move-Item -Path $PrevApp -Destination $AppDir -ErrorAction Stop
                $restored = $true
            }
            catch {
            }
        }
        # Fallback in one unified path: merge the CHILDREN of .prev back into $AppDir.
        # This covers both (a) the failure happened while moving the old build aside
        # (promotion never ran, so everything in $AppDir is OLD), and (b) the make-way
        # move above could not vacate $AppDir.
        if (!$restored) {
            $prevChildren = @(Get-ChildItem -LiteralPath $PrevApp -Force -ErrorAction SilentlyContinue)
            if ($prevChildren.Count -gt 0) {
                Move-Item -Path (Join-Path $PrevApp "*") -Destination $AppDir -ErrorAction SilentlyContinue
            }
            $leftover = @(Get-ChildItem -LiteralPath $PrevApp -Force -ErrorAction SilentlyContinue)
            if ((Test-Path $AppDir) -and ($leftover.Count -eq 0)) {
                $restored = $true
            }
        }
        if ($restored) {
            if (Test-Path $PrevApp) {
                Remove-Item -Path $PrevApp -Recurse -Force -ErrorAction SilentlyContinue
            }
            Write-Host "Swap failed; the previous build was restored to $AppDir"
        }
        else {
            Write-Host "WARNING: swap failed AND the previous build could not be restored from $PrevApp"
        }
    }
    # Best-effort .staging cleanup. Tolerate failures: a promotion-level failure can
    # leave locked files inside .staging, and a hard delete here would raise a second
    # error that masks the original one.
    if (Test-Path $StagingRoot) {
        Remove-Item -Path $StagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    throw $failure
}

# Only now is the previous build expendable.
if (Test-Path $PrevApp) {
    Remove-Item -Path $PrevApp -Recurse -Force
}
if (Test-Path $StagingRoot) {
    Remove-Item -Path $StagingRoot -Recurse -Force
}

# A zero exit code must mean "a fresh exe exists at the final path".
$ExePath = Join-Path $AppDir $ExeName
if (!(Test-Path -Path $ExePath -PathType Leaf)) {
    throw "Build reported success but the executable is missing: $ExePath"
}
$ExeItem = Get-Item -Path $ExePath
$ExeBytes = $ExeItem.Length
if ($ExeBytes -lt 1MB) {
    throw "Executable looks wrong: $ExePath is only $ExeBytes bytes"
}

Write-Host ""
Write-Host "Build finished."
Write-Host ("Executable: {0}" -f $ExePath)
Write-Host ("Size: {0:N1} MB, built {1}" -f ($ExeBytes / 1MB), $ExeItem.LastWriteTime)
Write-Host ""
Write-Host "Copy the whole dist\DataConverterTool folder to another Windows computer."
