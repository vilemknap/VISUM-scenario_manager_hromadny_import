# VISUM Scenario Manager – hromadná výměna geometrie variant R1

Tento repozitář obsahuje bezpečnostně orientovaný skript pro **PTV Visum 2026**, který hromadně vymění hlavní trasu v existujících geometry modifications Scenario Manageru bez ručního přepisování desítek scénářů.

Skript je připraven pro projekt `R1_VARIANTY` a pracuje s těmito modifikacemi:

| Varianta | Geometry modification | TRA | Nová trasa | identita `ENTITYHAND` |
|---|---|---|---|---|
| fialová | `G_W1_oprava` | `M000021.tra` | `variant_2_fialova.geojson` | `14AF` |
| zelená | `G_W2` | `M000002.tra` | `variant_3_zelena.geojson` | `1E11` |
| hnědá | `G_W3` | `M000003.tra` | `variant_5_hneda.geojson` | `26B3` |
| modrá | `G_W4` | `M000004.tra` | `variant_4_modra.geojson` | `2E29` |
| oranžová | `G_W5` | `M000005.tra` | `variant_6_oranzova.geojson` | `3156` |

## Co skript nedělá

- **Neupravuje `R1_VARIANTY.vpdb`.**
- Nemění čísla uzlů ani klíče linků.
- Nemění šířkové modifikace `W*_S1/S2/S3`.
- Zatím neupravuje kombinovanou geometry modification `G_W1+W2_oprava` (`M000025.tra`), protože její původní `.tra` nebyla součástí ověřených vstupů.

Díky zachování `NodeNo`/link keys zůstávají vazby existujících scénářů a navazujících šířkových modifikací zachované.

## Princip

Geometry modification je pouze **delta vůči base version**. Některé části hlavní trasy proto v konkrétní `.tra` nejsou, protože jsou zděděné z `R1_VARIANTYBase.ver`.

Skript proto pro každou variantu:

1. načte `R1_VARIANTYBase.ver`,
2. aplikuje původní geometry `.tra`,
3. v kompletní síti vyhledá celou hlavní trasu podle `TYP=hlavni_krivka` a unikátního `ENTITYHAND`,
4. seřadí její uzly a linky jako jednu souvislou trasu,
5. promítne existující trasové uzly na novou GeoJSON křivku,
6. zachová `NodeNo` a link keys, ale změní jejich polohu/průběh,
7. načte nový `LINKPOLY` přes standardní Visum `.net` rozhraní,
8. zkontroluje výsledný `WKTPoly`,
9. nechá Visum metodou `GenerateModelTransferFile()` vytvořit novou kanonickou `.tra` proti stejné base verzi,
10. v režimu `apply` zazálohuje a nahradí původní `.tra`.

Pokud některá kontrola selže, skript skončí před nahrazením originálů.

## Vstupy

Lokálně na počítači s PTV Visum 2026 potřebujete:

- `R1_VARIANTYBase.ver`
- `R1_VARIANTY.vpdb` – skript ho nemění
- `M000002.tra`
- `M000003.tra`
- `M000004.tra`
- `M000005.tra`
- `M000021.tra`
- nové **celé hlavní trasy** v GeoJSON, **EPSG:25834**:
  - `variant_2_fialova.geojson`
  - `variant_3_zelena.geojson`
  - `variant_4_modra.geojson`
  - `variant_5_hneda.geojson`
  - `variant_6_oranzova.geojson`

> Nepoužívejte rozdělené `*_useky_*` ani bodové SHP. Skript potřebuje celou novou osu varianty.

## Doporučená struktura

```text
C:\VISUM-scenario_manager_hromadny_import\
  visum_bulk_replace_geometry.py
  config.example.json
  config.json
  routes\
    variant_2_fialova.geojson
    variant_3_zelena.geojson
    variant_4_modra.geojson
    variant_5_hneda.geojson
    variant_6_oranzova.geojson
```

## Postup spuštění

### 1. Stáhněte repozitář

Přes **Code → Download ZIP**, nebo:

```bat
git clone https://github.com/vilemknap/VISUM-scenario_manager_hromadny_import.git
```

### 2. Připravte `config.json`

Zkopírujte:

```text
config.example.json -> config.json
```

Nastavte minimálně:

```json
"project_dir": "C:/CESTA/K/R1_VARIANTY"
```

Pokud jsou `M000*.tra` jinde, nastavte `tra_dir`. `routes_dir` je standardně `routes` vedle skriptu.

Pro první běh musí zůstat:

```json
"mode": "dry-run"
```

### 3. Vložte nové trasy

Do `routes` vložte pět GeoJSONů v **EPSG:25834** s názvy uvedenými výše.

