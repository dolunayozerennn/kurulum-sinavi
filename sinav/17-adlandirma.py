"""Run against an installed product 17 exam box using its normal launchers and genuine photo."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import unicodedata

from PIL import Image


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nfc(name):
    return unicodedata.normalize("NFC", name)


def images(folder):
    return {nfc(p.name): p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg"}}


def run(box, inputs, output, options):
    win = os.name == "nt"
    launcher = (["cmd.exe", "/d", "/c", "Baslat.cmd"] if win else ["/bin/bash", "./Baslat.command"])
    started = time.monotonic()
    p = subprocess.Popen(launcher + [str(inputs), "--cikti", str(output), *options], cwd=box,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"),
                         **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if win else {"start_new_session": True}))
    try:
        raw = p.communicate(timeout=420)[0]
    except subprocess.TimeoutExpired:
        if win:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise
    return {"baslatici": launcher[-1], "secenekler": options, "kod": p.returncode,
            "saniye": round(time.monotonic()-started,2), "ozet": raw.decode("utf-8", "replace").strip()}


def inspect(files):
    result = {}
    for name, path in sorted(files.items()):
        with Image.open(path) as im:
            im.load()
            row = {"bicim": im.format, "mod": im.mode, "boyut": list(im.size), "sha256": sha(path)}
            if im.format == "PNG" and im.mode == "RGBA":
                row["alfa"] = list(im.getchannel("A").getextrema())
            result[name] = row
    return result


def remove_test_diagnostic(path, before, scratch):
    if not path.exists():
        return
    base = before or b""
    after = path.read_bytes()
    if after == base:
        return
    require(after.startswith(base), "Önceki işlem günlüğü değişmiş.")
    added = after[len(base):].decode("utf-8")
    headers = re.findall(r"(?m)^\d{4}-\d\d-\d\d .*? (?:WARNING|ERROR|INFO|CRITICAL|DEBUG) (.*)$", added)
    require(len(headers)==1 and scratch.name in headers[0] and "a.png" in headers[0],
            "İşlem günlüğünde başka bir kayıt bulundu. Sınav kaydı ayrıştırılamadı.")
    if before is None:
        path.unlink()
    else:
        with path.open("r+b") as stream:
            stream.truncate(len(base))


def exercise(box, tmp, report):
    inputs, output = tmp / "Fotoğraflarım", tmp / "İlanlarım"
    inputs.mkdir()
    output.mkdir()
    solo = "Ürün fotoğrafı 1"
    dimensions = {solo+".jpg": (480, 360), "a.jpg": (320, 240), "a.png": (224,168)}
    with Image.open(box / "sinav" / "renk-karti.jpg") as pic:
        for name,size in dimensions.items():
            pic.convert("RGB").resize(size).save(inputs/name)
        # Two old-format image files already belong to the student.
        pic.convert("RGBA").save(output/(solo+".jpg_seffaf.png"))
        pic.convert("RGB").save(output/(solo+".jpg_beyaz.jpg"))
    old = {name: sha(path) for name,path in images(output).items()}

    first = report["ilk"] = run(box, inputs, output, ["--golge"])
    require(first["kod"] == 0 and "Tamamlanan: 3. Atlanan: 0." in first["ozet"], "Üç fotoğraf işlenmedi.")
    all_files = images(output)
    require(all(sha(all_files[n])==h for n,h in old.items()), "Eski adla duran görsel değiştirildi.")
    new = {name:p for name,p in all_files.items() if name not in old}
    first["gorseller"] = inspect(new)
    data = first["gorseller"]
    require(len(new)==9, "Üç girdi için altı standart çıktı ve üç gölge bekleniyordu.")
    png = {n:r for n,r in data.items() if r["bicim"]=="PNG"}
    white = {n for n,r in data.items() if r["bicim"]=="JPEG" and "_beyaz" in n}
    shadow = {n for n,r in data.items() if r["bicim"]=="JPEG" and "_golgeli" in n}
    require(len(png)==len(white)==len(shadow)==3, "Standart görseller veya isteğe bağlı gölgeler eksik.")
    require({tuple(r["boyut"]) for r in png.values()}==set(dimensions.values()) and
            all(r.get("alfa")==[0,255] for r in png.values()), "Kaynağa özgü saydam PNG boyutları eşleşmedi.")
    require(all(data[n]["boyut"]==[1600,1600] for n in white|shadow), "İlan görseli 1600 kare olmalı.")
    by_input = {name:next(n for n,r in png.items() if tuple(r["boyut"])==size)
                for name,size in dimensions.items()}
    require(by_input[solo+".jpg"]==solo+"_seffaf.png" and solo+"_beyaz.jpg" in white and
            solo+"_golgeli.jpg" in shadow, "Tek gövdenin sade çıktı adları eşleşmedi.")
    report["kaynak_png_eslesmesi"] = by_input

    # End markers keep the files decodable and prove exactly which previous files were replaced.
    tag = b"\n17-A-previous-output\n"
    for name in set(png)|white:
        with new[name].open("ab") as f:
            f.write(tag)
    before = {name:sha(path) for name,path in all_files.items()}
    original_png = (inputs/"a.png").read_bytes()
    (inputs/"a.png").write_bytes(b"17-A invalid image")
    rerun = report["tekrar"] = run(box, inputs, output, [])
    require(rerun["kod"]==2 and "Tamamlanan: 2. Atlanan: 1." in rerun["ozet"],
            "Bozuk dosya atlanırken diğer iki fotoğrafın işlenmesi bekleniyordu.")
    after = images(output)
    require(set(after)==set(all_files), "Tekrar çalışınca eski bir görsel kayboldu veya isimler değişti.")
    final_hashes = {n:sha(p) for n,p in after.items()}
    unchanged = {n for n,h in before.items() if final_hashes[n]==h}
    failed_png = by_input["a.png"]
    # Compare the common source part, independent of which collision suffix the product uses.
    source_key = Path(failed_png).stem.replace("_seffaf", "", 1)
    failed_jpegs = {n for n in white if Path(n).stem.replace("_beyaz", "", 1)==source_key}
    require(len(failed_jpegs)==1 and unchanged & set(png)=={failed_png} and unchanged & white==failed_jpegs,
            "Atlanan çakışan kaynağın iki önceki çıktısı korunamadı.")
    require(unchanged.issuperset(set(old)|shadow), "Eski ad veya istenmeyen gölge yeniden yazıldı.")
    changed = set(after)-unchanged
    require(len(changed)==4 and all(not after[n].read_bytes().endswith(tag) for n in changed),
            "Seçili iki görselin dört çıktısı yenilenmedi.")
    final_data = inspect({n:after[n] for n in changed})
    for name,d in final_data.items():
        require(d["boyut"]==data[name]["boyut"] and d["mod"]==data[name]["mod"] and
                d.get("alfa")==data[name].get("alfa"), "Yeniden yazılan dosyanın kaynağı değişti.")
    report["yenilenen"] = final_data
    report["korunan_sha256"] = {n:final_hashes[n] for n in sorted(unchanged)}

    # Open only the repaired file; its sibling is outside the input selection this time.
    (inputs/"a.png").write_bytes(original_png)
    one = report["tek_dosya"] = run(box, inputs/"a.png", output, [])
    require(one["kod"]==0 and "Tamamlanan: 1. Atlanan: 0." in one["ozet"],
            "Onarılan PNG tek başına işlenemedi.")
    last = images(output)
    require(set(last)==set(after), "Tek dosya işlenince yeni bir ad açıldı.")
    renewed = {n for n,p in last.items() if sha(p)!=final_hashes[n]}
    require(renewed==({failed_png}|failed_jpegs), "Tek dosya çalıştırması kardeşinin çıktısını değiştirdi.")
    one["yenilenen"] = inspect({n:last[n] for n in renewed})
    require(one["yenilenen"][failed_png]["boyut"]==list(dimensions["a.png"]) and
            one["yenilenen"][failed_png].get("alfa")==[0,255], "Tek dosyanın PNG ölçüsü veya maskesi yanlış.")



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--box",type=Path,required=True)
    parser.add_argument("--json",type=Path)
    args = parser.parse_args()
    box = args.box.resolve()
    report = {"gecti":False,"sistem":os.name}
    diagnostic = box / "islem-hatasi.txt"
    previous = diagnostic.read_bytes() if diagnostic.exists() else None
    with tempfile.TemporaryDirectory(prefix="ArkaPlan17-A-") as dirname:
        tmp = Path(dirname)
        try:
            exercise(box,tmp,report)
            report["gecti"]=True
        except Exception as error:
            report["hata"] = str(error)
        finally:
            try:
                remove_test_diagnostic(diagnostic,previous,tmp)
                report["gunluk_korundu"] = ((diagnostic.read_bytes() if diagnostic.exists() else None) == previous)
            except Exception as error:
                report.update(gecti=False,temizlik_hatasi=str(error))
    report["gecici_klasor_silindi"] = not tmp.exists()
    if args.json:
        args.json.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    print("ADLANDIRMA_JSON "+json.dumps(report,ensure_ascii=True),flush=True)
    return 0 if report["gecti"] and report.get("gunluk_korundu") and report["gecici_klasor_silindi"] else 1


if __name__=="__main__":
    raise SystemExit(main())
