# Futbol Maç Tahmin Sistemi — Tasarım Dokümanı

**Tarih:** 2026-07-05
**Durum:** Uygulandı (revize: veri kaynağı ESPN, AI analiz katmanı eklendi)

> **Revizyon notu (uygulama sırasında):**
> 1. **Veri kaynağı API-Football → ESPN public API.** API-Football ücretsiz
>    planı yalnızca ±1 gün penceresine izin veriyor ve form verisi (`last`
>    parametresi / geçmiş sezonlar) kapalı. ESPN site API'si anahtarsız ve
>    kotasızdır; hem bülteni hem takım sezon sonuçlarını verir. Kota koruması
>    maddeleri (12 maç sınırı hariç) önemini yitirdi; önbellek performans
>    için korunuyor.
> 2. **AI analiz katmanı eklendi** (kanca yerine gerçek özellik): kullanıcı
>    Anthropic anahtarı sağladı. `ai_analysis.py`, Claude Fable 5 ile
>    (Opus 4.8 sunucu taraflı fallback) tahmin yorumları üretir; sonuçlar
>    fixture başına önbelleğe alınır.

## Amaç

Günün futbol maçlarını listeleyen ve seçilen maç için istatistiksel tahmin
üreten, web tabanlı, ücretsiz çalışan bir sistem. Her maç için dört tahmin
gösterilir ve en yüksek olasılıklı olan öne çıkarılır.

## Kapsam

### Dahil
- Seçilen tarihin (bugün veya ileri tarih) maçlarını API-Football'dan çekme
- Seçili birkaç büyük ligle sınırlı analiz (ücretsiz kota koruması)
- Bir maç için iki takımın son ~10 maçından gol istatistiği toplama
- Poisson modeliyle olasılık hesaplama
- Dört tahmin türü: Maç sonucu (1/X/2), 2.5 Üst/Alt, Karşılıklı Gol (Var/Yok),
  en olası kesin skor
- En yüksek olasılıklı tahmini görsel olarak vurgulama
- Adil oran (`1/olasılık`) hesaplama, 2.00+ olanları işaretleme
- **Otomatik kupon:** seçili ligleri analiz edip en yüksek güvenli tahminleri
  sıralama, en emin N maçı (varsayılan 5, ayarlanabilir) kupon olarak sunma;
  toplam oran ve birleşik isabet olasılığı gösterme
- Kota korumak için bellek içi önbellek

### Dışı (YAGNI)
- Hava durumu, sakatlık, kadro verisi (ücretsiz planda güvenilmez, karmaşık)
- AI/Fable yorum katmanı (kod içinde kanca bırakılır, sonradan eklenebilir)
- **Nesine.com veya başka bahis sitesine bağlanma / oradan oran çekme / otomatik
  bahis oynatma** — halka açık API yok, scraping kullanım şartlarını ihlal eder,
  otomatik bahis güvenlik riski. Sistem yalnızca kendi tahminlerini üretir.
- Kullanıcı hesabı, veritabanı, geçmiş tahmin kaydı
- Canlı skor takibi

## Tahmin Yöntemi (istatistiksel çekirdek)

Standart Poisson gol modeli:

1. Her takımın son ~10 maçından attığı/yediği gol ortalaması alınır.
2. Lig ortalama gol sayısına göre her takımın **hücum gücü** ve
   **savunma zaafı** oranları hesaplanır.
3. Bu maç için beklenen goller:
   - `λ_ev  = ev_hücum × dep_savunma_zaafı × lig_ort`
   - `λ_dep = dep_hücum × ev_savunma_zaafı × lig_ort`
4. Poisson olasılık kütle fonksiyonu ile 0..N gol matrisi (skor olasılıkları)
   üretilir.
5. Bu matristen türetilir:
   - **1/X/2:** ev>dep / ev=dep / ev<dep hücrelerinin toplamı
   - **2.5 Üst/Alt:** toplam gol ≥3 olan hücrelerin toplamı
   - **KG Var/Yok:** her iki takımın da ≥1 gol attığı hücreler
   - **En olası skor:** en yüksek olasılıklı tek hücre

> Not: Kesin skor olasılıkları doğası gereği düşüktür (en olası skor genelde
> %12-18). Yüksek yüzdeler yalnızca geniş kategorilerde (1/X/2, üst/alt, KG)
> çıkar. En olası skor kendi gerçek yüzdesiyle gösterilir; yanıltıcı yüksek
> yüzde atfedilmez.

Kesinlik garanti edilmez; tutarlı ve açıklanabilir olasılıklar üretir.

## Mimari

