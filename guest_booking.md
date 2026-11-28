# Hesapsız rezervasyon ("Üye olmadan devam et"): akış ve plan

**Tarih:** 2026-11-28 · **Durum:** Backend yazıldı ve test edildi (commit yok), frontend yazılıyor. Mikroservis kısmı yazıldı, yayın bekliyor. Backend'in uyguladığı hâli `AGENTS.md` §4.3.3 · **Kapsam:** `transfer-backend`, `transfer-frontend`, `transfer-microservice`

Misafir, hesap açmadan ve oturum almadan rezervasyon yapar. Arka planda her müşteri yine bir `User`'dır (gölge/misafir kullanıcı), misafir yalnızca rezervasyona özel bir token'la o transfere erişir. Bulgular kod okunarak çıkarıldı, çalıştırılarak denenmedi; kontrol edilmeyenler §10'da.

---

## 1. Özet

- **Giriş noktası:** Create Transfer'da "Continue" artık seçenek menüsü açmaz, Login sayfasına gider. Login'de (taslak varsa) üstte **"Üye olmadan devam et →"** butonu çıkar.
- **Veri modeli:** `Transfer.customer` zorunlu kalır. Misafir için `is_guest` işaretli, şifresiz bir CUSTOMER kaydı tutulur. Ödeme, iade, atama ve operatör ekranları aynı çalışır.
- **Erişim:** Misafire tam oturum verilmez. Rezervasyon token'ı kısa ömürlü, **kapsamlı bir JWT**'ye çevrilir (`GUEST` rolü, tek transfer); mevcut uçlar ve WebSocket bununla çalışır, ayrı misafir uçları yazılmaz.
- **Kayıtlı e-posta:** Akış kesilmez. Transfer hesaba bağlanır ama hesap sahibi sahiplenene kadar panelinde görünmez.
- **Ödeme kapalı bölge** (`State.payment_enabled = false`): onaydan önce e-posta kodu istenir. Ödeme açık bölgede kod yok.
- **Tahmini süre:** 8-10 iş günü.

## 2. Akış

```
Create Transfer formu ─► Continue ─► Login sayfası
                                       ├─ Giriş / kayıt / sosyal giriş ─► mevcut autoSubmit ─► /transfers/:id
                                       └─ [Üye olmadan devam et →]  (sadece taslak varsa)
                                              │
                         İletişim adımı: ad, soyad, e-posta, telefon + şartlar onayı (checkbox)
                                              │
                                 createGuestTransfer (public, rate limit'li)
                                              │
                ┌─────────────────────────────┼─────────────────────────────┐
           e-posta yok                  misafir kullanıcı                aktif kullanıcı
     misafir User açılır          aynı User'a bağlanır            bağlanır, sahiplenene kadar
                                                                  panelinde gizli (claimed_at boş)
                └─────────────────────────────┼─────────────────────────────┘
                                              ▼
                     PENDING transfer + rezervasyon token'ı ─► /booking/:id
                                              │
                ┌─────────────────────────────┴─────────────────────────────┐
         Ödeme açık bölge                                          Ödeme kapalı bölge
   "Öde" ─► ödeme sayfası (kayıtlı kart kapalı)          e-postaya 6 haneli kod ─► "Rezervasyonu onayla"
   ─► /booking/:id (token tarayıcıdan okunur)            ─► ACCEPTED (isOfflinePayment)
                └─────────────────────────────┬─────────────────────────────┘
                                              ▼
            ACCEPTED ─► mail: "Rezervasyonu yönet" linki (sadece müşteriye)
                        sayfada: durum, sürücü, mesajlaşma, iptal, "bağlantıyı kopyala"
                        isteğe bağlı: "Hesap oluştur, rezervasyonların tek yerde görünsün"
```

### Kurallar

