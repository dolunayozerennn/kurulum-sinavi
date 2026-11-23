"""Temiz Windows koşusunda öğrencinin kısa kurulum komutlarını gerçek paket üzerinde sına."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PS = ["powershell.exe", "-NoProfile", "-Command"]


def check(test, message):
    if not test:
        raise RuntimeError(message)


def ask(args, timeout):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                            timeout=timeout)
    print(result.stdout.strip(), flush=True)
    check(result.returncode == 0, f"Komut çıkış kodu {result.returncode}.")
    return result.stdout


def read_state(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def stop_own_worker(kit, state):
    if not state.exists():
        return
    current = read_state(state)
    pid, token = current.get("pid"), current.get("id")
    if not isinstance(pid, int) or not isinstance(token, str) or not token:
        return
    query = ("[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); "
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine")
    text = subprocess.check_output(PS + [query], encoding="utf-8", errors="replace", timeout=20)
    if str(kit / "kurulum.py").casefold() in text.casefold() and "_calistir" in text and token in text:
        subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], timeout=20, check=True)
        print(f"Sınava ait kurulum ağacı durduruldu (PID {pid}).", flush=True)


def cleanup(marker, kit, roots, state, expect_uninstalled=False):
    if not marker.exists():
        return
    check(marker.read_text(encoding="utf-8") == str(kit), "Sınavın temizlik kaydı eşleşmedi.")
    still_installed = (state.parent.exists() or (roots[1] / "meetily.exe").exists() or
                       any((roots[2] / "models").glob("ggml-*.bin")))
    stop_own_worker(kit, state)
    # Bu üç dizinin de sınav başlangıcında bulunmadığı kaydedildi.
    for root in roots:
        if root.exists():
            shutil.rmtree(root)
    marker.unlink()
    check(not expect_uninstalled or not still_installed,
          "Kaldır komutu uygulamayı, Whisper modelini veya kurulum takip klasörünü bıraktı.")
    print("Yalnız bu bulut sınavına ait kurulum ve takip klasörleri temizlendi.", flush=True)


def main():
    check(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true" and
          os.environ.get("RUNNER_OS") == "Windows", "Temiz Windows sınav makinesi gerekir.")
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    kit = Path.cwd()
    local = Path(os.environ["LOCALAPPDATA"])
    root = local / "AI Factory" / "Toplanti Notlari"
    app = local / "meetily"
    data = Path(os.environ["APPDATA"]) / "com.meetily.ai"
    roots = (root, app, data)
    state = root / "kurulum-takibi" / "durum.json"
    marker = Path(os.environ["RUNNER_TEMP"]) / "16-arka-sinav.temizlik"
    if sys.argv[1:] == ["--temiz"]:
        cleanup(marker, kit, roots, state, expect_uninstalled=True)
        return
    check(not any(path.exists() for path in (*roots, marker)), "Profil sınav başında boş olmalı.")
    with marker.open("x", encoding="utf-8") as stream:
        stream.write(str(kit))
    # Dış bağlantı gerçek. Bu tavan, başlatıcı ve ebeveyn kapandığında veri
    # aktarımını gözlemek için en az bir dakika süren bir ilk kurulum sağlar.
    env = dict(os.environ, TOPLANTI_INDIRME_KIB_SN="8192")
    start = time.monotonic()
    command = ('[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); '
               '& py -3 kurulum.py baslat; exit $LASTEXITCODE')
    shell = subprocess.Popen(PS + [command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", env=env)
    try:
        output = shell.communicate(timeout=20)[0]
        returned = time.monotonic() - start
        print(f"Ebeveyn PowerShell PID {shell.pid}, çıkış {shell.returncode}, dönüş {returned:.2f} sn.", flush=True)
        print(output.strip(), flush=True)
        check(shell.returncode == 0 and returned < 20, "Başlatıcı kısa sürede çıkmadı.")
        status = ["py", "-3", "kurulum.py", "durum"]
        running = ask(status, 15)
        check("ÇALIŞIYOR" in running, "Ebeveyn çıktıktan sonra canlı kurulum görülmedi.")
        info = read_state(state)
        worker = info["pid"]
        print(f"Başlatan PowerShell kapandıktan sonra çalışan kurucu PID {worker}.", flush=True)
        cfg = read_state(kit / "surum.json")
        partials = [root / ("meetily-" + cfg["windows_sha256"] + ".part.exe"),
                    data / "models" / ("whisper-" + cfg["whisper_sha256"] + ".part")]
        observed = {}
        grew = False
        # İşin dışarıda canlı kaldığını aynı dosyanın iki ardışık boyutundan ölç.
        while time.monotonic() - start < 180 and not grew:
            for path in partials:
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue
                if 0 < observed.get(path, 0) < size:
                    grew = True
                    print(f"Ebeveyn kapalıyken {path.suffix} {observed[path]} → {size} bayt.", flush=True)
                observed[path] = size
            if not grew:
                time.sleep(0.4)
        check(grew, "Ebeveyn kapalıyken indirmenin ilerlediği gözlenmedi.")
        polls = 0
        while time.monotonic() - start < 1200:
            before = time.monotonic()
            response = ask(status + ["--bekle", "30"], 40)
            polls += 1
            print(f"Durum çağrısı {polls} süresi {time.monotonic()-before:.2f} sn.", flush=True)
            if "HAZIR" in response:
                break
            check("ÇALIŞIYOR" in response and read_state(state)["pid"] == worker,
                  "Kurulum durumu veya çalışan iş değişti.")
            if time.monotonic() - before < 1:
                time.sleep(1)
        else:
            raise RuntimeError("Bitiş durumu süre sınırında alınamadı.")
        model = data / "models" / cfg["whisper_url"].split("/")[-1]
        digest = hashlib.sha256()
        with model.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        check((app / "meetily.exe").is_file() and digest.hexdigest() == cfg["whisper_sha256"],
              "HAZIR ile kurulu uygulama/model eşleşmedi.")
        print(f"HAZIR doğrulandı. Meetily ve {model.stat().st_size} bayt Whisper, "
              f"SHA-256 {digest.hexdigest()}.", flush=True)
    except BaseException:
        if shell.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(shell.pid), "/T", "/F"], timeout=20)
        stop_own_worker(kit, state)
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(kit / "kaldir.ps1")], timeout=180)
        cleanup(marker, kit, roots, state)
        raise


if __name__ == "__main__":
    main()
