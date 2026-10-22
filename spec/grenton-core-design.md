# Grenton Core — projekt techniczny biblioteki przenośnej

| | |
|---|---|
| **Status** | Projekt (do implementacji) |
| **Wersja** | 0.10.1 |
| **Data** | 2026-10-22 |
| **Implementacja referencyjna** | `homeassistant-grenton` (Python, integracja Home Assistant) |
| **Przeznaczenie** | Specyfikacja niezależna od języka — podstawa dla portów (pierwszy: Swift) |
| **Zestaw zgodności** | `conformance/` + kontrakt w [`conformance.md`](conformance.md) |

Ten dokument specyfikuje **Grenton Core**: przenośną bibliotekę implementującą protokół „interfejsu mobilnego" systemu Grenton — od pobrania konfiguracji z Object Managera, przez szyfrowaną komunikację UDP z kontrolerami CLU, po kanoniczny model urządzeń. Dokument jest agnostyczny technologicznie: opisuje typy, algorytmy, formaty przewodowe i kontrakty, a nie składnię konkretnego języka. Wszystkie fakty protokołowe pochodzą z implementacji referencyjnej (reverse-engineering zachowania aplikacji myGrenton); tam, gdzie Core celowo odchodzi od implementacji referencyjnej, odstępstwo oznaczono symbolem **Δ**.

---

## 1. Cel i zakres

### 1.1 Co biblioteka robi

1. **Provisioning** — pobiera i parsuje definicję interfejsu mobilnego z Grenton Object Managera (HTTP + ZIP + JSON) albo odczytuje ją z pliku projektu `.omp` (§4.5).
2. **Runtime** — utrzymuje szyfrowane sesje UDP z kontrolerami CLU: keep-alive, subskrypcje zmian stanu, wykonywanie akcji.
3. **Model kanoniczny urządzeń** — mapuje widgety interfejsu na urządzenia o znormalizowanym stanie i intencjach (włącz, ustaw jasność, ustaw pozycję…), tak aby adaptery platformowe były cienkie.

### 1.2 Czego biblioteka nie robi

- Nie zawiera encji/komponentów żadnej platformy (Home Assistant, HomeKit, SwiftUI…).
- Nie zawiera UI ani flow konfiguracyjnych.
- Nie persystuje niczego — konfiguracja wchodzi jako wartość, ustawienia użytkownika (device class, jednostki) są nieprzezroczystymi metadanymi adaptera (§6.4).
- Nie obsługuje trybu chmurowego Grentona (pole `connectionType` jest parsowane, ale Runtime zawsze łączy się lokalnie po UDP).
- Nie obsługuje push notifications (`pushNotifications` w JSON — parsowane, nieużywane).

### 1.3 Kontekst systemowy

```
┌──────────────────┐   HTTP (ZIP z JSON)   ┌─────────────────────┐
│ Object Manager   │◄──────────────────────│ Provisioning        │
│ (PC, LAN, :9998) │   raz, przy setupie   │                     │
└──────────────────┘                       │   G r e n t o n     │
┌──────────────────┐   UDP + AES-CBC       │      C o r e        │
│ CLU #1..N        │◄─────────────────────►│                     │
│ (LAN, port z cfg)│   ciągle, runtime     │ Runtime + Devices   │
└──────────────────┘                       └─────────┬───────────┘
                                                     │ kanoniczne urządzenia,
                                                     │ zdarzenia stanu, intencje
                                           ┌─────────▼───────────┐
                                           │ Adapter platformowy │
                                           │ (HA / Swift / …)    │
                                           └─────────────────────┘
```

Object Manager jest potrzebny **wyłącznie w fazie provisioningu** i musi być wtedy uruchomiony w tej samej sieci. Cały runtime rozmawia bezpośrednio z CLU.

---

## 2. Architektura modułów

Jedna biblioteka, cztery moduły o wymuszonych granicach zależności:

```
Provisioning ──► Model ◄── Runtime ◄── Devices
   (opcjonalny)   (czyste typy, zero I/O)
```

| Moduł | Odpowiedzialność | Zależności | I/O |
|---|---|---|---|
| **Model** | Typy DTO interfejsu, typy domenowe (CLU, ValueRef, ActionRef), `GrentonValue`, parsowanie/casting | brak | brak |
| **Provisioning** | Klient Object Managera: HTTP GET → ZIP → JSON → `MobileInterface`; import pliku projektu `.omp` → ten sam `MobileInterface` | Model | HTTP |
| **Runtime** | Szyfr, format ramek, katalog wiadomości, sesje CLU (sockety, chunking, pętle), magazyn stanu | Model | UDP |
| **Devices** | Mapowanie widget→urządzenie, normalizacja stanu, intencje, zdarzenia | Model, Runtime | brak (przez Runtime) |

Zasady:

- **Runtime przyjmuje sparsowany `MobileInterface` jako wejście** i nie wie, skąd pochodzi. Provisioning jest jednym z możliwych źródeł (inne: plik, backend aplikacji, QR); sam ma dwa: pobranie z OM (§4.1–§4.3) i plik projektu (§4.5).
- Wszystkie efekty uboczne (sieć, czas, losowość, logi) wchodzą przez **kontrakt portu** (§7) — interfejsy implementowane per platforma. Dzięki temu logika jest testowalna deterministycznie.
- Nazwy API w tym dokumencie (np. `CluSession`, `StateStore`, `GrentonValue`) są **nazwami referencyjnymi na potrzeby specyfikacji i testów zgodności** — wiążąca jest semantyka i zachowanie, nie brzmienie nazw; port może użyć nazw idiomatycznych dla swojego języka, dokumentując mapowanie.

---

## 3. Moduł Model

### 3.1 `GrentonValue` — typ wartości

Wartości w systemie Grenton są dynamicznie typowane. Kanoniczny typ sumy:

```
GrentonValue = string | bool | int | double | null
```

**Algorytm castowania stringa przewodowego** (`castString(s) -> GrentonValue`), stosowany do wartości z `clientReport` — kolejność jest znacząca:

1. Obetnij białe znaki z obu stron; następnie obetnij **wszystkie** wiodące i końcowe cudzysłowy `"` (jak Pythonowe `strip('"')`: `""ON""` → `ON`).
2. Jeżeli wynik, bez rozróżniania wielkości liter, to `nil` → `null`.
3. Jeżeli `true` → `true`; jeżeli `false` → `false` (bez rozróżniania wielkości liter).
4. Jeżeli pasuje do gramatyki `^-?[0-9]+$` **i mieści się w Int64** → `int` (64-bit ze znakiem); przy przepełnieniu Int64 → spróbuj jako `double`.
5. Jeżeli pasuje do gramatyki `^-?([0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)([eE][+-]?[0-9]+)?$` **z wymaganą kropką lub wykładnikiem** (czysta postać całkowita złapana w pkt. 4) i daje wartość skończoną → `double`. Zakazane: `inf`, `nan`, postać szesnastkowa, separatory (`_`).
6. W przeciwnym razie → `string` (już bez cudzysłowów).

**Δ** Implementacja referencyjna używa `int()`/`float()` Pythona (dowolna precyzja int, akceptacja `1_0`, `inf`). Core specyfikuje ścisłą gramatykę ASCII z pkt. 4–5 i szerokość Int64; porty nie mogą dziedziczyć liberalności parsera języka.

### 3.2 Referencje stanu — `ValueRef`

Referencja mówi, *skąd* czytać wartość na danym CLU. JSON:

```json
{ "cluId": "CLU_501234567", "objectName": "DOU8272", "index": "0", "callType": "ATTRIBUTE" }
```

| `callType` | Znaczenie | Klucz subskrypcji na drucie (§5.3.2) |
|---|---|---|
| `VARIABLE` | zmienna globalna CLU; `index` to jej nazwa | `"{index}"` (w cudzysłowach) |
| `ATTRIBUTE` | cecha obiektu; `objectName` + numer cechy `index` | `{{objectName},{index}}` |

Kanoniczne typy: `ValueRef.variable(cluId, name)` i `ValueRef.attribute(cluId, objectName, index)`. Pole `objectName` występuje w JSON także dla `VARIABLE` (ignorowane logicznie, ale parsowane).

### 3.3 Akcje — `ActionRef`

Akcja mówi, *co* wysłać do CLU. JSON:

```json
{ "cluId": "...", "objectName": "DOU8272", "index": "0", "value": "1",
  "event": "ON", "callType": "ATTRIBUTE" }
```

- `event ∈ {CLICK, ON, OFF}` — rola akcji w widgecie (po którym zdarzeniu UI ją wywołać).
- `callType ∈ {VARIABLE, ATTRIBUTE, METHOD, SCRIPT}` — wariant wykonania; `SCRIPT` **nie ma** pola `index`.
- `value` — argument; dla suwaków nadpisywany w locie wartością wybraną przez użytkownika, dla scen może być nadpisany parametrem (§6.3.10).
- **Placeholder `$value$`:** Object Manager eksportuje akcje ustawiające (np. `setValueAction` ściemniacza, `setPositionAction` rolety, wszystkie `set*Action` termostatu) z `value` równym dokładnie `$value$`. Intencja, która nadpisuje wartość, **zastępuje całe `value`** — dla `$value$` wynik jest ten sam co podstawienie. Akcja, której `value` to dokładnie `$value$`, nie może zostać wysłana bez nadpisania: budowa jej payloadu (§5.3.4) kończy się błędem `PayloadError.unresolvedPlaceholder` i nic nie trafia do CLU. Dotyczy to także niskopoziomowego wykonania surowej akcji; w katalogu §6.3 każda taka akcja jest używana wyłącznie przez intencję z wartością.

Payloady przewodowe akcji — patrz §5.3.4.

### 3.4 Schemat interfejsu mobilnego (`data.json`)

Korzeń:

| Pole | Typ | Uwagi |
|---|---|---|
| `id` | string | unikalny identyfikator interfejsu (tożsamość konfiguracji) |
| `version` | int | wersja interfejsu |
| `name`, `icon` | string | metadane prezentacyjne |
| `theme` | enum | `GRENTON`, `RED`, … (15 wartości; prezentacyjne) |
| `encryption` | obiekt | `{ "key": <base64>, "iv": <base64> }` — wspólne dla wszystkich CLU interfejsu |
| `clus` | tablica | patrz niżej |
| `pages` | tablica | strony UI z widgetami |
| `pushNotifications` | tablica | `{cluId:int, objectId, objectType}` — parsowane, nieużywane |

CLU:

| Pole | Typ |
|---|---|
| `id` | string (np. `CLU_501234567`) |
| `serialNumber` | string |
| `name` | string |
| `ip` | string |
| `port` | int (port UDP protokołu CLU) |
| `connectionType` | enum: `LOCAL_ONLY`, `CLOUD_CAPABLE`, `CLOUD` |

Strona (`page`): `name`, `icon`, `isFullscreenWidget: bool`, `widgets: []`.

### 3.5 Katalog widgetów

Każdy widget ma `id: string` i dyskryminator `type`. 16 obsługiwanych typów (plus `HEADER`, poniżej):

| `type` | Kluczowe pola |
|---|---|
| `VALUE_V2` | `label`, `icon`, `unit`, `valueType` (`STRING\|FLOAT\|INTEGER`), `precision:int`, `object.value: ValueRef` |
| `VALUE_DOUBLE` | `componentLeft` i `componentRight`, każdy: `label, icon, unit, valueType, precision, object.value` |
| `ON_OFF` | `components[]` typu `BUTTON_BISTABLE`: `label, rowId:int, unit, state: ValueRef, actions: ActionRef[], onIndication, offIndication` |
| `ON_OFF_DOUBLE` | `components[]` typu `BUTTON_BISTABLE_V2` (pola jak wyżej) |
| `DIMMER_V2` | `label, icon, min:double, max:double, precision:int`, `object: {value, setValueAction, onAction, offAction}` |
| `LED` | `components[]` — unia po `type`: `BUTTON_BISTABLE` (`image, onIndication, offIndication`), `SLIDER_HUE`, `SLIDER_SATURATION`, `SLIDER_BRIGHTNESS` (każdy slider: `range:{min:int,max:int}`); wszystkie mają `state` i `actions[]` |
| `CONTACT_SENSOR` | `label, icon, onIndication, offIndication, reverseState:bool`, `object: {value, clickAction?}` |
| `CONTACT_SENSOR_DOUBLE` | `componentLeft/Right`: `label, icon, onIndication, offIndication, reverseState, object` |
| `SLIDER` | `label, icon, unit, min:double, max:double, precision:int`, `object: {value, setValueAction, clickAction?}` |
| `MULTISENSOR` | `label, icon, labelVisible:bool` + 7 obiektów kanałów: `objectAirCo2, objectSound, objectAirVoc, objectLight, objectPressure, objectHumidity, objectTemperature`, każdy `{value, minValue?, maxValue?, threshold?, sensitivity?}` |
| `ROLLER_SHUTTER` (legacy) | `components[]` typu `BUTTON_BISTABLE`: `image, state, indication` (`ON_OFF\|OPEN_CLOSED\|OPEN_LOCKED\|UP_DOWN`), `actions[]` |
| `ROLLER_SHUTTER_V3` | `icon, label`, `object` z ~30 polami; wymagane: `state, position, lamelPosition, setPositionAction, setLamelPositionAction, moveUpAction, moveDownAction, startAction, setRollerBlockedAction`; opcjonalne m.in.: `stopAction?, lamelMoveTimeout?, up?, down?, holdAction?, calibrationAction?` |
| `CAMERA` | `components[]` typu `CAMERA_STREAM`: `label, rowId, unit, image, value` (**`value` = URL strumienia**, np. RTSP) |
| `SCENE` | `components[]` typu `BUTTON_MONOSTABLE`: `label, rowId, unit, actions: ActionRef[]` |
| `SCENE_DOUBLE` | jak `SCENE` (zwykle 2 komponenty, **oba z `rowId` 0**); opcjonalne `background` (prezentacyjne) |
| `THERMOSTAT_V2` | `label`, `icon?`, `no_of_fan_speeds?`, `object`: wymagane `currentTemperature`, `targetTemperature`, `setTargetTemperatureAction`; opcjonalne `minTemperature`, `maxTemperature`, `mode` + `setModeAction`, `state` + `setStateAction`, `controlOutValue`, `setMin/MaxTemperatureAction`, `scheduleData` + `setScheduleDataAction` |

