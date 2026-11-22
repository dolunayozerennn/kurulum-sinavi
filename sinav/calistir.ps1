param(
    [Parameter(Mandatory)][string]$Urun,
    [Parameter(Mandatory)][string]$Zip,
    [Parameter(Mandatory)][string]$Varlik,
    [Parameter(Mandatory)][string]$Log
)
# Ogrencinin Windows'ta yaptigini taklit eder: indirilen zip'i Gezgin ile acar (internet isareti),
# Turkce ve bosluklu klasor, temiz PATH, kurulum belgesindeki komutlar Windows PowerShell 5.1 ile.
# Konsola yalniz adim sonucu yazilir; ayrintilar sifrelenecek kayit dosyasina gider.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$kok = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ayda 398 $ Tasarruf\İndirilenler Şükrü Öz'
New-Item -ItemType Directory -Force -Path $kok | Out-Null
Expand-Archive -LiteralPath $Zip -DestinationPath $kok -Force
Get-ChildItem -LiteralPath $kok -Recurse -File | ForEach-Object {
    Set-Content -LiteralPath $_.FullName -Stream Zone.Identifier -Value "[ZoneTransfer]`r`nZoneId=3"
}
$proje = (Get-ChildItem -LiteralPath (Join-Path $kok 'Projeler') -Directory | Select-Object -First 1).FullName
$sistem = @("$env:SystemRoot\system32", "$env:SystemRoot", "$env:SystemRoot\System32\Wbem",
    "$env:SystemRoot\System32\WindowsPowerShell\v1.0\", "$env:SystemRoot\System32\OpenSSH\",
    "$env:LOCALAPPDATA\Microsoft\WindowsApps")
$basMakine = [Environment]::GetEnvironmentVariable('PATH', 'Machine') -split ';'
$script:sonuc = @()

function Ogrenci-Path {
    $yeniMakine = [Environment]::GetEnvironmentVariable('PATH', 'Machine') -split ';' | Where-Object { $_ -and ($basMakine -notcontains $_) }
    $kullanici = [Environment]::GetEnvironmentVariable('PATH', 'User') -split ';' | Where-Object { $_ }
    (@($sistem) + @($yeniMakine) + @($kullanici)) -join ';'
}

function Adim([string]$Ad, [string]$Komut, [int]$Dakika = 45, [switch]$KaldiBeklenir) {
    "`r`n===== $Ad`r`n> $Komut" | Out-File -LiteralPath $Log -Append -Encoding utf8
    $govde = "`$env:PATH = '$(Ogrenci-Path)'; Set-Location -LiteralPath '$proje'; `$global:LASTEXITCODE = 0; $Komut; if (-not `$?) { exit 1 }; exit `$LASTEXITCODE"
    $kod = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($govde))
    $cikis = Join-Path $env:RUNNER_TEMP 'adim.out'; $hata = Join-Path $env:RUNNER_TEMP 'adim.err'
    $sure = [Diagnostics.Stopwatch]::StartNew()
    # Ogrenci PowerShell'i Baslat menusunden acar; pwsh 7'nin modul yolu 5.1'e sizmasin
    $eskiModul = $env:PSModulePath
    $env:PSModulePath = "$env:USERPROFILE\Documents\WindowsPowerShell\Modules;$env:ProgramFiles\WindowsPowerShell\Modules;$env:SystemRoot\system32\WindowsPowerShell\v1.0\Modules"
    $p = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-EncodedCommand', $kod) -PassThru -NoNewWindow `
        -RedirectStandardOutput $cikis -RedirectStandardError $hata
    $env:PSModulePath = $eskiModul
    if (-not $p.WaitForExit($Dakika * 60000)) {
        & taskkill /PID $p.Id /T /F | Out-Null
        $durum = "ZAMAN ASIMI ($Dakika dk)"
    } else {
        $durum = if ($p.ExitCode -eq 0) { 'GECTI' } else { "KALDI (cikis $($p.ExitCode))" }
    }
    foreach ($f in @($cikis, $hata)) { if (Test-Path $f) { Get-Content -LiteralPath $f -Encoding utf8 | Out-File -LiteralPath $Log -Append -Encoding utf8 } }
    $satir = "{0,-34} {1}  ({2:n0} sn)" -f $Ad, $durum, $sure.Elapsed.TotalSeconds
    if ($KaldiBeklenir) { $satir += '  [beklenen: kalmasi]' }
    Write-Host $satir
    "SONUC: $satir" | Out-File -LiteralPath $Log -Append -Encoding utf8
    $script:sonuc += [pscustomobject]@{ Ad = $Ad; Durum = $durum }
    return ($durum -eq 'GECTI')
}

