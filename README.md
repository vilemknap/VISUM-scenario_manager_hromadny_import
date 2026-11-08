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
- **Fialová (`G_W1_oprava`) se nahrazuje celá (43 km)** (`"trim_to_old_extent": false`). Stará fialová vedla v úseku ~15–43 km
  po stávajících silnicích, které W1_S1/S2/S3 upgradovaly na dálnici a uzavíraly okolní napojení
  (2001801, 2001825, 2002262, 2002261, 2002238, 2001839, 2002160, …). Tyto řádky se z W1 šířkových modifikací vypustí,
  takže se silnice vrátí do stavu v base síti. Konce nové osy se napojí na nejbližší stávající uzel do 300 m
  (`endpoint_connect_existing_m`).
- **Ostatní varianty**: nová osa se použije od stávajícího napojovacího uzlu 9200065 (~0,4 km od začátku osy) do konce,
  protože tam začínala i stará modifikace. Zbytek osy je ve scénářích stávající síť.

### Velké kruhy a napojení
- Na každém velkém kruhu vznikne nový uzel.
- Kruh do 150 m od stávajícího koncového uzlu (např. 9200065) se sloučí s tímto uzlem.
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
| `trim_to_old_extent` (i per varianta) | `false` = nahradit celou osu | `true`, u fialové `false` |
| `endpoint_connect_existing_m` | slepý konec nové osy napojit na stávající uzel do této vzdálenosti | 300 |
| `extra_connectors` | ruční napojení: `{"node": 1003317, "to": "konec", "template_tra": "M000005.tra", "template_link": 9800868}`; `to` = číslo uzlu / `"konec"` / `"zacatek"` / vynechat (nejbližší nový uzel) | hnědá: 1003317, 1003323 |
| `new_node_no_start`, `new_link_no_start` | první nové číslo uzlu/linku (`auto` = nad maximem v base i všech `.tra`) | `auto` |

Per-variantu lze `keep_links` a `extra_connectors` zapsat i přímo do položky v `geometry_modifications`.

## Kombinovaná W1+W2
`G_W1+W2_oprava` (`M000025.tra`) je v configu připravená, ale vypnutá (`"enabled": false`).
Zapněte ji, až projde dry-run ostatních variant.

## Když něco selže
Skript skončí dřív, než cokoliv přepíše, a chybu uloží do `LAST_ERROR.txt` vedle skriptu.