| Konu | Kural |
|---|---|
| Zorunlu alanlar | Ad, soyad, e-posta, telefon, şartlar onayı (checkbox, onay zamanı kaydedilir) |
| Araç seçimi | Ödeme/onay için zorunlu; formdaki seçim transferle kaydedilir |
| Misafirin yapabildikleri | Görme, ödeme veya (ödeme kapalıysa) onay, iptal, mesajlaşma, canlı durum |
| İlk sürümde olmayanlar | Düzenleme, bahşiş, push bildirimi, transfer listesi (cihazdaki liste hariç) |
| Fiyat | Her zaman sunucuda, `payTransfer`/`confirmTransfer` ile aynı kurallar |
| İptal | Hazır sebep listesi (`reasons` public); müşteri ceza kuralı rolden değil açıkça uygulanır |
| E-posta gönderimi | Yalnızca ödemeden ya da kodlu onaydan sonra |
| Yolcu bilgisi | Girilen bilgiden; `set_default_passenger` misafirde müşteri kaydından **doldurmaz** |
| Sonradan hesap | Kayıt, sosyal giriş, şifre sıfırlama ya da kodla giriş aynı kaydı sahiplenir |

## 3. Kararlar (2026-11-28)

| # | Konu | Karar |
|---|---|---|
| 1 | Aktif kullanıcının e-postası | Transfer bağlanır, sahiplenene kadar panelde gizli; oturum verilmez |
| 2 | Ödeme açık bölgede e-posta kodu | Hayır (sadece ödeme kapalı bölgede onaydan önce) |
| 3 | Token ömrü | **Süre sınırı yok.** Yenilenebilir (yenilenince eskisi geçersiz) |
| 4 | İptal sebebi | `reasons` public, liste gösterilir |
| 5 | Şartlar | Gerektiği yerde checkbox ile onaylatılır; yeni sözleşme gerekirse sonra eklenir |
| 6 | Ödenmemiş misafir transferi | 24 saatte `EXPIRED` (öneri, itiraz edilmedi) |

## 4. Kimlik ve erişim

| Katman | Nasıl alınır | Ne yapabilir |
|---|---|---|
| **Rezervasyon token'ı** | Oluşturmada ve mailde: `HMAC(SECRET_KEY, id:nonce)`, DB'de sadece nonce (mailde link yeniden üretilebilsin diye hash yerine) | `guestSession(transferId, token)` ile kısa ömürlü JWT'ye çevrilir (`scope: guest`, `transfer_id`). Sadece o transfer: görme, ödeme/onay, iptal, mesajlaşma, abonelik |
| **Cihazdaki misafir listesi** | Token'lar `localStorage`'da | "Bu cihazdaki rezervasyonlarım"; backend gerekmez |
| **Tam oturum** | Şifre, sosyal giriş, e-posta kodu | Bütün geçmiş, profil, yeni rezervasyon |

- `GUEST` rolü `permissions.json`'da **beyaz liste**: `transfer`, `payTransfer`, `confirmTransfer`, `cancelTransfer`, `reasons`, mesaj uçları, `subscribe`. Listede olmayan her uç `ERR001` (her uç için test).
- `get_transfers` misafirde sadece JWT'deki transferi döner; abonelik olayları `transfer_id`'ye göre filtrelenir (misafirin `user_<id>` grubuna diğer transferlerin olayları da düşer).
- Misafire DB `Notification` kaydı ve push üretilmez.
- Token süresiz olduğu için: admin ve misafir linki yenileyebilir; sayfa `noindex` + `Referrer-Policy: no-referrer`; token ödeme dönüş adresine konmaz, linkte fragment'tedir (`/booking/<id>#<token>`); sayfa okuyunca adresten siler.
- Kısa ömürlü JWT sahiplenmede ve token yenilenince geçersizleşir (`token_version`).

## 5. E-posta eşleşme kuralları

| E-posta | Davranış |
|---|---|
| Sistemde yok | `is_guest` CUSTOMER açılır, şifresiz, dil isteğin dili |
| Misafir kullanıcıya ait | Aynı User; kayıtlı ad/telefon değişmez, yeni bilgiler transfere yazılır |
| Aktif kullanıcıya ait | Transfer bağlanır, `is_claimed = false`: müşterinin listesinde görünmez. Oturum yok, geçmiş gösterilmez. "Giriş yaparsan rezervasyon hesabında görünür" önerisi |
| Pasif (silinmiş) hesap | Misafir gibi; kayıt hesabı yeniden açar |