Write-Host "Urun $Urun · klasor: Masaustu\Ayda 398 `$ Tasarruf\İndirilenler Şükrü Öz · internet isaretli dosyalar"
switch ($Urun) {
    '14' {
        Adim 'kur (belgedeki komut)' 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\kur.ps1' | Out-Null
        Adim 'birlestir (127.0.0.1:18765)' "Add-Type -AssemblyName System.Net.Http; `$u='http://127.0.0.1:18765/api/v1/general/merge-pdfs'; `$s=[Net.Http.HttpClient]::new(); `$f=[Net.Http.MultipartFormDataContent]::new(); foreach(`$n in 'a.pdf','b.pdf'){ `$c=[Net.Http.ByteArrayContent]::new([IO.File]::ReadAllBytes('$Varlik\' + `$n)); `$f.Add(`$c,'fileInput',`$n) }; `$f.Add([Net.Http.StringContent]::new('orderProvided'),'sortType'); `$r=`$s.PostAsync(`$u,`$f).Result; `$b=`$r.Content.ReadAsByteArrayAsync().Result; Write-Host `$r.StatusCode `$b.Length; if(-not `$r.IsSuccessStatusCode -or [Text.Encoding]::ASCII.GetString(`$b,0,4) -ne '%PDF'){ exit 1 }" 10 | Out-Null
        Adim 'kur ikinci kez' 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\kur.ps1' | Out-Null
        Adim 'kaldir' 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\kaldir.ps1' | Out-Null
    }
    '15' {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Adim 'winget git (belgedeki)' 'winget install --id Git.Git --exact --accept-source-agreements --accept-package-agreements; if (Test-Path "$env:ProgramFiles\Git\cmd\git.exe") { exit 0 }' 15 | Out-Null
            $script:sistem += "$env:ProgramFiles\Git\cmd"
            Adim 'winget uv (belgedeki)' 'winget install --id astral-sh.uv --exact --accept-source-agreements --accept-package-agreements' 15 | Out-Null
        } else {
            Adim 'uv (winget yok, resmi kurucu)' 'irm https://astral.sh/uv/install.ps1 | iex' 15 | Out-Null
        }
        Adim 'kur (belgedeki komut)' 'powershell -NoProfile -ExecutionPolicy Bypass -File .\kur.ps1' 60 | Out-Null
        Adim 'durum' '.\.venv\Scripts\python.exe ses.py durum' 5 | Out-Null
        Adim 'oku (CPU)' ".\.venv\Scripts\python.exe ses.py oku --ses '$Varlik\ref.wav' --metin 'Bugün 3 Aralık Perşembe, saat 09:45 için 2 başlık hazırladık.' --cikti Ciktilar/deneme; if (-not (Test-Path Ciktilar/deneme.wav)) { exit 1 }" 30 | Out-Null
        Adim 'kaldir' 'powershell -NoProfile -ExecutionPolicy Bypass -File .\kaldir.ps1' 10 | Out-Null
    }
    '16' {
        $ok = Adim 'kur (belgedeki: RemoteSigned)' 'powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\kur.ps1' 45
        if (-not $ok) { Adim 'kur (Bypass ile yeniden)' 'powershell -NoProfile -ExecutionPolicy Bypass -File .\kur.ps1' 45 | Out-Null }
        Adim 'son toplanti bilgisi (py -3)' 'py -3 son_toplanti.py --bilgi' 5 | Out-Null
        Adim 'kaldir (belgedeki: RemoteSigned)' 'powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\kaldir.ps1' 10 | Out-Null
    }
    '17' {
        Adim 'kur (belgedeki komut)' 'powershell -NoProfile -ExecutionPolicy Bypass -File .\kur.ps1' 45 | Out-Null
        Adim 'sil (Fotoğraflarım)' "New-Item -ItemType Directory -Force 'Fotoğraflarım' | Out-Null; Copy-Item '$Varlik\urun.jpg' 'Fotoğraflarım\ürün fotoğrafı.jpg'; .\.ortam\Scripts\python.exe sil.py 'Fotoğraflarım' --cikti 'İlanlarım' --golge; `$n=(Get-ChildItem 'İlanlarım' -File).Count; Write-Host cikti `$n; if (`$n -lt 1) { exit 1 }" 20 | Out-Null
        Adim 'kaldir' 'powershell -NoProfile -ExecutionPolicy Bypass -File .\kaldir.ps1' 10 | Out-Null
    }
}
if ($script:sonuc | Where-Object { $_.Durum -ne 'GECTI' }) { exit 1 }
exit 0
