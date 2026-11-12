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
| `DRIVER` | Kendisine atanan transferleri görür, durumunu `STARTED`/`COMPLETED` yapar, mesajlaşır, bahşişlerini görür, kendi adına araç ekler (`createVehicle`, `driverId` her zaman kendisi; diğer roller `driverId` vermezse `ERR030`) |
| `ADMIN`, `STAFF` | Her şeyi görür. Araç kategorisi yönetimi ve iade (`refundTransaction`) sadece bunlarda |

Kayıt e-posta doğrulamalı (`getCode` → `validateCode`) iki mutation'la yapılır: `createCustomer` (`CUSTOMER`) ve `createDriver` (`DRIVER`), ikisi de `CreateUser` input'unu alır. `OPERATOR`'ü ADMIN `updateAdminUser` ile verir.

- **E-posta:** Django mail backend'i kullanılmaz. `tasks.send_email(data)` (celery) `MAIL_MS_URL`'e (mail mikroservisi, `transfer-microservice` `/mail/v1/sendTransferEmail`, `X-Access-Token: MS_ACCESS_TOKEN`) POST atar. `data`: `action` (şablon adı, `<action>_<dil>.html`), `to_user` (`email`, `language`), `subject`, `message`, opsiyonel `link`/`transfer`/`from_user`. `getCode` doğrulama kodunu `action: 'verification'`, `message: <kod>` ile gönderir. Hata loglanır, istemciye dönmez.
- **Müşteri daveti** (`schemas/invitation.py`, model `Invitation`): `inviteCustomer(email, language?)` ADMIN/STAFF/OPERATOR. `language` verilmezse davet edenin dili. Aktif kullanıcı varsa `ERR011`, geçersiz e-posta `ERR030`. Aynı e-postaya `PENDING` davet varsa yenilenir (gönderen, dil, `expires_at`) ve tekrar gönderilir. Mail `action: 'invitation'`, `message: <secret>`, `link: FRONTEND_URL/register?invitation=<secret>`, `from_user` (davet eden). Secret `is_validated` taşır, yani e-posta doğrulaması yerine geçer ve doğrudan `createCustomer`'a verilir (`createDriver`'da `ERR001`). Kayıtta davet `ACCEPTED` (`receiver` set) olur, müşteri `status=ACCEPTED` açılır. Davetsiz kayıt da o e-postanın `PENDING` davetlerini kapatır. Kullanılmış/bulunamayan davet `ERR044`, süresi geçmiş secret `ERR009` (`INVITATION_EXPIRE_DAYS`, varsayılan 7). `invitations` sorgusu ADMIN/STAFF hepsini, OPERATOR kendi gönderdiklerini görür. Public `invitationInfo(secret)` kayıt formu için e-posta + dil döner. Süresi geçen davet `PENDING` kalır, istemci `expiresAt`'e bakar.
- **Telefon zorunlu:** `CreateUser` ve `CreateAdminUser`'da `phone` zorunlu, boşlukları atılır, boşsa `ERR043`. `updateUser` telefonu değiştirebilir ama boş/`null` ile silemez (`ERR043`). DB alanı `blank=True` kaldı (eski kullanıcılarda boş olabilir), kural API'de. `createTransfer` müşterinin (OPERATOR/ADMIN/STAFF `customerId` verirse o müşterinin) telefonu yoksa `ERR043`, kullanıcı önce profilinden telefon eklemeli.

- **`User.status`** (`VerifyStatus`: `PENDING` varsayılan, `ACCEPTED`, `REJECTED`): sadece ADMIN/STAFF görür (`permissions.json`). `updateAdminUser` ile ADMIN ve STAFF günceller, STAFF bu mutation'da sadece `status` gönderebilir (`role`/`isActive` ADMIN'e özel, `ERR001`). `createCustomer`/`createDriver` ile açılanlar `PENDING`. Status henüz hiçbir akışı (giriş, atama) etkilemiyor.
- **`createAdminUser`** (ADMIN, STAFF): e-posta + şifre ile her rolde kullanıcı açar, `status=ACCEPTED`. `ADMIN`'i sadece ADMIN açabilir (`ERR001`). Opsiyonel `districtIds`, `OPERATOR` için zorunlu (`ERR030`), bulunamayan district `ERR035`. Kullanıcı iletişim bilgisi (`email`, `phone`) sadece kişinin kendisine ve ADMIN/STAFF/OPERATOR'e görünür.