**Sahiplenme:** misafir e-postasında `getCode` `ERR011` vermez; `sign_up`, `socialSignIn`, şifre sıfırlama ve kodla giriş aynı User'ı devralır (`is_guest = false`, `token_version` artar). Aktif hesaba bağlı gizli transfer, hesap sahibi giriş yapmışken linki açıp "hesabıma ekle" deyince (`claimTransfer`) `is_claimed = true` olur. Sahiplenmede ödenmemiş misafir transferleri panelde gösterilmeyebilir (başkası o e-postayla açmış olabilir).

## 6. Gerekli değişiklikler

### 6.1 Backend (`transfer-backend`)

| Dosya | Değişiklik |
|---|---|
| `core/models.py` + migration `0024` | `User.is_guest`, `User.token_version`; `Transfer.access_nonce`, `is_claimed`, `payer_name`, `payer_email`, `payer_phone`, `terms_accepted_at` |
| `core/schemas/guest.py` (yeni) | `createGuestTransfer(data, contact, language)`, `guestSession(transferId, token)`, `renewBookingLink`, `claimTransfer`, `sendConfirmCode` (ödeme kapalı bölge) |
| `core/decorators.py`, `permissions.json` | `GUEST` rolü, kapsamlı JWT, beyaz liste, `token_version` kontrolü |
| `core/utils.py` | `get_transfers` misafir kapsamı ve `claimed_at` filtresi; misafire bildirim kaydı yok |
| `core/schemas/transfer.py` | `confirmTransfer`'da misafir + ödeme kapalı → kod kontrolü; yolcu bilgisi girilen bilgiden; misafir iptalinde müşteri ceza kuralı |
| `core/schemas/user.py` | `getCode`, `sign_up`, `socialSignIn`, şifre sıfırlama: sahiplenme; `validateCode` deneme sınırı, `ADMIN_CODE` prod'da kapalı |
| `core/schemas/reason.py` | `reasons` public (sadece aktif sebepler) |
| `core/schemas/user.py` `subscribe` | Misafir JWT'si: olaylar `transfer_id` ile filtrelenir |
| `core/psp.py` | Misafir ödemesinde dönüş adresleri, `customerId = guest-<transferId>`, `stored_cards: false`; müşteri maili link'li ve operatör/admin mailinden ayrı; mailde `payer_*` |
| `core/tasks.py` | Ödenmemiş misafir transferi 24 saatte `EXPIRED`. Sürücü atandı / iptal mailleri **henüz yok** (mikroservis şablonları yayınlanınca) |
| `core/ratelimit.py`, settings | `createGuestTransfer`, `guestSession`, kod için IP/e-posta limiti; e-posta başına en fazla 2 açık misafir transferi |
| `errors.txt`, `AGENTS.md`, `core/tests.py` | `ERR062` giriş gerekli (personel/sürücü e-postası), `ERR063` geçersiz link, `ERR064` açık rezervasyon sınırı, `ERR065` kod gerekli; `GuestBookingTests` |

### 6.2 Frontend (`transfer-frontend`): yazıldı (`frontend_structure.md` §8.15)

