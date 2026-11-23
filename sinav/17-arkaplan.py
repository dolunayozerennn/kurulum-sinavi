"""A short-lived student's shell must be able to start one independent, observable installer."""
import argparse
import contextlib
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = Path(__file__).resolve()
WIN = os.name == "nt"
INSTALL = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\\kur.ps1"]
           if WIN else ["/bin/bash", "kur.sh"])
FRESH_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {"start_new_session": True})


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def atomic_json(path, data):
    temp = path.with_suffix(".new")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    os.replace(temp, path)


def command(args, box, timeout=25):
    start = time.monotonic()
    p = subprocess.run(args, cwd=box, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout, env=dict(os.environ, PYTHONUTF8="1"))
    return {"kod": p.returncode, "sn": round(time.monotonic()-start, 3),
            "satir": p.stdout.decode("utf-8","replace").strip()[-1300:]}


def wrapper(box, gate, capture):
    # The launcher waits after the start call exits so killing its *whole* tree is observable.
    deadline = time.monotonic()+30
    while not gate.exists() and time.monotonic() < deadline:
        time.sleep(0.03)
    require(gate.exists(),"Başlangıç işareti ulaşmadı.")
    atomic_json(capture, command(INSTALL + ["baslat"], box))
    time.sleep(120)


def running_worker(box):
    try:
        if WIN:
            state = json.loads((box / ".arka-plan-kurulum" / "durum.json").read_text(encoding="utf-8-sig"))
            if state.get("asama") == "calisiyor" and int(state["pid"]) > 0:
                return {"pid": int(state["pid"]), "kimlik": state["kimlik"]}
            return {}
        pieces = (box / ".kurulum-takibi" / "durum").read_text().strip().split()
        if len(pieces) == 3 and pieces[0] == "calisiyor":
            return {"pid": int(pieces[1]), "grup": int(pieces[2])}
    except (OSError, ValueError, KeyError):
        pass
    return {}


def exists(pid):
    if WIN:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        lib.OpenProcess.restype = ctypes.c_void_p
        lib.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.CloseHandle.argtypes = [ctypes.c_void_p]
        h = lib.OpenProcess(0x00100000, 0, pid)
        if not h:
            return False
        try:
            return lib.WaitForSingleObject(h, 0) == 258
        finally:
            lib.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def terminate(pid, group=None):
    if WIN:
        return subprocess.run(["taskkill.exe", "/T", "/F", "/PID", str(pid)], capture_output=True).returncode
    try:
        os.killpg(group or pid, signal.SIGTERM)
        return 0
    except ProcessLookupError:
        return 1