## 4. Akış

```
createTransfer ──► PENDING + vehicleOptions (uygun kategoriler, her biri fiyatlı)
   │ (updateTransfer: rota/ihtiyaç değişirse seçim sıfırlanır, link expire edilir)
payTransfer(vehicleCategoryId) ──► kategori + fiyat transfere yazılır, /createPaymentLink
   │
psp-return PAYMENT_RETURN success ──► PAID, PAYMENT transaction, tüm OPERATOR'lere bildirim (TRANSFER_PAID)
   │
availableDrivers / availableVehicles ──► assignDriver ──► ASSIGNED (driver + vehicle + operator)
   │                                        sürücüye ve müşteriye TRANSFER_ASSIGNED
updateTransferStatus ──► STARTED ──► COMPLETED ──► tipDriver ──► /createTipLink ──► TIP_RETURN
cancelTransfer (PENDING/PAID/ASSIGNED) ──► ödenmişse /refundPayment, değilse /expirePaymentLink
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
- **State** (`schemas/state.py`): `state`/`states` (ADMIN, STAFF, OPERATOR), `createState`/`updateState`/`deleteState` (ADMIN, STAFF). `code` büyük/küçük harf duyarsız benzersiz (`ERR038`). `customerImposedHours` artan pozitif saatler, `customerImposedRates` aynı uzunlukta 0–1 arası oranlar (`ERR030`), oranlar string olarak saklanır. Silme şehir ve district'leri de siler, transfer bağlıysa `ERR037`.

### 4.0.1 Ara duraklar (`Leg`)
- `createTransfer` opsiyonel `legs` listesi alır (`CreateLeg`: başlangıç/bitiş konum, adres ve zaman, `order`, `route`). Gelirse transferle birlikte kaydedilir, `Transfer.legs` olarak `order` sırasıyla döner.
- `order` verilmezse listedeki sırası (0'dan) kullanılır. `startTime < endTime` olmalı ve `order` negatif olamaz, değilse `ERR030`. Leg'ler transfer kaydedilmeden önce doğrulanır, yani geçersiz leg varsa transfer de oluşmaz.
- Leg'ler fiyatı, uygunluğu ve sürücü müsaitliğini etkilemez. `updateTransfer` leg'leri değiştirmez.

### 4.1 Araç kategorisi ve fiyat
- `VehicleCategory`: `max_passengers`, `max_luggage`, `child_seat_capacity`, `is_wheelchair_accessible`, `base_fare`, `per_km_price`, `per_minute_price`, `minimum_fare`, `currency`, `order`, `is_active`. `name`/`description` çok dilli (`name_en`, `name_tr`, `name_de`, `name_ru`; boş olan dil `en`'e düşer).
- **Uygunluk** (`VehicleCategoryQuerySet.eligible_for`): aktif ve `passenger_count`, `luggage_count`, `child_seat_count` kapasiteyi aşmıyor, `needs_wheelchair` ise erişilebilir.
- **Fiyat** (`VehicleCategory.get_price`): `max(minimum_fare, base_fare + distance_km * per_km_price + duration_min * per_minute_price)`, 2 ondalık.
- `distance` (km) ve `duration` (dk) istemciden gelir (harita SDK'sı), fiyat her zaman sunucuda hesaplanır.
- `vehicleOptions(transferId)` aynı listeyi tekrar döner. İsimler kullanıcının dilinde döner. Public `vehicleCategories(language)` sorgusunda dil argümandan alınır.

### 4.2 Uygun sürücü sorgusu (`core/managers.py`)
- `User.objects.available_drivers(transfer)`: aktif ve `status=ACCEPTED` `DRIVER`, transferin kategorisinde **aktif aracı olan** ve o saatte **başka bir işi olmayan** kullanıcılar.
- **Meşgul** = `ASSIGNED`/`STARTED` durumundaki başka bir transferi, `[pickup_time - buffer, end_time + buffer]` aralığıyla kesişen sürücü. `end_time = pickup_time + duration` (`Transfer.save`), `buffer = DRIVER_BUFFER_MINUTES` (30). Yani iki iş arasında en az 30 dk olmalı.
- `Vehicle.objects.available_for(transfer)`: uygun sürücülerin uygun araçları (`availableVehicles`).
- `assignDriver` aynı sorguyla doğrular (`ERR015`). `vehicleId` verilmezse sürücünün uygun ilk aracı atanır (`ERR027`). `PAID` ve `ASSIGNED` iken (yeniden atama) çağrılabilir. Eski sürücüye `TRANSFER_CANCELLED` bildirimi gider.
- **Dikkat:** liste döndüren `sbd.field`'larda argüman adı `id` olursa strawberry-django onu pk filtresi olarak uygular. Transfer'e bağlı sorgular bu yüzden `transferId` alır.

### 4.2.1 Sürücü ücreti (`driver_name`, `driver_price`, `driver_price_currency`)
- Opsiyonel `Transfer` alanları. `driverPrice`/`driverPriceCurrency` sadece ADMIN/STAFF/OPERATOR görür, `driverName`'i ayrıca CUSTOMER da görür (`permissions.json`). Diğerlerine `null` döner (DRIVER `driverName`'i de göremez).
- `assignDriver`: `driverName` sürücünün adından (`first_name last_name`) set edilir, opsiyonel `driverPrice` (2 ondalığa yuvarlanır, negatifse 0 kaydedilir) ve `driverPriceCurrency` alınır. Yeniden atamada fiyat verilmezse önceki değer kalır.
- `updateTransfer`: bu üç alanı sadece OPERATOR/ADMIN/STAFF gönderebilir (`ERR001`). Sadece bu alanlar gönderilirse `PENDING`/`ACCEPTED`/`STARTED` iken güncellenir, başka alan da varsa eskisi gibi sadece `PENDING` (`ERR014`). Transferde sürücü atanmışsa `driverName` yok sayılır.

### 4.3 Ödeme (`core/psp.py`)
Ödeme mikroservisi (`3rd-party-microservices`) istemcisi. Payload anahtarları ottobus/meetngreet sözleşmesini izler. `PSP_MS_URL`, `MS_ACCESS_TOKEN`, `PSP_STATE` (`.env`). Mikroservis/ağ hatası istemciye `ERR031` döner (`psp.PSPError`), mikroservisin kendi hatası `error_code` özelliğinde taşınır (ağ hatasında `ERR031`).

| İstek | Ne zaman |
|---|---|
| `/createPaymentLink` | `payTransfer`. Önceki link varsa önce `/expirePaymentLink` |
| `/expirePaymentLink` | Seçim değişince, rota güncellenince, ödenmemiş transfer iptalinde |
| `/createTipLink` | `tipDriver` (sadece `COMPLETED`). `provider` = sürücünün `psp_data`'sı |
| `/refundPayment` | `cancelTransfer` (ödenmişse) ve `refundTransaction` |

- **Webhook `POST /psp-return/`** (`X-Access-Token`): `PAYMENT_RETURN` → transfer koşullu update ile `PENDING → PAID` (tekrar gelen istek bir kez işlenir), `PAYMENT` transaction, müşteriye `PAYMENT_SUCCESS`, operatörlere bildirim + `TRANSFER_PAID`. `TIP_RETURN` → `TIP` transaction, sürücüye bildirim. Başarısızsa `PAYMENT_FAILED` / `TIP_PAYMENT_FAILED`.
- **Transaction** kategorileri: `PAYMENT`, `TIP`, `REFUND` (`parent` = iade edilen kayıt). `user` her zaman müşteri. Sorgu kapsamı: ADMIN/STAFF hepsi, OPERATOR operatörü olduğu transferlerinkiler, DRIVER kendi transferlerinin bahşişleri (ve iadeleri), diğerleri kendi kayıtları. Kapsam `utils.get_transactions`'ta. `Transfer.transactions` alanı da aynı kapsamla o transferin kayıtlarını döner (token yoksa boş liste), transfer detayında ayrıca `transactions` sorgusuna gerek yok.
- **`is_payout_sent`**: sadece OPERATOR/ADMIN/STAFF görür. `updateTransaction(id, note?, isPayoutSent?)` OPERATOR/ADMIN/STAFF, OPERATOR sadece operatörü olduğu transferlerin kayıtlarını görür ve günceller (`ERR034`). `TransactionFilter.isPayoutSent` da sadece bu rollerde, diğerleri kullanırsa `ERR001`.
- **`Transaction.status`** (`TransactionStatus`: `PENDING` varsayılan, `FAILED`, `COMPLETED`; `FAILED`'ı şu an hiçbir akış set etmiyor): `PAYMENT`/`TIP` mikroservis başarıyı bildirdiğinde `COMPLETED` olarak oluşur. `REFUND` önce `PENDING` kaydedilir, `/refundPayment` başarılıysa `COMPLETED` olur.
- **İade:** `refundTransaction(id, amount?)` ADMIN/STAFF, kısmi veya kalan tutarın tamamı, toplam iade ödemeyi aşamaz (`ERR032`). Bahşiş de iade edilebilir.
  - **Kilit:** her iade bir DB transaction'ında satır kilidiyle yapılır (`select_for_update`, `psp.create_refund`/`resend_refund`): yeni iadede ödeme (parent) satırı, tekrar denemede iade satırı kilitlenir. Aynı anda gelen istekler kilidi bekler ve işlenmiş iadeyi görür, aynı tutar iki kez iade edilmez. Kilit `/refundPayment` isteği boyunca tutulur. SQLite'ta (testler) `select_for_update` etkisizdir, kilit PostgreSQL'de çalışır.
  - **Başarısız iade:** mikroservis hata verirse iade `PENDING` olarak kalır, mikroservisin hatası `Transaction.error_code`'a yazılır (dict ise `code`/`message`, en fazla 50 karakter; ulaşılamazsa `ERR031`), başarılı tekrar denemede temizlenir (hata dönmez, kayıt döner), müşteriye `REFUND` bildirimi sadece `COMPLETED` olunca gider. `PENDING` iadeler iade edilebilir tutardan düşülür, yani üstüne yeni iade açılamaz, tekrar denenmelidir. `cancelTransfer`'da iade başarısız olsa da transfer `CANCELLED` olur.
  - **`retryRefund(id)`** (CUSTOMER, OPERATOR, ADMIN, STAFF; kapsam `transactions` sorgusu gibi, bulunamazsa `ERR034`): `PENDING` veya `FAILED` iadeyi aynı kayıtla tekrar gönderir. `COMPLETED` iade için `ERR042`.
- **İptal cezası:** `CANCEL_PENALTY_RULES` (settings, varsayılan `[]` = her zaman tam iade). Sadece müşterinin iptalinde uygulanır, OPERATOR/ADMIN/STAFF iptali tam iade.

### 4.4 Mesajlaşma (`schemas/message.py`)
- Mesajlar `Transfer`'e bağlı. Katılımcılar: `customer`, `operator` (atamayı yapan), `driver` ve **tüm aktif ADMIN/STAFF kullanıcıları** (her transferin mesajlaşmasına dahil). Alıcılar gönderen dışındaki katılımcılar (`utils.get_messaging_receiver_ids`), yani ADMIN/STAFF her mesajın bildirimini ve WebSocket olayını alır.
- Sadece `ACCEPTED`/`STARTED` iken açık (`ERR019`). Gönderebilen roller: CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF. ADMIN/STAFF hepsini okur. Sonradan eklenen ADMIN/STAFF eski mesajların alıcısı olmaz ama hepsini okuyabilir.
- Reply (`parentId`), dosya (2MB, `ERR024`) ve konum eki var. Düzenleme 1 saat içinde (`is_edited` true olur), silme soft delete. Her mesajda alıcılara `MESSAGE` bildirimi ve WebSocket olayı.

### 4.5 Bildirim ve push
- `utils.create_notifications` DB + WebSocket (`NOTIFICATION`) + FCM push (`FCM_ENABLED`, yani `FIREBASE_CREDENTIALS` doluysa, `tasks.send_push`).
- `utils.notify_users` her alıcının dilinde bildirim üretir (`gettext_noop` metinleri). Diller: `en`, `tr`, `de`, `ru` (`enums.Language`, `settings.LANGUAGES`). Çeviriler `locale/<dil>/LC_MESSAGES/django.po`; yeni metin eklenince `makemessages -l tr -l de -l ru --ignore=venv` → çevir → `compilemessages --ignore=venv` (`.mo` dosyaları da commit'lenir).
- Cihaz: `updateFcmToken(registrationId, type)`, `deleteFcmToken(registrationId)`. `deleteUser` cihazları siler.

### 4.5.1 Periyodik işler (`core/tasks.py`, celery beat)
- `CELERY_BEAT_SCHEDULE` (`settings/base.py`): `run_hourly_tasks` her saat başı (`crontab(minute=0)`). Çalışması için `celery -A transfer worker` yanında `celery -A transfer beat` gerekir.
- `run_hourly_tasks` şimdilik sadece `check_transfers_expiring`'i çağırır, yeni saatlik işler buraya eklenir.
- `check_transfers_expiring`: `pickup_time`'ı geçmiş **sadece `PENDING`** (ödenmemiş) transferler `EXPIRED` olur, ödeme linki `/expirePaymentLink` ile kapatılır. Koşullu update ile yapılır, aynı anda gelen ödeme kazanır. `ACCEPTED`/`STARTED` transferlere dokunulmaz (kullanıcı kararı, ödenmiş transfer otomatik expire/iade edilmez). Bildirim gönderilmez.

### 4.6 WebSocket olayları (`subscribe`, `SubscriptionResponse`)
`USER_UPDATED`, `USER_DEACTIVATED`, `NOTIFICATION`, `MESSAGE`, `UPDATE_MESSAGE`, `DELETE_MESSAGE`, `PAYMENT_SUCCESS`, `PAYMENT_FAILED`, `TIP_PAYMENT_SUCCESS`, `TIP_PAYMENT_FAILED`, `REFUND` (sadece bildirim), `TRANSFER_PAID`, `TRANSFER_ASSIGNED`, `TRANSFER_STATUS_UPDATED`, `TRANSFER_CANCELLED`. Transfer olaylarında `transferId` dolu.

## 5. Hata kodları

`errors.txt` tek kaynak. Sıradaki kod **ERR045**.

| Aralık | Alan |
|---|---|
| ERR001–ERR004, ERR009–ERR012, ERR043, ERR044 | Auth, kullanıcı (ERR043: telefon zorunlu, ERR044: davet bulunamadı/kullanılmış) |
| ERR013–ERR016, ERR035 | Transfer (ERR035: district bulunamadı) |
| ERR017–ERR024 | Mesajlaşma |
| ERR025–ERR029 | Araç kategorisi, araç |
| ERR030 | Geçersiz parametre |
| ERR031–ERR034, ERR042 | Ödeme (ERR042: iade zaten tamamlandı) |
| ERR036–ERR038 | State |
| ERR039–ERR041 | City, district (ERR039: city bulunamadı, ERR040/041: kullanımda) |

## 6. Proje kararları (§15)

| Opsiyon | Karar |
|---|---|
| Mesajların bağlanacağı model | `Transfer` (iş modeli) |
| Reply + dosya/konum eki | ✅ |
| Özel mesaj, zaman penceresi, bildirim tercihi, UUID misafir, üye hesapları | ❌ |
| Çok dilli alanlar (modeltranslation) | ✅ `VehicleCategory.name/description`, en + tr |
| Kayıtta rol seçimi | ✅ CUSTOMER / DRIVER |
| S3 dosya depolama | ✅ `USE_S3=true` ise, değilse `MEDIA_ROOT` |
| FCM push | ✅ `FIREBASE_CREDENTIALS` doluysa |
| Sosyal giriş | Sorulmadı, ❌ |
| Araç–sürücü ilişkisi | `Vehicle` modeli, sürücüye bağlı (`Vehicle.driver`) |
| Mesafe/süre kaynağı | İstemci gönderir, fiyat sunucuda |

## 7. Açık konular

| Konu | Durum |
|---|---|
| Ödeme mikroservisi | Sadece istek kodları ve webhook yazıldı. Payload anahtarları ve `state` (`PSP_STATE`) mikroservisle doğrulanmalı. Webhook'un `payment_id` gönderip göndermediği bilinmiyor, son link ödendi varsayılıyor |
| Sürücü ödeme hesabı | `User.psp_data` (bahşiş için `provider`) dolduran bir akış (`/createUpdateAccount`) yok |
| İptal edilmiş transfer için gelen başarılı ödeme | Sadece loglanıyor, otomatik iade yok |
| Eşzamanlı atama | `assignDriver` kontrol + kayıt arasında kilit yok, iki operatör aynı anda aynı sürücüyü çakışan transferlere atayabilir |
| Alış saati geçmiş ödenmiş (`ACCEPTED`) transferler | Otomatik işlem yok, `check_transfers_expiring` sadece `PENDING`'i expire eder |
| Çeviriler | `locale/` (tr, de, ru); mail şablonları mikroserviste `<action>_<dil>.html`, de/ru şablonları orada eklenmeli |
| District eşleştirme | Sadece isimle, şehir olmadan. Aynı isimli district'lerde yanlış eşleşme olabilir. PostgreSQL'de Türkçe `İ/ı` harfleri `iexact` ile her zaman eşleşmeyebilir |
