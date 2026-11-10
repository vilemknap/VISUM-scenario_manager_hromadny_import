# VISUM Scenario Manager – náhrada tras variant R1

Skript pro **PTV Visum 2026**. V geometry modifications Scenario Manageru nahradí starou trasu novou:
**starou trasu smaže a vloží nové uzly a nové linky** podle nových podkladů.

| Varianta | Geometry modification | `.tra` | Nová osa | Velké kruhy (uzly) |
|---|---|---|---|---|
| fialová | `G_W1_oprava` | `M000021.tra` | `variant_2_fialova.geojson` | `variant_2_fialova_velke_kruhy_body_snap.geojson` |
| zelená | `G_W2` | `M000002.tra` | `variant_3_zelena.geojson` | `variant_3_zelena_velke_kruhy_body_snap.geojson` |
| hnědá | `G_W3` | `M000003.tra` | `variant_5_hneda.geojson` | `variant_5_hneda_velke_kruhy_body_snap.geojson` |
| modrá | `G_W4` | `M000004.tra` | `variant_4_modra.geojson` | `variant_4_modra_velke_kruhy_body_snap.geojson` |
| oranžová | `G_W5` | `M000005.tra` | `variant_6_oranzova.geojson` | `variant_6_oranzova_velke_kruhy_body_snap.geojson` (prázdný) |

Všechny podklady jsou ve složce `routes/` (EPSG:25834).

## Co skript udělá

Pro každou variantu:

1. Načte `R1_VARIANTYBase.ver` a aplikuje původní geometry `.tra`.
2. **Smaže celý starý komplex trasy**: hlavní linky (`TYP=hlavni_krivka` + `ENTITYHAND` varianty), jízdní pásy, rampy, větve MÚK a všechny uzly, které stará modifikace vložila.
3. **Na každém velkém kruhu vloží nový uzel** (obyčejná úrovňová křižovatka, nové číslo uzlu).
4. **Mezi uzly vloží nové linky** s polygonem podle nové osy (nová čísla linků). Typ, kapacitu, rychlost a ostatní atributy převezme ze starého linku trasy ve stejném staničení.
5. **Napojí stávající síť**: každý stávající uzel, na který se stará trasa napojovala, propojí jedním linkem s nejbližším novým uzlem (velkým kruhem) ve staničení. Číslo a atributy linku převezme z původního napojení. **Žádné rampy ani MÚK se nevytváří.**
6. Visum z toho vygeneruje novou geometry `.tra` vůči base verzi.
7. **Přegeneruje i šířkové modifikace S1/S2/S3**, které na geometrii závisí (zjistí je z `R1_VARIANTY.vpdb`):
   - řádky pro staré linky trasy se přenesou na nové linky ve stejném staničení,
   - řádky pro napojení se převedou na nové napojení,
   - řádky pro smazané rampy a MÚK se vypustí,
   - řádky pro stávající síť zůstanou beze změny.
8. Všechno ověří: base + nová geometrie + každá nová šířková modifikace se musí ve Visumu načíst bez chyby.
9. V režimu `apply` udělá zálohu a přepíše `.tra` soubory. **`R1_VARIANTY.vpdb` se nikdy nezapisuje**, takže všech 69 scénářů zůstane navázaných.

### Rozsah náhrady
- **Všechny varianty se nahrazují v celé délce nové osy** (`"trim_to_old_extent": false`).
- **Zelená, hnědá, modrá a oranžová začínají v MÚK Most (0. km osy).** Na začátku osy vznikne uzel, který se napojí
  na nejbližší stávající uzel MÚK Most 9000544 (30 m). Trasa pak prochází stávajícím uzlem 9200065 (0,44 km),
  kde dosud začínaly staré trasy. Stávající uzel ležící přímo na nové ose (do 20 m, `existing_node_on_axis_m`) je vždy
  průjezdným uzlem trasy.
- **Fialová**: konce osy se napojí na nejbližší stávající uzel do 300 m (9900377, 9000280) a trasa prochází uzlem 1002957.
  Stará fialová vedla v úseku ~15–43 km po stávajících silnicích, které W1_S1/S2/S3 upgradovaly na dálnici a uzavíraly okolní
  napojení (2001801, 2001825, 2002262, 2002261, 2002238, 2001839, 2002160, …). Tyto řádky se z W1 šířkových modifikací vypustí
  (`"replace_old_route_on_existing_network": true`), takže se silnice vrátí do stavu v base síti.

