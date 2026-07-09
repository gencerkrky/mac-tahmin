# Maç Kartı UX Sadeleştirmesi + Cron Saati — Tasarım

Tarih: 2026-07-09

## Amaç

İki bağımsız iyileştirme:

1. **Cron saati:** Günlük site üretimi sabah ~08:00 TR'de çalışsın (şu an ~09:37 TR).
2. **Maç kartı UX'i:** Her kartın en üstüne tek net "öne çıkan tahmin" özeti;
   anlaşılması zor satırları (Çifte Şans, İY/MS, Tek/Çift) açıklama ile birlikte
   katlanabilir "Detaylı olasılıklar" bölümüne al.

Model/veri tarafına dokunulmuyor — gösterilen tüm alanlar (`best_pick`,
`most_likely_score`, `double_chance`, `htft`, `odd_even`) zaten üretiliyor.

## Değişecek dosyalar

- `.github/workflows/daily.yml` — cron ifadesi + yorum.
- `site_template/index.html` — `predHTML`, `basketballPredHTML` render fonksiyonları
  ve ilgili CSS.

Python, model, test dosyalarına dokunulmuyor.

## 1. Cron

- `cron: "37 6 * * *"` → `cron: "7 5 * * *"` (05:07 UTC ≈ 08:07 TR).
- Tam saat başı (0. dakika) GitHub'da yoğun/atlanan slot olduğu için `:07`
  dakikası korunur.
- Tek çalışma (yedek 2. deneme istenmedi).
- Yorum bloğu yeni saate göre güncellenir.

## 2. Maç kartı — "Öne çıkan" özeti

Her maç kartında, detay satırlarından **önce** vurgulu bir blok:

```
⭐ ÖNE ÇIKAN
2.5 ALT · %67 · @1.49
Tahmini skor: 0-1
```

- **Bahis:** mevcut `best_pick` (en yüksek olasılıklı seçim). `pickLabel`/takım
  adı mantığı korunur.
- **Skor:** mevcut `most_likely_score`.
- **Basketbol:** aynı desen — `best_pick` + beklenen skor (`expected_points`).

Yeni CSS sınıfı `.headline` (accent kenarlıklı, büyük punto).

## 3. Detay sadeleştirme

Kartta **varsayılan açık** kalanlar:
- Maç sonucu (1/X/2)
- 2.5 Üst/Alt
- KG Var/Yok
- En olası skor

Katlanabilir **"▸ Detaylı olasılıklar"** (`<details>`) içine alınanlar:
- 1.5 Üst/Alt, 3.5 Üst/Alt
- Çifte Şans (1X/12/X2)
- Toplam Gol Tek/Çift
- İY/MS (en olası 3)
- Tüm skorlar tablosu (mevcut `<details>`, bu üst detayın içine yerleşir)

Detay bölümünün başında bir kez küçük açıklama satırları:
- **Çifte Şans:** "İki sonuçtan biri tutsa yeter — 1X: ev *veya* berabere,
  12: berabere olmaz, X2: deplasman *veya* berabere."
- **İY/MS:** "İlk yarı / maç sonu yönü (2/2 = ev önde başlar, ev kazanır)."
- **Tek/Çift:** "Toplam gol sayısı tek mi çift mi."

Açıklamalar yalnızca genişletilmiş Poisson çıktısı (`p.over_under`) varken gösterilir.

## Hata / kenar durumlar

- `p.over_under` yoksa (eski/eksik veri): sadece "öne çıkan" + en olası skor
  gösterilir, detay bölümü hiç render edilmez (mevcut davranış korunur).
- `best_pick` yoksa öne çıkan blok atlanır, kart yine de listelenir.

## Test / doğrulama

- `python generate_site.py` çalıştır, `public/index.html` + `data.json` üret.
- Tarayıcıda aç: öne çıkan blok görünür, detay katlı açılıp kapanır, açıklamalar
  doğru, basketbol kartı da öne çıkan özeti gösteriyor.
- Mevcut Python testleri değişmediği için etkilenmez; yine de koşulup geçtiği
  doğrulanır.