def run_background(box, tmp, info):
    commands = []
    seen = {}
    try:
        gate = tmp / "basla"
        done = [tmp / ("sonuc%d.json"%i) for i in range(2)]
        for record in done:
            commands.append(subprocess.Popen([sys.executable, str(HERE),"--launcher",str(box),str(gate),str(record)],
                                             stdin=subprocess.DEVNULL, **FRESH_GROUP))
        gate.touch()
        deadline = time.monotonic() + 35
        ids = set()
        while time.monotonic()<deadline:
            state = running_worker(box)
            if state:
                seen = state
                ids.add(state["pid"])
            if all(p.exists() for p in done) and seen:
                break
            if any(p.poll() is not None for p in commands):
                raise RuntimeError("Başlatıcı sonuç üretmeden çıktı.")
            time.sleep(0.08)
        require(all(p.exists() for p in done) and seen, "İki başlat çağrısının kısa sonucu veya işçi kimliği eksik.")
        info["baslat"] = [json.loads(p.read_text(encoding="utf8")) for p in done]
        require(all(r["kod"] == 0 and r["sn"]<25 for r in info["baslat"]), "Çift başlat kısa sürede tamamlanmadı.")
        require(len(ids)==1 and exists(seen["pid"]), "Aynı kutuda tek işçi bulunmalı.")
        info["sahip"] = seen
        if not WIN:
            require(os.getpgid(seen["pid"])==seen["grup"] and seen["grup"] not in {p.pid for p in commands},
                    "İşçi, öğrencinin komut grubuna bağlı kaldı.")
        states = info["izleme"] = []
        def count_status():
            r = command(INSTALL+["durum"], box)
            states.append(r)
            return r["kod"]
        require(count_status()==2, "Süren indirmenin durum kodu 2 olmalı.")
        info["baslatan_gruplar"] = [p.pid for p in commands]
        info["kesme"] = "taskkill.exe /T /F" if WIN else "killpg(SIGTERM)"
        info["kesme_kodlari"] = [terminate(p.pid) for p in commands]
        require(info["kesme_kodlari"]==[0,0], "Öğrencinin iki komut ağacı kapanamadı.")
        for p in commands:
            p.wait(timeout=10)
        require(exists(seen["pid"]) and running_worker(box)==seen and count_status()==2,
                "Kısa çağrıların ağacı ölünce kurulum kayboldu.")
        # Re-enter through the public entry point after both calling shells have died.
        r = info["tekrar_baslat"] = command(INSTALL+["baslat"],box)
        require(r["kod"]==0 and running_worker(box)==seen, "Yeniden açmak ikinci bir işçi başlattı.")
        deadline = time.monotonic()+2400
        while time.monotonic()<deadline:
            time.sleep(12)
            code = count_status()
            if code == 0:
                break
            require(code==2 and running_worker(box)==seen,
                    "Kurulumun durumu veya işçi kimliği beklenmedik şekilde değişti.")
        require(states[-1]["kod"]==0, "Kurulumun başarıyla bitişi doğrulanamadı.")
        for _ in range(75):
            if not exists(seen["pid"]):
                break
            time.sleep(0.2)
        info["is_oldugunda_isci_kapandi"] = not exists(seen["pid"])
        require(info["is_oldugunda_isci_kapandi"], "Kurulumun ardından işçi kapanmadı.")
        py = box / ".ortam" / ("Scripts/python.exe" if WIN else "bin/python")
        pic = box / "sinav" / "renk-karti.jpg"
        output = tmp / "Sonuçlar"
        cut = command([str(py), "sil.py", str(pic), "--cikti", str(output)],box,240)
        require(cut["kod"]==0,cut["satir"])
        inspect = ("from PIL import Image;import sys,json;"
                   "a=Image.open(sys.argv[1]);b=Image.open(sys.argv[2]);"
                   "assert a.mode=='RGBA' and a.getchannel('A').getextrema()==(0,255) and b.size==(1600,1600);"
                   "print(json.dumps(dict(png=a.size,alfa=a.getchannel('A').getextrema(),jpg=b.size)))")
        info["cikti"] = json.loads(subprocess.check_output([str(py), "-c", inspect,
                                    str(output/(pic.stem+"_seffaf.png")), str(output/(pic.stem+"_beyaz.jpg"))],cwd=box))
        info["gecti"] = True
    finally:
        for p in commands:
            if p.poll() is None:
                terminate(p.pid)
                p.wait(timeout=10)
        if not info.get("gecti"):
            state = running_worker(box)
            # Limit emergency cleanup to the worker recorded in this dedicated, disposable box.
            if state and exists(state["pid"]):
                terminate(state["pid"], state.get("grup"))


def main():
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--launcher",nargs=3,type=Path)
    src.add_argument("--ci-box",type=Path)
    src.add_argument("--zip",type=Path)
    p.add_argument("--json",type=Path)
    p.add_argument("--foreground",action="store_true")
    args = p.parse_args()
    if args.launcher:
        wrapper(*args.launcher)
        return
    info = {"platform":sys.platform,"gecti":False}
    if args.ci_box:
        require(os.environ.get("GITHUB_ACTIONS")=="true","Yalnız uçucu CI kutusu kullanılabilir.")
    with contextlib.ExitStack() as clean:
        tmp = Path(clean.enter_context(tempfile.TemporaryDirectory(prefix="ArkaPlan-17-kisa-")))
        try:
            box = args.ci_box.resolve() if args.ci_box else None
            if args.zip:
                info["zip_sha256"] = hashlib.sha256(args.zip.read_bytes()).hexdigest()
                subprocess.run(["/usr/bin/ditto","-x","-k",str(args.zip),str(tmp)],check=True)
                box = tmp / "Projeler" / "Arka_Plan_Silici"
            info["ilk_kurulum_bos"] = not (box/".ortam").exists() and not (box/".model").exists()
            require(info["ilk_kurulum_bos"],"İlk kuruluma boş bir kutu gerekir.")
            run_background(box,tmp,info)
            if args.foreground:
                spec = importlib.util.spec_from_file_location("urun17_kesinti", HERE.with_name("17-kesinti.py"))
                bench = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(bench)
                follow = tmp / "kesinti"
                follow.mkdir()
                inner = info["on_plan_kesinti"] = {"gecti":False}
                bench.exercise(box, follow, inner)
        except Exception as error:
            info.update(gecti=False,hata=repr(error))
            raise
        finally:
            clean.close()
            info["gecici_klasor_silindi"] = not tmp.exists()
            if args.json:
                args.json.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf8")
            print("ARKAPLAN_JSON "+json.dumps(info,ensure_ascii=True),flush=True)


if __name__ == "__main__":
    main()