### 4. Spusťte povinný DRY-RUN v PTV Visum 2026

1. Uložte případnou rozpracovanou síť.
2. Otevřete **PTV Visum 2026**.
3. Zvolte **Scripts → Run script file...**.
4. Vyberte `visum_bulk_replace_geometry.py`.
5. Skript automaticky načte `config.json` ze stejné složky.

Dry-run **nemění původní `.tra` ani `.vpdb`**.

Výstupy vzniknou v:

```text
_geometry_update_work\YYYYMMDD_HHMMSS_dry_run\
  canonical_tra\
  linkpoly_patch\
  report\
```

### 5. Co zkontrolovat

V `report\summary.json` musí být:

```json
"status": "PASS"
```

V `route_node_moves.csv` zkontrolujte zejména `snap_m`. Výchozí pojistka je:

```json
"max_route_node_snap_m": 3000.0
```

V `link_geometry_validation.csv` musí mít všechny řádky:

```text
ok = True
```

### 6. Ostré použití

Až dry-run projde, změňte:

```json
"mode": "apply"
```

a spusťte skript z Visumu znovu.

Skript před přepisem vytvoří zálohu:

```text
backup_geometry_modifications\YYYYMMDD_HHMMSS\
```

a nahradí pouze:

```text
M000002.tra
M000003.tra
M000004.tra
M000005.tra
M000021.tra
```

`R1_VARIANTY.vpdb` se nikdy nezapisuje.

## Kontrola v Scenario Manageru po APPLY

1. Znovu otevřete `R1_VARIANTY.vpdb`.
2. Otevřete alespoň jeden scénář W1–W5.
3. Zkontrolujte novou hlavní trasu.
4. Zkontrolujte hlavně MÚK a navazující rampy.
5. Ověřte, že šířkové modifikace `S1/S2/S3` zůstaly na stejných linkách.
6. Scénáře znovu přepočítejte.

## Poznámka k rampám

Skript zachovává ID a přemisťuje uzly hlavní trasy na novou osu. Tím zůstávají ostatní linky připojené ke stejným uzlům. **Vnitřní polygon navazujících ramp ale skript automaticky nedeformuje.**

Pokud se MÚK posunula výrazně, může být potřeba druhá automatizovaná etapa pro geometrickou deformaci ramp. Proto je první dry-run a vizuální kontrola povinná.

## Kombinovaná W1+W2

`G_W1+W2_oprava` (modification 25) zatím není zahrnuta, protože původní `M000025.tra` nebyla ověřena spolu s ostatními vstupy.

## Bezpečnostní pojistky

- `.vpdb` se nemění,
- `dry-run` nemění původní `.tra`,
- trasa se identifikuje pomocí `TYP=hlavni_krivka` + unikátního `ENTITYHAND`,
- trasa musí být jedna neodbočující cesta,
- hlídá se maximální posun uzlů,
- hlídá se pořadí uzlů podél nové osy,
- po importu se kontroluje `WKTPoly`,
- před přepisem se zálohují všechny cílové `.tra`.

## Použité Visum 2026 COM funkce

Skript používá zejména:

- `Visum.IO.LoadVersion(...)`
- `Visum.ApplyModelTransferFile(...)`
- `Visum.IO.LoadNet(..., ReadAdditive=True)`
- `Visum.GenerateModelTransferFile(...)`
- `Visum.Net.Nodes.ItemByKey(...)`
- `Visum.Net.Links.ItemByKey(...)`
- `GetMultipleAttributes(...)`

Nejsou potřeba externí Python balíčky typu geopandas/shapely.


## Důležitá změna: nová topologie, ne ohýbání staré trasy

Aktuální verze skriptu nepředpokládá jednu nerozvětvenou starou trasu.

Pro každou variantu používá:
- celou novou osu `variant_N_*.geojson`,
- autoritativní uzlové body z velkých kruhů `variant_N_*_velke_kruhy_body_snap.geojson`.

Z těchto vstupů vytvoří nové úseky. Staré `NodeNo` a `LinkNo` používá pouze jako stabilní identifikátory kvůli Scenario Manageru a S1/S2/S3. Uzly dostanou nové souřadnice, hlavní linky nový `LINKPOLY` a navazující rampové linky se geometricky deformují podle posunu uzlů.

Pokud je v MÚK více starých uzlů v jednom místě, mohou být zachována jejich různá ID, ale jsou přesunuta ke stejnému novému uzlovému bodu. To zachová vazby ramp a šířkových modifikací.

Pro oranžovou variantu v dodaném DXF nebyly nalezeny žádné velké `CIRCLE` body. Skript proto pro W5 použije jasně označený fallback: staré uzlové členění promítne na novou oranžovou osu.