- **Modrá** měla v původních W4_S1 a W4_S2 úvodní úsek (9200065 → ~19 km) zavřený v obou směrech. Při náhradě se otevře
  (`"open_closed_route_links": true`): S1 typ 2 / 22 500 / 120 km/h, S2 typ 4 / 10 000 / 90 km/h, stejně jako zbytek modré.

- **Zelená W2_S2** měla úvodní úsek od MÚK Most po 8,4 km zavřený, také se otevře (typ 4 / 10 000 / 90 km/h).
- **Oranžová W5_S2** měla u úvodních linků v jednom směru prázdný typ, doplní se typ 4.
- **Fialová W1_S1/S2** přestanou zavírat silnici 1002477 – 9900345 – 1002655 (linky 9800699, 9800700).

### Velké kruhy a napojení
- Na každém velkém kruhu vznikne nový uzel.
- Kruh do 150 m od stávajícího uzlu, kterým trasa prochází (např. 1002957), se sloučí s tímto uzlem.
- Stávající uzel, který byl napojen na starou trasu, se napojí na nejbližší velký kruh do 1500 m staničení.
  **Když tam žádný kruh není, vznikne nový uzel v průmětu staré křižovatky na novou osu** a napojí se na něj
  (např. 1001981 u zelené ve staničení ~8,4 km). Oranžová nemá žádné kruhy, takže takto vzniknou všechny její uzly.
- **Hnědá**: na konci osy vznikne nový uzel a napojí se na 1003317 a 1003323 stejně jako dříve oranžová.
  Atributy se převezmou z oranžových linků 9800868/9800869 (`extra_connectors` v configu).
- Velké kruhy, na které se zatím nenapojuje žádná stávající silnice, skript vypíše v upozorněních.

## Spuštění

### 1. Stažení
**Code → Download ZIP**, rozbalit např. do `C:\VISUM-scenario_manager_hromadny_import`.

### 2. `config.json`
Zkopírujte `config.example.json` → `config.json` a zkontrolujte cesty (**lomítka `/`**):

```json
"project_dir": "C:/R1_VARIANTY",
"tra_dir": "C:/R1_VARIANTY/Modifications",
```

V `project_dir` musí být `R1_VARIANTYBase.ver` a `R1_VARIANTY.vpdb`. V `tra_dir` musí být všechny `M0000xx.tra`, tedy geometrické i šířkové (2–5, 9–24).

### 3. DRY-RUN (nic nepřepisuje)
1. Uložte rozpracovanou síť a zavřete Scenario Manager projekt.
2. PTV Visum 2026 → **Scripts → Run script file...** → `visum_bulk_replace_geometry.py`.
3. Výstup bude v `C:/R1_VARIANTY/_route_replace_work/<čas>_dry_run/`:
   - `report/summary.json`: `"status": "PASS"` a seznam upozornění,
   - `report/napojeni_stavajici_site.csv`: který stávající uzel se napojil na který nový uzel,
   - `report/nove_linky.csv`: nové linky, délky, staničení a starý link, ze kterého převzaly atributy,
   - `report/sirkove_modifikace_radky.csv`: co se stalo s každým řádkem S1/S2/S3,
   - `<kód>/nahled_nova_trasa.geojson`: náhled nových uzlů a linků a smazané staré trasy (otevřete v QGIS),
   - `<kód>/canonical/*.tra`: nové `.tra` soubory.

### Průběh a výsledek (tichý režim)
Při `"silent": true` (výchozí) skript během běhu nezobrazuje žádná okna. Vše zapisuje vedle skriptu do:
- `LAST_STATUS.txt`: jednořádkový výsledek `PASS dry-run …`, `PASS apply …` nebo `FAIL …`,
- `LAST_RUN.log`: celý průběh s časy (kopie je i v `report/run.log`),
- `LAST_ERROR.txt`: jen při chybě, s podrobnostmi.

Skript skončil, když Visum přestane být zaneprázdněné. Výsledek pak zjistíte v `LAST_STATUS.txt`.
Při `"silent": false` se průběh vypisuje do Visumu (každý řádek jako okno) a chyba se zobrazí jako dialog.

