"""Exercise the shipped installer and official HTTPS transfer using an isolated process tree."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

WINDOW_NAME = os.name == "nt"
MODEL_BYTES = 224005088
MODEL_SHA256 = "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333"
HALF_RATE = 2_000_000
MODEL_NAME = "birefnet-general-lite"
PROGRESS = re.compile(r"Kesim modeli.*%(\d+(?:[,.]\d+)?)\s*\(([\d.,]+)\s*/\s*([\d.,]+)\s*MB", re.I)
INSTALL = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\\kur.ps1"]
           if WINDOW_NAME else ["/bin/bash", "kur.sh"])


def require(condition, detail):
    if not condition:
        raise RuntimeError(detail)


def sha(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def events(path):
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines()]


def live_tree(pid, http):
    if not WINDOW_NAME:
        transfer = int(events(http)[0]["pid"])
        require(os.getpgid(pid) == pid == os.getpgid(transfer), "Model indirmesi kurucunun süreç grubundan ayrılmış.")
        return {pid: "kur.sh", transfer: "model indirmesi"}
    # Capture actual descendants just before taskkill; also inspect any reparented survivor afterwards.
    ps = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
          "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress")
    raw = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", ps], encoding="utf-8")
    records = json.loads(raw)
    descendants = {pid}
    while True:
        children = {int(x["ProcessId"]) for x in records if int(x["ParentProcessId"]) in descendants}
        if children <= descendants:
            break
        descendants |= children
    return {int(x["ProcessId"]): x["Name"] for x in records if int(x["ProcessId"]) in descendants}


def remaining(pid, recorded):
    if not WINDOW_NAME:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return {}
        return {pid: "süreç grubu hâlâ var"}
    ids = ",".join(map(str, recorded))
    cmd = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
           "Get-Process -ID " + ids + " -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd], capture_output=True,
                            encoding="utf-8")
    return {int(line): recorded[int(line)] for line in result.stdout.splitlines() if line.strip().isdigit()}


def stop_tree(process):
    if WINDOW_NAME:
        return subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return 1
    return 0


def probe_locks(box):
    # Empty persistent kernel-lock files may remain on disk. Verify actual exclusive acquisition.
    if not WINDOW_NAME and (box / ".mac-bakim-kilidi").exists():
        subprocess.run(["/usr/bin/lockf", "-ks", "-t", "0", str(box / ".mac-bakim-kilidi"),
                        "/usr/bin/true"], check=True)
    if (box / ".mac-bakim-pid").exists():
        raise RuntimeError("macOS PID kilidi kaldı")
    with (box / ".bakim-kilidi").open("r+b") as lock:
        if WINDOW_NAME:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def run_stage(box, probe, root, part, stop, report):
    name = "kesilen" if stop else "tamamlanan"
    http = root / (name + ".jsonl")
    env = dict(os.environ, PYTHONPATH=str(probe), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               K17_TEST_HTTP=str(http), K17_TEST_BPS=str(HALF_RATE if stop else 12_000_000))
    cmd = subprocess.Popen(INSTALL, cwd=box, env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOW_NAME
                              else {"start_new_session": True}))
    start = time.monotonic()
    rows = []
    def read_output():
        for line in iter(cmd.stdout.readline, b""):
            line = line.decode("utf-8", "replace").strip()
            if line:
                rows.append((round(time.monotonic() - start, 2), line))
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    stage = report[name] = {"komut": INSTALL, "kareler": rows}
    known = {}
    try:
        while cmd.poll() is None and time.monotonic() - start < 1800:
            if stop and part.exists() and part.stat().st_size >= MODEL_BYTES * 0.42:
                known = live_tree(cmd.pid, http)
                require(len(known) >= (2 if WINDOW_NAME else 1), "Kesilecek işlem ağacı yakalanamadı.")
                stage["pid_agaci"] = known
                stage["kesme"] = "taskkill /PID ... /T /F" if WINDOW_NAME else "killpg(SIGTERM)"
                require(stop_tree(cmd) == 0, "Süreç ağacı sonlandırılamadı.")
                break
            time.sleep(0.15)
        code = cmd.wait(timeout=10)
        reader.join(timeout=10)
        require(not reader.is_alive(), "Kurucunun çıktısını açık tutan bir alt süreç var.")
        stage["saniye"] = round(time.monotonic() - start, 2)
        stage["kod"] = code
        stage["http"] = events(http) if http.exists() else []
        require((code != 0 and bool(known)) if stop else code == 0,
                f"{name} beklenmeyen çıkış kodu {code}.")
        if stop:
            survivors = remaining(cmd.pid, known)
            for _ in range(15):
                if not survivors:
                    break
                time.sleep(0.2)
                survivors = remaining(cmd.pid, known)
            stage["kalan_surecler"] = survivors
            require(not survivors, "Süreç ağacından kalan işlem var.")
        probe_locks(box)
        stage["kilit_serbest"] = True
        if stop:
            times = [t for t, line in rows if PROGRESS.search(line)]
            intervals = [round(b-a, 2) for a,b in zip(times, times[1:])]
            stage["ilerleme_araliklari_sn"] = intervals
            require(len(times) >= 3 and all(15 <= t <= 20 for t in intervals),
                    "Canlı yüzde ve MB satırları 15–20 saniye aralığında bekleniyordu.")
    finally:
        if cmd.poll() is None:
            stop_tree(cmd)
            cmd.wait(timeout=15)
        reader.join(timeout=10)
        cmd.stdout.close()


def exercise(box, root, report):
    probe = root / "olcer"
    probe.mkdir()
    shutil.copyfile(Path(__file__).with_name("17-ag-olcer.py"), probe / "sitecustomize.py")
    model = box / ".model" / "models" / MODEL_NAME / (MODEL_NAME + ".onnx")
    part = model.with_suffix(".onnx.parca")
    # In CI the separately measured cold install already populated this disposable student box.
    existing = root / "onceki-model"
    if model.exists():
        require(not part.exists(), "Temiz koşuda eski bir kısmi dosya var.")
        shutil.move(model, existing)
    try:
        run_stage(box, probe, root, part, True, report)
        p = part.stat().st_size
        require(MODEL_BYTES * 0.40 <= p < MODEL_BYTES * 0.75 and not model.exists(),
                "Kurucu bitmeden son dosya yazıldı veya kısmi dosya ölçülemedi.")
        report["kalan_bayt"] = p
        require(report["kesilen"]["http"][0]["status"] == 200, "İlk HTTP yanıtı tam dosya olmalı.")
        run_stage(box, probe, root, part, False, report)
        http_log = report["tamamlanan"]["http"]
        require(len(http_log) == 2, "Tek bir HTTP devam isteğinin başlık ve gövde sayısı bekleniyordu.")
        info, body = http_log
        require(info["request_range"] == f"bytes={p}-" and info["status"] == 206
                and info["content_range"] == f"bytes {p}-{MODEL_BYTES-1}/{MODEL_BYTES}",
                "Gerçek sunucuya doğru bayttan devam eden istek görülmedi.")
        require(body["body_bytes"] == MODEL_BYTES - p, "Yinelenen veya eksik aktarılan bayt var.")
        require(not part.exists() and model.stat().st_size == MODEL_BYTES and sha(model) == MODEL_SHA256,
                "Biten modelin boyutu, özeti veya kısmi dosya durumu doğrulanamadı.")
        report.update(son_sha256=MODEL_SHA256, son_bayt=MODEL_BYTES)
        py = box / ".ortam" / ("Scripts/python.exe" if WINDOW_NAME else "bin/python")
        pic = box / "sinav" / "renk-karti.jpg"
        out = root / "İşlenen"
        subprocess.run([str(py), "sil.py", str(pic), "--cikti", str(out)], cwd=box, check=True, timeout=240)
        check = ("from PIL import Image;import sys,json;"
                 "a=Image.open(sys.argv[1]); b=Image.open(sys.argv[2]);"
                 "assert a.mode=='RGBA' and a.getchannel('A').getextrema()==(0,255) and b.size==(1600,1600);"
                 "print(json.dumps(dict(png=a.size,alfa=a.getchannel('A').getextrema(),jpg=b.size)))")
        report["cikti"] = json.loads(subprocess.check_output([str(py), "-c", check,
                                     str(out / (pic.name + "_seffaf.png")), str(out / (pic.name + "_beyaz.jpg"))],
                                     cwd=box))
        report["gecti"] = True
    finally:
        if existing.exists() and not model.exists():
            model.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(existing, model)


def main():
    p = argparse.ArgumentParser()
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", type=Path)
    source.add_argument("--ci-box", type=Path)
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    report = {"platform": sys.platform, "gecti": False}
    if args.zip:
        report["zip_sha256"] = sha(args.zip)
    if args.ci_box:
        require(os.environ.get("GITHUB_ACTIONS") == "true", "Bu seçenek yalnız uçucu CI makinesi içindir.")
    with contextlib.ExitStack() as clean:
        tmp = Path(clean.enter_context(tempfile.TemporaryDirectory(prefix="ArkaPlan-17-kesinti-")))
        try:
            box = args.ci_box
            if args.zip:
                subprocess.run(["/usr/bin/ditto", "-x", "-k", str(args.zip), str(tmp)], check=True)
                box = tmp / "Projeler" / "Arka_Plan_Silici"
            exercise(box, tmp, report)
        except Exception as e:
            report["hata"] = repr(e)
            raise
        finally:
            clean.close()
            report["gecici_klasor_silindi"] = not tmp.exists()
            if args.json:
                args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("KESINTI_JSON " + json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
