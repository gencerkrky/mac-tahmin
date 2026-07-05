#!/usr/bin/env python3
"""API-Football'dan günün futbol maçlarını çekip ekrana basan test scripti.

Kullanım:
    export API_FOOTBALL_KEY="senin_anahtarin"
    python3 fetch_fixtures.py            # bugünün maçları
    python3 fetch_fixtures.py 2026-07-06 # belirli bir gün

Ücretsiz plan: ~100 istek/gün, kredi kartı gerekmez.
Kayıt: https://www.api-football.com/
"""

import os
import sys
from datetime import date, datetime

import requests

# API-Football v3 ana adresi. Ücretsiz plan bu host üzerinden çalışır.
API_BASE_URL = "https://v3.football.api-sports.io"
FIXTURES_ENDPOINT = f"{API_BASE_URL}/fixtures"

# Ağ isteklerinin sonsuza kadar askıda kalmaması için makul bir zaman aşımı.
REQUEST_TIMEOUT_SECONDS = 15


def get_api_key() -> str:
    """API anahtarını ortam değişkeninden okur; yoksa anlaşılır bir hata verir."""
    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        sys.exit(
            "Hata: API_FOOTBALL_KEY ortam değişkeni tanımlı değil.\n"
            "Önce şunu çalıştırın:  export API_FOOTBALL_KEY=\"senin_anahtarin\"\n"
            "Anahtar için: https://www.api-football.com/ (ücretsiz kayıt)"
        )
    return api_key


def parse_target_date() -> str:
    """Komut satırından tarihi alır; verilmezse bugünü kullanır.

    Tarih formatını burada doğrularız ki API'ye geçersiz istek göndermeyelim.
    """
    if len(sys.argv) < 2:
        return date.today().isoformat()

    raw_date = sys.argv[1]
    try:
        # Sadece formatı doğrulamak için parse edip tekrar stringe çeviriyoruz.
        return datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        sys.exit(f"Hata: Geçersiz tarih '{raw_date}'. Beklenen format: YYYY-MM-DD")


def fetch_fixtures(api_key: str, target_date: str) -> list:
    """Belirtilen gün için maç listesini API'den çeker.

    Her ağ/HTTP hatasını yakalayıp kullanıcıya açık mesaj döneriz;
    sessizce yutmayız.
    """
    headers = {"x-apisports-key": api_key}
    params = {"date": target_date}

    try:
        response = requests.get(
            FIXTURES_ENDPOINT,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        sys.exit(f"API isteği başarısız oldu: {exc}")

    payload = response.json()

    # API-Football hataları 200 içinde 'errors' alanında da dönebilir.
    api_errors = payload.get("errors")
    if api_errors:
        sys.exit(f"API hata döndürdü: {api_errors}")

    return payload.get("response", [])


def print_fixtures(fixtures: list, target_date: str) -> None:
    """Maçları okunabilir biçimde ekrana yazar."""
    if not fixtures:
        print(f"{target_date} için maç bulunamadı.")
        return

    print(f"\n{target_date} — {len(fixtures)} maç bulundu:\n")

    for item in fixtures:
        league = item["league"]["name"]
        country = item["league"]["country"]
        home = item["teams"]["home"]["name"]
        away = item["teams"]["away"]["name"]

        # Başlama saati ISO formatında gelir; sadece saat kısmını gösteriyoruz.
        kickoff_iso = item["fixture"]["date"]
        kickoff_time = kickoff_iso[11:16] if len(kickoff_iso) >= 16 else kickoff_iso

        status = item["fixture"]["status"]["short"]
        goals_home = item["goals"]["home"]
        goals_away = item["goals"]["away"]

        # Maç başladıysa/bittiyse skoru, başlamadıysa saati gösteririz.
        if goals_home is not None and goals_away is not None:
            score = f"{goals_home}-{goals_away}"
        else:
            score = "-:-"

        print(
            f"  [{kickoff_time}] {country} · {league}\n"
            f"      {home}  {score}  {away}  ({status})"
        )


def main() -> None:
    api_key = get_api_key()
    target_date = parse_target_date()
    fixtures = fetch_fixtures(api_key, target_date)
    print_fixtures(fixtures, target_date)


if __name__ == "__main__":
    main()
