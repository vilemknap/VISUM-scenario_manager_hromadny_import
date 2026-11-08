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
Nové osy jsou celé trasy (35–46 km). Stará modifikace měla novou trasu jen mezi stávajícími napojovacími uzly
(např. 9200065 ~ 0,5 km od začátku osy, u fialové 1002957 → 1002655). Skript proto použije novou osu jen v tomto úseku
(`trim_to_old_extent`). Zbytek je ve scénářích dál tvořen stávající sítí. Velké kruhy mimo úsek se vynechají a skript je vypíše.

### Velké kruhy
- Kruh do 150 m od stávajícího koncového uzlu (např. 1002957) se sloučí s tímto uzlem.
- Stávající uzel, který byl napojen na starou trasu, ale do 1500 m staničení nemá žádný velký kruh, se **nepřipojí**. Skript ho vypíše v upozorněních a v `napojeni_stavajici_site.csv`.
- Oranžová varianta nemá v DXF žádné velké kruhy. Skript proto pro ni vytvoří uzly v průmětu starých míst napojení. Vypnete to pomocí `"junction_fallback_when_no_circles": false`.

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

### 4. APPLY
Pokud je dry-run v pořádku, nastavte `"mode": "apply"` a spusťte skript znovu.
Původní `.tra` se uloží do `C:/R1_VARIANTY/backup_modifications/<čas>/` a nahradí se novými.
Vrácení: zkopírujte soubory ze zálohy zpět.

### 5. Kontrola
Otevřete `R1_VARIANTY.vpdb`, načtěte např. scénář `W2_S1_2040` a zkontrolujte trasu a napojení. Potom scénáře přepočítejte.

## Volby v `config.json`

| klíč | význam | výchozí |
|---|---|---|
| `trim_to_old_extent` | novou osu použít jen v úseku staré modifikace | `true` |
| `endpoint_snap_to_old_anchor_m` | max. vzdálenost stávajícího koncového uzlu od osy | 500 |
| `circle_merge_into_anchor_m` | kruh tak blízko koncového uzlu = tentýž uzel | 150 |
| `circle_max_offset_m` | max. vzdálenost kruhu od osy (jinak chyba) | 50 |
| `junction_match_station_m` | max. rozdíl staničení mezi starým napojením a velkým kruhem | 1500 |
| `junction_fallback_when_no_circles` | varianta bez kruhů → uzly v průmětu starých napojení | `true` |
| `keep_links` | čísla linků ze staré modifikace, které se nemají mazat | `[]` |
| `extra_connectors` | ruční napojení: `{"node": 1003317, "template_link": 9800848}` (volitelně `"to": <nový uzel>`) | `[]` |
| `new_node_no_start`, `new_link_no_start` | první nové číslo uzlu/linku (`auto` = nad maximem v base i všech `.tra`) | `auto` |

Per-variantu lze `keep_links` a `extra_connectors` zapsat i přímo do položky v `geometry_modifications`.

## Kombinovaná W1+W2
`G_W1+W2_oprava` (`M000025.tra`) je v configu připravená, ale vypnutá (`"enabled": false`).
Zapněte ji, až projde dry-run ostatních variant.

## Když něco selže
Skript skončí dřív, než cokoliv přepíše, a chybu uloží do `LAST_ERROR.txt` vedle skriptu.
