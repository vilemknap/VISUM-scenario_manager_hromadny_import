# routes

Do teto slozky vlozte nove vstupy v **EPSG:25834**.

Pro kazdou variantu jsou dva typy vstupu:

1. cela nova osa varianty:
   - variant_2_fialova.geojson
   - variant_3_zelena.geojson
   - variant_4_modra.geojson
   - variant_5_hneda.geojson
   - variant_6_oranzova.geojson

2. autoritativni uzlove body z velkych kruhu:
   - variant_2_fialova_velke_kruhy_body_snap.geojson
   - variant_3_zelena_velke_kruhy_body_snap.geojson
   - variant_4_modra_velke_kruhy_body_snap.geojson
   - variant_5_hneda_velke_kruhy_body_snap.geojson
   - variant_6_oranzova_velke_kruhy_body_snap.geojson

Skript z cele osy a techto bodu vytvori nove useky, prepise polohy uzlu a
LINKPOLY a zachova puvodni NodeNo/LinkNo, aby zustaly funkcni Scenario Manager
vazby a S1/S2/S3.

Pozor: v dodanem DXF nebyly pro oranzovou variantu zadne entity CIRCLE na
vrstve velkych kruhu. Jeji bodovy GeoJSON je proto prazdny a skript pouzije
transparentne oznaceny fallback: stare cleneni promita na novou oranzovou osu.
