# İddaa Tahmin Sistemi

Poisson tabanlı futbol/basketbol maç tahmini, günlük otomatik kupon ve
isabet takibi. Veri kaynağı ESPN'in açık API'si (anahtar gerektirmez).

## Modeli değerlendirirken bilinmesi gerekenler

Bu bölüm önce geliyor çünkü sayıların ne anlama geldiğini bilmeden kullanmak
yanıltıcı olur.

**Ölçülen isabet: ~%57** (803 maç, `evaluate.py`, depodaki gerçek maçlar
üzerinde sızıntısız backtest). Bu, modelin her maç için seçtiği tek "en iyi
tahmin"in tutma oranıdır.

**Model kendine olduğundan fazla güveniyor.** Ölçülen kalibrasyon:

| Model diyor | Gerçekte tutuyor | Fark |
|---|---|---|
| %57 | %55 | −2 puan |
| %65 | %56 | −9 puan |
| %75 | %55 | −20 puan |
| %86 | %64 | −22 puan |

Yani ekranda görünen yüzde, gerçek tutma olasılığından yüksektir ve fark
güven arttıkça büyür. "%86" yazması o bahsin %86 tutacağı anlamına gelmez.
Bu, Poisson modellerinin bilinen bir sınırıdır (gol sayıları maç sonucunu
öngörmekte zayıf bir tabandır), parametre ayarıyla kapanmaz.

**Kupon tek maç önerir.** Her ek bacak tutma şansını çarpar: %57'lik üç
bacaklı kupon ~%18 tutar. Tek güçlü tahmin hem daha dürüst hem daha sık
tutar.

**Bu bir kazanç garantisi değildir.** Bahis şirketi marjı (~%5-8) düşüldükten
sonra %57 isabet uzun vadede kâr anlamına gelmez.

## Kurulum

```bash
pip install -r requirements.txt
```

Yerel web arayüzü:

```bash
python app.py            # http://localhost:5001
```

## Maç deposu (warehouse)

`warehouse.py` ESPN geçmişini SQLite'a indirir. Amacı model üzerinde deney
yapmayı mümkün kılmak: depo olmadan tek bir backtest ölçümü 10+ dakika
sürerdi (her takım için ağ çağrısı), depoyla ~30 saniye.

```bash
python warehouse.py full 2      # 2 sezonluk tüm ligleri indir (uzun sürer)
python warehouse.py daily 7     # son 7 günü tazele (günlük iş)
python warehouse.py stats 500   # eksik kutu skorlarını (şut, topla oynama) doldur
python warehouse.py info        # depo özeti
```

Depo `warehouse.db` dosyasında tutulur ve repoya girmez (`.gitignore`).
GitHub Actions'ta Pages üzerinden taşınır: her sabah indirilir, tazelenir,
geri yayınlanır.

Saklanan veri: skor, ev/deplasman, rakip, sezon ve ESPN'in kutu skoru
istatistikleri (şut, isabetli şut, topla oynama, korner, faul, kart).

## Model değerlendirme

`evaluate.py` depodaki maçlar üzerinde modeli sızıntısız koşturur. Her maç
yalnızca **kendisinden önce oynanmış** maçlarla tahmin edilir (zaman
dilimleme SQL'de yapılır).

```bash
python evaluate.py              # tüm depo
python evaluate.py tur.1        # tek lig
```

Raporlanan üç ölçüt:

- **isabet** — en iyi tahminin tutma oranı
- **Brier** — olasılıkların kalitesi (düşük iyi). İsabetin göremediğini
  görür: %51 deyip haklı çıkmak, %90 deyip aynı sıklıkta haklı çıkmaktan
  iyidir.
- **kalibrasyon hatası** — "%70" gerçekten %70 mü? Güven bandı başına
  raporlanır, çünkü model genelde iyi kalibre olup tam da kuponun seçim
  yaptığı bantta bozuk olabilir.

Model varyantı denemek için `evaluate(matches, predict_fn=...)`.

## Dosya düzeni

| Dosya | Sorumluluk |
|---|---|
| `poisson.py` | Saf tahmin motoru (ağ yok, IO yok) |
| `basketball.py` | Basketbol modeli (ayrı, sayı tabanlı) |
| `api_client.py` | ESPN çağrıları, önbellek, hata normalizasyonu |
| `warehouse.py` | Maç geçmişi deposu (SQLite) |
| `evaluate.py` | Depo üzerinde model değerlendirme |
| `backtest.py` | Canlı hattın belirli tarihlerdeki performansı |
| `store.py` | Kupon kalıcılığı ve isabet takibi |
| `app.py` | Flask API + kupon kurucu |
| `generate_site.py` | Statik site üretimi (GitHub Pages) |
| `ai_analysis.py` | Tahmini *açıklar* — tahmin üretmez |

## Testler

```bash
python -m pytest -q
```

Tüm testler ağdan bağımsızdır (ESPN çağrıları monkeypatch'lenir, SQLite
geçici dosyada).

## Günlük otomasyon

`.github/workflows/daily.yml` her sabah 05:07 UTC'de (≈08:07 TR):

1. Önceki geçmişi ve maç deposunu Pages'ten indirir
2. Depoyu son 7 günle tazeler, eksik kutu skorlarını doldurur
3. Biten maçları sonuçlandırır, günün kuponunu ve bültenini üretir
4. Siteyi ve depoyu Pages'e yayınlar