### 4. APPLY
Pokud je dry-run v pořádku, nastavte `"mode": "apply"` a spusťte skript znovu.
Původní `.tra` se uloží do `C:/R1_VARIANTY/backup_modifications/<čas>/` a nahradí se novými.
Vrácení: zkopírujte soubory ze zálohy zpět.

### 5. Kontrola
Otevřete `R1_VARIANTY.vpdb`, načtěte např. scénář `W2_S1_2040` a zkontrolujte trasu a napojení. Potom scénáře přepočítejte.

## Volby v `config.json`

| klíč | význam | výchozí |
|---|---|---|
| `endpoint_snap_to_old_anchor_m` | max. vzdálenost stávajícího koncového uzlu od osy | 500 |
| `circle_merge_into_anchor_m` | kruh tak blízko koncového uzlu = tentýž uzel | 150 |
| `circle_max_offset_m` | max. vzdálenost kruhu od osy (jinak chyba) | 50 |
| `junction_match_station_m` | max. rozdíl staničení mezi starým napojením a velkým kruhem | 1500 |
| `junction_fallback_when_no_circles` | stávající napojení bez velkého kruhu → nový uzel v průmětu | `true` |
| `keep_links` | čísla linků ze staré modifikace, které se nemají mazat | `[]` |
| `trim_to_old_extent` (i per varianta) | `true` = novou osu použít jen v úseku staré modifikace | `false` |
| `existing_node_on_axis_m` | stávající uzel takto blízko osy = průjezdný uzel trasy | 20 |
| `open_closed_route_links` (per varianta) | zavřené úseky hlavní trasy v S1/S2/S3 otevřít nastavením zbytku trasy v téže modifikaci | modrá `true` |
| `fill_empty_route_type` (per varianta) | doplnit chybějící typ trasy v S1/S2/S3 typem zbytku trasy | oranžová `true` |
| `drop_width_rows_for_links` (per varianta) | čísla linků, jejichž řádky se z S1/S2/S3 vypustí | fialová `[9800699, 9800700]` |
| `replace_old_route_on_existing_network` (per varianta) | vypustit z S1/S2/S3 upgrade stávajících silnic staré trasy | fialová `true` |
| `endpoint_connect_existing_m` | slepý konec nové osy napojit na stávající uzel do této vzdálenosti | 300 |
| `extra_connectors` | ruční napojení: `{"node": 1003317, "to": "konec", "template_tra": "M000005.tra", "template_link": 9800868}`; `to` = číslo uzlu / `"konec"` / `"zacatek"` / vynechat (nejbližší nový uzel) | hnědá: 1003317, 1003323 |
| `new_node_no_start`, `new_link_no_start` | první nové číslo uzlu/linku (`auto` = nad maximem v base i všech `.tra`) | `auto` |

Per-variantu lze `keep_links` a `extra_connectors` zapsat i přímo do položky v `geometry_modifications`.

## Kombinovaná W1+W2
`G_W1+W2_oprava` (`M000025.tra`) je v configu připravená, ale vypnutá (`"enabled": false`).
Zapněte ji, až projde dry-run ostatních variant.

## Když něco selže
Skript skončí dřív, než cokoliv přepíše, a chybu uloží do `LAST_ERROR.txt` vedle skriptu.

## Aktualizace jednotlivých variant (`visum_aktualizace_variant.py`)

Samostatný skript pro situaci, kdy už je náhrada tras jednou provedená (`apply`) a pro některou variantu přijdou
nové podklady. Vezme **původní** `.tra` dané varianty (geometrie + její S1/S2/S3) ze zálohy
`backup_modifications`, nahradí trasu novými podklady stejným postupem a přepíše **jen soubory této varianty**.
Ostatní varianty ani `R1_VARIANTY.vpdb` nemění. Předtím zazálohuje aktuální stav.

Připravená aktualizace podle DXF z 9. 11. 2026:
- **fialová** (`G_W1_oprava`): nová osa `variant_2_fialova_aktualni.geojson` (změna mezi ~11,7 a 16,2 km, posun až 420 m)
  a 4 velké kruhy místo 6 (`variant_2_fialova_aktualni_velke_kruhy_body_snap.geojson`). Uzel 1002655 je teď 700 m od osy
  a bez kruhu, vznikne pro něj nový uzel v průmětu (~13,1 km),
