"""Yalnız temiz GitHub Windows makinesinde. Ürünün öğrenci komutunu keser ve yeniden çalıştırır."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

PS = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
RATE = "640"  # KiB/sn, 42,6 MB kurucuya da iki düzenli ara ölçüm bırakır.


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def process_table():
    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name "
              "| ConvertTo-Json -Compress")
    data = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", script], timeout=25)
    return json.loads(data)


def process_tree(root):
    records = process_table()
    tree = {root}
    while True:
        extra = {row["ProcessId"] for row in records if row["ParentProcessId"] in tree} - tree
        if not extra:
            break
        tree.update(extra)
    return {row["ProcessId"]: row["Name"] for row in records if row["ProcessId"] in tree}


def kill_tree(process):
    if process.poll() is None:
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, check=True, timeout=25)
        process.wait(timeout=15)


def check_mutex():
    import ctypes.wintypes as wt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = (wt.DWORD, wt.BOOL, wt.LPCWSTR)
    kernel.OpenMutexW.restype = wt.HANDLE
    named = kernel.OpenMutexW(0x100000, False, r"Local\AIFactoryToplantiNotlari")
    if named:
        kernel.CloseHandle.argtypes = (wt.HANDLE,)
        kernel.CloseHandle(named)
        raise RuntimeError("Kesintiden sonra kurulum kilidi hâlâ açık.")
    require(ctypes.get_last_error() == 2, "Kurulum kilidinin kapanışı doğrulanamadı.")


def read_output(stream, events):
    try:
        for line in stream:
            events.put((time.monotonic(), line.rstrip()))
    finally:
        stream.close()
        events.put((time.monotonic(), None))


def install(kit, target=None, partial=None, must_resume=None):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if target:
        env["TOPLANTI_INDIRME_KIB_SN"] = RATE
    else:
        env.pop("TOPLANTI_INDIRME_KIB_SN", None)
    process = subprocess.Popen(PS + [str(kit / "kur.ps1")], cwd=kit, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", errors="replace")
    events = queue.Queue()
    reader = threading.Thread(target=read_output, args=(process.stdout, events), daemon=True)
    reader.start()
    start = time.monotonic()
    latest = None
    resumed = not must_resume
    try:
        while time.monotonic() - start < 1200:
            try:
                stamp, line = events.get(timeout=10)
            except queue.Empty:
                continue
            if line is None:
                rc = process.wait(timeout=10)
                require(rc == 0 and not target and resumed, "Kurulum beklenen aşamaya ulaşmadı, çıkış " + str(rc))
                return
            print(f"{stamp-start:7.2f} sn | {line}", flush=True)
            if must_resume and re.search(re.escape(must_resume[0]) + r" kaldığı yerden sürüyor\s*\(("
                                         + str(must_resume[1]) + r") bayt\)", line, re.IGNORECASE):
                resumed = True
            if not target or target.lower() not in line.lower() or "MB" not in line:
                continue
            pct = re.search(r"%\s*(\d+)", line)
            if not pct or not 0 < int(pct.group(1)) < 100:
                continue
            if latest is None:
                latest = (stamp, int(pct.group(1)))
                continue
            delta = stamp - latest[0]
            require(14.5 <= delta <= 20.5 and int(pct.group(1)) > latest[1],
                    f"{target} ara satır aralığı {delta:.2f} sn veya yüzde ilerlemiyor.")
            require(resumed, "İlk kesintide kalıcı olan bayttan devam edilmedi.")
            before = process_tree(process.pid)
            require(len(before) >= 2, "Kurucunun alt işlemi bulunamadı.")
            kill_tree(process)
            after = {row["ProcessId"] for row in process_table()}
            require(not (before.keys() & after), "Kesilen kurucunun işlem ağacında çalışan kaldı.")
            check_mutex()
            size = partial.stat().st_size
            require(size > 0, "Devam edilecek dosyada bayt bulunamadı.")
            print(f"{target} ara satırlar {delta:.2f} sn. taskkill /T /F ile sonlandırılan {before}. "
                  f"Yaşayan işlem 0, kilit yok, diskte korunan {size} bayt.", flush=True)
            return size
        raise RuntimeError("İndirme sınavı süre sınırını aştı.")
    finally:
        kill_tree(process)
        reader.join(timeout=10)


def main():
    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true" and
            os.environ.get("RUNNER_OS") == "Windows", "Sınav yalnız temiz Windows bulut makinesinde çalışır.")
    kit = Path.cwd()
    cfg = json.loads((kit / "surum.json").read_text(encoding="utf-8-sig"))
    local = Path(os.environ["LOCALAPPDATA"])
    app = local / "meetily"
    state = local / "AI Factory" / "Toplanti Notlari"
    data = Path(os.environ["APPDATA"]) / "com.meetily.ai"
    model = data / "models" / cfg["whisper_url"].split("/")[-1]
    partial_app = state / ("meetily-" + cfg["windows_sha256"] + ".part.exe")
    partial_model = model.parent / ("whisper-" + cfg["whisper_sha256"] + ".part")
    roots = (state, app, data)
    require(not any(p.exists() for p in roots), "Kesinti sınavına temiz bir Windows kullanıcısı gerekir.")
    diagnostics = [Path(os.environ["TEMP"]) / ("ToplantiNotlari-" + name + ".log")
                   for name in ("kurulum", "kaldirma")]
    own_logs = [p for p in diagnostics if not p.exists()]
    try:
        app_bytes = install(kit, "Meetily", partial_app)
        model_bytes = install(kit, "Whisper", partial_model, ("Meetily", app_bytes))
        install(kit, must_resume=("Whisper", model_bytes))
        digest = hashlib.sha256()
        with model.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        require(digest.hexdigest() == cfg["whisper_sha256"] and (app / "meetily.exe").is_file(),
                "Tam model özeti veya kurulu uygulama doğrulanamadı.")
        require(not partial_app.exists() and not partial_model.exists(), "İndirme kalıntısı bulundu.")
        check_mutex()
        print(f"Whisper {model.stat().st_size} bayt, SHA-256 {digest.hexdigest()}. "
              "Meetily kuruldu. İki kesinti doğrulandı.", flush=True)
    finally:
        result = subprocess.run(PS + [str(kit / "kaldir.ps1")], cwd=kit, timeout=180)
        for p in own_logs:
            if p.is_file():
                print(p.read_text(encoding="utf-8-sig", errors="replace")[-2200:], flush=True)
                p.unlink()
        for root in roots:
            if root.exists():
                shutil.rmtree(root)
        require(result.returncode == 0 and not any(p.exists() for p in roots), "Sınav kaldırılamadı.")
        print("Sınav kurulum klasörleri ve tanı günlükleri temizlendi.", flush=True)


if __name__ == "__main__":
    main()
