# routes

Nové osy variant a velké kruhy (budoucí uzly/křižovatky) v **EPSG:25834**, převedené z
`05_11_2026_prehladna_situacia_25000_export.dxf`.

- `variant_N_<barva>.geojson`: celá nová osa (LineString)
- `variant_N_<barva>_velke_kruhy_body_snap.geojson`: středy velkých kruhů přichycené na osu (Point)

Skript umí číst i `.shp` (Point/PolyLine/Polygon). Kruh jako polygon nebo uzavřená čára se převede na svůj střed.

Oranžová varianta nemá v DXF žádné velké kruhy, takže její bodový soubor je prázdný.
