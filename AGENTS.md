# transfer-backend: Agent Rehberi

Mesajlaşmalı transfer (VIP/özel araç) uygulamasının Django + Strawberry GraphQL backend'i. `backend_structure.md` standardına göre kuruldu, referans implementasyon `meetngreet-backend`.

**Kaynakların önceliği:** Kod > bu dosya > `backend_structure.md`. Bu dosya ile kod çelişirse kod doğrudur, bu dosyayı güncelle.

---

## 1. Önce bilmen gerekenler

- **İsteğe bağlı özellikleri uygulamadan önce kullanıcıya sor** (`backend_structure.md` §15). Bu projede verilen kararlar §6'da.
- **Kullanıcı Türkçe yazıyor.** Açıklamalar Türkçe, koddaki isimler/yorumlar/hata metinleri İngilizce.
- **Sürümler sabit:** `strawberry-graphql==0.327.7` + `graphql-core==3.2.13` (graphql-core 3.3.0 veri sızıntısı hatası, §1.3 #12). Bu yüzden `strawberry-graphql-django==0.91.0` (0.92+ strawberry 0.328 istiyor). `django-health-check==4.8.0` (4.9+ Python 3.14 istiyor).
- **PostgreSQL 15+ gerekir** (Django 6.1). Yerelde 5432'deki 14.18 çalışmaz, `.env` 5433'teki PostgreSQL 17'yi (`transfer` DB) kullanır. Testler SQLite ile çalışır.

## 2. Çalıştırma ve test

```bash
python3.13 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env        # SECRET_KEY, JWT_SECRET, FERNET_KEY, PSP_MS_URL, MAIL_MS_URL, MS_ACCESS_TOKEN doldur
DJANGO_SETTINGS_MODULE=transfer.settings.test ./venv/bin/python -W error::DeprecationWarning manage.py test core
./venv/bin/python manage.py makemigrations --check --dry-run
./venv/bin/python manage.py migrate && ./venv/bin/daphne transfer.asgi:application
```

Testlerdeki traceback'ler beklenen `{"code": "ERR0xx"}` loglarıdır, sonuç satırına bak. Ödeme mikroservisi testlerde `core.psp.async_post`, mail mikroservisi `core.tasks.post` mock'lanır.

## 3. Roller

| Rol | Ne yapar |
|---|---|
| `CUSTOMER` | Transfer oluşturur, araç kategorisi seçip öder, iptal eder, bahşiş verir, mesajlaşır |
| `OPERATOR` | Ödenen transferlerden haberdar olur, uygun sürücüyü bulup atar, araç yönetir, iptal eder, mesajlaşır (atadığı transferde) |
| `DRIVER` | Atandığı (`TransferDriver`) transferleri görür, durumunu `STARTED`/`COMPLETED` yapar, mesajlaşır, bahşişlerini görür, kendi adına araç ekler (`createVehicle`, `driverId` her zaman kendisi; diğer roller `driverId` vermezse `ERR030`) |
| `ADMIN`, `STAFF` | Her şeyi görür. Araç kategorisi yönetimi ve iade (`refundTransaction`) sadece bunlarda |

Kayıt e-posta doğrulamalı (`getCode` → `validateCode`) iki mutation'la yapılır: `createCustomer` (`CUSTOMER`) ve `createDriver` (`DRIVER`), ikisi de `CreateUser` input'unu alır. `OPERATOR`'ü ADMIN `updateAdminUser` ile verir.

- **E-posta:** Django mail backend'i kullanılmaz. `tasks.send_email(data)` (celery) `MAIL_MS_URL`'e (mail mikroservisi, `transfer-microservice` `/mail/v1/sendTransferEmail`, `X-Access-Token: MS_ACCESS_TOKEN`) POST atar. `data`: `action` (şablon adı, `<action>_<dil>.html`), `to_user` (`email`, `language`), `subject`, `message`, opsiyonel `link`/`transfer`/`from_user`. `getCode` doğrulama kodunu `action: 'verification'`, `message: <kod>` ile gönderir. Hata loglanır, istemciye dönmez.
- **Müşteri daveti** (`schemas/invitation.py`, model `Invitation`): `inviteCustomer(email, language?)` ADMIN/STAFF/OPERATOR. `language` verilmezse davet edenin dili. Aktif kullanıcı varsa `ERR011`, geçersiz e-posta `ERR030`. Aynı e-postaya `PENDING` davet varsa yenilenir (gönderen, dil, `expires_at`) ve tekrar gönderilir. Mail `action: 'invitation'`, `message: <secret>`, `link: FRONTEND_URL/register?invitation=<secret>`, `from_user` (davet eden). Secret `is_validated` taşır, yani e-posta doğrulaması yerine geçer ve doğrudan `createCustomer`'a verilir (`createDriver`'da `ERR001`). Kayıtta davet `ACCEPTED` (`receiver` set) olur, müşteri `status=ACCEPTED` açılır. Davetsiz kayıt da o e-postanın `PENDING` davetlerini kapatır. Kullanılmış/bulunamayan davet `ERR044`, süresi geçmiş secret `ERR009` (`INVITATION_EXPIRE_DAYS`, varsayılan 7). `invitations` sorgusu ADMIN/STAFF hepsini, OPERATOR kendi gönderdiklerini görür. Public `invitationInfo(secret)` kayıt formu için e-posta + dil döner. Süresi geçen davet `PENDING` kalır, istemci `expiresAt`'e bakar.
- **Telefon zorunlu:** `CreateUser` ve `CreateAdminUser`'da `phone` zorunlu, boşlukları atılır, boşsa `ERR043`. `updateUser` telefonu değiştirebilir ama boş/`null` ile silemez (`ERR043`). DB alanı `blank=True` kaldı (eski kullanıcılarda boş olabilir), kural API'de. `createTransfer` müşterinin (OPERATOR/ADMIN/STAFF `customerId` verirse o müşterinin) telefonu yoksa `ERR043`, kullanıcı önce profilinden telefon eklemeli.
- **Sosyal giriş** (`socialSignIn`, `core/social.py`): istemci sağlayıcıdan OpenID Connect `id_token` alır (`provider`, `idToken`, `nonce`, opsiyonel `firstName`/`lastName`), backend token'ı sağlayıcının JWKS'iyle doğrular (RS256, `aud` = `GOOGLE_CLIENT_IDS` / `MICROSOFT_CLIENT_IDS`, `iss`, `exp`, `nonce` eşleşmesi, doğrulanmış e-posta). `GOOGLE` ve `MICROSOFT` tanımlı (`social.PROVIDERS`). Microsoft `common` uç noktası: `iss` token'ın `tid`'iyle kurulur; kişisel hesap (`tid` = `MSA_TENANT`) e-postası güvenilir, iş/okul hesabı sadece `xms_edov` claim'i true ise (nOAuth); ad `given_name`/`family_name` yoksa `name`'den bölünür; tanımsız sağlayıcı veya boş client id `ERR030`, geçersiz token `ERR045`. E-postaya göre kullanıcı bulunur ve `signIn` gibi token döner (rol/ad değişmez); yoksa şifresiz (`set_unusable_password`, isterse şifre sıfırlamayla belirler), telefonsuz, `PENDING` CUSTOMER açılır, dil isteğin dili, o e-postanın `PENDING` davetleri kabul edilir. Pasif kullanıcı `ERR010` (sign_up'ın aksine yeniden açılmaz). Testlerde `core.social.signing_key` patch'lenir.

- **`User.status`** (`VerifyStatus`: `PENDING` varsayılan, `ACCEPTED`, `REJECTED`): sadece ADMIN/STAFF görür (`permissions.json`). `updateAdminUser` ile ADMIN ve STAFF günceller, STAFF bu mutation'da sadece `status` gönderebilir (`role`/`isActive` ADMIN'e özel, `ERR001`). `createCustomer`/`createDriver` ile açılanlar `PENDING`. Status henüz hiçbir akışı (giriş, atama) etkilemiyor.
- **`createAdminUser`** (ADMIN, STAFF): e-posta + şifre ile her rolde kullanıcı açar, `status=ACCEPTED`. `ADMIN`'i sadece ADMIN açabilir (`ERR001`). Opsiyonel `districtIds`, `OPERATOR` için zorunlu (`ERR030`), bulunamayan district `ERR035`. Kullanıcı iletişim bilgisi (`email`, `phone`) sadece kişinin kendisine ve ADMIN/STAFF/OPERATOR'e görünür.

