#requires -Version 5.1
# Temiz Windows sınav makinesinde yalnız bu paketin uygulamasını başlatır.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$exe = Join-Path $env:LOCALAPPDATA 'meetily\meetily.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Kurulan Meetily bulunamadı.' }
function Get-KitWindow {
    @(Get-Process -Name meetily -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -eq $exe -and $_.MainWindowHandle -ne 0
    })
}
function Wait-KitWindow {
    $end = [DateTime]::UtcNow.AddSeconds(50)
    $last = ''
    $stable = 0
    do {
        $visible = @(Get-KitWindow)
        if ($visible.Count -eq 1 -and $visible[0].MainWindowTitle) {
            $current = '{0}/{1}/{2}' -f $visible[0].Id,$visible[0].MainWindowHandle,$visible[0].MainWindowTitle
            if ($current -eq $last) { $stable++ } else { $stable = 0; $last = $current }
            if ($stable -ge 12) { return $visible[0] }
        } else { $stable = 0; $last = '' }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $end)
    throw 'Tek bir Meetily penceresi bulunamadı.'
}
function Stop-TestApp {
    Get-Process -Name meetily -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } |
        ForEach-Object { Stop-Process -Id $_.Id -Force; $_.WaitForExit(10000) | Out-Null }
}
$group = 'Meetily-16-' + [Guid]::NewGuid()
try {
    Stop-TestApp
    $webview = @(Get-ChildItem -Path "${env:ProgramFiles(x86)}\Microsoft\EdgeWebView\Application\*\msedgewebview2.exe" -File)
    if (-not $webview) { throw 'WebView2 bulunamadı.' }
    foreach ($file in (@($exe) + @($webview.FullName))) {
        New-NetFirewallRule -DisplayName $group -Group $group -Direction Outbound -Action Block -Enabled True -Profile Any -Program $file | Out-Null
    }
    & py -3 ac.py
    if ($LASTEXITCODE -ne 0) { throw 'İlk açılış başarısız.' }
    $first = Wait-KitWindow
    $handle = $first.MainWindowHandle
    Write-Output ("Çevrimdışı ilk pencere: PID={0} HWND={1}" -f $first.Id, $handle)
    & py -3 ac.py
    if ($LASTEXITCODE -ne 0) { throw 'İkinci açılış başarısız.' }
    Start-Sleep -Seconds 4
    $second = @(Wait-KitWindow)
    Write-Output ("İkinci açılışta gözlenen: PID={0} HWND={1}" -f $second[0].Id, $second[0].MainWindowHandle)
    if ($second.Count -ne 1 -or $second[0].Id -ne $first.Id -or $second[0].MainWindowHandle -ne $handle) {
        throw 'İkinci çağrı aynı pencereye dönmedi.'
    }
    Write-Output ("İkinci çağrı aynı pencerede: PID={0} HWND={1}" -f $second[0].Id, $second[0].MainWindowHandle)
} finally {
    Stop-TestApp
    Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
}
