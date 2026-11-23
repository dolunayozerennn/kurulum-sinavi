"""Clean Intel Mac test. Run in the extracted product directory on a hosted runner."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time

def live_members(pgid):
    ps = subprocess.check_output(["/bin/ps", "-axo", "pgid=,stat=,comm="], text=True)
    rows = [r.split(None, 2) for r in ps.splitlines() if r.strip()]
    return [r[2] for r in rows if int(r[0]) == pgid and not r[1].startswith("Z")]

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(2**20), b""):
            h.update(block)
    return h.hexdigest()

def is_listening():
    with socket.socket() as s:
        s.settimeout(.4)
        return s.connect_ex(("127.0.0.1",18765)) == 0

def stop_group(proc):
    if live_members(proc.pid):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(12)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(5)

def test(root):
    config = json.loads(Path("surum.json").read_text())
    expected, total = config["jar_sha256"], config["jar_bayt"]
    home = root/"home"
    home.mkdir()
    curl_home = root/"curl"
    curl_home.mkdir()
    env = dict(os.environ, HOME=str(home), CURL_HOME=str(curl_home))
    install = home/"Library/Application Support/PDFAtolyesi"
    part = install/f"motor/indirmeler/{expected}.part"
    final = install/"motor/Stirling-PDF.jar"
    assert not is_listening(), "Test portu dolu"
    stats = root/"transfer.tsv"
    curlrc = curl_home/".curlrc"
    meter = 'write-out = "%output{>>' + str(stats) + '}%{http_code} %{size_download} %{filename_effective}\\n"\n'
    curlrc.write_text(meter + "limit-rate = 2200k\n")
    groups = []
    progress = []
    try:
        first = subprocess.Popen(["/bin/bash","kur.sh"], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, start_new_session=True)
        groups.append(first)
        start = time.monotonic()
        def read_progress():
            for line in first.stdout:
                elapsed = round(time.monotonic()-start, 2)
                print(f"{elapsed}s {line.rstrip()}", flush=True)
                if "%" in line and "MB" in line:
                    progress.append((elapsed, line.rstrip()))
        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        while first.poll() is None and time.monotonic()-start < 260:
            size = part.stat().st_size if part.exists() else 0
            if size >= total//4 and len(progress) >= 2:
                break
            time.sleep(.2)
        else:
            raise RuntimeError("Motor indirilirken iki ilerleme satırı alınamadı")
        assert size < total and not final.exists()
        print("SIGTERM öncesi süreç grubu", first.pid, live_members(first.pid), flush=True)
        os.killpg(first.pid, signal.SIGTERM)
        first.wait(15)
        reader.join(5)
        for _ in range(50):
            if not live_members(first.pid):
                break
            time.sleep(.1)
        left = live_members(first.pid)
        assert not left, left
        saved = part.stat().st_size
        assert 0 < saved < total and first.returncode != 0
        prefix = digest(part)
        print("KESINTI",json.dumps(dict(bayt=saved,toplam=total,grupta_kalan=left,
              cikis=first.returncode,ilk_parca_sha256=prefix,ilerleme=progress),ensure_ascii=False),flush=True)
        curlrc.write_text(meter)
        # Run a fresh, ordinary installer. No lock removal or cache repair by the test.
        retry = subprocess.Popen(["/bin/bash","kur.sh"],env=env,start_new_session=True)
        groups.append(retry)
        assert retry.wait(1200) == 0
        assert digest(final) == expected and final.stat().st_size == total
        with final.open("rb") as f:
            assert hashlib.sha256(f.read(saved)).hexdigest() == prefix
        rows = [line.split(" ", 2) for line in stats.read_text().splitlines()]
        ranges = [(int(n),name) for code,n,name in rows if code=="206" and expected in name]
        assert any(n == total-saved for n,name in ranges), (saved,total,ranges)
        assert not part.exists()
        print("DEVAM",json.dumps(dict(baslangic=saved,http=206,ikinci_aktarim_bayt=total-saved,
              son_sha256=expected,son_bayt=total),ensure_ascii=False),flush=True)
    finally:
        if is_listening() and (install/"motor.pid").is_file():
            subprocess.run(["/bin/bash","kapat.sh"],env=env,timeout=45,check=False)
        for proc in groups:
            if live_members(proc.pid):
                stop_group(proc)
        assert not is_listening()
        assert all(not live_members(proc.pid) for proc in groups)

if __name__ == "__main__":
    assert os.environ.get("RUNNER_OS")=="macOS" and os.environ.get("GITHUB_ACTIONS")=="true"
    with tempfile.TemporaryDirectory(prefix="pdf14-kesinti-",dir=os.environ["RUNNER_TEMP"]) as work:
        test(Path(work))
    assert not Path(work).exists()
    print("TEMIZLIK geçici ev kaldırıldı, süreç grupları boş, ürün portu kapalı.")
