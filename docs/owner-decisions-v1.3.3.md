# Tycho owner kararları v1.3.3 (26 Ekim 2026, sahip: jj)
Monograf v1.3.2'ye bağlayıcı ektir. Bu dosya ile monograf/registry farklıysa bu dosya geçerlidir.

## D-39 Sıfır hedef / yön değişimi (#45)
Hedef 0 ve pozisyon ≠ 0 ise, ya da sign(hedef) ≠ sign(mevcut ağırlık) ve ikisi de ≠ 0 ise:
farkın tamamı işlem görür; bant ve kısmi işlem oranı uygulanmaz.
Yön değişimi şöyle yürür: eski pozisyon tamamen kapatılır (realize edilir), kalan kısım aynı fiyattan açılır.
Pozisyonu tamamen sıfıra kapatmak min_notional altında da serbesttir.
Config: execution.zero_crossing_rule = "full_trade_on_zero_or_cross".
ledger-spec'teki "koordinatör yorumu" notu normatif hale gelir.

## D-40 Null'lar ve temel çizgiler (#46)
Bütün null'lar stratejinin gerçekleşen hedef ağırlık paneli w[t, varlık] üzerinden üretilir.
Hepsi aynı sıralı ledger'dan geçirilir: aynı maliyet, fonlama, dolum ve gecikme.
Maliyet ve turnover KORUNMAZ, yeniden hesaplanır.
Bir varlığın uygun günleri = varlığın PIT evrende ve listede olduğu günler.
Blok = uygun günlerde w'nin işaretinin (+, -, 0) aynı kaldığı en uzun ardışık dizi.

(a) Pozisyon dönemi bazlı yön karıştırma (tekrar sayısı = config stats.null_reps):
    Her varlık için bağımsız olarak, flat olmayan her blokta s ∈ {+1, -1} çek (p = 0,5).
    Blok içinde null w = s * |w|. Flat bloklar flat kalır.
    Komşu bloklar aynı işareti alıp birleşebilir; buna izin verilir.
    Birebir korunan: her (t, varlık) için |w|; tarih bazlı gross; blok sınırları (metadata olarak).
    Korunmayan: gerçekleşen tutma süresi (birleşmeler yüzünden), net maruziyet yolu, kesitsel işaret korelasyonu, turnover, maliyet.

(b) Rastgele giriş (tekrar sayısı = config stats.null_reps):
    Her varlık için bağımsız olarak, varlığın uygun-gün indeksinde çalış (uygun olmayan günler çıkarılmış, sıkıştırılmış).
    Blokları (run ve flat) ayır.
    Blok sırasını tohumlu Fisher-Yates ile düzgün karıştır.
    Her run kendi |w| profilini taşır ve bağımsız ±1 işaret alır.
    Sonucu takvim günlerine geri eşle.
    Ardından stratejiyle aynı gross tavanını uygula (2,0, orantılı küçültme). Vol tavanını yeniden uygulama.
    Gross tavanının kaç kez bağladığını raporla.
    Birebir korunan: varlık başına run uzunlukları kümesi, varlık başına flat uzunlukları kümesi, tavan öncesi varlık başına toplam |w|, uygunluk (uygun olmayan günde pozisyon yok).
    Korunmayan: tarih bazlı kesitsel gross, net maruziyet yolu, kesitsel korelasyon, turnover, maliyet.

(c) Eşit riskli al-ve-tut: aynı E-001 pipeline'ı, expert skoru her uygun varlıkta her gün +1'e sabitlenmiş olarak koşar.
    Veri kaynaklı ABSTAIN yine uygulanır.
    Aynı evren, aynı b, aynı EWMA σ, aynı tavanlar, aynı bant, aynı D-39 kuralı, aynı maliyet, aynı ledger.

(d) Trend Following (BTC): aynı E-001 pipeline'ı, evren = {BTC}. b, C-04 formülüyle N = 1 için yeniden hesaplanır (b = hedef oynaklık).

(e) İşlem yapmama: ağırlıklar sıfır, özsermaye sabit.

Her null/temel çizgi koşusu trial log'a "role": "baseline" veya "role": "null" ile yazılır.
Bunların DSR N_eff ailesine girip girmediği burada KARARLAŞTIRILMAZ, PENDING kalır.
Her üretecin docstring'i yukarıdaki korunan/korunmayan listelerini içerir.

## D-41 Scanner parity kapsamı (#53)
Sıralı ledger yetkilidir (monograf §07).
config/parity.toml altında birebir üçlü parity YALNIZCA şu üçü arasında aranır: kör referans ledger ↔ sıralı ledger ↔ Nautilus.
Scanner için şartlar:
  - maliyetsiz fikstürlerde: ledger ile config/parity.toml altında birebir parity;
  - maliyetli fikstürlerde: günler üzerinde max |scanner_equity − ledger_equity| / ledger_equity ≤ 1e-4.
Bu sınır YENİ dosya config/scanner_bound.toml'a yazılır ve maliyetli herhangi bir scanner karşılaştırması koşulmadan ÖNCE commit edilir. Koşudan sonra asla değiştirilmez.
Scanner hiçbir E-001 geçiş kriterinde kullanılmaz (varyant sayısı = 1).
Scanner birebir (exact) formüle dönüştürülmez.

## D-42 ExpertManifest şeması (#52)
Alan kümesi = monograf §08A.4 metni ile registry manifest_fields'in birleşimi:
expert_id (id'nin yeni adı), semantic_version, code_hash, params_hash, snapshot_schema, owner, sleeve,
research_status, request_schema_version, universe_contract, decision_schedule, output_schema_version,
pre_registration_id, evidence_ids, requires, portfolio_budget_class.
Kilitli prereg olmadığı için manifest hash'inin değişmesi serbesttir.

## D-43 1 saatlik gecikme diagnostiği (#51)
Sentetik fazın kapsamından çıkarılmıştır. Bu bir diagnostiktir, geçiş kriteri değildir.
Gerçek veri fazında Binance 1h kline'larıyla yapılacaktır.
V4 bu yüzden FAIL veremez. #51 "deferred-real-data" etiketiyle açık kalır.