## 4. Akış

```
(misafir) quoteTransfer ──► aynı vehicleOptions + state, hiçbir şey kaydedilmez (§4.1.1)
createTransfer(vehicleCategoryId?, vehicleCount?, refundOption?) ──► PENDING + vehicleOptions (uygun kategoriler, her biri fiyatlı); seçim verilirse fiyatıyla kaydedilir, link açılmaz
   │ (updateTransfer: rota/ihtiyaç değişirse seçim sıfırlanır, link expire edilir; vehicleCategoryId verilirse yeni seçim yazılır)
payTransfer(vehicleCategoryId, vehicleCount?, refundOption?) ──► yolcu boşsa müşteriden (yine boşsa ERR058/ERR057), kategori + araç sayısı + customer_price (birim × sayı) + cancel_price (koşulsuz iadede) yazılır, /createPaymentLink (price = toplam)
   │ (state'te ödeme kapalıysa payTransfer ERR060, yerine confirmTransfer ──► doğrudan ACCEPTED + isOfflinePayment, §4.3.2)
   │
psp-return PAYMENT_RETURN success ──► PAID, PAYMENT transaction, tüm OPERATOR'lere bildirim (TRANSFER_PAID)
   │
availableDrivers / availableVehicles ──► assignDriver (en fazla vehicle_count, iç veya dış sürücü) ──► TransferDriver
   │                                        status değişmez (ACCEPTED kalır), iç sürücüye ve müşteriye TRANSFER_ASSIGNED
updateTransferStatus ──► STARTED ──► COMPLETED ──► tipDriver
   │                    (ACCEPTED ──► COMPLETED de olur: "başlat ve bitir" tek adımda) ──► /createTipLink ──► TIP_RETURN
cancelTransfer(id, cancellationReasonId | cancellationReasonText) (PENDING/ACCEPTED, sebep zorunlu) ──► ödenmişse /refundPayment, değilse /expirePaymentLink
her saat (celery beat) ──► pickup_time'ı geçmiş PENDING ──► EXPIRED + /expirePaymentLink
```

### 4.0 Konum (`State` → `City` → `District`)
- `createTransfer` adreslerle birlikte `pickupDistrictName` ve `dropoffDistrictName` alır (ikisi de zorunlu). İsimler aktif `District` kayıtlarında büyük/küçük harf duyarsız (`name__iexact`, baştaki/sondaki boşluk atılır) aranır (`schemas/transfer.py` `set_districts`).
- Bulunanlar `pickup_district_id` / `dropoff_district_id` olarak yazılır, bulunamayan `null` kalır. **İkisi de bulunamazsa `ERR035`.**
- Bir district ancak aktifse ve ona bağlı aktif, `status=ACCEPTED` bir OPERATOR varsa eşleşir. Transferin operatörü (`set_operator`) bu district'lerin ACCEPTED operatörleri arasından seçilir (önce pickup district'inin, sonra en az `ACCEPTED` transferi olan). PENDING/REJECTED operatöre transfer düşmez.
- `state_id` = pickup district'in state'i, o yoksa dropoff'unki.
- `updateTransfer` bu isimlerden birini alırsa yeniden hesaplanır, verilmeyen taraf mevcut district'ini korur.
- Aynı isimde birden fazla district varsa (ör. "Merkez") ilk eşleşen (alfabetik) alınır. Şehir adı alınmadığı için ayırt edilemez.
- **City** (`schemas/city.py`) ve **District** (`schemas/district.py`): `city`/`cities`, `district`/`districts` token'sız çalışır (public). Pasif district'leri sadece ADMIN/STAFF görür. `districts` filtresinde `stateId` var. `create`/`update`/`delete` ADMIN, STAFF. Şehir silmek district'lerini de siler. Transferde kullanılan district/şehir silinemez (`ERR041`/`ERR040`), district pasifleştirilmeli. Public sorgular `state`'i açtığı için `State.psp` ve `driverBufferMinutes` sadece ADMIN/STAFF/OPERATOR'e görünür.
- **State** (`schemas/state.py`): `state`/`states` (ADMIN, STAFF, OPERATOR), `createState`/`updateState`/`deleteState` (ADMIN, STAFF). `allowMultipleVehicles` çoklu araç seçimini açar (§4.1), `allowUnconditionalRefund` + `cancelPriceRate` (0–1) koşulsuz iadeyi (§4.3.0) açar; açıkken oran 0'dan büyük olmalı (`ERR030`). `paymentEnabled` (varsayılan true) online ödemeyi açıp kapatır (§4.3.2). Bunlar public (müşteri `transfer.state` üzerinden okur). `code` büyük/küçük harf duyarsız benzersiz (`ERR038`). `customerImposedHours` artan pozitif saatler, `customerImposedRates` aynı uzunlukta 0–1 arası oranlar (`ERR030`), oranlar string olarak saklanır. Silme şehir ve district'leri de siler, transfer bağlıysa `ERR037`.