| Dosya | Durum |
|---|---|
| `features/booking/GuestBookingMenu.tsx` | ✅ Kaldırıldı; Continue → Login (taslak korunur) |
| Login sayfası | ✅ Taslak varsa "Üye olmadan devam et →" (sürücü kaydı butonunun yerinde) |
| `features/guest/GuestContactPage.tsx` | ✅ `/booking/new`: ad, soyad, e-posta, telefon, şartlar → `createGuestTransfer` |
| `features/guest/GuestBookingPage.tsx` | ✅ `/booking/:id`: token → `guestSession`; mevcut detay sayfası misafir modunda, düzenleme (`/booking/:id/edit`, `updateTransfer` GUEST'e açık), mesaj drawer'ı, iptal, ödeme kapalıysa kod diyaloğu (`GuestConfirmDialog`), bağlantıyı kopyala, hesabıma ekle |
| Apollo/WebSocket istemcisi | ✅ `createGuestClient` (ayrı istemci ve WS) |
| `lib/storage.ts` | ✅ `guestBookingStore` (cihazdaki token'lar). "Bu cihazdaki rezervasyonlarım" listesi ❌ henüz yok |
| Operatör ekranları | ⏳ `isGuestBooking`/`payer_*` detayda sorgulanıyor, rozet ve gösterim yok |
| `app/router.tsx`, i18n, `frontend_structure.md` | ✅ Public rotalar, `useNoIndex`, 4 dil, ERR062–065 |

### 6.3 Mikroservis (`transfer-microservice`): yazıldı, `origin/main`'de (`3d043df`). Backend `driver_assigned` / `transfer_cancelled` maillerini henüz göndermiyor

Backend'in kullanacağı sözleşme: `/createPaymentLink` opsiyonel `success_url`, `cancel_url` (yalnızca `FRONTEND_URLS` adresleri, değilse 422) ve `stored_cards` (misafirde `false`). E-posta action'ları `driver_assigned` (`transfer.drivers`: `driver_name`, `vehicle_brand`, `vehicle_model`, `plate`) ve `transfer_cancelled` (opsiyonel `transfer.refund_amount`); üç transfer şablonunda buton `link` verilirse onu kullanır. Lidio'da dönüş adresi hâlâ yok.

| Yer | Değişiklik |
|---|---|
| `core/inputs.py` `CreatePaymentLinkInput` | Opsiyonel `success_url`, `cancel_url`, `stored_cards` |
| Stripe / Iyzico link fonksiyonları | Verilirse bu adresler kullanılır |
| Lidio link fonksiyonu | `StoredCard` ve `cardSaveOffer` gönderilmez, `customerId = guest-<id>`; istekte dönüş adresi hiç yok, desteği araştırılmalı |
| Adres güvenliği | Yalnızca kendi alan adımıza izin ver (açık yönlendirme olmasın) |
| `transfer_accepted_*.html` | Buton `data.link` varsa onu kullanır |
| Yeni şablonlar (en, tr, de, ru) | Sürücü atandı, transfer iptal edildi/iade edildi |

Dönüş adresi kısa tutulur: `/booking/{id}`, **secret'sız**. Iyzico iki adresi de kendi callback adresinin içine kodluyor; secret eklenirse adres çok uzar ve secret üçüncü tarafa gider. Sayfa secret'ı tarayıcıdan okur.



## 7. Riskler ve önlemler

| Risk | Önlem |
|---|---|
| Başkasının hesabına transfer ekleme | Aktif hesapta panelde gizli (`claimed_at`); yolcu bilgisi hesaptan kopyalanmaz |
| Kayıtlı kartların görünmesi (Lidio `customerId = email`) | Misafir ödemesinde `customerId = guest-<transferId>`, kart saklama kapalı |
| Ödeme kapalıyken sahte rezervasyon | Onaydan önce e-posta kodu; IP/e-posta/telefon limiti; e-posta başına en fazla 2 açık transfer |
| Yabancı adrese mail attırma | Mail sadece ödeme ya da kodlu onaydan sonra |
| Kod kaba kuvvet | `validateCode`'a deneme sınırı (e-posta başına 5), `ADMIN_CODE` prod'da kapalı |
| Çöp kayıt | IP rate limit, ödenmemiş misafir transferi 24 saatte `EXPIRED` |
| Süresiz token sızması | Hash'li saklama, yenileme, fragment'te taşıma, `no-referrer`, `noindex` |
| `GUEST` rolüne yanlışlıkla uç açılması | Beyaz liste + her uç için `ERR001` testi |
| Misafir olaylarının diğer transferlerine sızması | Abonelikte `transfer_id` filtresi |
| Yanlış e-posta | Sayfada "Onay a***@... adresine gidecek", bağlantıyı kopyala; telefon zorunlu |

## 8. Uygulama sırası

| Aşama | İş | Süre |
|---|---|---|
| 1 | Mikroservis: dönüş adresi, kart güvenliği, şablon linki, yeni şablonlar (yayın) | 1-2 gün |
| 2 | Backend: model, misafir uçları, `GUEST` rolü/JWT, sahiplenme, kod, limitler, mailler, testler | 3-4 gün |
| 3 | Frontend: Login girişi, iletişim sayfası, `/booking/:id`, cihaz listesi | 3 gün |
| 4 | Uçtan uca test (ödeme açık/kapalı, her sağlayıcı, 4 dil, mobil) | 1-2 gün |

Mikroservis önce yayınlanmalı: yeni alanlar opsiyonel olduğu için mevcut akış bozulmaz.

## 9. Ek: mevcut yapı tespitleri

### 9.1 İşimize yarayanlar
- **Fiyat önizlemesi zaten misafire açık.** `quoteTransfer` token okumaz, IP başına rate limit vardır. Formun tamamı girişsiz doldurulabiliyor.
- **Şifresiz müşteri kaydı modeli var.** `socialSignIn`, `set_unusable_password()` ile CUSTOMER açıyor.
- **İmzalı secret altyapısı var.** `utils.encode_secret` / `decode_secret` (Fernet + JWT, süreli). Davetler bunu kullanıyor.
- **Secret'lar karışmaz.** Kayıt ve şifre sıfırlama `is_validated` ister; misafir secret'ı bunu taşımayacağı için orada kullanılamaz.
- **Kayıt mevcut satırı devralabiliyor.** `sign_up` aktif olmayan kullanıcının satırını kullanıyor; misafir için küçük bir koşul yeterli.
- **Operatör ödemeden önce uyarılmıyor.** `createTransfer` bildirim göndermiyor; bildirim `process_payment_return` içinde.
- **Operatör seçimi sahte kayıttan etkilenmiyor.** `set_operator` yalnızca `ACCEPTED` transferleri sayıyor.
- **Ödeme sonucu WebSocket olmadan da izlenebiliyor.** `PaymentPendingBanner` 5 sn'de bir, en fazla 2 dk yoklama yapıyor.
- **Ödeme e-postası müşteriye gidiyor.** `send_email_to(transfer.customer, ...)`.
- **Ödenmemiş transferler temizleniyor**, ama ancak alış saati geçince (`check_transfers_expiring`).

### 9.2 Engel olanlar

| # | Tespit | Sonuç |
|---|---|---|
| 1 | `Transfer.customer` ve `Transaction.user` zorunlu | Misafir için de kullanıcı kaydı gerekir |
| 2 | `User.email` benzersiz | Aynı e-postayla ikinci kayıt açılamaz |
| 3 | `create/pay/cancel_transfer` ve `transfer` sorgusu token ister | Misafir kapsamlı JWT (`GUEST` rolü) ile aynı uçları kullanır (§4) |
| 4 | `reasons` sorgusu giriş ister | Misafir iptalinde sebep listesi alınamaz |
| 5 | `driverName`, `plate` gibi alanlar role bağlı (`permissions.json`) | `GUEST` rolü `permissions.json`'da CUSTOMER'ın gördüğü alanlarla tanımlanır |
| 6 | `/transfers/:id` sayfası `RequireAuth` altında | Misafir sayfası ayrı olmalı |
| 7 | WebSocket aboneliği giriş ister | Misafir JWT'siyle abone olur, olaylar `transfer_id` ile filtrelenir |
| 8 | Stripe ve Iyzico'da ödeme dönüş adresi mikroserviste sabit: başarı `/transfers/{id}`, iptal `/transfers/{id}/vehicle` | Misafir ödeme sonrası **giriş ekranına düşer** |
| 9 | `CreatePaymentLinkInput`'ta `success_url`/`cancel_url` alanı yok | Adres istek başına değiştirilemiyor |
| 10 | `transfer_accepted_*.html` linki sabit (`/transfers/{id}`), `data.link` kullanılmıyor | E-postadaki buton misafiri giriş ekranına götürür |
| 11 | Aynı `email_data` operatör, admin ve müşteriye gidiyor | Gizli bağlantı eklenirse **operatör ve adminlere de gider**, müşteri için ayrılmalı |
| 12 | Sürücü atanınca müşteriye yalnızca WebSocket olayı gidiyor | Misafir sürücüyü öğrenemez, e-posta gerekir |
| 13 | İptal, durum ve iade bilgisi uygulama içi bildirim (DB + push) | Misafire ulaşmaz, e-posta gerekir |
| 14 | `getCode(isUser: false)` aktif e-postada `ERR011` verir | `is_guest` hariç tutulmazsa misafir sonradan kayıt olamaz |

## 10. Kontrol edilmeyenler
- Canlıda hangi ödeme mikroservisinin ve hangi sağlayıcıların kullanıldığı. `AGENTS.md` `3rd-party-microservices` diyor; bu belge `transfer-microservice` koduna dayanıyor.
- Lidio sayfasının kayıtlı kartları gerçekten ek doğrulama olmadan gösterip göstermediği.
- Lidio panelinde tanımlı bir dönüş adresi olup olmadığı (istekte dönüş adresi gönderilmiyor).
- Lidio'da `cardSaveOffer` gönderilmeyince kart saklama teklifinin gerçekten kapandığı.
- Stripe'ın `#` içeren dönüş adresi davranışı (öneride kullanılmıyor).

## 11. Analiz sırasında bulunan, bu işten bağımsız sorunlar

| # | Sorun | Yer | Etki |
|---|---|---|---|
| 1 | **(Düzeltildi, yayınlanmadı)** Ödemeden vazgeçen kullanıcı `/transfers/{id}/vehicle` üzerinden **düzenleme sayfasına** düşüyor. Ödeme artık detay sayfasında | `transfer-microservice/core/settings.py` `SPECS.cancel` | Adres `/transfers/{id}` olmalı |
| 2 | `PENDING` olmayan transfere gelen başarılı ödeme **loglanmadan** atlanıyor (`AGENTS.md` "loglanıyor" diyor), otomatik iade yok | `core/psp.py` `process_payment_return` | Para alınır, kayıt oluşmaz |
| 3 | Google/Microsoft ile yeni gelen kullanıcının telefonu yok: otomatik oluşturma `ERR043` verir, profile gidince form kaybolur | `CreateTransferPage.tsx` | "With an account" akışında kayıp |
| 4 | **(Düzeltildi, yayınlanmadı)** Lidio link fonksiyonu `bonus_points`, `coupon_discount`, `offer_expire_time` bekliyor; backend bunları göndermiyor | `transfer-microservice/core/services.py` `create_lidio_payment_link` | Lidio ödemesi hata verir görünüyor |
| 5 | Sürücü atanınca giriş yapmış müşteriye de bildirim/e-posta gitmiyor, yalnızca WebSocket | `core/schemas/transfer.py` `assign_driver` | Uygulama kapalıysa haberi olmaz |
| 6 | Şifre değişince eski token'lar geçerli kalıyor (14 gün) | `core/decorators.py`, `JWT_EXPIRE_DAYS` | Çalınan oturum kapatılamaz |
| 7 | `validateCode`'da deneme sınırı yok, `ADMIN_CODE` her e-posta için geçerli | `core/schemas/user.py` | Şifre sıfırlamada kaba kuvvet |
| 8 | **(Transfer şablonlarında düzeltildi, yayınlanmadı)** E-posta şablonlarında HTML kaçışı kapalı (`Environment` autoescape yok); diğer şablonlarda sürüyor | `transfer-microservice/core/services.py` | Kullanıcının yazdığı metin e-postaya HTML olarak girebilir |