```
Tarayıcı (tek HTML sayfa)
   │  fetch() JSON
   ▼
Flask backend            ← API anahtarı sunucuda, tarayıcıya asla gitmez
   ├── GET /api/fixtures?date=YYYY-MM-DD  → o tarihin (seçili ligler) maç listesi
   ├── GET /api/predict?fixture=<id>      → o maç için 4 tahmin
   └── GET /api/coupon?date=...&size=5    → en emin N maçtan otomatik kupon
```

Seçili ligler `LEAGUES` sabitinde tutulur (lig id → ad). Ücretsiz kotayı
korumak için kupon yalnızca bu ligleri analiz eder.

### Bileşenler ve sorumlulukları

| Dosya | Sorumluluk | Bağımlılık |
|-------|-----------|-----------|
| `poisson.py` | Saf istatistik: gol ort. → olasılıklar. Ağ/IO yok, tam test edilebilir. | yok (stdlib `math`) |
| `api_client.py` | API-Football çağrıları, hata yönetimi, önbellek. | requests |
| `app.py` | Flask rotaları, api_client + poisson'u birleştirir, kupon üretir, JSON döner. | flask |
| `static/index.html` | Tek sayfa arayüz: maç listesi + tahmin kartları. | yok (vanilla JS) |
| `test_poisson.py` | poisson.py birim testleri. | pytest |

**Sınır netliği:** `poisson.py` hiçbir ağ çağrısı bilmez — sadece sayı alır,
olasılık döner. Bu sayede bağımsız test edilir ve API değişse bile bozulmaz.
`api_client.py` veri getirir, `app.py` ikisini birleştirir.

## Veri Akışı (bir tahmin isteği)

1. Kullanıcı arayüzde bir maça tıklar → `GET /api/predict?fixture=123`
2. `app.py` → `api_client.get_team_recent_goals(home_id)` ve `(away_id)`
   (önbellekte varsa oradan)
3. Ortalamalar `poisson.predict(...)` fonksiyonuna verilir
4. Dört tahmin + en olası skor + oranlar hesaplanır
5. JSON olarak arayüze döner, en yüksek olasılıklı vurgulanır

## Otomatik Kupon Mantığı

1. Seçili tarih + `LEAGUES` listesindeki maçlar çekilir.
2. Her maç için `poisson.predict(...)` çalıştırılır; dört tahminin en yüksek
   olasılıklı olanı o maçın "en iyi bahsi" olur.
3. Tüm maçlar bu en iyi bahsin olasılığına göre azalan sıralanır.
4. En üstteki N maç (varsayılan 5) kupona alınır.
5. Kupon metrikleri:
   - **Toplam oran** = seçilen bahislerin adil oranlarının çarpımı
   - **Birleşik isabet olasılığı** = seçilen olasılıkların çarpımı
     (bağımsızlık varsayımı; not olarak "tahmini" ibaresiyle sunulur)

> Uyarı metni: kupondaki oranlar modelin *adil oranlarıdır*, bir bahis
> şirketinin oranları değildir. Karşılaştırma kullanıcıya bırakılır.

## Hata Yönetimi

- API anahtarı yoksa → başlangıçta açık hata, sunucu başlamaz
- API çağrısı başarısız (ağ/HTTP/kota) → 502 + anlaşılır mesaj, arayüzde gösterilir
- Yetersiz maç verisi (yeni sezon, az maç) → lig ortalamasına düşülür, uyarı notu
- Geçersiz tarih/fixture → 400

## Kota Yönetimi

- Ücretsiz plan ~100 istek/gün
- Bir tahmin ≈ 2 istek (iki takımın form verisi)
- Bellek içi önbellek (süreç ömrü boyunca): aynı takımın verisi tekrar
  çekilmez. Fixture listesi de günlük anahtarla önbelleğe alınır.

## Test Stratejisi

- `poisson.py` saf fonksiyonları için birim testler: bilinen girdi→çıktı,
  olasılıkların 1.0'a toplanması, simetri (eşit takımlar → eşit 1 ve 2), sınır
  durumları.
- api_client ve app entegrasyon katmanı; TDD öncelik poisson çekirdeğinde.

## Güvenlik

- API anahtarı `.env` dosyasında, `.gitignore`'da. `.env.example` şablon olarak.
- Anahtar yalnızca backend'de; frontend'e hiçbir zaman gönderilmez.
- Kullanıcı girdileri (tarih, fixture id) doğrulanır.

## Gelecek Kancalar (bu spec'te uygulanmaz)

- `app.py` içinde tahmin sonrası opsiyonel `explain(prediction)` çağrısı için
  yer bırakılır; ileride Anthropic API ile doğal dil açıklaması eklenebilir.