### 4.0.1 Ara duraklar (`Leg`)
- `createTransfer` opsiyonel `legs` listesi alır (`CreateLeg`: başlangıç/bitiş konum, adres ve zaman, `order`, `route`). Gelirse transferle birlikte kaydedilir, `Transfer.legs` olarak `order` sırasıyla döner.
- `order` verilmezse listedeki sırası (0'dan) kullanılır. `startTime < endTime` olmalı ve `order` negatif olamaz, değilse `ERR030`. Leg'ler transfer kaydedilmeden önce doğrulanır, yani geçersiz leg varsa transfer de oluşmaz.
- Leg'ler fiyatı, uygunluğu ve sürücü müsaitliğini etkilemez.
- `updateTransfer` de opsiyonel `legs` alır: verilirse (boş liste dahil) transferin tüm leg'leri silinip yenileri yazılır (`[]` tüm durakları kaldırır), verilmezse dokunulmaz. Aynı doğrulama (`ERR030`, geçersizse hiçbir şey değişmez), sadece PENDING (`ERR014`). Leg vermek rota değişikliği sayılır: ödeme linki expire edilir, araç seçimi sıfırlanır.

### 4.1 Araç kategorisi ve fiyat
- `VehicleCategory`: `max_passengers`, `max_luggage`, `child_seat_capacity`, `is_wheelchair_accessible`, `base_fare`, `per_km_price`, `per_minute_price`, `minimum_fare`, `currency`, `order`, `is_active`. `name`/`description` çok dilli (`name_en`, `name_tr`, `name_de`, `name_ru`; boş olan dil `en`'e düşer).
- **Uygunluk** (`VehicleCategoryQuerySet.eligible_for`): aktif ve `passenger_count`, `luggage_count`, `child_seat_count` kapasiteyi aşmıyor, `needs_wheelchair` ise erişilebilir.
- **Fiyat** (`VehicleCategory.get_price`): `max(minimum_fare, base_fare + distance_km * per_km_price + duration_min * per_minute_price)`, 2 ondalık.
- `distance` (km) ve `duration` (dk) istemciden gelir (harita SDK'sı), fiyat her zaman sunucuda hesaplanır.
- **Araç sayısı** (`VehicleCategoryQuerySet.options_for`, `get_vehicle_categories`): transferin state'inde `allow_multiple_vehicles` (varsayılan `False`, `createState`/`updateState`) açıksa `with_counts` kullanılır: kapasiteyi aşan kategoriler de döner, `count` = `max(ceil(passenger_count / max_passengers), ceil(luggage_count / max_luggage))`. Kapalıysa (veya transferin state'i yoksa) `eligible_for` + `count=1`, yani sadece tek araca sığan kategoriler. Bayrak `transfer.state`'ten okunur (`allows_multiple_vehicles`, sorgu atmaz): çağıranlar state'i `expand=['state']` ile yükler, `createTransfer`/`updateTransfer`'da `set_districts` state değişince yenisini yükler. Çocuk koltuğu ve tekerlekli sandalye araç başına filtredir. `price` tek araç fiyatıdır. `payTransfer(vehicleCategoryId, vehicleCount?)` aynı listeyi kullanır: `vehicleCount` verilmezse kategorinin `count`'u, `count`'tan azsa `ERR046`, bayrak kapalıyken 1'den farklıysa `ERR047`. `Transfer.vehicle_count`'a yazılır, `customer_price` = birim fiyat × `vehicle_count`. Seçim sıfırlanınca (`clear_vehicle_selection`) `vehicle_count` 1, `customer_price` `null`, `cancel_price` 0 olur. Atama sayısı `vehicle_count`'u aşamaz (§4.2).
- **Yolcu bilgisi** (`set_default_passenger`, `check_passenger`): `passengerName` (100) ve `passengerPhone` (20) `createTransfer`'da opsiyonel; verilmezse/boşsa transferin müşterisinin adı soyadı ve telefonu yazılır (personel `customerId` ile açarsa o müşterininki). Boşluk atılır, uzunsa `ERR030`. `payTransfer` yolcu almaz: transferde boşsa (eski kayıt) yine müşteriden doldurur, hâlâ boşsa ad `ERR058`, telefon `ERR057`. `updateTransfer` verilen alanı boşa çeviremez (`ERR058`/`ERR057`), verilmeyene bakmaz.
- **Oluşturmada seçim** (`set_vehicle_selection`, `payTransfer` ile ortak): `createTransfer` opsiyonel `vehicleCategoryId`/`vehicleCount`/`refundOption` alır; verilirse `payTransfer`'daki aynı kurallarla (`ERR025`/`ERR046`/`ERR047`/`ERR055`, hata varsa transfer oluşmaz) kategori, adet, `customer_price`, `cancel_price` yazılır, ödeme linki açılmaz. `updateTransfer` da aynı alanları alır: seçim güncellenen transfere göre (yeni rota/ihtiyaç) fiyatlanır ve varsa ödeme linki expire edilir; rota değişip seçim verilmezse seçim sıfırlanır. `payTransfer` seçimi değiştirebilir.
- **Sınırlar** (`check_transfer`, `createTransfer`/`updateTransfer`/`quoteTransfer`): `0 < distance ≤ 2000` km, `0 < duration ≤ 2880` dk, `1 ≤ passengerCount ≤ 50`, `luggageCount ≤ 100`, `childSeatCount ≤ 20`, değilse `ERR030`.
- `vehicleOptions(transferId)` aynı listeyi tekrar döner. İsimler kullanıcının dilinde döner. Public `vehicleCategories(language)` sorgusunda dil argümandan alınır.

### 4.1.1 Fiyat önizlemesi (`quoteTransfer`, public)
- Girişten önce fiyat göstermek için: `quoteTransfer(data: QuoteTransfer, language?)` → `QuoteResponse { vehicleOptions, state }`. `QuoteTransfer` sadece fiyatı/uygunluğu belirleyen alanlar: district adları, `distance`, `duration`, opsiyonel `pickupTime` (verilirse gelecekte, `ERR016`), kişi/bagaj/çocuk koltuğu/tekerlekli sandalye.
- `createTransfer` ile aynı yol: kaydedilmemiş bir `Transfer` üzerinde `check_transfer` → `set_districts` (`ERR035`, state, operatör) → `get_vehicle_options`. Aynı girdiyle `createTransfer`'ın `vehicleOptions`'ıyla birebir aynı sonuç (test edilir). **DB'ye yazmaz.** Bağlayıcı değildir: fiyat/kategori/bölge ayarı arada değişebilir, ödenen fiyat her zaman `createTransfer`/`payTransfer` sonucudur.
- `state` çoklu araç ve iade seçeneklerini önizlemek içindir (`allowMultipleVehicles`, `allowUnconditionalRefund`, `cancelPriceRate`, `customerImposed*`); `psp`/`driverBufferMinutes` misafire `null` (`permissions.json`).
- **Token okunmaz** (misafirin tarayıcısındaki bayat token `ERR002/003` üretmesin). İsimler `language`'da, verilmezse `en`.
- **Rate limit** (`core/ratelimit.py`): client IP başına sabit pencere, `QUOTE_RATE_LIMIT` (30) / `QUOTE_RATE_WINDOW` (60 s), aşılınca `ERR056`. Her resolver çağrısı sayılır, yani tek istekteki alias'lar (`a: quoteTransfer b: quoteTransfer`) ayrı ayrı sayılır. Giriş yapmak limiti kaldırmaz. Sayaçlar Django cache'inde (`CACHES`, Redis, `CACHE_URL` yoksa `REDIS_URL`, `KEY_PREFIX=transfer`; local/test locmem). Cache'e ulaşılamazsa istek **geçer** (loglanır), Redis kesintisi rezervasyonu durdurmaz.
- **Client IP:** prod'da channels consumer'ın `scope['client']`'ı. daphne `--proxy-headers` bunu Caddy'nin yazdığı `X-Forwarded-For`'dan alır; Caddy `trusted_proxies` olmadan istemcinin gönderdiği `X-Forwarded-For`'u yok sayar, yani sahte IP ile limit atlanamaz. Caddy önüne CDN/başka proxy eklenirse (ör. Cloudflare) Caddy'de `trusted_proxies` ayarlanmalı, yoksa herkes proxy'nin IP'sini paylaşır. Django view'da (testler) `REMOTE_ADDR`.

### 4.2 Uygun sürücü sorgusu (`core/managers.py`)
- **Atamalar** (`TransferDriver`, `Transfer.drivers`): bir transferde en fazla `vehicle_count` kadar sürücü olur. **İç sürücü** `driver` (DRIVER kullanıcısı) + `vehicle` taşır, `driver_name` kullanıcının adından, `plate`/`vehicle_brand`/`vehicle_model` aracın plaka/marka/modelinden kopyalanır. **Dış sürücü** sistemde hesabı olmayan kişidir: `driver`/`vehicle` boş, `driver_name` (zorunlu, 100), `driver_phone` (20), `plate` (20), `vehicle_brand`/`vehicle_model` (50) elle girilir. `vehicle_category` her zaman transferin kategorisidir (atamada yazılır, seçilmez; iç sürücüler zaten bu kategoride boş aracı olanlardır). `isExternal` = `driverId` boş. Aynı iç sürücü bir transferde bir kez olabilir (`unique_transfer_driver`). Atamalar transferin `status`'ünü **değiştirmez**.
- `User.objects.available_drivers(transfer)`: aktif ve `status=ACCEPTED` `DRIVER`, transferin kategorisinde **boşta aktif aracı olan**, bu transfere henüz atanmamış ve o saatte **başka bir işi olmayan** kullanıcılar.
- **Meşgul** = `ACCEPTED`/`STARTED` durumundaki başka bir transferde ataması olan ve o transferin `[pickup_time - buffer, end_time + buffer]` aralığı bu transferle kesişen sürücü (araç için de aynı kural, `Vehicle.objects.suitable_for` bu transferde veya çakışan transferde kullanılan aracı eler). `end_time = pickup_time + duration` (`Transfer.save`), `buffer = State.driver_buffer_minutes` (varsayılan 30).
- `Vehicle.objects.available_for(transfer)`: uygun sürücülerin uygun araçları (`availableVehicles`).
- **`assignDriver(id, driverId?, vehicleId?, driverName?, driverPhone?, plate?, vehicleBrand?, vehicleModel?, driverPrice?, driverPriceCurrency?)`** (OPERATOR/ADMIN/STAFF, sadece `ACCEPTED`): atama ekler, döndürdüğü `Transfer`'in `drivers`'ı güncel listedir. Atama sayısı `vehicle_count`'a ulaştıysa `ERR048`. `driverId` varsa iç sürücü: uygun sürücü sorgusuyla doğrulanır (`ERR015`), `vehicleId` verilmezse uygun ilk aracı (`ERR027`), `driverName`/`driverPhone`/`plate`/`vehicleBrand`/`vehicleModel` yok sayılır. `driverId` yoksa dış sürücü: `driverName` boşsa, uzunluklar aşılırsa veya `vehicleId` verilirse `ERR030`. OPERATOR atarsa transferin `operator`'ü olur. İç sürücüye `TRANSFER_ASSIGNED` bildirimi, iç sürücü + müşteriye WebSocket olayı.
- **`updateTransferDriver(id, ...)`** (OPERATOR/ADMIN/STAFF, transfer `ACCEPTED`/`STARTED`, yoksa `ERR014`): fiyat alanları, dış sürücüde ayrıca ad/telefon/plaka/marka/model (iç sürücüde yok sayılır). Sürücüyü değiştirmek için atama silinip yeniden yapılır. Görünmeyen/olmayan atama `ERR049`.
- **`removeTransferDriver(id)`** (OPERATOR/ADMIN/STAFF, sadece `ACCEPTED`): atamayı siler, iç sürücüye `TRANSFER_CANCELLED` bildirimi ("unassigned"), müşteriye `TRANSFER_ASSIGNED` olayı. Sürücü transferi artık göremez.
- Filtreler: `TransferFilter.driverId` / `vehicleId` (atamalardan herhangi biri), `hasDriver` (en az bir atama).
- **Dikkat:** liste döndüren `sbd.field`'larda argüman adı `id` olursa strawberry-django onu pk filtresi olarak uygular. Transfer'e bağlı sorgular bu yüzden `transferId` alır.

### 4.2.1 Sürücü bilgisi ve ücreti (`TransferDriver`)
- Görünürlük (`permissions.json`): `driverName`, `plate`, `vehicleBrand`, `vehicleModel` ADMIN/STAFF/OPERATOR/CUSTOMER, `driverPhone`, `driverPrice`, `driverPriceCurrency` sadece ADMIN/STAFF/OPERATOR. Diğerlerine `null` döner (DRIVER diğer sürücülerin adını da göremez).
- `driverPrice` 2 ondalığa yuvarlanır, negatifse 0 kaydedilir.
- Eski `Transfer.driver`/`vehicle`/`driver_name`/`driver_price`/`driver_price_currency` alanları ve `isDriverExternal` kaldırıldı, migration `0016_transfer_drivers` bunları birer `TransferDriver`'a taşıdı (sadece `driver_name` dolu olan dış sürücü oldu). `updateTransfer` artık sürücü alanı almaz. Ödeme mikroservisine giden `transfer` payload'ında (`utils.get_dict`) bu alanlar artık yok.

### 4.3 Ödeme (`core/psp.py`)
Ödeme mikroservisi (`3rd-party-microservices`) istemcisi. Payload anahtarları ottobus/meetngreet sözleşmesini izler. `PSP_MS_URL`, `MS_ACCESS_TOKEN`, `PSP_STATE` (`.env`). Mikroservis/ağ hatası istemciye `ERR031` döner (`psp.PSPError`), mikroservisin kendi hatası `error_code` özelliğinde taşınır (ağ hatasında `ERR031`).

| İstek | Ne zaman |
|---|---|
| `/createPaymentLink` | `payTransfer`. Önceki link varsa önce `/expirePaymentLink` |
| `/expirePaymentLink` | Seçim değişince, rota güncellenince, ödenmemiş transfer iptalinde |
| `/createTipLink` | `tipDriver` (sadece `COMPLETED`). `provider` = seçilen iç sürücünün `psp_data`'sı |
| `/refundPayment` | `cancelTransfer` (ödenmişse) ve `refundTransaction` |

- **Webhook `POST /psp-return/`** (`X-Access-Token`): `PAYMENT_RETURN` → transfer koşullu update ile `PENDING → PAID` (tekrar gelen istek bir kez işlenir), `PAYMENT` transaction, müşteriye `PAYMENT_SUCCESS`, operatörlere bildirim + `TRANSFER_PAID`. `TIP_RETURN` → `TIP` transaction (`driver` = bahşişi alan, `psp_data.tip_driver_id`'den), o sürücüye bildirim. Başarısızsa `PAYMENT_FAILED` / `TIP_PAYMENT_FAILED`.
- **Transaction** kategorileri: `PAYMENT`, `TIP`, `REFUND` (`parent` = iade edilen kayıt). `user` her zaman müşteri, `driver` bahşişi alan sürücü (sadece `TIP`). Sorgu kapsamı: ADMIN/STAFF hepsi, OPERATOR operatörü olduğu transferlerinkiler, DRIVER kendisine verilen bahşişler (ve onların iadeleri), diğerleri kendi kayıtları. Kapsam `utils.get_transactions`'ta. `Transfer.transactions` alanı da aynı kapsamla o transferin kayıtlarını döner (token yoksa boş liste), transfer detayında ayrıca `transactions` sorgusuna gerek yok.
- **`is_payout_sent`**: sadece OPERATOR/ADMIN/STAFF görür. `updateTransaction(id, note?, isPayoutSent?)` OPERATOR/ADMIN/STAFF, OPERATOR sadece operatörü olduğu transferlerin kayıtlarını görür ve günceller (`ERR034`). `TransactionFilter.isPayoutSent` da sadece bu rollerde, diğerleri kullanırsa `ERR001`.
- **`Transaction.status`** (`TransactionStatus`: `PENDING` varsayılan, `FAILED`, `COMPLETED`; `FAILED`'ı şu an hiçbir akış set etmiyor): `PAYMENT`/`TIP` mikroservis başarıyı bildirdiğinde `COMPLETED` olarak oluşur. `REFUND` önce `PENDING` kaydedilir, `/refundPayment` başarılıysa `COMPLETED` olur.
- **Bahşiş:** `tipDriver(id, amount, transferDriverId?)` CUSTOMER, sadece iç sürücüye. `transferDriverId` verilmezse transferin tek iç sürücüsü; iç sürücü yoksa veya verilen atama dış sürücüyse `ERR033`, birden fazla iç sürücü varken verilmezse `ERR030`.
- **İade:** `refundTransaction(id, amount?)` ADMIN/STAFF, kısmi veya kalan tutarın tamamı, toplam iade ödemeyi aşamaz (`ERR032`). Bahşiş de iade edilebilir.
  - **Kilit:** her iade bir DB transaction'ında satır kilidiyle yapılır (`select_for_update`, `psp.create_refund`/`resend_refund`): yeni iadede ödeme (parent) satırı, tekrar denemede iade satırı kilitlenir. Aynı anda gelen istekler kilidi bekler ve işlenmiş iadeyi görür, aynı tutar iki kez iade edilmez. Kilit `/refundPayment` isteği boyunca tutulur. SQLite'ta (testler) `select_for_update` etkisizdir, kilit PostgreSQL'de çalışır.
  - **Başarısız iade:** mikroservis hata verirse iade `PENDING` olarak kalır, mikroservisin hatası `Transaction.error_code`'a yazılır (dict ise `code`/`message`, en fazla 50 karakter; ulaşılamazsa `ERR031`), başarılı tekrar denemede temizlenir (hata dönmez, kayıt döner), müşteriye `REFUND` bildirimi sadece `COMPLETED` olunca gider. `PENDING` iadeler iade edilebilir tutardan düşülür, yani üstüne yeni iade açılamaz, tekrar denenmelidir. `cancelTransfer`'da iade başarısız olsa da transfer `CANCELLED` olur.
  - **`retryRefund(id)`** (CUSTOMER, OPERATOR, ADMIN, STAFF; kapsam `transactions` sorgusu gibi, bulunamazsa `ERR034`): `PENDING` veya `FAILED` iadeyi aynı kayıtla tekrar gönderir. `COMPLETED` iade için `ERR042`.
- **İptal cezası (standart iade):** state'in `customerImposedHours`/`customerImposedRates` basamakları (`psp.get_penalty_rate`). Sadece müşterinin iptalinde uygulanır, OPERATOR/ADMIN/STAFF iptali tam iade.

#### 4.3.0 Fiyat alanları ve koşulsuz iade (iptal güvencesi)
- `Transfer.customer_price` (eski `price`, migration `0019` yeniden adlandırdı): araç fiyatı. `Transfer.cancel_price` (varsayılan 0.00): güvence ücreti. `Transfer.price` **property**: `customer_price + cancel_price` (seçim yoksa `None`); GraphQL'de `price` toplamdır, `customerPrice`/`cancelPrice` ayrı döner. Ödeme linki, `psp_data.order_amount` ve mail `price`'ı toplamdır; mikroservise giden `transfer` payload'ına `price` elle eklenir (`get_dict` property'yi görmez).
- `payTransfer(refundOption?)` (`RefundOption`: `STANDARD` varsayılan, `UNCONDITIONAL`): `UNCONDITIONAL`'da state `allow_unconditional_refund` kapalıysa `ERR055`, açıksa `cancel_price = customer_price × cancel_price_rate` (kuruşa `ROUND_HALF_UP`, `get_cancel_price`). Tekrar `STANDARD` ile ödeme `cancel_price`'ı 0 yapar. Transferde ayrı bir bayrak yok: `cancel_price > 0` (`has_unconditional_refund`) koşulsuz iade demektir.
- İptal iadesi (`psp.refund_cancelled_transfer`): OPERATOR/ADMIN/STAFF iptali her zaman hepsini (güvence ücreti dahil) iade eder. Müşteri iptalinde güvence varsa saat fark etmeksizin `customer_price` iade edilir, `cancel_price` tutulur; yoksa standart ceza basamakları.

### 4.3.1 İptal sebebi (`Reason`, `schemas/reason.py`)
- Ottobus'taki hazır sebep listesi mantığı, ama transfere metin kopyalanmaz, **FK** ile bağlanır.
- **`Reason`**:
  - `code`: benzersiz snake_case anahtar (`[a-z0-9]+(_[a-z0-9]+)*`, en fazla 50; geçersizse `ERR030`, varsa `ERR053`). Filtrelerde kullanılır.
  - `category` (`ReasonCategory`): `CUSTOMER_TERMINATION` (müşteri ödenmemiş transferden vazgeçer), `CUSTOMER_CANCELLATION` (müşteri ödenmiş transferi iptal eder), `OPERATOR_CANCELLATION` (OPERATOR/ADMIN/STAFF), `DRIVER_CANCELLATION` (şimdilik kullanılmıyor, DRIVER iptal edemez), `DRIVER_DISAPPROVAL`/`VEHICLE_DISAPPROVAL` (sürücü/araç onayının reddi için, henüz bir akış yok).
  - `description` çok dilli (`description_en` zorunlu, `tr`/`de`/`ru` boşsa `en`'e düşer, en fazla 200). `order` (sıralama `category`, `order`, `id`). `is_active` (varsayılan true).
- **Sorgu/mutation:** `reasons(category?, code?, isActive?, language?)` ALL (giriş gerekli), `description` `language`'da veya kullanıcının dilinde. Pasif sebepleri sadece ADMIN/STAFF görür (`isActive` ile süzer), diğer rollere sadece aktifler döner. `createReason`/`updateReason`/`deleteReason` ADMIN, STAFF; bulunamayan `ERR050`. Transferde kullanılan sebep silinemez (`PROTECT`, `ERR054`), pasifleştirilir.
- **`cancelTransfer(data: CancelTransfer)`**: `id` + **ya** `cancellationReasonId` **ya** `cancellationReasonText` (ikisi de yoksa/metin boşsa `ERR051`, ikisi birden `ERR030`). Hata olursa iptal olmaz.
  - `cancellationReasonId`: aktif olmalı (`ERR050`) ve kategorisi iptal edene uymalı (`ERR052`): müşteri + `PENDING` → `CUSTOMER_TERMINATION`, müşteri + `ACCEPTED` → `CUSTOMER_CANCELLATION`, diğerleri → `OPERATOR_CANCELLATION` (`schemas/transfer.py` `cancellation_category`).
  - `cancellationReasonText` ("Diğer"): trim, 3–200 karakter (`ERR030`), yazıldığı dilde saklanır.
  - Çıktı: `Transfer.cancellationReason` (`Reason`, açıklama kullanıcının dilinde), `cancellationReasonId`, `cancellationReasonText`. Sebep metni düzenlenirse eski iptallerde de değişir (anlam değişecekse yeni sebep eklenmeli).
- `TransferFilter`: `cancellationReasonId`, `cancellationReasonCode: [String!]` (kodlardan biri), `hasCancellationReasonText`.
- Başlangıç verisi `core/fixtures/reasons.json`: 73 kayıt, 6 kategori, en/tr/de/ru, kategori içi `order`, kodlar kategori önekiyle (`withdraw_`, `cancel_`, `operator_`, `driver_`, `driver_reject_`, `vehicle_reject_`). `manage.py loaddata reasons`; pk'lar 1–73, aynı pk'lı kayıtların üstüne yazar. pk'lar kaydırıldıysa eski sürümün üstüne yüklenemez (`code` benzersiz), önce eski sebepler silinmeli (transferde kullanılanlar `PROTECT`).
- `transfer`/`transfers` sorguları ve `cancelTransfer` sonucu kullanıcının dilini aktif eder (`cancellationReason.description`, `vehicleCategory.name`). `notify_users` alıcıların dilini aktif ettiği için mutation'larda `activate(user.language)` bildirimden **sonra** çağrılmalı.

### 4.3.2 Ödemeyi kapatma (`State.payment_enabled`)
- ADMIN/STAFF `updateState(paymentEnabled: false)` ile bölgede online ödemeyi kapatır. Kapalıyken `payTransfer` ve `tipDriver` `ERR060`, koşulsuz iade seçilemez (`get_cancel_price` `ERR055`; create/update/confirm hepsinde).
- **`confirmTransfer(data: ConfirmTransfer)`** (CUSTOMER, OPERATOR, ADMIN, STAFF; `PayTransfer` ile aynı alanlar): sadece ödeme kapalıyken (açıksa `ERR061`). `payTransfer`'ın kontrolleri ve araç seçimi, açık ödeme linki expire edilir, koşullu update ile `PENDING → ACCEPTED` + `Transfer.is_offline_payment = true` (eski linkin ödemesi araya girdiyse `ERR014`). Bildirim ödenmiş transferdeki gibi (`psp.notify_transfer_accepted`: ADMIN/STAFF + operatör bildirimi ve `transfer_accepted` maili, müşteriye mail, `TRANSFER_ACCEPTED`), metni "confirmed without payment". PAYMENT transaction oluşmaz, ücret sistem dışında alınır.
- `isOfflinePayment` transferi iptal etmek iade çağırmaz (`refund_cancelled_transfer` PAYMENT yoksa bir şey yapmaz), müşteri iptal cezası uygulanamaz.
- Anahtar kapatılınca açık ödeme linklerine dokunulmaz: kendi süresinde kapanır, ödenirse webhook normal işler. `refundTransaction`/`retryRefund` kapalı bölgede de çalışır (eski ödemeler).

### 4.4 Mesajlaşma (`schemas/message.py`)
- Mesajlar `Transfer`'e bağlı. Katılımcılar: `customer`, `operator` (atamayı yapan), transferin bütün iç sürücüleri (`utils.get_driver_ids`, dış sürücülerin hesabı yok) ve **tüm aktif ADMIN/STAFF kullanıcıları** (her transferin mesajlaşmasına dahil). Alıcılar gönderen dışındaki katılımcılar (`utils.get_messaging_receiver_ids`), yani ADMIN/STAFF her mesajın bildirimini ve WebSocket olayını alır.
- Sadece `ACCEPTED`/`STARTED` iken açık (`ERR019`). Gönderebilen roller: CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF. ADMIN/STAFF hepsini okur. Sonradan eklenen ADMIN/STAFF eski mesajların alıcısı olmaz ama hepsini okuyabilir.
- Reply (`parentId`), dosya (2MB, `ERR024`) ve konum eki var. Düzenleme 1 saat içinde (`is_edited` true olur), silme soft delete. Her mesajda alıcılara `MESSAGE` bildirimi ve WebSocket olayı.
- **Tikler** (`Message.status`, `MessageStatus`: `SENT` tek gri, `DELIVERED` çift gri, `SEEN` çift mavi): sadece ileri gider, kayıtlıdır (anlık rolden hesaplanmaz). Sadece gönderene döner, diğerlerine ve silinmiş mesajda `null`.
  - **Kim ilerletir** (`get_status_messages`): müşterinin ve sürücünün mesajını ADMIN, STAFF veya transferin OPERATOR'ünden **herhangi biri** (sonradan eklenen ADMIN/STAFF dahil); ADMIN/STAFF/OPERATOR'ün mesajını **transferin müşterisi**. Sürücünün ve gönderenin kendi okuması sayılmaz.
  - `deliverMessages(transferId?)`: istemci `MESSAGE` olayını alınca (o transfer) ve bağlanınca (hepsi) çağırır, tekrar çağrılması etkisizdir. `seeMessages(transferId, upToMessageId?)`: eskisi gibi `is_seen` + bildirimler, ayrıca `SEEN`. `upToMessageId` verilirse sonraki mesajlara dokunmaz (bildirimler yine görüldü olur).
  - Değişiklik satır kilidiyle yapılır (`update_message_status`), olayı sadece gerçekten değiştiren istek yayınlar: `MESSAGE_STATUS`, sadece gönderene, `transferId` + `messageIds` + `messageStatus`.
  - `updateMessage`/`deleteMessage` `update_fields` ile kaydeder, eski `status`'ü geri yazmaz.
  - Migration `0021` eski mesajlardan okunmuş olanları `SEEN` yapar, diğerleri `SENT`.
- **Tepkiler** (`MessageReaction`, `Message.reactions`): kişi başına mesaj başına tek tepki. `emoji` `ReactionEmoji` kodu olarak saklanır (`LIKE` 👍, `LOVE` ❤️, `LAUGH` 😂, `WOW` 😮, `SAD` 😢, `PRAY` 🙏; gösterim istemcide). `reactMessage(id, emoji?)`: emoji ekler/değiştirir, `null` kaldırır. Kendi mesajına da verilebilir. Mesajı göremeyen/olmayan `ERR001`, silinmiş `ERR021`, sohbet kapalı `ERR019`. Mesaj silinince tepkileri de silinir. **Bildirim gitmez**, tikleri etkilemez; sadece `MESSAGE_REACTION` olayı, tepki veren dışında mesajı hâlâ görebilenlere (`get_reaction_receiver_ids`): `transferId`, `messageId`, `message` (emoji kodu, kaldırmada boş).

### 4.5 Bildirim ve push
- `utils.create_notifications` DB + WebSocket (`NOTIFICATION`) + FCM push (`FCM_ENABLED`, yani `FIREBASE_CREDENTIALS` doluysa, `tasks.send_push`).
- `utils.notify_users` her alıcının dilinde bildirim üretir (`gettext_noop` metinleri). Diller: `en`, `tr`, `de`, `ru` (`enums.Language`, `settings.LANGUAGES`). Çeviriler `locale/<dil>/LC_MESSAGES/django.po`; yeni metin eklenince `makemessages -l tr -l de -l ru --ignore=venv` → çevir → `compilemessages --ignore=venv` (`.mo` dosyaları da commit'lenir).
- Cihaz: `updateFcmToken(registrationId, type)`, `deleteFcmToken(registrationId)`. `deleteUser` cihazları siler.

### 4.5.1 Periyodik işler (`core/tasks.py`, celery beat)
- `CELERY_BEAT_SCHEDULE` (`settings/base.py`): `run_hourly_tasks` her saat başı (`crontab(minute=0)`). Çalışması için `celery -A transfer worker` yanında `celery -A transfer beat` gerekir.
- `run_hourly_tasks` şimdilik sadece `check_transfers_expiring`'i çağırır, yeni saatlik işler buraya eklenir.
- `check_transfers_expiring`: `pickup_time`'ı geçmiş **sadece `PENDING`** (ödenmemiş) transferler `EXPIRED` olur, ödeme linki `/expirePaymentLink` ile kapatılır. Koşullu update ile yapılır, aynı anda gelen ödeme kazanır. `ACCEPTED`/`STARTED` transferlere dokunulmaz (kullanıcı kararı, ödenmiş transfer otomatik expire/iade edilmez). Bildirim gönderilmez.

### 4.6 WebSocket olayları (`subscribe`, `SubscriptionResponse`)
`USER_UPDATED`, `USER_DEACTIVATED`, `NOTIFICATION`, `MESSAGE`, `UPDATE_MESSAGE`, `DELETE_MESSAGE`, `MESSAGE_STATUS` (§4.4), `MESSAGE_REACTION` (§4.4), `PAYMENT_SUCCESS`, `PAYMENT_FAILED`, `TIP_PAYMENT_SUCCESS`, `TIP_PAYMENT_FAILED`, `REFUND` (sadece bildirim), `TRANSFER_PAID`, `TRANSFER_ASSIGNED`, `TRANSFER_STATUS_UPDATED`, `TRANSFER_CANCELLED`. Transfer olaylarında `transferId` dolu.

## 5. Hata kodları

`errors.txt` tek kaynak. Sıradaki kod **ERR062**.

| Aralık | Alan |
|---|---|
| ERR001–ERR004, ERR009–ERR012, ERR043–ERR045, ERR059 | Auth, kullanıcı (ERR010: kullanıcı yok/pasif, ERR012: yeni şifre kurallara uymuyor, ERR059: `signIn`'de şifre yanlış, ERR043: telefon zorunlu, ERR044: davet bulunamadı/kullanılmış, ERR045: sosyal giriş token'ı geçersiz) |
| ERR013–ERR016, ERR035, ERR048–ERR049 | Transfer (ERR035: district bulunamadı, ERR048: sürücü sayısı `vehicle_count`'a ulaştı, ERR049: sürücü ataması bulunamadı) |
| ERR017–ERR024 | Mesajlaşma |
| ERR025–ERR029, ERR046–ERR047 | Araç kategorisi, araç (ERR046: araç sayısı kategorinin gereksiniminden az, ERR047: state çoklu araca izin vermiyor) |
| ERR030 | Geçersiz parametre |
| ERR057, ERR058 | Yolcu telefonu / adı zorunlu (`payTransfer`, `updateTransfer`'da boşa çevirme) |
| ERR056 | Çok fazla istek (rate limit, `quoteTransfer`) |
| ERR031–ERR034, ERR042, ERR055, ERR060–ERR061 | Ödeme (ERR042: iade zaten tamamlandı, ERR055: state koşulsuz iadeye izin vermiyor veya ödeme kapalı, ERR060: state'te ödeme kapalı (`payTransfer`, `tipDriver`), ERR061: ödeme açık, `confirmTransfer` kullanılamaz) |
| ERR036–ERR038 | State |
| ERR039–ERR041 | City, district (ERR039: city bulunamadı, ERR040/041: kullanımda) |
| ERR050–ERR054 | İptal sebebi (ERR050: reason bulunamadı/pasif, ERR051: iptal sebebi zorunlu, ERR052: sebep bu iptale uygun değil, ERR053: kod zaten var, ERR054: sebep kullanımda) |

## 6. Proje kararları (§15)

| Opsiyon | Karar |
|---|---|
| Mesajların bağlanacağı model | `Transfer` (iş modeli) |
| Reply + dosya/konum eki | ✅ |
| Özel mesaj, zaman penceresi, bildirim tercihi, UUID misafir, üye hesapları | ❌ |
| Çok dilli alanlar (modeltranslation) | ✅ `VehicleCategory.name/description`, `Reason.description` (en, tr, de, ru) |
| Kayıtta rol seçimi | ✅ CUSTOMER / DRIVER |
| S3 dosya depolama | ✅ `USE_S3=true` ise, değilse `MEDIA_ROOT` |
| FCM push | ✅ `FIREBASE_CREDENTIALS` doluysa |
| Sosyal giriş | Sorulmadı, ❌ |
| Araç–sürücü ilişkisi | `Vehicle` modeli, sürücüye bağlı (`Vehicle.driver`). Transfer–sürücü: `TransferDriver` (birden fazla, iç veya dış) |
| Mesafe/süre kaynağı | İstemci gönderir, fiyat sunucuda |

## 7. Açık konular

| Konu | Durum |
|---|---|
| Ödeme mikroservisi | Sadece istek kodları ve webhook yazıldı. Payload anahtarları ve `state` (`PSP_STATE`) mikroservisle doğrulanmalı. Webhook'un `payment_id` gönderip göndermediği bilinmiyor, son link ödendi varsayılıyor |
| Sürücü ödeme hesabı | `User.psp_data` (bahşiş için `provider`) dolduran bir akış (`/createUpdateAccount`) yok |
| İptal edilmiş transfer için gelen başarılı ödeme | Sadece loglanıyor, otomatik iade yok |
| Eşzamanlı atama | `assignDriver` kontrol + kayıt arasında kilit yok, iki operatör aynı anda aynı sürücüyü çakışan transferlere atayabilir veya `vehicle_count`'u aşabilir |
| Alış saati geçmiş ödenmiş (`ACCEPTED`) transferler | Otomatik işlem yok, `check_transfers_expiring` sadece `PENDING`'i expire eder |
| Çeviriler | `locale/` (tr, de, ru); mail şablonları mikroserviste `<action>_<dil>.html`, de/ru şablonları orada eklenmeli |
| District eşleştirme | Sadece isimle, şehir olmadan. Aynı isimli district'lerde yanlış eşleşme olabilir. PostgreSQL'de Türkçe `İ/ı` harfleri `iexact` ile her zaman eşleşmeyebilir |
