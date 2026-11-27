# KVitals PL — aktualizator i utrzymanie

Stan: 2026-11-27. Wydanie produkcyjne: [v3.4.0-pl.3](https://github.com/wojko6/kvitals/releases/tag/v3.4.0-pl.3). Update Manager 0.2. Projekt zachowuje licencję GPL-3.0 i autorstwo upstream [yassine20011/kvitals](https://github.com/yassine20011/kvitals).

## Kanały i lokalizacja

- `master`: gałąź przeznaczona do śledzenia upstream.
- `feat/polish-localization`: poprawki lokalizacyjne dla [PR #136](https://github.com/yassine20011/kvitals/pull/136); na dzień 2026-11-27 PR jest otwarty, nie został scalony.
- `stable-pl`: polski kanał dystrybucji, narzędzia utrzymania i CI.
- 244 komunikaty gettext w `po/pl.po`, polski opis `KPlugin.Description[pl]` oraz poprawki internacjonalizacji QML. Nie oznacza to audytu każdego możliwego tekstu zależnego od systemu lub sprzętu.

## Obsługa zainstalowanego aktualizatora

Na przetestowanym stanowisku Fedora 45 / KDE Plasma aktualizator znajduje się w `~/.local/bin/kvitals-update`. Nie wymaga sudo ani tokenu API.

```bash
~/.local/bin/kvitals-update status
~/.local/bin/kvitals-update check
~/.local/bin/kvitals-update history
systemctl --user list-timers --all kvitals-update.timer
journalctl --user -u kvitals-update.service -n 30 --no-pager
```

`check` sprawdza najnowsze stabilne wydanie i przygotowuje pakiet, ale nie instaluje go. `adopt` jest jednorazowym poleceniem wersji 0.2 dla instalacji zgodnej z przypiętym wydaniem pl.2; nie należy go ponawiać ani traktować jako ogólnego instalatora nowej wersji.

Codzienny timer użytkownika (`OnCalendar=daily`, `RandomizedDelaySec=15min`, `Persistent=true`) uruchamia usługę `kvitals-update.service`. Działa w ramach aktywnego menedżera systemd użytkownika. Nie uruchamia ponownie Plasma ani nie instaluje aktualizacji automatycznie.

### Wdrożenie lub rollback

Najpierw wykonaj `status`, a przed aktualizacją również `check` i upewnij się, że wynik to `UPDATE=STAGED`. Przed rollbackiem upewnij się, że istnieje kopia poprzedniej wersji. Zamknij Discover i okna zarządzania widżetami. Na przetestowanym stanowisku, gdzie timer i powłoka są aktywne, użyj poniższego bloku. Panel na chwilę zniknie. Nie wykonuj go jako root.

```bash
(
set -euo pipefail
U="$HOME/.local/bin/kvitals-update"
ACTION=apply # zmień na rollback, aby przywrócić poprzednią wersję
"$U" status
systemctl --user is-active --quiet plasma-plasmashell.service
systemctl --user is-active --quiet kvitals-update.timer
trap 'systemctl --user start plasma-plasmashell.service; systemctl --user start kvitals-update.timer' EXIT
systemctl --user stop kvitals-update.timer
test "$(systemctl --user show kvitals-update.service -p ActiveState --value)" = inactive
systemctl --user stop plasma-plasmashell.service
"$U" "$ACTION"
systemctl --user start plasma-plasmashell.service
systemctl --user start kvitals-update.timer
systemctl --user is-active plasma-plasmashell.service
systemctl --user is-active kvitals-update.timer
trap - EXIT
"$U" status
"$U" history
)
```

Po operacji sprawdź interfejs i odczyty czujników. W razie błędu nie ponawiaj `adopt` ani nie usuwaj dziennika transakcji. Zachowaj logi i kopie; przy niejednoznacznym stanie potrzebna jest ręczna diagnostyka. Rollback przywraca ostatnią wersję zapisaną jako poprzednia, nie dowolny punkt historii. Timer po rollbacku może ponownie przygotować nowsze wydanie, ale go sam nie zainstaluje.

### Dane lokalne

- Widżet: `~/.local/share/plasma/plasmoids/org.kde.plasma.kvitals`.
- Stan: `~/.local/share/kvitals-pl/state.json`.
- Staging: `~/.local/share/kvitals-pl/staged/`.
- Kopie: `~/.local/share/kvitals-pl/backups/`.
- Dziennik w toku: `~/.local/share/kvitals-pl/transaction.json`.
- Jednostki użytkownika: `~/.config/systemd/user/kvitals-update.{service,timer}`.

Lokalny wpis KVitals o ID 2347917 usunięto z `~/.local/share/knewstuff3/plasmoids.knsregistry` po wykonaniu kopii. Nie usunięto widżetu ani jego konfiguracji. To nie jest oficjalna blokada KDE Store: aktualizator wykrywa ponowną rejestrację i przerywa działanie. Nie należy mieszać kanałów.

## Budowanie i publikacja

Workflow [.github/workflows/release.yml](../../.github/workflows/release.yml) na gałęzi stable-pl uruchamia testy, kompiluje MO przez msgfmt, buduje ZIP i sprawdza kompatybilność z aktualizatorem. Commit na stable-pl daje artefakt testowy, a poprawny tag `vMAJOR.MINOR.PATCH-pl.N` wskazujący na commit w historii stable-pl inicjuje publikację. Wersja KPlugin.Version pozostaje wersją upstream; pole X-KVitals-PL-Release rozróżnia wydania w pakiecie.

Pakiet ma nazwę `org.kde.plasma.kvitals-${TAG}.plasmoid`. Drugim wymaganym zasobem wydania jest `SHA256SUMS`. Publikacja tworzy szkic, przesyła zasoby i publikuje wydanie bez nadpisywania istniejącego. Nie przesuwaj istniejących tagów ani nie zastępuj zasobów już wydanego pakietu. Aktualizacje upstream należy najpierw przejrzeć, przetestować lokalizację i zgodność, a dopiero później zatwierdzić nowy tag. Synchronizacja z upstream nie jest automatyczna.

## Dowody walidacji produkcyjnej

Data: 2026-11-27, Fedora 45 / KDE Plasma, aktualizator 0.2. Wyniki pochodzą z logów operatora:

| Etap | Wynik |
| --- | --- |
| A07I-R1: odłączenie od rejestru KDE Store i restart Plasma | PASS |
| A07K: zgodność 48 plików produkcyjnych z pl.2 | PASS |
| A07M: 15 testów aktualizatora | PASS |
| A07N/O: publikacja narzędzia i timer użytkownika | PASS |
| A07Q-R1/R2: dwa udane przebiegi CI | PASS |
| A07R: automatyczne wydanie pl.3 | PASS |
| A07S: systemd pobrał i zweryfikował pl.3 bez zmiany widżetu | PASS |
| A07T/U/V: aktualizacja, rollback i ponowna aktualizacja | PASS |

Przebiegi: [CI R1](https://github.com/wojko6/kvitals/actions/runs/46009579618), [CI R2](https://github.com/wojko6/kvitals/actions/runs/46009702605), [publikacja pl.3](https://github.com/wojko6/kvitals/actions/runs/46010358980).

| Czas UTC | Operacja | Wynik |
| --- | --- | --- |
| 13:50:48 | pl.2 → pl.3 | PASS |
| 13:51:26 | pl.3 → pl.2 | PASS |
| 13:52:33 | pl.2 → pl.3 | PASS |

Końcowy stan: pl.3 zainstalowane i najnowsze, LOCAL_INTEGRITY=PASS, KDE_STORE_DETACH=PASS, HISTORY_INTEGRITY=PASS, BACKUPS_AVAILABLE=3, TRANSACTION_CLEAN=PASS, UPDATE=NONE, FULL_UPDATE_CYCLE=PASS, FINAL_PRODUCTION_VALIDATION=PASS. W logu potwierdzono metadane polskie i aktywność usług, ale końcowej wizualnej kontroli wszystkich czujników nie odnotowano osobno.

SHA-256 pakietu pl.3: `8c328d2f2047f5e77a5dc8c91ddc85d66d9a7137a448a4f6bb0f8e9f5af858f6`.

## Ograniczenia i dalszy rozwój

- Aktualizator sprawdza HTTPS, SHA-256 z GitHub API i manifest wydania; nie weryfikuje niezależnych podpisów kryptograficznych.
- Atomowa wymiana katalogów wymaga Linux renameat2 i tego samego systemu plików. Testowano symulowane przerwania, nie rzeczywiste zaniki zasilania.
- Zgodność potwierdzono dla tego stanowiska, nie dla wszystkich dystrybucji ani przyszłych wersji Plasma.
- Brak automatycznego wdrażania, automatycznej synchronizacji upstream, polityki retencji kopii i ogólnego mechanizmu bootstrapu aktualizatora dla nowych instalacji.
- W razie wycofania wydania lub regresji należy przejrzeć logi i przygotować nowe wydanie, nie nadpisywać opublikowanego pakietu.
