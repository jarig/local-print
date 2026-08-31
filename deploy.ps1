<#
.SYNOPSIS
    Deploys LocalPrint to the local-print folder on my-nas.

.DESCRIPTION
    Stages every application file with LF line endings, uploads it over scp
    into ~/local-print/ on the NAS, makes the entry points executable and
    verifies the app imports cleanly inside the server virtualenv.

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -Restart
#>
[CmdletBinding()]
param(
    [string]$RemoteHost = $(if ($env:LOCALPRINT_HOST) { $env:LOCALPRINT_HOST } else { "my-nas" }),
    [string]$RemoteUser = $(if ($env:LOCALPRINT_USER) { $env:LOCALPRINT_USER } else { "me" }),
    [string]$RemotePath = "local-print",
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$target = "$RemoteUser@$RemoteHost"
$source = $PSScriptRoot

$files = @(
    "app.py",
    "printing.py",
    "config.py",
    "requirements.txt",
    "start.sh",
    "install.sh",
    "templates/index.html",
    "static/style.css",
    "static/app.js"
)

# Third-party bundles are copied byte for byte, never line-ending rewritten.
$verbatim = @(
    "static/vendor/pdf.min.js",
    "static/vendor/pdf.worker.min.js"
)

# Text files must use LF; a CRLF shebang makes start.sh unrunnable on Linux.
Write-Host "Staging $($files.Count) files with LF line endings..." -ForegroundColor Cyan
$staging = Join-Path ([IO.Path]::GetTempPath()) ("localprint-" + [Guid]::NewGuid().ToString("N"))
try {
    foreach ($file in $files) {
        $from = Join-Path $source ($file -replace "/", "\")
        if (-not (Test-Path $from)) {
            throw "Missing source file: $from"
        }

        $to = Join-Path $staging ($file -replace "/", "\")
        New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null

        $text = [IO.File]::ReadAllText($from) -replace "`r`n", "`n"
        [IO.File]::WriteAllText($to, $text, (New-Object Text.UTF8Encoding $false))
    }

    foreach ($file in $verbatim) {
        $from = Join-Path $source ($file -replace "/", "\")
        if (-not (Test-Path $from)) {
            throw "Missing vendored file: $from"
        }

        $to = Join-Path $staging ($file -replace "/", "\")
        New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null
        Copy-Item $from $to
    }

    Write-Host "Checking connection to $target..." -ForegroundColor Cyan
    ssh -o BatchMode=yes -o ConnectTimeout=10 $target "true"
    if ($LASTEXITCODE -ne 0) { throw "Cannot reach $target over SSH." }

    ssh $target "mkdir -p ~/$RemotePath/templates ~/$RemotePath/static/vendor"
    if ($LASTEXITCODE -ne 0) { throw "Could not create remote directories." }

    Write-Host "Uploading to ${target}:~/$RemotePath/..." -ForegroundColor Cyan
    scp (Join-Path $staging "app.py") `
        (Join-Path $staging "printing.py") `
        (Join-Path $staging "config.py") `
        (Join-Path $staging "requirements.txt") `
        (Join-Path $staging "start.sh") `
        (Join-Path $staging "install.sh") `
        "${target}:~/$RemotePath/"
    if ($LASTEXITCODE -ne 0) { throw "Upload of application files failed." }

    scp (Join-Path $staging "templates\index.html") "${target}:~/$RemotePath/templates/"
    if ($LASTEXITCODE -ne 0) { throw "Upload of templates failed." }

    scp (Join-Path $staging "static\style.css") `
        (Join-Path $staging "static\app.js") `
        "${target}:~/$RemotePath/static/"
    if ($LASTEXITCODE -ne 0) { throw "Upload of static assets failed." }

    scp (Join-Path $staging "static\vendor\pdf.min.js") `
        (Join-Path $staging "static\vendor\pdf.worker.min.js") `
        "${target}:~/$RemotePath/static/vendor/"
    if ($LASTEXITCODE -ne 0) { throw "Upload of vendored pdf.js failed." }
}
finally {
    if (Test-Path $staging) {
        Remove-Item $staging -Recurse -Force
    }
}

Write-Host "Setting permissions and verifying..." -ForegroundColor Cyan
$verify = "cd ~/$RemotePath && chmod +x start.sh install.sh app.py && " +
          "venv/bin/python3 -c 'import app; print(""import ok - printer:"", app.config.PRINTER, ""port:"", app.config.PORT)'"
ssh $target $verify
if ($LASTEXITCODE -ne 0) { throw "Remote verification failed." }

if ($Restart) {
    Write-Host "Restarting service..." -ForegroundColor Cyan

    # Once install.sh has registered the systemd unit, systemd owns the
    # process; starting another copy by hand would just fight it for the port.
    $managed = ssh -n $target "systemctl cat localprint.service >/dev/null 2>&1 && echo managed || echo manual"

    if ($managed -match "managed") {
        ssh -n $target "sudo systemctl restart localprint"
        if ($LASTEXITCODE -ne 0) { throw "systemctl restart failed." }
        Start-Sleep -Seconds 3
        ssh -n $target "systemctl is-active localprint && journalctl -u localprint -n 5 --no-pager"
    }
    else {
        # All three descriptors of the remote command must be redirected, or ssh
        # keeps the channel open waiting on the backgrounded server.
        # The process shows up as `venv/bin/python3 app.py` (relative cmdline),
        # so match on that rather than on the deploy path.
        $stop = "pkill -u `$USER -f 'venv/bin/python3 app.py' || true"
        ssh -n $target $stop | Out-Null
        Start-Sleep -Seconds 2

        $start = "(cd ~/$RemotePath && setsid ./start.sh > local-print.log 2>&1 < /dev/null &) > /dev/null 2>&1"
        ssh -n $target $start
        Start-Sleep -Seconds 3

        ssh -n $target "cat ~/$RemotePath/local-print.log | head -n 6"
    }
}

Write-Host "Deployed to ${target}:~/$RemotePath/" -ForegroundColor Green
Write-Host "Install as a service:  ssh $target 'cd ~/$RemotePath && ./install.sh'" -ForegroundColor Green
