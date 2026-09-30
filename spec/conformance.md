# Grenton Core — kontrakt zestawu zgodności

| | |
|---|---|
| **Wersja zestawu** | 0.4.4 (specyfikacja 0.4.5, schemat przypadków 1) |
| **Dotyczy** | `spec/grenton-core-design.md` §9 |

Zestaw zgodności to **dane, nie kod**: każdy przypadek to plik JSON w `conformance/cases/`. Port dostarcza cienki runner, który wczytuje przypadki, wywołuje produkcyjne API portu i porównuje obserwację z oczekiwaniem. Dopisanie przypadku raz obejmuje wszystkie porty.

## 1. Profile

Zgodność deklaruje się **per profil** — przejście części zestawu nie jest „pełną zgodnością".

| Profil | Zakres | Status |
|---|---|---|
| `stateless` | funkcje bez stanu: AES, ramki, payloady, casting, URL/PIN, koercje, format liczb | 0.1.0 |
| `mapping` | `data.json` → urządzenia/encje, pominięte widgety | 0.2.0 (0.3.0: termostat, `SCENE_DOUBLE`, `HEADER`; 0.4.0: `page`, obiekty fizyczne) |
| `provisioning` | odpowiedzi HTTP i archiwa ZIP → interfejs lub błąd | planowany |
| `session` | scenariusze z fake CLU i wirtualnym czasem | planowany — wymaga semantyki barier czasu |

## 2. Plik przypadku

```json
{
  "schemaVersion": 1,
  "id": "value.cast.int64-max",
  "specRefs": ["model.cast"],
  "description": "…",
  "provenance": "spec §3.1 + §9.2",
  "operation": "value.cast",
  "input": { "text": "9223372036854775807" },
  "expect": { "outcome": "ok", "value": { "value": { "type": "int", "value": "9223372036854775807" } } }
}
```