- **oranžová** (`G_W5`): nový velký kruh r = 500 m ve staničení ~35,2 km
  (`variant_6_oranzova_aktualni_velke_kruhy_body_snap.geojson`, převedený z S-JTSK, odchylka < 5 m).

Spuštění:
1. Vedle skriptu musí být `visum_bulk_replace_geometry.py` a váš `config.json` (cesty a volby variant).
2. Zkopírujte `config_aktualizace.example.json` → `config_aktualizace.json`. Variantu, kterou aktualizovat
   nechcete, vypnete `"enabled": false`.
3. Visum → Scripts → Run script file… → `visum_aktualizace_variant.py` s `"mode": "dry-run"`.
   Výsledek: `LAST_STATUS_aktualizace.txt`, průběh `LAST_RUN_aktualizace.log`.
4. Pak `"mode": "apply"` a znovu. Přepíšou se jen soubory aktualizovaných variant (fialová `M000021`–`M000024`,
   oranžová `M000005`, `M000018`–`M000020`).
5. Ve Scenario Manageru přepočítejte scénáře těchto variant.

Původní soubory skript najde sám (`"source_tra_dir": "auto"`): první zálohu, ve které jsou soubory ještě
v původní podobě. Případně zadejte složku ručně.

### Původní soubory z jiného projektu
`source_tra_dir` v `config_aktualizace.json` může ukazovat i na složku záloh jiného projektu
(např. `C:/R1_VARIANTY/backup_modifications`) – skript v ní sám najde zálohu s původními soubory.
Hodí se pro kopii projektu (např. na OneDrive), která vlastní `backup_modifications` nemá.

## Přenos změn base verze do jiného projektu (`visum_prenos_base.py`)

Když se v jiné kopii projektu (zdroj) upravila **base verze** (např. obchvaty) a chcete ji
dostat do projektu s aktuálními variantami (cíl), aniž by se rozbily modifikace.

Skript:
1. načte starou base (cíl) a novou base (zdroj), najde nové / smazané / rozdělené uzly a linky,
2. každou modifikaci cíle (geometrické i šířkové S1/S2/S3) „přeloží“ na novou base:
   - koliduje-li číslo vkládaného uzlu/linky s číslem nově přidaným v base → přečísluje,
   - mazání linky, která byla v nové base rozdělena (nový uzel na ní) → maže všechny její části,
   - uzly v `keep_nodes` se nemažou (smaže se jen jejich původní linky, napojení zůstane),
   - řádky na objekty, které v nové base už neexistují (odbočení, pruhy…), vypustí,
3. každou modifikaci ověří (aplikace na novou base ve Visumu + kontrola sítě),
4. v režimu `apply` zazálohuje cílovou base a Modifications do `backup_pred_prenosem_base/<čas>/`,
   nahradí base verzi cíle base verzí zdroje a modifikace přeloženými soubory.
   `R1_VARIANTY.vpdb` se nemění.

Postup:
1. `visum_prenos_base.py` dejte vedle `visum_bulk_replace_geometry.py`.
2. `config_prenos_base.example.json` → `config_prenos_base.json`, zkontrolujte `source_project_dir`,
   `target_project_dir`, `keep_nodes`.
3. Zavřete projekt ve Visumu (Scenario Manager), pozastavte synchronizaci OneDrive.
4. `"mode": "dry-run"` → Visum → Scripts → Run script file → `visum_prenos_base.py`.
   Výsledek v `LAST_STATUS_prenos_base.txt` (PASS/FAIL), podrobnosti v `LAST_RUN_prenos_base.log`
   (řádky `POZOR` stojí za přečtení).
5. `"mode": "apply"` → spustit znovu.
6. Otevřít projekt, ve Scenario Manageru přepočítat (znovu sestavit) všechny scénáře.

Volitelné klíče: `only_geometry_modifications` (seznam kódů), `target_tra_dir`, `target_vpdb`,
`work_dir`, `backup_dir`, `node_map_tolerance_m` (uzel zdroje do této vzdálenosti = stejný uzel).

### Jen vybrané modifikace do projektu, který už novou base má
`config_prenos_base_jen_fialova.example.json`: `"replace_base": false` (base cíle se nemění),
`"old_base_version": "auto"` (stará base ze zálohy posledního přenosu), `source_tra_dir` = složka
s modifikacemi vůči staré base, `only_geometry_modifications` = které varianty.
