#!/bin/bash
# Ogrencinin Mac'te yaptigini taklit eder: karantinali zip, Finder gibi acma, Turkce ve $'li klasor,
# Homebrew'suz PATH. Konsola yalniz adim sonucu; ayrinti sifrelenecek kayda.
URUN="$1"; ZIP="$2"; VARLIK="$3"; LOG="$4"
KOK="$HOME/Desktop/Ayda 398 \$ Tasarruf/İndirilenler Şükrü Öz"
mkdir -p "$KOK"
xattr -w com.apple.quarantine "0081;00000000;Safari;" "$ZIP" 2>/dev/null
ditto -x -k "$ZIP" "$KOK"
PROJE=$(find "$KOK/Projeler" -mindepth 1 -maxdepth 1 -type d | head -1)
TEMIZ="/usr/bin:/bin:/usr/sbin:/sbin"
KALDI=0
adim() { # ad dakika komut
  local ad="$1" dk="$2" komut="$3" bas=$(date +%s) kod
  printf '\n===== %s\n> %s\n' "$ad" "$komut" >> "$LOG"
  ( cd "$PROJE" && PATH="$TEMIZ:$HOME/.local/bin" timeout_sn=$((dk*60)) perl -e 'alarm shift; exec @ARGV' $((dk*60)) /bin/bash -c "$komut" ) >> "$LOG" 2>&1
  kod=$?
  local durum="GECTI"; [ $kod -ne 0 ] && { durum="KALDI (cikis $kod)"; KALDI=1; }
  printf '%-34s %s  (%s sn)\n' "$ad" "$durum" "$(( $(date +%s) - bas ))" | tee -a "$LOG"
  return $kod
}
echo "Urun $URUN · $(uname -m) · macOS $(sw_vers -productVersion)"
case "$URUN" in
  14) adim "kur" 45 "bash kur.sh"
      adim "birlestir" 10 "curl -sf -o /tmp/b.pdf -F fileInput=@'$VARLIK/a.pdf' -F fileInput=@'$VARLIK/b.pdf' -F sortType=orderProvided http://127.0.0.1:18765/api/v1/general/merge-pdfs && head -c4 /tmp/b.pdf | grep -q '%PDF'"
      adim "kaldir" 10 "bash kaldir.sh" ;;
  15) adim "kur (Intel'de durmali)" 30 "bash kur.sh" ;;
  16) adim "Intel destegi: indirmeden aciklama" 2 'test "$(uname -m)" = x86_64 || exit 1
      mesaj=$(bash kur.sh 2>&1); kod=$?
      echo "$mesaj"
      test "$kod" = 1 && grep -q "Apple Silicon" <<< "$mesaj" &&
      test ! -e "$HOME/Applications/meetily.app" &&
      test ! -d "$HOME/Library/Application Support/com.meetily.ai/models"' ;;
  17) adim "kur" 45 "bash kur.sh"
      sinav17="$(cd -- "$(dirname -- "$0")" && pwd)/17-kesinti.py"
      adim "kesinti ve bayttan devam" 35 "./.ortam/bin/python '$sinav17' --ci-box '.'"
      adim "sil" 20 "mkdir -p 'Fotoğraflarım' && cp '$VARLIK/urun.jpg' 'Fotoğraflarım/ürün fotoğrafı.jpg' && ./.ortam/bin/python sil.py 'Fotoğraflarım' --cikti 'İlanlarım' --golge && ls 'İlanlarım' | grep -q ."
      adim "kaldir" 10 "bash kaldir.sh" ;;
esac
exit $KALDI