- `id` — stabilny, globalnie unikalny, równy nazwie pliku; zaczyna się od grupy operacji.
- `specRefs` — stabilne identyfikatory wymagań (tabela §6), nie numery sekcji — przenumerowanie specyfikacji nie unieważnia przypadków.
- `provenance` — skąd pochodzi oczekiwany wynik. **Oczekiwania nigdy nie są generowane przez testowany port**; źródła: dosłowny tekst specyfikacji, `openssl`, semantyka implementacji referencyjnej (Python).
- `expect` — dokładnie jedno z:
  - `{"outcome": "ok", "value": …}` — operacja się udała, `value` to projekcja wyniku,
  - `{"outcome": "error", "code": "…"}` — operacja zgłosiła błąd z taksonomii §8 (`crypto.invalidKeyMaterial`, `provisioning.connection`, `payload.unresolvedPlaceholder`),
  - `{"outcome": "reject"}` — wejście odrzucone bez błędu (parser zwraca „brak wyniku", odbiór cicho porzuca datagram).

Schemat: `conformance/schemas/case.schema.json` (JSON Schema 2020-12). Nieznane pola są błędem.

## 3. Wartości

`GrentonValue` jest kodowany jako wartość otagowana:

| Typ | Kodowanie | Uwagi |
|---|---|---|
| null | `{"type":"null"}` | |
| bool | `{"type":"bool","value":true}` | |
| int | `{"type":"int","value":"42"}` | **string dziesiętny** — Int64 nie mieści się w liczbach JS |
| double | `{"type":"double","bits":"4035800000000000","decimal":"21.5"}` | porównywane są **bity binary64**; `decimal` jest informacyjny i runner go ignoruje |
| string | `{"type":"string","value":"ON"}` | |

Porównanie `ok.value`: strukturalna równość JSON, bez znaczenia kolejności kluczy obiektów; tablice porównywane pozycyjnie. Wynikowe stringi (payloady, URL, format liczb) porównywane bajt w bajt.

## 4. Operacje (profil `stateless`)

| Operacja | Wejście | `ok.value` | Inne wyniki |
|---|---|---|---|
| `crypto.encrypt` | `keyBase64`, `ivBase64`, `plaintextHex` | `ciphertextHex` | `error crypto.invalidKeyMaterial` |
| `crypto.decrypt` | `keyBase64`, `ivBase64`, `ciphertextHex` | `plaintextHex` | `reject` (rozmiar/padding) |
| `wire.request` | `msgId`, `payload` | `frame` | |
| `wire.response` | `frame` | `cluIp`, `msgId`, `payload`, `isNotification` | `reject` |
| `payload.register` | `sessionId`, `keys[]` (`{type:variable,name}` / `{type:attribute,objectName,index}`) | `payload` | |
| `payload.action` | `action` — obiekt ActionRef dokładnie jak w `data.json` | `payload` | `reject` (błąd parsowania, §3.6); `error payload.unresolvedPlaceholder` (`value` = `$value$`, §3.3) |
| `payload.report` | `payload` | `sessionId`, `values[]` | `reject` |
| `value.cast` | `text` | `value` | |
| `provisioning.url` | `host`, `port`? (domyślnie 9998), `pin` | `url` | `error provisioning.connection` |
| `coercion.boolFromOnOff` | `value` | `result` (`true`/`false`/`null` = nieznany) | |
| `coercion.truthy` | `value` | `result` | |
| `number.format` | `value` (double), `precision` | `text` | |

Profil `mapping`:

| Operacja | Wejście | `ok.value` | Inne wyniki |
|---|---|---|---|
| `mapping.interface` | `fixture` — ścieżka pliku `data.json` względem `conformance/` | `devices[]`, `physicalObjects[]`, `skippedWidgets[][]` | `reject` (interfejs nie dekoduje się jako całość) |

Deszyfracja zwraca **bajty**; dekodowanie UTF-8 i obcinanie białych znaków należą do ścieżki odbioru (profil `session`), nie do tej operacji.

## 4a. Projekcja mapowania

`ok.value` operacji `mapping.interface`:

```json
{
  "devices": [
    { "id": "w-onoff", "widgetType": "ON_OFF", "kind": "switch", "name": "Lampa / Kinkiet",
      "page": { "index": 0, "name": "Salon" },
      "entities": [ { "kind": "switch", "id": "w-onoff_0", "name": "Lampa", "state": { … }, "onAction": { … }, … } ] }
  ],
  "physicalObjects": [
    { "key": { "cluId": "CLU_1", "kind": "object", "name": "DOU8272", "index": "0" },
      "widgets": [ { "deviceId": "w-onoff", "widgetType": "ON_OFF", "name": "Lampa / Kinkiet",
                     "page": { "index": 0, "name": "Salon" }, "entityIds": ["w-onoff_0"] } ] }
  ],
  "skippedWidgets": [ ["FANCY_NEW_WIDGET", null], [] ]
}
```

- `devices` — w kolejności normatywnej (spec §6.1); porównywane pozycyjnie, łącznie z urządzeniami bez encji.
- `physicalObjects` — grupowanie z §6.6, w kolejności pierwszego użycia; porównywane pozycyjnie.
- `skippedWidgets` — po jednej liście na stronę: `type` pominiętych widgetów w kolejności (`null` bez `type`). Treść powodu nie jest porównywana — komunikaty błędów są specyficzne dla języka.
- `ValueRef`: `{"callType":"VARIABLE","cluId","index"}` albo `{"callType":"ATTRIBUTE","cluId","objectName","index"}` (`objectName` zmiennej jest ignorowane logicznie, §3.2).
- `ActionRef`: `{"callType","cluId","objectName","event","value"}` + `index` dla wariantów innych niż `SCRIPT`.
- Liczby z `data.json` (`min`, `max`, `precision`, `range`) — zwykłe liczby JSON: pochodzą z JSON-a, więc parsują się identycznie w każdym języku.
- Pola encji według `kind`:

| `kind` encji | Pola (poza `kind`, `id`, `name`) |
|---|---|
| `switch` | `state`, `onAction`, `offAction`, `onIndication`, `offIndication` |
| `dimmableLight` | `state`, `min`, `max`, `precision`, `onAction`, `offAction`, `setValueAction` |
| `hsbLight` | `powerState`, `onAction`, `offAction`, `hue` / `saturation` / `brightness`: `{state, setAction, range:{min,max}}` |
| `contactSensor` | `state`, `reversed`, `onIndication`, `offIndication` |
| `sensor` | `state`, `sensorKind`, `unit` (kanoniczna lub `null`), `grentonUnit`, `valueType`, `precision` (`null`, gdy brak) |
| `numberSetting` | `state`, `setValueAction`, `min`, `max`, `precision` |
| `cover` | `motionState`, `position`, `setPositionAction`, `tilt` (`{position, setAction}` lub `null`), `stopAction` (lub `null`) |
| `stateSensor` | `state` |
| `trigger`, `sceneTrigger` | `action` |
| `camera` | `streamUrl` |
| `thermostat` | `currentTemperature`, `targetTemperature`, `setTargetTemperatureAction`, `minTemperature`, `maxTemperature`, `output` (`null`, gdy brak), `mode` / `power` (`{state, setAction}` lub `null`) |

**Fixtury** leżą w `conformance/fixtures/` i są w manifeście z sumą SHA-256 (runner weryfikuje je tak jak przypadki). Widget, który **musi** zostać pominięty z powodu brakującego pola lub nieznanego enuma, jest w fixturze oznaczony polem `"x-conformance-expect-skip": true`. Porty je ignorują (nieznane pola, §3.6 pkt 4); czyta je tylko wyrocznia `tools/oracles/mapping.py`, bo §3.5 nie podaje pełnego schematu pól każdego widgetu.

## 5. Obowiązki runnera

Runner **zawodzi zamknięty** (fail closed). Przebieg jest nieudany, jeśli:

1. brakuje zestawu (np. niezainicjalizowany submodule) — wyraźny komunikat, nie „0 testów",
2. `manifest.json` nie zgadza się z plikami (brakujący/nadmiarowy przypadek lub fixtura, zła suma SHA-256),
3. przypadek ma nieznaną operację, nieobsługiwane `schemaVersion`, nie da się go zdekodować albo nie spełnia kształtu z §2 (nadmiarowe lub brakujące pola, puste `specRefs`, zły kształt `expect`),
3a. deklaracja portu wymienia profil, którego manifest nie zna; runner wykonuje wszystkie przypadki zadeklarowanych profili, a przypadki pozostałych profili raportuje jako nieobjęte deklaracją (nie jako sukces),
4. deklaracja portu wskazuje pominięcie nieistniejącego przypadku albo pominięty przypadek **przechodzi** (nieoczekiwany sukces — usuń pominięcie),
5. wersja zestawu w deklaracji portu ≠ `suiteVersion` z manifestu.

Runner wywołuje wyłącznie produkcyjne API i serializuje obserwację. **Nie wolno** mu implementować castingu, mapowania, normalizacji PIN-u ani decyzji o błędach. Raport per przypadek: `id`, oczekiwanie, obserwacja, pierwsza różniąca się ścieżka JSON.

### Deklaracja portu

```json
{
  "suiteVersion": "0.1.0",
  "profiles": ["stateless"],
  "skips": [{ "id": "…", "reason": "…", "issue": "…" }]
}
```

Port z niepustą listą `skips` deklaruje zgodność profilu z wyjątkami — i musi je wymienić.

## 5a. Dystrybucja

Zestaw jest wersjonowany razem ze specyfikacją w repo `grenton-core-spec`. Porty przypinają konkretny commit (git submodule) albo importują snapshot z sumą kontrolną. Poprawka oczekiwanego wyniku lub nowy przypadek = nowa wersja zestawu, nawet bez zmiany specyfikacji.

## 6. Identyfikatory wymagań

| Id | Sekcja specyfikacji |
|---|---|
| `crypto.aes-cbc` | §5.1 |
| `crypto.padding` | §5.1 |
| `crypto.key-material` | §5.1 |
| `wire.request` | §5.2 |
| `wire.response` | §5.2 |
| `wire.msg-id-case` | §5.2 |
| `payload.register` | §5.3.2 |
| `payload.report` | §5.3.2 |
| `payload.action` | §5.3.4 |
| `model.cast` | §3.1 |
| `model.action-ref` | §3.3, §3.6 |
| `provisioning.url` | §4.1 |
| `provisioning.pin` | §4.1 |
| `devices.coercion` | §6.2 |
| `devices.number-format` | §10 pkt 3 |
| `model.widget-tolerance` | §3.6 |
| `devices.mapping` | §6.1, §6.3 |
| `devices.order` | §6.1 |
| `devices.physical-objects` | §6.6 |

## 7. Utrzymanie

```sh
python3 tools/suite.py check      # walidacja przypadków + niezależne wyrocznie (openssl, Python round)
python3 tools/suite.py manifest   # przebudowa manifestu po dodaniu/zmianie przypadku
```

`check` ponownie wylicza oczekiwania tam, gdzie istnieje niezależna wyrocznia: szyfrogramy przez `openssl`, `number.format` przez Pythonowe `round()`, koercje przez wyrażenia implementacji referencyjnej (`value == "ON" or value == True`, `bool(value)`), `mapping.interface` przez `tools/oracles/mapping.py` (napisany ze specyfikacji, nie z portu), a każdą parę `decimal`/`bits` przez parser Pythona. Zgłasza też fixtury, których nie używa żaden przypadek.

## 8. Poza zakresem — otwarte decyzje

- **Walidacja `msg_id`:** §5.2 mówi „8 znaków hex", ale odbiór tego nie wymusza. Zestaw nie testuje zniekształconych `msg_id`, dopóki specyfikacja nie rozstrzygnie: ściśle czy liberalnie.
- **Pusta rejestracja** (`keys = []`): zachowanie niespecyfikowane — brak przypadku.
- **Zbiór białych znaków przy castingu i parsowaniu raportu:** przypadki używają tylko spacji i tabulatora; szczegółowy zbiór do ustalenia.
- **Pozycja pominiętego widgetu** (np. `/pages/0/widgets/3`) — na razie porównywane są tylko typy per strona.
- **Limit 10 MB** (§4.3) jest zaleceniem, nie normą — testy brzegowe po jego ustaleniu.