**Widgety prezentacyjne:** `HEADER` (nagłówek sekcji: `components[]` z samym `label`) jest rozpoznawany, **nie tworzy urządzenia i nie jest raportowany jako pominięty** (§3.6) — to układ UI, nie błąd.

**Znane, nieobsługiwane w v1:** `EVENT_SCHEDULER` (harmonogram zdarzeń: lista reguł, dodawanie/usuwanie) i `TV_REMOTE_CONTROL` (pilot TV: `vodLabel`, `icon`, `label`, `object` z akcjami przycisków) — pomijane jak nieznany typ.

Wspólna baza komponentów listowych: `label: string`, `rowId: int`, `unit: enum {UNKNOWN, PERCENT, DEGREE}`.

### 3.6 Reguły parsowania

1. **Unie dyskryminowane.** Widgety dyskryminowane po `type`, komponenty LED po `type`, `ValueRef` po `callType`, `ActionRef` po `callType`. Nieznana wartość dyskryminatora = błąd parsowania danego elementu.
2. **Tolerancja na poziomie widgetu.** Widget, który nie parsuje się (nieznany `type`, brak pola, nieznana wartość enuma), jest **pomijany z logiem na poziomie debug** — strona i cały interfejs parsują się dalej. Pominięcie jest **obserwowalne**: dla każdej strony implementacja udostępnia listę wartości `type` pominiętych widgetów w kolejności wystąpienia (`null`, gdy widget nie ma `type`). Tolerancja dotyczy wyłącznie widgetów — błąd na poziomie korzenia (np. brak `encryption`) odrzuca cały interfejs. To gwarancja kompatybilności w przód: nowe typy widgetów w aplikacji myGrenton nie mogą wysypać biblioteki.
3. **Ścisłość poniżej widgetu.** Wewnątrz widgetu brak wymaganego pola = odrzucenie całego widgetu (nie „częściowe" urządzenie).
4. Pola nieznane (nadmiarowe) są ignorowane na każdym poziomie.

---

## 4. Moduł Provisioning

### 4.1 Endpoint

```
GET http://{host}:{port}/api/v1/interface/hash/{pin}
```

- `host`, `port` — adres komputera z uruchomionym Object Managerem (domyślny port **9998**).
- `pin` — PIN interfejsu myGrenton (ten sam, którym paruje się aplikację mobilną). OM wyświetla go jako „Token” w oknie „Wyślij na urządzenia mobilne”, pogrupowany spacją (`149 853`). Przed zbudowaniem URL-a implementacja **usuwa wszystkie białe znaki** z PIN-u — każdy znak z właściwością Unicode `White_Space` (spacja, tab, znaki nowej linii, NBSP, U+3000…), w dowolnym miejscu (`149 853` → `149853`). Znaki spoza `White_Space` (np. U+200B) zostają.
- PIN jest **jednym segmentem ścieżki**: znaki spoza `unreserved` / `sub-delims` / `:` / `@` (RFC 3986) koduje się procentowo jako UTF-8; w szczególności `/` → `%2F`, `?` → `%3F`, `#` → `%23`, `%` → `%25`.
- Nie da się zbudować URL-a (PIN pusty po usunięciu białych znaków, port spoza 1–65535) → `ProvisioningError.connection`.
- Host IPv6 podawany jest gołym literałem (`fe80::1`) lub w nawiasach (`[fe80::1]`); w URL-u zawsze w nawiasach.
- Zwykłe HTTP bez TLS (tak działa OM; kanał jest lokalny).

### 4.2 Odpowiedzi

| Status | Znaczenie | Błąd kanoniczny |
|---|---|---|
| `200` | ciało = plik ZIP | — |
| `401` | zły PIN | `ProvisioningError.authenticationFailed` |
| inne | — | `ProvisioningError.connection(status)` |
| błąd sieci | — | `ProvisioningError.connection(cause)` |

### 4.3 Przetwarzanie ZIP

1. Otwórz ciało odpowiedzi jako archiwum ZIP; błąd formatu → `ProvisioningError.invalidData("bad zip")`.
2. Wymagany wpis: **`mygrenton/data.json`**; brak → `invalidData`.
3. Odczytaj jako UTF-8, sparsuj JSON → `MobileInterface` (Model, §3); błąd JSON/schematu → `invalidData`.
4. **Limity zasobów:** rozmiar ciała odpowiedzi i rozmiar `data.json` po dekompresji ograniczone (zalecane: 10 MB każdy) — przekroczenie → `invalidData` (ochrona przed zip-bombą/odpowiedzią-śmieciem); ścieżka wpisu dopasowywana dokładnie, bez interpretacji `..`.

### 4.4 Tożsamość i rekonfiguracja

`interface.id` identyfikuje konfigurację. Konsument biblioteki powinien:
- przy pierwszym pobraniu zapisać `id` jako tożsamość instalacji,
- przy ponownym pobraniu (rekonfiguracja) odrzucić interfejs o innym `id` (ochrona przed podpięciem cudzej instalacji),
- porównywać `version` do wykrycia zmian.

Core dostarcza tylko dane; polityka zapisu należy do adaptera.

### 4.5 Import z pliku projektu (`.omp`)

Drugie źródło tego samego `MobileInterface`: plik projektu Object Managera. Projekt przechowuje interfejsy mobilne w formacie edytora OM; import przekłada je na schemat §3.4, tak jak robi to OM przy wysyłce na telefon, więc dalej (§3, §6) nic nie zależy od źródła. Nie wymaga PIN-u ani uruchomionego OM.

Wszystkie błędy importu to `ProvisioningError` (§8). Kolejność sprawdzania, gdy błędów jest kilka: archiwum (§4.5.1) → `properties.xml` i `system.xml` jako XML → lista interfejsów i wybór (§4.5.2) → klucz (§4.5.3) → CLU (§4.5.4).

#### 4.5.1 Archiwum

1. Plik `.omp` jest archiwum ZIP; błąd formatu, błąd odczytu lub dekompresji dowolnego używanego wpisu, dwa wpisy o tej samej nazwie → `invalidData`.
2. Używane wpisy (nazwa dopasowywana w całości): **`properties.xml`**, **`system.xml`** i każdy wpis o nazwie dokładnie `nodes/{n}/data.json`, gdzie `{n}` to niepusty ciąg cyfr ASCII. Brak `properties.xml` lub `system.xml` → `invalidData`. Pozostałe wpisy (skrypty, diagnostyka) są ignorowane — nie czyta się ich.
3. **Limity zasobów:** rozmiar pliku, rozmiar każdego używanego wpisu po dekompresji i łączny rozmiar używanych wpisów po dekompresji — każdy co najwyżej 64 MiB (zalecane); liczba wpisów archiwum co najwyżej 10 000. Przekroczenie → `invalidData`. Limit jest wyższy niż w §4.3, bo `system.xml` dużej instalacji ma kilka megabajtów.
4. **XML bez DTD.** Dokument z deklaracją typu dokumentu (`<!DOCTYPE …>`) w dowolnym kodowaniu → `invalidData`; o kodowaniu decyduje parser XML (deklaracja `encoding`, BOM), nie wyszukiwanie bajtów. Bez DTD nie ma encji, więc nie ma XXE ani „billion laughs". Błąd składni XML → `invalidData`.
5. **JSON ścisły.** Wpis `nodes/{n}/data.json` czyta się jako UTF-8; niepoprawny UTF-8, niepoprawny JSON, wartości spoza JSON (`NaN`, `Infinity`), liczba całkowita spoza zakresu 64-bitowego ze znakiem, liczba, która jako double nie jest skończona (`1e400`), albo powtórzony klucz w obiekcie → `invalidData`, także gdy wpis nie okaże się interfejsem.
6. **Referencje XML** (serializacja OM). Element z atrybutem `reference="{r}"` oznacza element z atrybutem `id="{r}"`. Każdy element czytany w §4.5.4–§4.5.5 — `TreeObject`, `specificObject`, `children` i pola tekstowe — który ma `reference`, zastępuje się celem; cel z własnym `reference` rozwiązuje się dalej. Brak celu, cykl referencji albo dwa elementy o tym samym `id` → `invalidData`.
7. **Tekst pola XML** to treść elementu z obciętymi białymi znakami na początku i końcu; element pusty albo zawierający same białe znaki traktuje się jak brak pola. **Liczba szesnastkowa** to 1–8 cyfr `0-9a-fA-F`, bez znaku, prefiksu i separatorów.

#### 4.5.2 Wybór interfejsu

- Każdy używany wpis `nodes/{n}/data.json`, którego wartością jest obiekt JSON z polem `pages` będącym tablicą, jest interfejsem. Import udostępnia listę `{id, name}` (z pól `id` i `label`) wszystkich interfejsów, w kolejności ich wpisów w archiwum. Interfejs, którego `id` albo `label` nie jest napisem, oraz dwa interfejsy o tym samym `id` → `invalidData`.
- Wybór po `id`: brak interfejsu o tym `id` → `ProvisioningError.interfaceNotFound`.
- Bez podanego `id`: archiwum z dokładnie jednym interfejsem → ten interfejs; z kilkoma → `ProvisioningError.interfaceAmbiguous` (projekt może mieć np. osobny interfejs dla integracji; zgadywanie byłoby niebezpieczne); bez żadnego → `interfaceNotFound`.

#### 4.5.3 Korzeń interfejsu

| Pole §3.4 | Źródło |
|---|---|
| `id` | `id` interfejsu |
| `name` | `label` |
| `icon` | `icon` |
| `theme` | `theme` zamienione na wielkie litery (edytor zapisuje `blue`, schemat ma `BLUE`) |
| `version` | **`0`** — projekt nie przechowuje licznika eksportów OM; wykrywanie zmian (§4.4) dla tego źródła porównuje treść |
| `encryption` | `{key, iv}` — tekst (§4.5.1 pkt 7, base64 bez dalszych zmian) pierwszego w kolejności dokumentu elementu `keyBytes` i pierwszego `ivBytes` w `properties.xml`; brak któregoś → `invalidData` |
| `clus` | §4.5.4 |
| `pages` | §4.5.6 |
| `pushNotifications` | §4.5.5 |

`icon` i `theme` muszą być napisami; w przeciwnym razie → `invalidData`.

#### 4.5.4 CLU

CLU projektu to elementy `system.xml` mające **bezpośrednie dzieci `nameOnCLU` i `ipAddress`** (sam element nie jest przy tym rozwiązywaną referencją), w kolejności dokumentu; ponowne wystąpienie tego samego tekstu `nameOnCLU` pomija się. Do `clus` trafiają **tylko CLU, do których odwołuje się wybrany interfejs** — `cluId` dowolnej referencji stanu lub akcji jego stron **po przekształceniu §4.5.6** (szkielety widgetów nie mają referencji) — w kolejności dokumentu, nie pierwszego użycia.

| Pole §3.4 | Źródło |
|---|---|
| `id` | tekst `nameOnCLU` |
| `name` | tekst dziecka `name` |
| `ip` | tekst `ipAddress` |
| `serialNumber` | tekst dziecka `serialNumber` jako liczba szesnastkowa (§4.5.1 pkt 7), zapisany dziesiętnie (`0bebc201` → `"200000001"`) |
| `port` | `1234` — projekt nie zapisuje portu, CLU zawsze słuchają na nim |
| `connectionType` | `LOCAL_ONLY`, gdy interfejs ma `blockCloud: true`, w przeciwnym razie `CLOUD` (pole jest parsowane, nieużywane — §1.2) |

Brak `name`, `ipAddress` lub poprawnego `serialNumber` u CLU, do którego odwołuje się interfejs → `invalidData`. `cluId`, do którego nie pasuje żaden CLU projektu, nie jest błędem importu (mapowanie urządzeń go nie potrzebuje).

#### 4.5.5 Powiadomienia push

Przejście w głąb po drzewie `TreeObject`: zaczyna się od bezpośrednich dzieci `TreeObject` korzenia `system.xml`, a dzieci węzła to elementy `TreeObject` będące bezpośrednimi dziećmi jego pierwszego elementu `children`. Każdy węzeł czytany jest po rozwiązaniu referencji (§4.5.1 pkt 6), a jego obiekt to pierwszy `specificObject` (też po rozwiązaniu). Kolejność: w głąb, węzeł przed dziećmi, dzieci w kolejności dokumentu. **Każdy węzeł odwiedza się co najwyżej raz** — przy pierwszym dojściu; kolejne dojścia (przez referencję albo cykl) pomija się, więc rozmiar wyniku nie przekroczy liczby węzłów, a implementacja nie powinna polegać na rekurencji o głębokości drzewa.

Węzeł, którego obiekt ma `typeName` = `Push`, daje wpis `{cluId, objectId, objectType}`:

- `cluId` — `serialNumber` (§4.5.1 pkt 7) obiektu najbliższego **przodka** (bez samego węzła), którego obiekt ma `serialNumber` i `ipAddress`;
- `objectId` — tekst `nameOnCLU`;
- `objectType` — `PUSH-{typeId}-{typeVersion}`, gdzie `typeId` to liczba szesnastkowa zapisana dziesiętnie (`31` → `49`), a `typeVersion` — tekst.

Wpis, któremu brakuje przodka CLU z poprawnym `serialNumber`, `nameOnCLU`, poprawnego `typeId` lub `typeVersion`, pomija się. Pole jest parsowane, nieużywane (§1.2); dlatego niepełne dane pomijają wpis, zamiast odrzucać import.

#### 4.5.6 Strony i widgety

`pages` interfejsu to tablica stron; strona, która nie jest obiektem z napisami `label` i `icon` oraz tablicą `widgets`, → `invalidData`. Strona: `{name: label, icon, isFullscreenWidget: false, widgets}`; kolejność stron i widgetów zachowana. `id` widgetu §3.4 to pole **`dtoId`** widgetu edytora. OM nadaje `dtoId` dopiero przy wysłaniu interfejsu na telefon, więc widget nigdy niewysłany go nie ma. Gdy `dtoId` nie jest niepustym napisem (brak, `null`, inny typ), `id` to **`omp-` i tekst dziesiętny `id` edytora**, o ile ten jest nieujemną liczbą całkowitą JSON (bez części ułamkowej i wykładnika; `true`/`false` nie są liczbą) — identyfikator zastępczy, np. `omp-5009`. Przedrostek sprawia, że nie zrówna się z żadnym `dtoId` nadanym przez OM. Dwa widgety interfejsu z tym samym `id` po przekształceniu → `invalidData`. Po pierwszym wysłaniu interfejsu z OM i zapisaniu projektu taki widget dostaje `dtoId`, więc jego `id` — a z nim identyfikatory urządzeń i encji (§6) — się zmieni; adapter powinien to uwzględnić, np. ostrzegając użytkownika.

**Pole wymagane** to pole obecne i różne od `null`. **Obiekt** to obiekt JSON; wartość innego rodzaju w miejscu obiektu traktuje się jak brak pola. **Pola przepisywane bez zmian** przechodzą tylko wtedy, gdy są obecne (także z wartością `null`) — import nie dopisuje pól, których nie było, i nie sprawdza ich typów; to robi parsowanie §3.6.

**Referencja stanu** (`ValueRef`) istnieje, gdy jest obiektem, którego `objectName` jest niepustym napisem; przechodzi jako `{cluId, objectName, index, callType}` z tymi z czterech pól, które ma. Referencja nieistniejąca (edytor trzyma puste szablony nieużywanych cech) jest **pomijana** — nie przechodzi ani pod swoją nazwą, ani jako `null`.

**Akcja** (`ActionRef`) istnieje na tej samej zasadzie i przechodzi jako:

| Pole §3.3 | Źródło |
|---|---|
| `cluId`, `objectName`, `value` | bez zmian (o ile obecne) |
| `index` | bez zmian; pominięte, gdy brak lub `null` (skrypty) |
| `event` | `type` (o ile obecne) |
| `callType` | `PSEUDO_METHOD` → `ATTRIBUTE`; inna wartość bez zmian (o ile obecne) |

`valueFormattedForDto` pomija się. Akcja nieistniejąca jest pomijana.

Widgety według `type` (pola **pogrubione** są wymagane do przekształcenia):

| `type` | Źródło w edytorze → pola §3.5 |
|---|---|
| `HEADER` | **`text`** (obiekt) → `components: [{unit: "UNKNOWN", label, type, rowId}]` (z `text`, bez zmian) |
| `ON_OFF` | **`buttonBistable`** → `components: [P]` |
| `ON_OFF_DOUBLE` | **`buttonBistableFirst`**, **`buttonBistableSecond`** → `components: [P₁, P₂]` |
| `SCENE` | **`buttonMonostable`** → `components: [M]`; `background` → B |
| `SCENE_DOUBLE` | **`buttonMonostableFirst`**, **`buttonMonostableSecond`** → `components: [M₁, M₂]`; `background` → B |
| `VALUE_V2` | `unit, valueType, precision, icon, label` bez zmian; referencja `value` → `object: {value}` |
| `VALUE_DOUBLE` | **`componentLeft`**, **`componentRight`** (obiekty) → każdy jak `VALUE_V2` (bez `id`, `type`) |
| `DIMMER_V2` | `min, max, precision, icon, label` bez zmian; **`object`** → O |
| `THERMOSTAT_V2` | `icon, label` bez zmian; `noOfFanSpeeds` → `no_of_fan_speeds`; **`object`** → O |
| `ROLLER_SHUTTER_V3`, `EVENT_SCHEDULER` | `icon, label` bez zmian; **`object`** → O |
| `CAMERA` | **`cameraStream`** → `components: [{unit: "UNKNOWN", value: url}]` plus bez zmian `image, label, type, rowId` (pole edytora `url` przechodzi jako `value`) |
| `LED` | **`buttonBistable`**, **`sliderHue`**, **`sliderSaturation`**, **`sliderBrightness`** → `components: [P, S_hue, S_saturation, S_brightness]` w tej kolejności |
| `CONTACT_SENSOR` | `icon, label, onIndication, offIndication, reverseState` bez zmian; **`object`** → O |
| `CONTACT_SENSOR_DOUBLE` | **`componentLeft`**, **`componentRight`** (obiekty) → każdy jak `CONTACT_SENSOR` (bez `id`, `type`) |
| `SLIDER` | `unit, min, max, precision, icon, label` bez zmian; **`object`** → O |
| `MULTISENSOR` | `icon, label, labelVisible` bez zmian; kanały → K |
| `ROLLER_SHUTTER` | **`buttonBistableClick`** → `components: [R]` |
| `TV_REMOTE_CONTROL` | `vodLabel, icon, label` bez zmian; **`object`** → O |

- **P** (przycisk bistabilny, obiekt): `{unit: "UNKNOWN", actions: [actionOn, actionOff]}` plus bez zmian `image, label, onIndication, offIndication, type, rowId` i referencja `state` — akcje w tej kolejności, nieistniejące pominięte; pole `indication` edytora pomija się.
- **M** (przycisk monostabilny, obiekt): `{unit: "UNKNOWN", actions: [actionClick]}` plus bez zmian `label, type, rowId`.
- **S** (suwak LED, obiekt): `{range: {min, max}, actions: [actionClick]}` (z `min`, `max` edytora, o ile są) plus bez zmian `unit, label, type, rowId` i referencja `state`.
- **R** (przycisk starej rolety, obiekt): `{unit: "UNKNOWN", actions: [actionClick]}` plus bez zmian `image, label, indication, rowId` i referencja `state`; `type` `BUTTON_BISTABLE_CLICK` przechodzi jako `BUTTON_BISTABLE`, inny bez zmian. Pole `status` widgetu edytora pomija się.
- **K** (kanały multisensora): pola edytora `objectAirCo2, objectSound, objectAirVco, objectLight, objectPressure, objectHumidity, objectTemperature`, każde będące obiektem, przechodzą jako obiekty zawierające te swoje pola, które są istniejącymi referencjami, pod nazwami mobilnymi `objectAirCo2, objectSound, objectAirVoc, objectLight, objectPressure, objectHumidity, objectTemperature` (edytor pisze kanał LZO jako `objectAirVco`).
- **B** (tło sceny, gdy `background` jest obiektem): `{color, name: image}`, gdzie `color` będący napisem zamienia się na wielkie litery; bez obiektu `background` pole `background` się pomija.
- **O** (obiekt widgetu): każde pole `object` edytora, które jest obiektem z polem `valueFormattedForDto`, jest akcją i przechodzi pod nazwą z przyrostkiem `Action`, o ile nazwa już się nim nie kończy (`setPosition` → `setPositionAction`, `setModeAction` bez zmian); każde inne pole będące obiektem — jako referencja stanu pod tą samą nazwą; nieistniejące pomija się. Gdy akcja bez przyrostka (`set`) celuje w nazwę pola, które w `object` już jest (`setAction` — akcja, referencja albo szablon), wygrywa to pole, a akcja bez przyrostka jest pomijana — niezależnie od kolejności w JSON.

Widget innego `type`, widget, który nie jest obiektem, oraz widget z tabeli bez pola wymaganego do przekształcenia (pogrubionego albo identyfikatora — `dtoId` lub liczbowego `id`) przechodzi dalej **tylko z polami `id` (jak wyżej) i `type`, o ile je ma** (`type` — gdy jest napisem). Bez pozostałych pól nie parsuje się, więc parsowanie §3.6 pkt 2 pomija go z diagnostyką, jak każdy niepoprawny widget. Pól edytora nieznanego typu nie przepisuje się, bo mogłyby przypadkiem sparsować się jako inny widget.

Wynik przechodzi następnie zwykłe parsowanie §3.6, jak interfejs pobrany z OM.

---

## 5. Moduł Runtime

### 5.1 Kryptografia

- **Algorytm:** AES w trybie **CBC**, padding **PKCS#7** (blok 16 B).
- **Klucz i IV:** z `interface.encryption` (base64). Klucz musi mieć 16/24/32 B (AES-128/192/256; w praktyce 128), IV dokładnie 16 B — inaczej `CryptoError.invalidKeyMaterial` przy tworzeniu sesji.
- **Każdy datagram UDP jest w całości jednym szyfrogramem**: `ciphertext = AES-CBC-PKCS7(key, iv, utf8(frame))`. Odbiór: deszyfruj, **zweryfikuj padding ściśle** (długość 1–16, wszystkie bajty paddingu równe długości; inaczej odrzuć datagram), dekoduj UTF-8, obetnij białe znaki.
- **IV jest statyczny** — ten sam dla każdej wiadomości. To własność protokołu Grentona, nie wybór biblioteki. Konsekwencje: (a) szyfrowanie jest deterministyczne, co umożliwia złote wektory testowe międzyportowe (§9.1); (b) poufność jest słaba (identyczne plaintexty ⇒ identyczne szyfrogramy); (c) **schemat nie zapewnia żadnej integralności ani uwierzytelnienia** — CBC bez MAC nie chroni przed modyfikacją, a walidacja paddingu odrzuca tylko część zniekształconych szyfrogramów. Biblioteka traktuje szyfr wyłącznie jako obfuskację; nie wolno tego „naprawiać" po stronie klienta — CLU oczekuje dokładnie tego schematu. Ścieżka odbioru nie rozróżnia na zewnątrz rodzaju błędu deszyfracji (padding vs rozmiar) — wszystkie kończą się tym samym cichym odrzuceniem.
- Datagram, którego nie da się odszyfrować/odpaddingować/zdekodować, jest **cicho odrzucany z logiem warning** (śmieci w sieci nie mogą psuć sesji).

### 5.2 Format ramek

Ramki są tekstowe (UTF-8), pola rozdzielone dwukropkami, dzielone na **maksymalnie 4 części** (trzy pierwsze dwukropki; payload może zawierać dalsze dwukropki).

**Żądanie** (klient → CLU):

```
req::{msg_id}:{payload}
```

Uwaga: między `req` a `msg_id` jest **puste pole** (stąd podwójny dwukropek).

**Odpowiedź / notyfikacja** (CLU → klient):

```
resp:{clu_ip}:{msg_id}:{payload}
```

Walidacja odbioru: musi zaczynać się od `resp:` i mieć dokładnie 4 części; inaczej ignoruj z logiem debug.

**`msg_id`:** 8 znaków hex (4 losowe bajty), **normalizowane do lowercase** przy nadawaniu i porównywaniu (CLU potrafi odpowiedzieć wielkimi literami).

**Notyfikacja** = ramka `resp` z `msg_id == "00000000"`. Jedyna znana notyfikacja to `clientReport` (§5.3.3).

### 5.3 Katalog wiadomości

#### 5.3.1 Ping (keep-alive)

- Żądanie: payload `checkAlive()`.
- `msg_id` pinga = **`keepAliveId`** — stały identyfikator sesji CLU, wylosowany raz (4 bajty hex) przy tworzeniu sesji. Kolejne pingi używają tego samego id (tak robi myGrenton; jednocześnie w locie jest najwyżej jeden ping, więc korelacja pozostaje jednoznaczna).
- Odpowiedź: dowolna ramka `resp` z pasującym `msg_id`; **payload odpowiedzi jest ignorowany** — sam fakt odpowiedzi oznacza sukces.
- Ping wykrywa, czy CLU odpowiada (§5.4.5). **Nie** przedłuża subskrypcji: CLU przestaje wysyłać `clientReport` około minutę po ostatniej rejestracji, niezależnie od pingów; subskrypcję odnawia wyłącznie re-rejestracja (§5.4.4).

#### 5.3.2 Rejestracja subskrypcji (`clientRegister`)

Żądanie (świeży losowy `msg_id`):

```
SYSTEM:clientRegister(0,0,{session},{K1,K2,...,Kn})
```

Argumenty to `(ip, port, session, keys)`:

- `ip`, `port` — adres, na który CLU ma wysyłać notyfikacje. **`0,0` oznacza adres i port źródłowy socketu, z którego przyszła rejestracja** — Core wysyła zawsze `0,0`. Inne formy (jawny adres i port) istnieją, ale Core ich nie używa: notyfikacje mają wracać na socket sesji. Forma `ip = 0` z niezerowym `port` nie jest przenośna: część CLU wysyła wtedy notyfikacje na port źródłowy, a część nie wysyła ich wcale — działa wszędzie tylko `0,0`. **Δ** Upstream `main` referencji (PR #12) wysyła w polu `port` losową wartość, przez co CLU nie wysyła notyfikacji; wydanie V0.2.3 wysyła `0`.
- `session` — **numer subskrypcji, dodatnia liczba całkowita**; w Core przydziela go sesja per chunk (§5.4.2). CLU rozróżnia subskrypcje trójką `(ip, port, session)`: ponowna rejestracja z tą samą trójką **zastępuje** listę kluczy; inna trójka zakłada osobną subskrypcję, także na tym samym sockecie. Numer wraca w odpowiedzi i w każdej notyfikacji (§5.3.3).
- Klucz `Ki` dla `ValueRef.variable(name)` → `"{name}"` (w cudzysłowach); dla `ValueRef.attribute(obj, idx)` → `{{obj},{idx}}` np. `{DOU8272,0}`.
- **Limit rozmiaru: ramka żądania (tekst UTF-8 przed szyfrowaniem, §5.2) ma najwyżej 1471 B**, czyli datagram najwyżej 1472 B (§5.1: PKCS#7 dopełnia do wielokrotności 16) — jeden pakiet IP przy MTU 1500. Na większe żądanie część CLU nie odpowiada wcale (nie składa fragmentów IP). Limit dotyczy bajtów, nie liczby kluczy; dzielenie na chunki — §5.4.2 (jedyny wyjątek: pojedynczy klucz większy niż limit).
- **Subskrypcja wygasa** około minutę po rejestracji, jeżeli nie zostanie odnowiona tą samą trójką (§5.4.4).

Odpowiedź — payload w formacie *client report*:

```
clientReport:{session}:{v1,v2,...,vn}
```

Wartości `vi` w kolejności kluczy żądania; są to **wartości początkowe** subskrybowanych stanów. Parsowanie payloadu:

1. Prefiks `clientReport:`; drugie pole = int — numer subskrypcji. Odpowiedź na rejestrację jest skorelowana przez `msg_id` (§5.4.1), więc numeru w odpowiedzi **nie sprawdza się**.
2. Trzecia część musi zaczynać się od `{` i kończyć `}` (nawiasy zewnętrzne; brak któregokolwiek → pkt 3). Zawartość między nimi dzielona jest na elementy **po przecinkach poza cudzysłowami i poza zagnieżdżonymi nawiasami**: `"` otwiera i zamyka tekst (bez escapowania), `{` i `}` poza tekstem zwiększają i zmniejszają głębokość, a elementy rozdziela tylko przecinek poza tekstem na głębokości 0. Zagnieżdżony blok `{…}` jest jednym elementem i po `castString` zostaje stringiem z nawiasami (np. `{0,{1,2},3}` → `0`, `{1,2}`, `3`). Każdy element: trim + zdjęcie cudzysłowów + `castString` (§3.1). Tekst niedomknięty na końcu zawartości, głębokość różna od 0 na końcu albo `}` przy głębokości 0 (głębokość nigdy nie spada poniżej zera) → payload odrzucony jak w pkt 3.
3. **Semantyka błędów parsowania:** payload bez prefiksu / z niepoprawnym numerem / z niedomkniętym tekstem lub niezrównoważonym nawiasem wewnątrz zawartości → payload odrzucony (dla odpowiedzi na rejestrację: **chunk uznany za nieudany**, §5.4.2; dla notyfikacji — zignorowany z logiem). Poprawny prefiks i numer, ale trzecia część bez `{` na początku lub bez `}` na końcu (także urwana, np. `{1,2`) albo z pustą zawartością → **pusta lista wartości**: dla odpowiedzi na rejestrację chunk nieudany (liczba wartości ≠ liczba kluczy), dla notyfikacji — raport zignorowany z logiem, bez aktualizacji stanu.
4. **Znane ograniczenie protokołu:** brak escapowania — wartość string zawierająca cudzysłów rozjedzie parsowanie pozycyjne (przecinek w tekście i zagnieżdżone nawiasy są obsługiwane przez pkt 2). Wpisane jako ograniczenie (§10).

**Wyrejestrowanie** (`clientDestroy`), świeży losowy `msg_id`:

```
SYSTEM:clientDestroy(0,0,{session})
```

Ta sama trójka co przy rejestracji; CLU przestaje wysyłać notyfikacje tej subskrypcji. Żądanie wysyła się **bez czekania na odpowiedź** — odpowiedź, jeśli przyjdzie, jest odrzucana jak każda nieoczekiwana ramka (§5.4.1 pkt 2). (CLU odpowiada numerem subskrypcji, a `nil`, gdy takiej subskrypcji nie ma — np. już wygasła.) Core wysyła je, gdy subskrypcja przestaje być potrzebna (§5.4.2 pkt 6, §5.4.5); bez niego CLU i tak przestałby wysyłać po wygaśnięciu.

**Odczyt jednorazowy** (`fetchValues`), świeży losowy `msg_id`:

```
SYSTEM:fetchValues({K1,K2,...,Kn})
```

Klucze jak w `clientRegister`, **co najmniej jeden** (na pustą listę CLU odpowiada zniekształconym payloadem, więc Core nigdy jej nie wysyła), w tym samym limicie ramki (§5.4.2; w podziale liczy się tekst ramki `fetchValues`). Odpowiedź:

```
values:{v1,v2,...,vn}
```

Prefiks `values:`, **bez numeru subskrypcji**; lista wartości według pkt 2 wyżej, w kolejności kluczy; klucz nieznany CLU daje `nil`, nie błąd. Payload bez prefiksu `values:` albo niespełniający pkt 2 → odrzucony. Odczyt **nie zakłada subskrypcji**: CLU niczego potem nie wysyła, nic nie wygasa i nie trzeba nic wyrejestrowywać. Core używa go tam, gdzie potrzebna jest jednorazowa, kompletna migawka bez subskrypcji (§5.4.2, `fetch`).

#### 5.3.3 Notyfikacja `clientReport`

Po rejestracji CLU wysyła — bez pytania, na adres i **port źródłowy socketu, z którego przyszła rejestracja** — ramki:

```
resp:{clu_ip}:00000000:clientReport:{session}:{v1,...,vn}
```

przy każdej zmianie któregokolwiek subskrybowanego stanu (raport zawiera zawsze **komplet wartości** subskrypcji, w kolejności rejestracji). Pole `session` to numer z rejestracji (§5.3.2) — po nim sesja dopasowuje raport do listy kluczy chunka (§5.4.3). Notyfikacja to pojedynczy datagram bez potwierdzenia i bez ponowienia; zgubiony raport naprawia dopiero re-rejestracja (§5.4.4).

#### 5.3.4 Akcje

Świeży losowy `msg_id`; payload zależny od wariantu `ActionRef`:

| Wariant | Payload |
|---|---|
| `VARIABLE` | `setVar("{index}","{value}")` |
| `ATTRIBUTE` | `{objectName}:set({index},"{value}")` |
| `METHOD` | `{objectName}:execute({index},"{value}")` |
| `SCRIPT` | `{objectName}({value})` — `value` **dosłownie** (składnia Lua: `"abc"`, `42`, pusty → `nazwa()`) |

Odpowiedź: ramka `resp` z pasującym `msg_id`; payload ignorowany (sukces = odpowiedź przyszła). Timeout = porażka akcji.

**Δ Cudzysłów wokół wartości.** Aplikacja myGrenton wstawia wartości atrybutów i metod dosłownie, bez cudzysłowu i ze spacją po przecinku (`set(0, 1)`, `execute(3, 22.0)`), a cytuje tylko wartości tekstowe (harmonogramy, `setVar`). CLU przyjmuje obie formy identycznie: `execute(3,"120.0")` i `execute(3, 120.0)` dają tę samą wartość, a CLU sam obcina część ułamkową tam, gdzie cecha jest całkowita. Core zostaje przy formie referencji z cudzysłowem. Niezależnie od formy CLU nie zgłasza złego rodzaju wywołania — `set` na indeksie metody odpowiada `nil` i nic nie zmienia; o `set`/`execute` decyduje `callType` z interfejsu (§3.3).

**Interpolacja bez escapowania:** pola `index`/`objectName`/`value` wchodzą do payloadu dosłownie — cudzysłów lub separator protokołu w wartości zmienia znaczenie payloadu (de facto injection do Lua CLU). Wartości generowane przez Core (liczby z intencji urządzeń) są bezpieczne z konstrukcji; wartości przekazywane od użytkownika (parametr sceny) są **świadomie dosłowne** — odpowiedzialność wywołującego, odnotowana w API.

#### 5.3.5 Przykładowa wymiana

```
→ req::a1b2c3d4:checkAlive()
← resp:192.168.1.10:a1b2c3d4:...

→ req::0f3e9a11:SYSTEM:clientRegister(0,0,1,{"AnalogIn1",{DOU8272,0}})
← resp:192.168.1.10:0f3e9a11:clientReport:1:{21.5,"ON"}
→ req::4d81c0e7:SYSTEM:clientRegister(0,0,2,{{DOU8273,0}})
← resp:192.168.1.10:4d81c0e7:clientReport:2:{"OFF"}
   … później, bez pytania, na ten sam socket:
← resp:192.168.1.10:00000000:clientReport:1:{22.0,"OFF"}
← resp:192.168.1.10:00000000:clientReport:2:{"ON"}

→ req::7c2d91aa:DOU8272:set(0,"1")
← resp:192.168.1.10:7c2d91aa:...

   … odczyt jednorazowy, bez subskrypcji:
→ req::b3e1a9c0:SYSTEM:fetchValues({{DOU8272,0},{NOPE_0000,0}})
← resp:192.168.1.10:b3e1a9c0:values:{1,nil}

   … przy zatrzymaniu sesji, bez czekania na odpowiedź:
→ req::9e02b7f3:SYSTEM:clientDestroy(0,0,1)
→ req::c57a1d20:SYSTEM:clientDestroy(0,0,2)
```

### 5.4 Sesja CLU — `CluSession`

Jedna instancja per CLU. Stan sesji:

- `clu` (id, ip, port), `cipher` (klucz+IV interfejsu — wspólne dla wszystkich CLU),
- `keepAliveId` (stały, §5.3.1),
- **socket główny** — jedyny socket sesji: pingi, akcje, rejestracje i odbiór notyfikacji,
- **lista chunków** — `{session, keys}` w kolejności kluczy, każdy w limicie ramki (§5.4.2), oraz **licznik numerów subskrypcji** (§5.4.2),
- mapa oczekujących żądań `msg_id → pending` (korelacja odpowiedzi),
- callback/strumień raportów do warstwy wyżej.

#### 5.4.1 Korelacja żądanie–odpowiedź

1. Wyślij: zapisz `pending[msg_id]`, zaszyfruj, `sendto(clu.ip, clu.port)`.
2. Odbiór datagramu: deszyfruj → waliduj ramkę → `msg_id`:
   - jest w `pending` → zdejmij, dostarcz odpowiedź czekającemu;
   - nie ma w `pending` i ramka jest `clientReport` (msg_id `00000000` + prefiks payloadu) → ścieżka raportu (§5.4.3);
   - inaczej → log debug, odrzuć.
3. **Timeout odpowiedzi: 5 s** — zdejmij z `pending`, zwróć błąd `TimeoutError`. Odpowiedź, która przyjdzie później, nie ma już `pending` i jest odrzucana jak każda nieoczekiwana ramka. Termin liczy się od przekazania datagramu transportowi i ogranicza **całą próbę**, także gdy transport nigdy nie zakończy wysyłki (socket, który stracił sieć): żądanie kończy się timeoutem niezależnie od stanu wysyłki, a zamknięcie lub wymiana socketu nie czeka na zawieszone wysyłki dłużej niż jeden timeout odpowiedzi. **Δ** Aplikacja myGrenton czeka 5 s na ping, ale 15 s na akcję i odczyt; CLU odpowiada na akcje w dziesiątkach milisekund, więc Core zostaje przy 5 s dla wszystkich żądań, a port może udostępnić osobny timeout akcji.
4. Zamknięcie/awaria socketu: wszystkie `pending` kończą się błędem `ConnectionClosed`. Awaria socketu głównego poza `stop()` przeprowadza sesję w `degraded` bez socketu głównego; następny ping otwiera nowy (§5.4.5).

#### 5.4.2 Chunking i rejestracja

`register(keys)`:

1. Podziel `keys` (w kolejności!) na chunki: każdy chunk to **najdłuższy ciąg kolejnych kluczy, dla którego ramka `req::{msg_id}:SYSTEM:clientRegister(0,0,999999,{K1,...,Km})` mieści się w limicie ramki** — domyślnie 1471 B UTF-8 (§5.3.2); port może pozwolić go zmniejszyć (np. przy VPN albo PPPoE z mniejszym MTU), nie zwiększyć. Numer liczy się jako 6 cyfr niezależnie od rzeczywistego, żeby podział nie zależał od numeracji. Klucz, który sam przekracza limit, tworzy własny chunk i jest wysyłany mimo to; jego wynik zależy od CLU jak każdego chunka (pkt 4) — CLU, który nie składa fragmentów IP, nie odpowie i slot będzie `failed`. Chunk `i` **zachowuje numer** dotychczasowego chunka `i`, jeżeli jego lista kluczy jest z nim identyczna (ta sama kolejność); w każdym innym przypadku dostaje **nowy numer** z licznika sesji (kolejne liczby od `1`, licznik zerowany w `start()`; numer ma najwyżej 6 cyfr, czyli ≤ 999999, zgodnie z rezerwą w podziale). Gdy potrzebny byłby numer 1000000, sesja wyrejestrowuje wszystkie chunki, zamyka socket główny, otwiera nowy i numeruje od `1` — nowy port daje nowe trójki (§5.3.2), więc żaden numer nie wraca z inną listą na tym samym porcie. Numer nigdy nie wraca do użycia z inną listą kluczy — inaczej spóźniona notyfikacja starej listy albo zgubione żądanie z nową trafiłyby wartości na złe klucze przy tej samej ich liczbie.
2. Dla każdego chunka **po kolei** (następny dopiero po odpowiedzi lub timeoucie poprzedniego): wyślij `clientRegister(0,0,{session},…)` **z socketu głównego**, czekaj na odpowiedź. Z chwilą wysłania chunk zapamiętuje swoje `keys` — niezależnie od wyniku, bo CLU mógł przyjąć rejestrację, choć odpowiedź nie dotarła.
3. Rejestracja z numerem, który już istnieje po stronie CLU, **zastępuje** tamtą listę (§5.3.2); odnowienie niezmienionej listy nie wymaga więc ani nowego socketu, ani wyrejestrowania.
4. Wynik: lista **slotów** pozycyjnie zgodna z `keys`, gdzie `Slot = failed | value(GrentonValue)`. Chunk, który zawiódł (timeout, błąd parsowania, zła liczba wartości), wnosi `failed` na swoich pozycjach — te pozycje **nie nadpisują** stanu (§5.5). **Δ** Referencja koduje awarię jako `null`, przez co prawdziwy `nil` z CLU jest nieodróżnialny od awarii; Core rozdziela te przypadki jawnym typem slotu.
5. Przychodzące notyfikacje są parowane pozycyjnie z `keys` chunka o tym samym numerze (§5.4.3).
6. Po zarejestrowaniu wszystkich chunków wyślij `clientDestroy(0,0,{session})` (§5.3.2, bez czekania na odpowiedź) dla każdego **numeru, którego nowa lista już nie używa** — zastąpionego nowym numerem albo nadmiarowego, gdy chunków jest mniej — rosnąco, i zapomnij te chunki. Przy `keys = []` wyrejestruj wszystkie.

**Δ Chunki sekwencyjnie, nie równolegle.** Referencja (upstream `main`, PR #12) wysyła wszystkie chunki naraz. CLU gubi żądania wysłane seriami: z wieloma chunkami naraz na część z nich nie odpowiada w czasie — encje tych chunków zostają bez stanu i bez push do następnej re-rejestracji (§5.4.4). Rejestracje **różnych** CLU mogą iść równolegle.

**Δ Jeden socket, chunki jako numery subskrypcji.** Referencja otwiera osobny socket na każdy chunk i przy każdej re-rejestracji nowe, a starych nie wyrejestrowuje — każda poprzednia subskrypcja wysyła wtedy notyfikacje na zamknięty port aż do wygaśnięcia. Core rejestruje wszystkie chunki z socketu głównego, każdy pod własnym numerem, i odnawia je tymi samymi numerami.

**`fetch(keys)` — odczyt jednorazowy.** Dzieli `keys` jak `register` (pkt 1, z tekstem ramki `fetchValues`), wysyła chunki **po kolei** z socketu głównego jako `SYSTEM:fetchValues({…})` (§5.3.2), każdy ze świeżym `msg_id`, i zwraca sloty pozycyjnie zgodne z `keys` (pkt 4: chunk bez odpowiedzi, z odrzuconym payloadem albo złą liczbą wartości → `failed`). Nie zmienia listy chunków, numerów ani pętli (§5.4.4) i niczego nie wyrejestrowuje; przy `keys = []` nic nie wysyła i zwraca pustą listę; bez otwartego socketu głównego (`idle`, `stopped`, `degraded` bez socketu) nic nie wysyła i zwraca same `failed`. Jest zserializowany z `start`, `register` i `stop` (§5.4.5). Warstwa wyżej może zasilić `StateStore` zwróconymi slotami (§5.5), tak jak slotami rejestracji.

#### 5.4.3 Ścieżka raportu

Datagram `clientReport` z `msg_id` `00000000` na sockecie głównym → odczytaj numer subskrypcji → chunk o tym numerze → sparsuj wartości → jeżeli ich liczba równa się liczbie `keys` chunka, wyemituj `(keys, values)` do warstwy wyżej (aktualizacja `StateStore` + zdarzenia). Numer bez chunka (np. po wyrejestrowaniu albo z poprzedniego uruchomienia sesji) albo liczba wartości różna od liczby kluczy → log debug, odrzuć.

#### 5.4.4 Pętle utrzymania

| Pętla | Interwał | Działanie |
|---|---|---|
| **Ping** | **5 s** od zakończenia poprzedniego pingu pętli (odpowiedź albo timeout) | `checkAlive()`; porażka → log warning (sesja żyje dalej — pojedynczy zgubiony ping to norma UDP) |
| **Re-rejestracja** | **30 s** od **rozpoczęcia** poprzedniej rejestracji (wysłania jej pierwszego chunka), nie wcześniej niż po jej zakończeniu, jeżeli lista kluczy jest niepusta | pełny `register(keys)` od nowa: te same numery chunków z tego samego socketu (§5.4.2). Pełni dwie funkcje: odnowienie subskrypcji po stronie CLU (wygasa po około minucie, §5.3.2) oraz okresową pełną resynchronizację wartości (samonaprawa po zgubionych raportach) |

Odliczanie od rozpoczęcia sprawia, że każdy chunk jest odnawiany około 30 s po swojej poprzedniej rejestracji niezależnie od tego, ile trwała cała sekwencja chunków. Interwał dobrano tak, by jedno zgubione odnowienie, jego timeout (§5.4.1 pkt 3) i naprawa przez pętlę pingów (§5.4.5) — razem 30 + 5 + 5 s — zmieściły się przed wygaśnięciem subskrypcji.

Pętle należą do sesji: pętla pingów rusza po zakończeniu `start()` (także gdy sesja jest `degraded` — wtedy pełni rolę retry, §5.4.5), a obie kończą się w `stop()`. **W stanie `degraded` pętla re-rejestracji nie rozpoczyna odnowienia** (termin, który minął w `degraded`, przepada — nie jest nadrabiany po powrocie): CLU nie odpowiada, więc każdy chunk kończyłby się timeoutem, a przy kilku chunkach kolejne odnowienia zachodziłyby na siebie; rejestrację po powrocie CLU wykonuje odzyskanie po udanym pingu (§5.4.5) i ono odlicza interwał od nowa. Odnowienie **już trwające**, gdy sesja staje się `degraded`, kończy swoje chunki jak każda rejestracja (§5.4.2: chunk bez odpowiedzi → `failed`), bo przerwanie w połowie zostawiłoby CLU z częściowo odnowioną listą; jego nieudane pozycje i tak naprawia odzyskanie. „Poprzednia rejestracja" to każda rejestracja sesji: jawne `register(keys)`, re-rejestracja po odzyskaniu połączenia (§5.4.5) i sama pętla; każda z nich odlicza 30 s od nowa. Wartości z re-rejestracji pętli trafiają do warstwy wyżej ścieżką raportu (§5.4.3), jak przy odzyskaniu połączenia. Interwały 5 s i 30 s są domyślne; port może pozwolić je skonfigurować (testy, tryby oszczędzania energii).

**Δ** Referencja odnawia co 45 s, czyli z jednym odnowieniem na czas życia subskrypcji; jej pętla ma też drobną usterkę sekwencji (sprawdza warunek przed `sleep`, przez co pierwsza iteracja może czekać 45 s mimo braku pracy). Core specyfikuje prosto: *30 s od rozpoczęcia poprzedniej rejestracji, jeżeli lista kluczy jest niepusta*.

**Δ** Core dodaje: zmiana zbioru subskrypcji w trakcie życia sesji (doszły/ubyły urządzenia) wyzwala natychmiastową re-rejestrację, bez czekania na tik pętli. W implementacji referencyjnej zbiór jest budowany raz przy starcie.

#### 5.4.5 Cykl życia

Sesja jest jawną maszyną stanów:

```
idle ──start()──► starting ──► ready ◄──► degraded ──stop()──► stopped
                     │            ▲            │
                     └── błąd ────┴── sukces ──┘
```

| Stan | Znaczenie |
|---|---|
| `idle` | utworzona, nic nie otwarte |
| `starting` | connect + ping początkowy + register początkowy w toku |
| `ready` | pętle działają, ostatni ping/rejestracja udane |
| `degraded` | pętle działają, ale CLU nie odpowiada (timeouty) — samoczynny powrót do `ready` po udanym pingu |
| `stopped` | zamknięta; wszystkie pending zakończone `ConnectionClosed` |

Reguły:

- `start()` i `stop()` są **idempotentne**; po `stop()` wolno wywołać `start()` ponownie na tej samej instancji (wymóg cyklu tło/pierwszy plan na mobile).
- `stop()` najpierw kończy oczekujące żądania błędem `ConnectionClosed`, potem wysyła `clientDestroy(0,0,i)` dla każdego zapamiętanego chunka (§5.3.2, bez czekania na odpowiedź), zamyka socket główny i zapomina chunki. Po ponownym `start()` lista chunków jest pusta — subskrypcje zakłada na nowo warstwa wyżej przez `register(keys)`.
- Operacje wieloetapowe (`start`, `register`, `stop`) są **wzajemnie zserializowane** — nie mogą się przeplatać (np. dwie rejestracje mieszające numery chunków). Wywołanie w trakcie trwającej operacji czeka lub jest łączone z bieżącą.
- Błąd `connect`/pierwszego pinga nie jest fatalny dla całego systemu — pozostałe CLU działają; sesja wchodzi w `degraded`, a pętla pingów pełni rolę retry (kolejny udany ping → re-rejestracja → `ready`). Wartości z tej re-rejestracji trafiają do warstwy wyżej ścieżką raportu (§5.4.3), jak z każdego chunka; chunk, który zawiódł, wnosi `failed` na swoich pozycjach. Jeżeli w `degraded` **socket główny nie istnieje** (jego utworzenie zawiodło albo socket uległ awarii, §5.4.1 pkt 4), każdy tik pingu najpierw ponawia otwarcie socketu głównego, a dopiero potem wysyła ping. Nowy socket główny to dla CLU nowy adres: subskrypcje założone ze starego socketu wygasają same, a re-rejestracja po udanym pingu zakłada je na nowo.
- **Rejestracja z nieudanym chunkiem zostawia sesję do odzyskania**, także gdy sesja pozostaje `ready` (CLU odpowiadał na pingi, a nie odpowiedział na jeden chunk): CLU ma wtedy niekompletną listę, więc najbliższy udany ping — jawny albo z pętli — rejestruje całą listę ponownie jak przy odzyskaniu, z wartościami ścieżką raportu, i dopiero rejestracja bez nieudanego chunka kończy ponawianie. Chunk, który zawiódł, nie zmienia stanu sesji; o `degraded` decydują tylko pingi.
- `execute` bez otwartego socketu głównego (`idle`, `stopped`, `degraded` bez socketu) kończy się błędem `SessionError.notConnected` i niczego nie wysyła. Akcja nie otwiera socketu sama; robi to `start()` albo ping.
- `ping()` i `register(keys)` w stanie `idle` albo `stopped` niczego nie wysyłają i nie zmieniają sesji: `ping()` zwraca porażkę, `register` zwraca same `failed` i **nie zapamiętuje kluczy** ani nie uruchamia pętli. Tylko `start()` otwiera sesję; wywołanie `register` w trakcie `start()` czeka na jego koniec (serializacja), a potem działa normalnie.
- **Trzy kolejne nieudane pingi** zamykają socket główny: oczekujące żądania kończą się `ConnectionClosed`, sesja jest `degraded` bez socketu, a najbliższy ping otwiera nowy (reguła wyżej) i udany ping wyzwala re-rejestrację. Licznik należy do socketu głównego: liczy **próby na drucie** (każdy wysłany `checkAlive()`, z pętli, jawny albo początkowy ze `start()`, liczony raz niezależnie od liczby wywołujących, którzy na niego czekają), które skończyły się timeoutem od otwarcia tego socketu albo od ostatniego udanego pingu; udany ping zeruje go, a nowy socket (po wymianie, po awarii, po `start()`) zaczyna od zera, więc dostaje własne trzy próby. Powód: transport, który stracił sieć (np. Wi-Fi), może trzymać socket w stanie, w którym wysyłki nigdy się nie kończą; nowy socket to nowy port i czyste subskrypcje.
- Rejestracja przerwana przez `stop()` kończy się jak chunk, który zawiódł: `failed` na pozycjach niedokończonych chunków (§5.4.2 pkt 4). Chunk, którego żądanie już wyszło, jest wyrejestrowany jak każdy inny.
- `fetch` przerwany przez `stop()` kończy się tak samo: `stop()` nie czeka na chunki odczytu — żądanie w locie kończy się `ConnectionClosed` i daje `failed`, kolejne chunki nie są wysyłane i też dają `failed`, a sloty chunków już odebranych zostają; `fetch` wraca z tą listą.

### 5.5 Magazyn stanu — `StateStore`

Struktura per CLU:

- `variables: map[name → GrentonValue]`
- `attributes: map[(objectName, index) → GrentonValue]`
- `subscriptionOrder: lista kluczy w kolejności rejestracji` — **kolejność jest częścią protokołu** (parowanie pozycyjne).

Operacje:

- `registerKey(ValueRef)` — idempotentna (klucz już obecny → no-op); dopisuje do `subscriptionOrder`. Wartość początkowa: „jeszcze nieznane" (**Δ** referencja używa pustego stringa).
- `updateKeys(keys, slots)` — pozycyjne nadpisanie; sloty `failed` są **pomijane**, sloty `value(v)` nadpisują — **w tym `value(null)`** (prawdziwy `nil` z CLU ustawia stan na `null`). **Δ** Referencja pomija każdy `None`, więc `nil` nigdy nie nadpisywał wartości; dzięki jawnemu typowi slotu (§5.4.2) Core może to poprawnie rozróżnić. Wartości z notyfikacji `clientReport` są zawsze slotami `value(…)`.
- `get(ValueRef) -> GrentonValue?` — odczyt bieżącej wartości (`nil`/„nieznane" rozróżnialne od `value(null)`).

**Δ Zdarzenia.** Implementacja referencyjna emituje jedno globalne „coś się zmieniło" (broadcast koordynatora HA). Core specyfikuje zdarzenia granularne: `StateChanged(cluId, key, oldValue, newValue)` emitowane tylko dla faktycznych zmian wartości. Wymagania wobec mechanizmu dostarczania: (a) **wielu niezależnych subskrybentów** — subskrypcja tworzy własny strumień/rejestrację, nie współdzieloną kolejkę jednego konsumenta; (b) zdefiniowana polityka buforowania wolnego subskrybenta (zalecane: bufor ograniczony, najnowsze wygrywają — konsument i tak czyta świeży stan ze store'a); (c) anulowanie subskrypcji sprząta zasoby. To umożliwia tanie bindowanie per urządzenie w adapterach.

### 5.6 Model współbieżności (wymagania semantyczne)

Neutralnie technologicznie (aktor / kolejka szeregowa / mutex — wybór portu):

1. Mutacje stanu jednej `CluSession` (mapa `pending`, lista chunków) są zserializowane.
2. `StateStore` jest bezpieczny przy współbieżnym odczycie z wielu wątków i zapisie z sesji.
3. Zdarzenia do konsumenta są dostarczane **w kolejności powstania** dla danego CLU.
4. Anulowanie (shutdown) jest bezpieczne w każdym momencie i nie gubi się (pętle, oczekujące żądania).

---

## 6. Moduł Devices — model kanoniczny

### 6.1 Zasady ogólne

- **Pipeline mapowania — dwa etapy:**
  1. **Deskryptory** (czysta funkcja na DTO, bez zależności od Runtime): `MobileInterface → pages[].widgets[] (spłaszczone) → DeviceDescriptor[]` — deskryptor niesie id, kind, nazwę, referencje (`ValueRef`), akcje (`ActionRef`), zakresy i reguły kompletności. Testowalny bez sieci.
  2. **Bindowanie** (fabryka w Runtime/systemie): `DeviceDescriptor + StateStore + CluSession → żywe encje` z odczytem stanu i intencjami.
  Rozdział jest obowiązkowy — konsument może użyć samych deskryptorów (np. do wyświetlenia listy urządzeń bez łączenia się z CLU).
- Strony służą tylko jako kontener — Core nie modeluje układu UI.
- Każde urządzenie ma: `id` (= `widget.id`), `widgetType` (= `widget.type`), `kind` (rodzaj kanoniczny), `name`, `page` (`{index, name}` strony, na której leży widget) oraz listę **encji kanonicznych** (jedno urządzenie może mieć ich kilka, np. podwójny przekaźnik → 2 przełączniki). Identyfikator encji: `{widget.id}_{sufiks}` — sufiksy podane niżej per typ (są **stabilnym kontraktem** — adaptery budują na nich unique_id). Urządzenia sterujące tym samym obiektem CLU grupuje §6.6.
- **`kind` urządzenia** mówi, czym jest urządzenie, niezależnie od encji, które wystawia (czujnik kontaktronowy z przyciskiem to nadal `contactSensor`). Stały per typ widgetu:

  | `widget.type` | `kind` |
  |---|---|
  | `ON_OFF`, `ON_OFF_DOUBLE` | `switch` |
  | `DIMMER_V2` | `dimmableLight` |
  | `LED` | `hsbLight` |
  | `CONTACT_SENSOR`, `CONTACT_SENSOR_DOUBLE` | `contactSensor` |
  | `VALUE_V2`, `VALUE_DOUBLE` | `sensor` |
  | `MULTISENSOR` | `multisensor` |
  | `SLIDER` | `numberSetting` |
  | `ROLLER_SHUTTER`, `ROLLER_SHUTTER_V3` | `cover` |
  | `SCENE`, `SCENE_DOUBLE` | `scene` |
  | `THERMOSTAT_V2` | `thermostat` |
  | `CAMERA` | `camera` |

- **`name` urządzenia:** `label` widgetu, jeśli typ go ma (`VALUE_V2`, `DIMMER_V2`, `CONTACT_SENSOR`, `SLIDER`, `MULTISENSOR`, `ROLLER_SHUTTER_V3`, `THERMOSTAT_V2`). W przeciwnym razie niepuste `label` komponentów w kolejności, złączone `" / "` (`VALUE_DOUBLE`, `CONTACT_SENSOR_DOUBLE`: lewy, prawy; widgety z `components[]`: kolejność listy). Wyjątek: `LED` → `label` pierwszego komponentu `BUTTON_BISTABLE`. Brak etykiet → `""`. Nazwy encji są niezależne (§6.3).
- **Kolejność jest częścią kontraktu:** urządzenia w kolejności `pages[]` → `widgets[]` (pominięte widgety nie tworzą urządzeń); encje urządzenia w kolejności podanej w §6.3 (komponenty w kolejności listy; `CONTACT_SENSOR_DOUBLE`: czujnik lewy, przycisk lewy, czujnik prawy, przycisk prawy; `MULTISENSOR`: kolejność tabeli §6.3.6; czujnik/suwak przed przyciskiem).
- **Reguły kompletności:** widget, który nie spełnia wymagań swojego mapowania (brak akcji, brak komponentu), produkuje urządzenie **bez encji** lub pomija daną encję — nigdy błąd całości.
- Encja czyta stan przez `StateStore.get(valueRef)` i wystawia **stan znormalizowany**; komendy tłumaczą wartości kanoniczne na format urządzenia i wykonują `ActionRef` przez sesję. Cała ta translacja (coercje, zakresy, odwrócenia skal, zaokrąglenia) mieszka w Core — adaptery jej **nie reimplementują**.
- Funkcja pomocnicza normalizacji:

```
mapRange((a,b) → (c,d), v) = c + (v−a)/(b−a) · (d−c);   gdy b−a ≤ 0 → c
```

- **Walidacja wejść intencji:** `mapRange` nie obcina — każda intencja przyjmująca wartość liczbową **najpierw przycina ją do zakresu kanonicznego** (brightness/saturation do [0,1], hue do [0,360], position/tilt do [0,100], setValue do [min,max]); zaokrąglanie połówek: half-to-even (zgodne z `round` referencji).

### 6.2 Koercje wspólne

- **`boolFromOnOff(v)`** (przełączniki): `true` ⇔ `v` to string `"ON"`, bool `true` **albo liczba równa 1** (`int` 1, `double` 1.0). Inne wartości → `false` (także `"1"`, `2`, `"on"`); `null` → stan nieznany. Referencja: `value == "ON" or value == True` — w Pythonie `1 == True` i `1.0 == True` są prawdziwe. Przekaźniki (`DOU…`, cecha 0) raportują stan jako liczbę 0/1, więc to ta gałąź decyduje o stanie większości przełączników.
- **`truthy(v)`** (czujniki binarne, LED on/off): standardowa prawdziwość (`0`/`0.0`/`""`/`false`/`null` → fałsz); `null` → stan nieznany (przed koercją).
- Odczyt liczbowy: `int`/`double` wprost; string liczbowo się nie re-parsuje (casting nastąpił przy odbiorze).

### 6.3 Katalog urządzeń

#### 6.3.1 `Switch` — z `ON_OFF` i `ON_OFF_DOUBLE`

- Po jednej encji na komponent; sufiks: `_{rowId}`.
- **Wymóg:** komponent musi mieć akcję z `event==ON` **i** z `event==OFF`; brak którejś → komponent pominięty. Przy kilku akcjach tego samego zdarzenia wygrywa **pierwsza** na liście; akcje `CLICK` są ignorowane.
- Stan: `isOn = boolFromOnOff(state)`. Intencje: `turnOn()` → akcja ON; `turnOff()` → akcja OFF.

#### 6.3.2 `DimmableLight` — z `DIMMER_V2`

- Jedna encja; sufiks `_0`.
- Parametry: `min`, `max`, `precision` z widgetu.
- Stan: `isOn = wartość > 0`; `brightness ∈ [0,1] = mapRange((min,max)→(0,1), wartość)`.
- Intencje: `turnOn()` → onAction; `turnOff()` → offAction; `setBrightness(b∈[0,1])` → `setValueAction` z `value = format(round(mapRange((0,1)→(min,max), b), precision))`.

#### 6.3.3 `HsbLight` — z `LED`

- **Wymóg kompletności:** wszystkie 4 komponenty (`BUTTON_BISTABLE`, `SLIDER_HUE`, `SLIDER_SATURATION`, `SLIDER_BRIGHTNESS`); brak któregokolwiek → urządzenie bez encji. Akcje ON i OFF na przycisku wymagane. Akcja sliderów: pierwsza z listy `actions`.
- Jedna encja; sufiks `_0`. Każdy slider ma własny `range{min,max}` i własny `ValueRef`.
- Stan: `isOn = truthy(state przycisku)`; `hue ∈ [0,360] = mapRange(hueRange→(0,360))`; `saturation ∈ [0,1] = mapRange(satRange→(0,1))`; `brightness ∈ [0,1] = mapRange(briRange→(0,1))`.
- Intencje: `setColor(h,s)` → dwie akcje (hue, saturation), wartości do zakresów urządzenia, zaokrąglone do 2 miejsc; `setBrightness(b)` analogicznie; `turnOn()/turnOff()`. Kolejność przy złożonej zmianie: kolor → jasność → ON (jak referencja).

#### 6.3.4 `ContactSensor` — z `CONTACT_SENSOR` i `CONTACT_SENSOR_DOUBLE`

- `CONTACT_SENSOR`: encja `_binary_sensor`; opcjonalnie encja `_button` (`Trigger`, §6.3.9), gdy jest `clickAction`.
- `CONTACT_SENSOR_DOUBLE`: encje `_binary_sensor_left` i `_binary_sensor_right`; opcjonalnie `_button_left` / `_button_right`, gdy dany komponent ma `clickAction` (sufiksy zgodne z referencją — kontrakt unique_id).
- Stan: `isActive = truthy(value) XOR reverseState`.
- Metadane: `onIndication`, `offIndication` (etykiety stanów dla UI adaptera).

#### 6.3.5 `Sensor` — z `VALUE_V2`, `VALUE_DOUBLE`

- `VALUE_V2`: encja `_0`. `VALUE_DOUBLE`: encje `_0` (componentLeft) i `_1` (componentRight) — sufiksy zgodne z referencją (kontrakt unique_id).
- Stan: `value: GrentonValue` (surowy, po castingu przewodowym). Metadane: `unit` (enum Grentona), `valueType`, `precision` — adapter decyduje o prezentacji; user-config (§6.4) może nadpisać klasę/jednostkę.

#### 6.3.6 `Sensor` ×7 — z `MULTISENSOR`

Siedem encji o stałych kanałach — kanał determinuje **kanoniczny rodzaj i jednostkę**:

| Sufiks encji | Kanał | Rodzaj kanoniczny | Jednostka |
|---|---|---|---|
| `_air_co2` | objectAirCo2 | `co2` | ppm |
| `_sound` | objectSound | `soundPressure` | dB |
| `_air_voc` | objectAirVoc | `voc` | ppb |
| `_light` | objectLight | `illuminance` | lx |
| `_pressure` | objectPressure | `pressure` | hPa |
| `_humidity` | objectHumidity | `humidity` | % |
| `_temperature` | objectTemperature | `temperature` | °C |

(Pola `minValue/maxValue/threshold/sensitivity` — na razie nieużywane; zostawić w DTO.)

#### 6.3.7 `NumberSetting` — z `SLIDER`

- Encja `_slider`: `value` (surowa liczba), `min`, `max`, `step = 10^(−precision)`; intencja `setValue(v)` → `setValueAction` z `value = format(round(v, precision))`.
- Opcjonalna encja `_button` (`Trigger`), gdy `clickAction` obecna.

#### 6.3.8 `Cover` — z `ROLLER_SHUTTER_V3`

- Jedna encja; **id = widget.id bez sufiksu** (tak w referencji — zachować dla zgodności unique_id).
- Subskrybowane stany: `state`, `position`, warunkowo `lamelPosition`.
- **Lamele (tilt) są obecne ⇔ pole `lamelMoveTimeout` występuje w obiekcie** (heurystyka referencyjna „roleta ma lamele"). `stop` obecny ⇔ `stopAction` występuje.
- Stan znormalizowany:
  - `position ∈ [0,100]` — surowa wartość `position` (0 = zamknięta, 100 = otwarta);
  - `motion`: z `state` — `0 → idle`, `1 → opening`, `2 → closing`;
  - `isClosed = (position == 0)`;
  - `tilt ∈ [0,100] = 100 − mapRange((0,90)→(0,100), lamelPosition)` — urządzenie raportuje 0–90 w skali odwrotnej do kanonicznej.
- Intencje: `open()` → setPosition("100"); `close()` → setPosition("0"); `setPosition(p)` → `setPositionAction` z int 0–100; `stop()` → `stopAction` (jeśli jest); `setTilt(t∈[0,100])` → `setLamelPositionAction` z `int(90 − mapRange((0,100)→(0,90), t))`; `openTilt()` = setTilt(100) ⇒ wartość urządzenia 0; `closeTilt()` = setTilt(0) ⇒ 90.
- Pozostałe pola obiektu V3 (moveUp/Down, hold, kalibracja…) — poza zakresem v1; DTO je zachowuje.

#### 6.3.9 `StateSensor` + `Trigger` — z `ROLLER_SHUTTER` (legacy)

- **Wymóg kompletności:** **pierwszy** komponent ma ≥1 akcję; inaczej urządzenie bez encji (**Δ** referencja indeksuje `components[0]` bez sprawdzenia — pusty komponent wysypuje mapowanie).
- Encja `_sensor`: stan wyliczeniowy z pierwszego komponentu: `0→stopped, 1→opening, 2→closing, 3→locked, inne→unknown`.
- Encja `_button` (`Trigger`): wykonuje **pierwszą** akcję pierwszego komponentu.
- `Trigger` to ogólna encja kanoniczna „przycisk": jedna intencja `press()` wykonująca przypisany `ActionRef`.

#### 6.3.10 `SceneTrigger` — z `SCENE` i `SCENE_DOUBLE`

- Po jednej encji na komponent. Sufiks: `SCENE` → `_{rowId}` (kontrakt unique_id referencji); `SCENE_DOUBLE` → `_{pozycja}` (0, 1… — indeks komponentu na liście, także gdy wcześniejszy komponent został pominięty), bo `rowId` się powtarza.
- **Wymóg:** akcja z `event==CLICK` i `callType==SCRIPT`; brak → komponent pominięty.
- Intencje: `run()` — wykonuje skrypt z `value` z widgetu; `run(parameter: string)` — wykonuje z `value` **zastąpionym dosłownie** parametrem (składnia Lua po stronie wywołującego: `"abc"`, `42`, `""` → `scene()`); oryginalny `ActionRef` pozostaje niezmutowany (kopiuj przy nadpisaniu).

#### 6.3.11 `Camera` — z `CAMERA`

- **Wymóg kompletności:** ≥1 komponent `CAMERA_STREAM`; inaczej urządzenie bez encji (**Δ** referencja indeksuje `components[0]` bez sprawdzenia).
- Encja `_0`; stan statyczny: `streamUrl = components[0].value` (np. RTSP). Core nie dotyka strumienia — odtwarzanie to sprawa adaptera.

#### 6.3.12 `Thermostat` — z `THERMOSTAT_V2`

Widget obiektu wirtualnego Grenton Thermostat. Semantyka cech według instrukcji Grentona („Obiekt wirtualny Termostat", Object Manager); **Δ** implementacja referencyjna tego widgetu nie obsługuje.

- Jedna encja; sufiks `_0`. Kompletność: wymagane pola z §3.5 (brak → widget pominięty); kontrolki opcjonalne istnieją, gdy jest **i** cecha, **i** jej akcja.
- Stan:
  - `currentTemperature`, `targetTemperature` — liczby (`targetTemperature` to wynikowa `TargetTemp`, zależna od trybu);
  - `minTemperature`, `maxTemperature` — granice (opcjonalne);
  - `mode` z cechy Mode: `0 → manual`, `1 → holiday`, `2 → schedule`, inne → nieznany;
  - `isOn = truthy(state)` — cecha State: 0 = termostat zatrzymany (nie steruje wyjściem), 1 = pracuje;
  - `output` — `controlOutValue` surowo (0/1 dla wyjścia cyfrowego, poziom dla analogowego).
- Intencje (wszystkie akcje mają `$value$`, §3.3):
  - `setTargetTemperature(t)` → `setTargetTemperatureAction` (PointValue, nastawa ręczna); `t` przycięte do `[min, max]`, gdy obie granice są znane; `value = format(round(t, 1))` (§10 pkt 3);
  - `setMode(m)` → `setModeAction` z `"0"`/`"1"`/`"2"`;
  - `turnOn()` / `turnOff()` → `setStateAction` z `"1"` / `"0"`;
  - brak kontrolki → intencja jest no-opem (jak `stop()` rolety bez `stopAction`).
- Poza zakresem v1: harmonogram (`scheduleData`), zmiana granic, wentylatory (`no_of_fan_speeds`).
- Wartości liczbowe: temperatury przychodzą jako `int` albo `double` (`"20"` → `int`, `"19.8"` → `double`), także ułamkowe temperatury zadane (`21.5`) — odczyt traktuje oba typy jako liczbę (§6.2). Granice `min/max` pochodzą z CLU; nie zakładać zakresu z instrukcji.
- W trybie ręcznym `targetTemperature` przyjmuje wartość zapisaną przez `setTargetTemperature`.

### 6.4 Metadane użytkownika (user config)

Adaptery pozwalają użytkownikowi doprecyzować prezentację (device class, jednostka, tryb suwaka). Skoro Core tych danych **nie interpretuje ani nie persystuje, nie przechowuje ich wcale**: konfiguracja użytkownika żyje w całości po stronie adaptera jako jego własna mapa `entityId → config`, kluczowana stabilnym identyfikatorem encji (§6.1). Gwarancją Core jest wyłącznie **stabilność identyfikatorów encji** między uruchomieniami i portami. Dzięki temu klucze konfiguracyjne HA (np. `device_class`) nie przeciekają do Core, a inny adapter (HomeKit) trzyma własne — i encje Core pozostają niemutowalne.

### 6.5 Zdarzenia urządzeń

Devices subskrybuje granularne `StateChanged` z Runtime (§5.5 Δ) i tłumaczy je na `DeviceStateChanged(deviceId, entityId)` — emitowane tylko, gdy zmiana dotyczy `ValueRef` danej encji. Adapter binduje encję do jej strumienia i odczytuje świeży stan znormalizowany.

### 6.6 Obiekty fizyczne

Urządzenie odpowiada **widgetowi**, a nie sprzętowi: ten sam obiekt CLU bywa pokazany przez kilka widgetów, na różnych stronach i pod różnymi nazwami (np. termostat na stronie pomieszczenia jako „Temperatura" i na zbiorczej stronie ogrzewania pod nazwą pomieszczenia). Identyfikatory urządzeń i encji zostają per widget (stabilny kontrakt §6.1), a Core **dodatkowo** udostępnia grupowanie po obiekcie, żeby adapter mógł pokazać jeden termostat zamiast dwóch.

- **Obiekt fizyczny** = to, czym encja steruje na CLU, identyfikowane kluczem `{cluId, kind, name, index}`:
  - `object` — cecha/metoda obiektu (`THE1001` / `14`, `ROL1001` / `…`), `name` = `objectName`, `index` = indeks cechy (lub metody dla encji bez stanu). **Indeks jest częścią klucza:** jeden moduł LED steruje osobnymi taśmami na osobnych kanałach (`LED1001` / `3` i `/ 4` to dwa różne urządzenia), a oba widgety termostatu czytają tę samą cechę (`THE1001` / `14`);
  - `variable` — zmienna globalna CLU, `name` = nazwa zmiennej, `index` = `null`;
  - `script` — skrypt Lua (sceny), `name` = `objectName` akcji `SCRIPT`, `index` = `null`.
- **Każda encja należy do jednego obiektu** — według głównej referencji: `switch`, `dimmableLight`, `contactSensor`, `sensor`, `numberSetting`, `stateSensor` → `state`; `hsbLight` → `powerState`; `cover` → `position`; `thermostat` → `currentTemperature`; encje bez stanu (`trigger`, `sceneTrigger`) → ich akcja (`VARIABLE` → zmienna, `SCRIPT` → skrypt, pozostałe → obiekt). `camera` nie dotyka CLU i nie jest grupowana.
- Widget może więc należeć do kilku obiektów (`ON_OFF_DOUBLE` → dwa przekaźniki; czujnik kontaktronowy z przyciskiem na zmiennej → obiekt czujnika i zmienna) — z właściwymi encjami przy każdym.
- **Wynik:** lista obiektów w kolejności pierwszego użycia (urządzenia w kolejności §6.1, encje w ich kolejności); każdy obiekt: `key` i `widgets[]` w kolejności urządzeń, każdy wpis: `deviceId`, `widgetType`, `name` (nazwa urządzenia), `page`, `entityIds[]` (encje tego widgetu należące do obiektu).
- Wybór nazwy i obszaru (np. strona pomieszczenia vs strona funkcyjna) należy do adaptera — Core podaje wszystkie.

---

## 7. Kontrakt portu

Interfejsy, które **musi dostarczyć platforma** (wstrzykiwane; nazwy kanoniczne):

### 7.1 `UdpSocketFactory` / `UdpSocket`

```
UdpSocketFactory.open(remoteHost, remotePort) -> UdpSocket   // bind na porcie efemerycznym
UdpSocket.send(datagram: bytes)                              // do ustalonego peera
UdpSocket.incoming: strumień/callback (bytes)                // datagramy przychodzące
UdpSocket.close()
```

Wymagania: socket ma **własny lokalny port efemeryczny** (to na niego CLU wysyła notyfikacje, §5.3.2); odbiór unicastów na ten port; brak wymogu multicast/broadcast. Filtrowanie źródła do adresu CLU jest dopuszczalne (**Δ** — referencja go nie robi), ale implementacja portu **musi mieć przetestowany tryb bez filtrowania** (odbiór od dowolnego nadawcy) jako pełnoprawny fallback: CLU z wieloma interfejsami sieciowymi może wysyłać raporty z innego adresu źródłowego niż adres, na który słano rejestrację.

### 7.2 `AesCbcCipher`

```
encrypt(plaintext: bytes) -> bytes   // PKCS7 + AES-CBC(key, staticIV)
decrypt(ciphertext: bytes) -> bytes? // null przy błędzie paddingu/rozmiaru
```

Konstruowany z materiału z §5.1 z walidacją długości.

### 7.3 `HttpClient` + `ZipReader` (tylko Provisioning)

```
HttpClient.get(url) -> (status, body: bytes)
ZipReader.entry(archive: bytes, path: string) -> bytes?
```

### 7.4 `Clock` / `TimerScheduler`

```
sleep(duration), now()   // z obsługą anulowania
```

Wstrzykiwany, żeby testy sterowały czasem (pętle 5 s / 30 s, timeout 5 s).

### 7.5 `RandomSource`

```
bytes(n) -> bytes        // msg_id, keepAliveId
int(in: 1...65535) -> int // nieużywane przez sesję — numery subskrypcji są kolejne (§5.4.2)
```

Wstrzykiwany dla determinizmu testów.

### 7.6 `Logger`

Poziomy `debug/info/warning/error`; Core loguje przez fasadę, nigdy bezpośrednio.

### 7.7 Wykonanie asynchroniczne

Port zapewnia prymitywy: uruchomienie zadania, anulowanie, serializacja mutacji (aktor/kolejka). Semantyka — §5.6.

---

## 8. Taksonomia błędów

| Kategoria | Warianty | Kiedy |
|---|---|---|
| `ProvisioningError` | `authenticationFailed`, `connection`, `invalidData`, `interfaceNotFound`, `interfaceAmbiguous` | §4; dwa ostatnie — wybór interfejsu z pliku projektu (§4.5.2) |
| `CryptoError` | `invalidKeyMaterial`, `encryptFailed`, `decryptFailed` | §5.1; `decryptFailed` na ścieżce odbioru → drop datagramu, nie propagacja |
| `ProtocolError` | `malformedFrame`, `malformedPayload` | walidacja ramek/payloadów |
| `PayloadError` | `unresolvedPlaceholder` | akcja z `value` = `$value$` bez nadpisania (§3.3) — nic nie jest wysyłane |
| `TimeoutError` | — | brak odpowiedzi w 5 s |
| `SessionError` | `notConnected`, `connectionClosed` | cykl życia §5.4.5 |

Zasada: błędy ścieżki odbioru (śmieciowy datagram) są logowane i połykane; błędy ścieżki żądania (akcja, rejestracja) są zwracane wywołującemu; błędy pętli utrzymania są logowane i pętla działa dalej.

---

## 9. Strategia testów

**Zestaw zgodności** (`conformance/`, kontrakt: [`conformance.md`](conformance.md)) jest normatywną, maszynowo wykonywalną formą tej sekcji: przypadki JSON niezależne od języka, które każdy port uruchamia własnym runnerem. Profile i ich zakres opisuje [`conformance.md`](conformance.md) §1; aktualną wersję zestawu podaje `conformance/manifest.json`. Tabele poniżej są ilustracją; przy rozbieżności rozstrzyga zestaw (i poprawia się jedno albo drugie).

### 9.1 Złote wektory kryptograficzne

Statyczny IV ⇒ szyfrowanie deterministyczne ⇒ identyczne wektory obowiązują każdy port. Materiał testowy:

```
key (b64): AAECAwQFBgcICQoLDA0ODw==        (hex 000102030405060708090a0b0c0d0e0f)
iv  (b64): EBESExQVFhcYGRobHB0eHw==        (hex 101112131415161718191a1b1c1d1e1f)
```

| Plaintext (UTF-8) | Ciphertext (hex) |
|---|---|
| `req::aabbccdd:checkAlive()` | `5eab3361763eb47aecf01654656faa9b4e31ef685544739464c5291048d6ab60` |
| `req::0f3e9a11:SYSTEM:clientRegister(0,12345,1,{"AnalogIn1",{DOU8272,0}})` | `32a53db2c8eb9ad8e14bd127c466c22f3ed8932f5614c2a355d3e57fee6e5aec934236153383944a680ec2c811803421df6a81a75c9be4370bfe85a669b47ffdb62893c77bc792803ca88917ac3fa61a` |
| `resp:192.168.1.10:00000000:clientReport:0:{21.5,"ON"}` | `9d53460871ebf060f96fe81c68fcd85ff81ed07fad264a4106ddeeb511329c8cafed3face4e8a334d20e5b307ddf62cb2ab676e0e1a0527242489ba39349b644` |

Test dwukierunkowy: encrypt(plaintext) == ciphertext oraz decrypt(ciphertext) == plaintext.

Wektor z `clientRegister(0,12345,…)` to wyłącznie fixture szyfru — forma jego argumentów nie jest normatywna (sesja wysyła `clientRegister(0,0,{session},…)`, §5.3.2).

### 9.2 Tabela castowania wartości

| Wejście | Wynik |
|---|---|
| `nil` / `NIL` | null |
| `true` / `false` | bool |
| `"ON"` | string `ON` |
| `42` / `-7` | int |
| `21.5` / `-3e2` | double |
| `"21.5"` | double 21.5 (cudzysłowy zdejmowane przed castem) |
| ` tekst ` | string `tekst` |

### 9.3 Fixtury parsowania

- Minimalny `data.json` z ≥1 widgetem każdego typu + 1 widget nieznanego typu (musi zostać pominięty) + 1 widget z brakującym polem (pominięty).
- Wspólny plik fixture w repo Core; każdy port dekoduje go i asertuje identyczną listę urządzeń/encji (id, sufiksy, kompletność).

### 9.4 Symulator CLU (fake CLU)

Testowy serwer UDP na loopbacku, z tym samym szyfrem:

- odpowiada na `checkAlive()` i akcje echem ramki `resp`,
- na `clientRegister` odpowiada `clientReport` z zadanymi wartościami początkowymi,
- potrafi na żądanie testu wyemitować notyfikację `clientReport` (msg_id `00000000`) na port źródłowy rejestracji,
- scenariusze negatywne: brak odpowiedzi (timeout), zła ramka, zły padding.

Na nim jeżdżą testy integracyjne sesji: rejestracja chunkowana (lista kluczy ponad limit ramki ⇒ ≥2 numery subskrypcji), routing raportów po numerze, re-rejestracja, wyrejestrowanie, aktualizacje magazynu stanu, akcje.

### 9.5 Checklist zgodności portu

1. Złote wektory AES (9.1) ✔ — zestaw: `crypto.*`
2. Tabela castowania (9.2) ✔ — zestaw: `value.cast.*`
3. Fixtura interfejsu → identyczny zestaw urządzeń/encji (9.3) ✔ — zestaw: `mapping.*`
4. Budowa payloadów: register (klucze var/attr), 4 warianty akcji — porównanie stringów 1:1 ✔ — zestaw: `wire.*`, `payload.*`
5. Scenariusze fake CLU (9.4) ✔
6. Normalizacje Devices: dimmer round-trip, LED HSB round-trip, cover tilt odwrócony, reverseState XOR ✔

---

## 10. Znane ograniczenia i decyzje

1. **Statyczny IV / brak MAC** — narzucone przez protokół CLU; nie do naprawienia po stronie klienta (§5.1).
2. **Parowanie pozycyjne bez escapowania** — string zawierający cudzysłów rozjedzie raport (§5.3.2). Przecinek w tekście i zagnieżdżone nawiasy parsują się poprawnie od 0.9.0; cudzysłowu protokół nie escapuje, więc tu obejścia nie ma. Ryzyko realne tylko dla zmiennych tekstowych.
3. **Format liczb w payloadach akcji:** referencja wysyła Pythonowe `str(round(v, p))` (np. `5.0` przy precision 0). **Domyślnym zachowaniem Core jest format zgodny z referencją** (CLU go przyjmuje, np. `"22.0"`). Format bez `.0` jest dostępny tylko za flagą — jego akceptacja przez CLU nie jest potwierdzona. Cudzysłów wokół liczby jest dla CLU obojętny (§5.3.4 Δ).
4. **Limit ramki rejestracji 1471 B** (§5.3.2) — bajtowy, nie liczba kluczy: liczba kluczy nie chroni przed długimi nazwami zmiennych. Wynika z MTU 1500, więc port może pozwolić go zmniejszyć, gdy po drodze jest mniejsze MTU; nie może go zwiększyć, bo część CLU nie składa fragmentów IP.
5. **Brak weryfikacji nadawcy datagramu** w referencji; Core dopuszcza filtrowanie po adresie CLU (Δ §7.1).
6. **Jeden socket na sesję, chunki jako numery subskrypcji** (§5.4.2). Wcześniejsze wydania tej specyfikacji zakładały, że numer w notyfikacji jest niewiarygodny, i wymagały socketu na chunk; to był błędny odczyt argumentów `clientRegister` (§5.3.2). Notyfikacje przychodzą na port źródłowy rejestracji, więc socket główny jest też odbiornikiem raportów.
7. **Wygasanie subskrypcji** po około minucie jest cechą CLU, nie klienta; nie da się go wyłączyć, można tylko odnawiać (§5.4.4).
8. **`interface.pushNotifications` i tryb chmurowy — poza zakresem.** `pushNotifications` wymienia obiekty, o których CLU powiadamia przez chmurę Grentona: telefon dostaje powiadomienie FCM z `cluId` i polem `encryptedData` (hex), zaszyfrowanym tym samym AES-CBC i kluczem interfejsu co ramki CLU (§5.1), z `{title, message}` w środku. Odbiór wymaga rejestracji tokenu w usłudze Grentona, więc Core tego nie implementuje; `GrentonCipher` odszyfrowałby taką treść, gdyby aplikacja dostała ją inną drogą. Tryb chmurowy aplikacji to MQTT (AWS IoT) z tymi samymi bajtami ramek co po UDP, uwierzytelniany certyfikatem należącym do producenta — poza zakresem z tego samego powodu.
9. Usterki referencji, których **nie przenosimy**: martwy kod `self.min = min` w encji LED; pusty string jako wartość początkowa; liberalne parsowanie liczb Pythona; sekwencja pętli re-rejestracji (§5.4.4).
10. **Termostat — tryby 1/2:** semantyka trybów urlopowego i harmonogramu pochodzi z dokumentacji Grentona; zapis Mode bezpośrednio przez cechę (zamiast metod `HolidayModeStart`/`AutoModeStart`) nie jest potwierdzony na CLU.

---

## 11. Słownik

| Termin | Znaczenie |
|---|---|
| **CLU** | Kontroler logiki Grenton — urządzenie na LAN, z którym rozmawia Runtime po UDP |
| **Object Manager (OM)** | Program konfiguracyjny Grentona na PC; źródło interfejsu mobilnego |
| **Interfejs mobilny** | Definicja UI/urządzeń projektowana w OM dla aplikacji myGrenton; JSON konsumowany przez Core |
| **Widget** | Element interfejsu mobilnego (typ + komponenty); jednostka mapowania na urządzenie |
| **Komponent** | Pod-element widgetu (przycisk, slider, strumień kamery) |
| **ValueRef / ActionRef** | Referencja odczytu stanu / definicja akcji na CLU (§3.2, §3.3) |
| **Chunk** | Porcja kluczy subskrypcji, której ramka rejestracji mieści się w limicie (§5.4.2), zarejestrowana pod własnym numerem subskrypcji (`session`) |
| **client report** | Payload z wartościami subskrypcji — jako odpowiedź na rejestrację i jako notyfikacja |
| **Encja kanoniczna** | Znormalizowany element urządzenia (przełącznik, czujnik…) wystawiany adapterom |
