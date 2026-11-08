# -*- coding: utf-8 -*-
"""
R1 / PTV Visum 2026
Hromadna vymena geometry modifications za jednoduchy koridor:

  velky kruh = jeden novy NODE
  usek nove osy mezi dvema velkymi kruhy = jeden novy LINK

Rampy, vetve MUK, turny, lanes a pomocne uzly se z puvodni geometry
modification NEPRENASI.

Puvodni .tra slouzi jen jako sablona:
- zachovame cisla dlouhych hlavnich LINKu, pokud je lze bezpecne vybrat,
- prevezmeme jejich hlavni dopravni atributy,
- nepouzivame jeji rampovou/topologickou geometrii.

Vysledek se generuje vzdy proti ciste R1_VARIANTYBase.ver.
"""

from __future__ import print_function

import csv
import datetime as dt
import json
import math
import os
from pathlib import Path
import shutil
import traceback

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = SCRIPT_DIR / "config.json"
SCRIPT_VERSION = "2026-11-08.6-simple-intersection-nodes"

DEFAULT_IDENTITIES = {
    "G_W1_oprava": "14AF",
    "G_W2": "1E11",
    "G_W3": "26B3",
    "G_W4": "2E29",
    "G_W5": "3156",
}

DEFAULT_NODE_BASES = {
    "G_W1_oprava": 9911000,
    "G_W2": 9921000,
    "G_W3": 9931000,
    "G_W4": 9941000,
    "G_W5": 9951000,
}

DEFAULT_COPY_LINK_ATTRIBUTES = [
    "TYPENO",
    "TSYSSET",
    "NUMLANES",
    "CAPPRT",
    "V0PRT",
    "VDFPARASETNO",
    "TOLLPRT",
    "ADDVAL1",
    "ADDVAL2",
    "ADDVAL3",
    "BARVA",
    "LAYER",
    "TYP",
    "ENTITYHAND",
]


def norm(v):
    return "" if v is None else str(v).strip().casefold()


def dist(a, b):
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def line_length(coords):
    return sum(dist(a, b) for a, b in zip(coords, coords[1:]))


def cumulative(coords):
    out = [0.0]
    s = 0.0
    for a, b in zip(coords, coords[1:]):
        s += dist(a, b)
        out.append(s)
    return out


def point_at(coords, cum, s):
    if s <= 0:
        return tuple(coords[0])
    if s >= cum[-1]:
        return tuple(coords[-1])
    for i in range(len(coords) - 1):
        if cum[i] <= s <= cum[i + 1]:
            den = cum[i + 1] - cum[i]
            t = 0.0 if den == 0 else (s - cum[i]) / den
            ax, ay = coords[i]
            bx, by = coords[i + 1]
            return (ax + t * (bx - ax), ay + t * (by - ay))
    return tuple(coords[-1])


def project(coords, cum, p):
    px, py = p
    best = (1e100, 0.0, tuple(coords[0]))
    for i, (a, b) in enumerate(zip(coords, coords[1:])):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else ((px - ax) * dx + (py - ay) * dy) / den
        t = max(0.0, min(1.0, t))
        q = (ax + t * dx, ay + t * dy)
        dd = dist(p, q)
        if dd < best[0]:
            best = (dd, cum[i] + t * math.sqrt(den), q)
    return best[1], best[2], best[0]


def subline(coords, cum, s0, s1):
    rev = s1 < s0
    a, b = (s1, s0) if rev else (s0, s1)
    pts = [point_at(coords, cum, a)]
    for i in range(1, len(coords) - 1):
        if a < cum[i] < b:
            pts.append(tuple(coords[i]))
    pts.append(point_at(coords, cum, b))
    clean = []
    for p in pts:
        if not clean or dist(clean[-1], p) > 1e-7:
            clean.append(p)
    if rev:
        clean.reverse()
    return clean


def parse_wkt_line(value):
    s = str(value or "").strip()
    if "(" not in s or ")" not in s:
        return []
    body = s[s.find("(") + 1:s.rfind(")")]
    pts = []
    for token in body.split(","):
        p = token.strip().split()
        if len(p) < 2:
            continue
        try:
            pts.append((float(p[0]), float(p[1])))
        except Exception:
            return []
    return pts


def load_geojson_line(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    lines = []
    for feat in feats:
        geom = feat.get("geometry", feat)
        if not geom:
            continue
        if geom.get("type") == "LineString":
            lines.append([(float(c[0]), float(c[1])) for c in geom["coordinates"]])
        elif geom.get("type") == "MultiLineString":
            for part in geom["coordinates"]:
                lines.append([(float(c[0]), float(c[1])) for c in part])
    lines = [x for x in lines if len(x) >= 2]
    if not lines:
        raise RuntimeError("GeoJSON nema LineString: %s" % path)
    return max(lines, key=line_length)


def load_geojson_points(path, route):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    rcum = cumulative(route)
    pts = []
    for feat in feats:
        geom = feat.get("geometry", feat)
        if not geom or geom.get("type") != "Point":
            continue
        c = geom.get("coordinates", [])
        if len(c) < 2:
            continue
        raw = (float(c[0]), float(c[1]))
        station, snapped, offset = project(route, rcum, raw)
        pts.append({
            "raw": raw,
            "xy": snapped,
            "station_m": station,
            "offset_m": offset,
            "properties": feat.get("properties", {}),
        })
    pts.sort(key=lambda x: x["station_m"])

    # slouceni pripadnych duplicitnich bodu
    out = []
    for p in pts:
        if out and abs(p["station_m"] - out[-1]["station_m"]) < 0.01:
            continue
        out.append(p)
    for i, p in enumerate(out, 1):
        p["order"] = i
    return out


def _repair_windows_path_values(raw):
    import re
    keys = ("project_dir", "tra_dir", "routes_dir", "work_dir", "backup_dir", "base_version")
    out = raw
    for key in keys:
        pattern = r'("' + re.escape(key) + r'"\s*:\s*")([^"]*)(")'
        def repl(m):
            return m.group(1) + m.group(2).replace("\\", "/") + m.group(3)
        out = re.sub(pattern, repl, out)
    return out


def load_config():
    if not CONFIG.exists():
        raise RuntimeError("Chybi config.json vedle skriptu: %s" % CONFIG)
    raw = CONFIG.read_text(encoding="utf-8-sig")
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as first_exc:
        try:
            cfg = json.loads(_repair_windows_path_values(raw))
            print("WARNING: Windows cesty v configu byly automaticky opraveny.")
        except Exception:
            raise RuntimeError(
                "config.json neni platny JSON. Pouzij C:/R1_VARIANTY. Puvodni chyba: %s"
                % first_exc
            )

    mods = cfg.get("modifications", [])
    if not isinstance(mods, list) or not mods:
        raise RuntimeError("config.json neobsahuje modifications.")

    for m in mods:
        code = m.get("code")
        if not m.get("identity") and code in DEFAULT_IDENTITIES:
            m["identity"] = DEFAULT_IDENTITIES[code]
        if not m.get("node_number_base") and code in DEFAULT_NODE_BASES:
            m["node_number_base"] = DEFAULT_NODE_BASES[code]
    return cfg


def path_resolve(value, base):
    p = Path(str(value))
    return p if p.is_absolute() else (Path(base) / p).resolve()


def get_visum():
    if globals().get("Visum") is not None:
        return globals()["Visum"]
    try:
        import win32com.client
        return win32com.client.Dispatch("Visum.Visum")
    except Exception as exc:
        raise RuntimeError(
            "Nelze ziskat Visum COM. Spust skript z PTV Visum 2026 -> Scripts -> Run script file."
        ) from exc


def safe_att(obj, name):
    try:
        return obj.AttValue(name)
    except Exception:
        return None


def safe_set(obj, name, value, warnings):
    if value is None:
        return
    try:
        obj.SetAttValue(name, value)
    except Exception as exc:
        warnings.append("%s=%r: %s" % (name, value, exc))


def node_xy(visum, node_no):
    node = visum.Net.Nodes.ItemByKey(int(node_no))
    return float(node.AttValue("XCOORD")), float(node.AttValue("YCOORD"))


def all_node_nos(visum):
    return {int(r[0]) for r in visum.Net.Nodes.GetMultipleAttributes(["NO"])}


def all_link_nos(visum):
    return {int(r[0]) for r in visum.Net.Links.GetMultipleAttributes(["NO"])}


def selected_main_groups(visum, m, route):
    attrs = ["NO", "FROMNODENO", "TONODENO", "TYP", "ENTITYHAND"]
    rows = visum.Net.Links.GetMultipleAttributes(attrs)
    groups = {}
    rcum = cumulative(route)

    for row in rows:
        if norm(row[3]) != "hlavni_krivka":
            continue
        if norm(row[4]) != norm(m["identity"]):
            continue

        no = int(row[0])
        a = int(row[1])
        b = int(row[2])
        groups.setdefault(no, {"no": no, "dirs": []})
        groups[no]["dirs"].append((a, b))

    if not groups:
        raise RuntimeError(
            "%s: v puvodni TRA nebyly nalezeny hlavni linky TYP=hlavni_krivka, ENTITYHAND=%s"
            % (m["code"], m["identity"])
        )

    for g in groups.values():
        a, b = g["dirs"][0]
        try:
            pts = parse_wkt_line(visum.Net.Links.ItemByKey(a, b).AttValue("WKTPOLY"))
        except Exception:
            pts = []
        if len(pts) < 2:
            pts = [node_xy(visum, a), node_xy(visum, b)]

        g["geometry"] = pts
        g["length_m"] = line_length(pts)
        mid = point_at(pts, cumulative(pts), g["length_m"] / 2.0)
        st, _q, off = project(route, rcum, mid)
        g["station_m"] = st
        g["offset_m"] = off

        # orientace stareho fyzickeho linku vzhledem k nove ose
        sta = project(route, rcum, node_xy(visum, a))[0]
        stb = project(route, rcum, node_xy(visum, b))[0]
        g["old_forward_pair"] = (a, b) if sta <= stb else (b, a)

    return list(groups.values())


def capture_link_template(visum, group, attributes):
    fwd_pair = group["old_forward_pair"]
    rev_pair = (fwd_pair[1], fwd_pair[0])

    def capture(pair):
        try:
            obj = visum.Net.Links.ItemByKey(int(pair[0]), int(pair[1]))
        except Exception:
            return {}
        return {name: safe_att(obj, name) for name in attributes}

    return {
        "link_no": group["no"],
        "length_m": group["length_m"],
        "station_m": group["station_m"],
        "forward_attrs": capture(fwd_pair),
        "reverse_attrs": capture(rev_pair),
    }


def choose_templates(groups, segment_count, visum, copy_attrs):
    if segment_count <= 0:
        raise RuntimeError("Nova trasa nema zadny usek mezi uzly.")
    if len(groups) < segment_count:
        raise RuntimeError(
            "Puvodni geometry modification ma jen %d hlavnich fyzickych linku, "
            "ale nova topologie potrebuje %d useku."
            % (len(groups), segment_count)
        )

    # Kratke interní linky MUK zahodime: vezmeme presne N nejdelsich.
    selected = sorted(groups, key=lambda g: g["length_m"], reverse=True)[:segment_count]
    selected.sort(key=lambda g: g["station_m"])

    templates = [capture_link_template(visum, g, copy_attrs) for g in selected]
    return templates


def allocate_nodes(visum, count, start_no):
    existing = all_node_nos(visum)
    out = []
    candidate = int(start_no)
    while len(out) < count:
        candidate += 1
        if candidate not in existing and candidate not in out:
            out.append(candidate)
    return out


def add_simple_nodes(visum, node_nos, points):
    report = []
    for no, p in zip(node_nos, points):
        node = visum.Net.AddNode(int(no))
        node.SetAttValue("XCOORD", float(p["xy"][0]))
        node.SetAttValue("YCOORD", float(p["xy"][1]))
        report.append({
            "node_no": int(no),
            "point_order": p["order"],
            "x": p["xy"][0],
            "y": p["xy"][1],
            "station_m": p["station_m"],
            "source_offset_m": p["offset_m"],
        })
    return report


def add_simple_links(visum, templates, node_nos, points, route, copy_attrs):
    rcum = cumulative(route)
    base_link_nos = all_link_nos(visum)
    report = []
    geometry_plan = []

    for i, template in enumerate(templates):
        link_no = int(template["link_no"])
        if link_no in base_link_nos:
            raise RuntimeError(
                "LinkNo %d uz existuje v base version. Nelze ho bezpecne znovu pouzit "
                "pro jednoduchy koridor. Zastavuji pred zapisem." % link_no
            )

        a_no = int(node_nos[i])
        b_no = int(node_nos[i + 1])
        a = visum.Net.Nodes.ItemByKey(a_no)
        b = visum.Net.Nodes.ItemByKey(b_no)

        new_link = visum.Net.AddLink(link_no, a, b)

        warnings = []
        fwd = None
        rev = None
        try:
            fwd = visum.Net.Links.ItemByKey(a_no, b_no)
        except Exception:
            fwd = new_link
        try:
            rev = visum.Net.Links.ItemByKey(b_no, a_no)
        except Exception:
            rev = None

        for name in copy_attrs:
            safe_set(fwd, name, template["forward_attrs"].get(name), warnings)
        if rev is not None:
            for name in copy_attrs:
                safe_set(rev, name, template["reverse_attrs"].get(name), warnings)

        s0 = points[i]["station_m"]
        s1 = points[i + 1]["station_m"]
        course = subline(route, rcum, s0, s1)
        course[0] = points[i]["xy"]
        course[-1] = points[i + 1]["xy"]

        length_m = line_length(course)
        length_unit = length_m * 0.001
        safe_set(fwd, "LENGTH", length_unit, warnings)
        if rev is not None:
            safe_set(rev, "LENGTH", length_unit, warnings)

        geometry_plan.append({
            "link_no": link_no,
            "from_node": a_no,
            "to_node": b_no,
            "course": course,
            "length_m": length_m,
        })

        report.append({
            "link_no": link_no,
            "from_node": a_no,
            "to_node": b_no,
            "length_m": length_m,
            "template_length_m": template["length_m"],
            "template_station_m": template["station_m"],
            "warnings": " | ".join(warnings),
        })

    return report, geometry_plan


def write_linkpoly_net(path, plan):
    lines = [
        "$VISION",
        "* generated by visum_bulk_replace_geometry.py",
        "$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT;VISUMVERSIONNO",
        "19;Net;ENG;KM;2026.01",
        "",
        "$LINKPOLY:FROMNODENO;TONODENO;INDEX;XCOORD;YCOORD;ZCOORD",
    ]

    for item in plan:
        pts = item["course"]
        interior = pts[1:-1]
        if not interior:
            a = pts[0]
            b = pts[-1]
            interior = [((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)]

        for idx, p in enumerate(interior, 1):
            lines.append(
                "%d;%d;%d;%.6f;%.6f;0"
                % (item["from_node"], item["to_node"], idx, p[0], p[1])
            )

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def verify_simple_network(visum, node_report, link_report, geometry_plan, tol):
    for r in node_report:
        xy = node_xy(visum, r["node_no"])
        err = dist(xy, (r["x"], r["y"]))
        if err > tol:
            raise RuntimeError(
                "Uzel %d nema po importu spravnou polohu, chyba %.3f m"
                % (r["node_no"], err)
            )

    for item in geometry_plan:
        link = visum.Net.Links.ItemByKey(item["from_node"], item["to_node"])
        actual = parse_wkt_line(link.AttValue("WKTPOLY"))
        if len(actual) < 2:
            raise RuntimeError(
                "Nelze overit WKTPOLY linku %d (%d->%d)"
                % (item["link_no"], item["from_node"], item["to_node"])
            )

        length_error = abs(line_length(actual) - item["length_m"])
        start_error = dist(actual[0], item["course"][0])
        end_error = dist(actual[-1], item["course"][-1])
        if start_error > tol or end_error > tol or length_error > max(tol, item["length_m"] * 0.002):
            raise RuntimeError(
                "Geometrie linku %d neprošla kontrolou: start %.2f m, end %.2f m, delka %.2f m"
                % (item["link_no"], start_error, end_error, length_error)
            )


def save_csv(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    fields = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def atomic_copy(src, dst):
    tmp = Path(str(dst) + ".tmp_geometry")
    shutil.copy2(str(src), str(tmp))
    os.replace(str(tmp), str(dst))


def process(visum, base_ver, tra, route_file, nodes_file, m, cfg, work):
    print("\n=== %s ===" % m["code"])

    route = load_geojson_line(route_file)
    points = load_geojson_points(nodes_file, route) if nodes_file and Path(nodes_file).exists() else []

    if len(points) < 2:
        if bool(m.get("skip_if_no_nodes", False)):
            print(
                "SKIP: %s nema alespon 2 autoritativni body z velkych kruhu."
                % m["code"]
            )
            return {
                "status": "SKIPPED",
                "code": m["code"],
                "reason": "less_than_2_authoritative_nodes",
                "tra": str(tra),
                "route": str(route_file),
                "nodes_file": str(nodes_file) if nodes_file else None,
            }
        raise RuntimeError(
            "%s: potrebuji alespon 2 body z velkych kruhu, nalezeno %d."
            % (m["code"], len(points))
        )

    segment_count = len(points) - 1
    copy_attrs = cfg.get("copy_link_attributes", DEFAULT_COPY_LINK_ATTRIBUTES)

    # Faze A: stara geometry modification je pouze sablona pro hlavni LINKy.
    visum.IO.LoadVersion(str(base_ver))
    visum.ApplyModelTransferFile(str(tra))

    groups = selected_main_groups(visum, m, route)
    templates = choose_templates(groups, segment_count, visum, copy_attrs)

    print(
        "sablona: %d hlavnich fyzickych linku -> vybrano %d nejdelsich pro %d novych useku"
        % (len(groups), len(templates), segment_count)
    )
    print(
        "LinkNo noveho koridoru: %s"
        % ", ".join(str(t["link_no"]) for t in templates)
    )

    # Faze B: cista base version. Zadna stara rampa se sem neprenese.
    visum.IO.LoadVersion(str(base_ver))

    node_nos = allocate_nodes(
        visum,
        len(points),
        int(m.get("node_number_base", DEFAULT_NODE_BASES[m["code"]])),
    )

    node_report = add_simple_nodes(visum, node_nos, points)
    link_report, geometry_plan = add_simple_links(
        visum,
        templates,
        node_nos,
        points,
        route,
        copy_attrs,
    )

    patch = work / "linkpoly_patch" / (m["code"] + ".net")
    write_linkpoly_net(patch, geometry_plan)
    visum.IO.LoadNet(str(patch), True)

    tol = float(cfg.get("geometry_verification_tolerance_m", 2.0))
    verify_simple_network(visum, node_report, link_report, geometry_plan, tol)

    canonical = work / "canonical_tra" / Path(tra).name
    canonical.parent.mkdir(parents=True, exist_ok=True)

    visum.GenerateModelTransferFile(
        str(base_ver),
        str(canonical),
        "",
        False,
        False,
        True,
        True,
    )

    if not canonical.exists() or canonical.stat().st_size < 100:
        raise RuntimeError("Nevznikla platna canonical TRA: %s" % canonical)

    # Nezavisla kontrola: base + nova TRA.
    visum.IO.LoadVersion(str(base_ver))
    visum.ApplyModelTransferFile(str(canonical))
    verify_simple_network(visum, node_report, link_report, geometry_plan, tol)

    for r in node_report:
        r["modification"] = m["code"]
    for r in link_report:
        r["modification"] = m["code"]

    return {
        "status": "PASS",
        "code": m["code"],
        "tra": str(tra),
        "canonical": str(canonical),
        "route": str(route_file),
        "nodes_file": str(nodes_file),
        "authoritative_nodes": len(points),
        "new_main_links": len(link_report),
        "new_node_nos": node_nos,
        "new_link_nos": [r["link_no"] for r in link_report],
        "nodes": node_report,
        "links": link_report,
    }


def main():
    cfg = load_config()
    mode = cfg.get("mode", "dry-run")
    if mode not in ("dry-run", "apply"):
        raise RuntimeError("mode musi byt dry-run nebo apply")

    project = path_resolve(cfg["project_dir"], SCRIPT_DIR)
    base_ver = path_resolve(cfg.get("base_version", "R1_VARIANTYBase.ver"), project)
    tra_dir = path_resolve(cfg.get("tra_dir", "."), project)
    routes_dir = path_resolve(cfg.get("routes_dir", "routes"), SCRIPT_DIR)
    work_root = path_resolve(cfg.get("work_dir", "_geometry_update_work"), project)
    backup_root = path_resolve(cfg.get("backup_dir", "backup_geometry_modifications"), project)

    if not base_ver.exists():
        raise RuntimeError("Nenalezena base version: %s" % base_ver)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = work_root / (stamp + "_" + mode.replace("-", "_"))
    work.mkdir(parents=True, exist_ok=False)

    resolved = []
    for m in cfg["modifications"]:
        for key in ("code", "tra", "route", "color", "identity"):
            if key not in m:
                raise RuntimeError("Chybi %s v modification %r" % (key, m))

        tra = path_resolve(m["tra"], tra_dir)
        route = path_resolve(m["route"], routes_dir)

        if m.get("nodes"):
            nodes_file = path_resolve(m["nodes"], routes_dir)
        else:
            inferred = Path(m["route"]).stem + "_velke_kruhy_body_snap.geojson"
            nodes_file = path_resolve(inferred, routes_dir)

        if not tra.exists():
            raise RuntimeError("Nenalezena TRA: %s" % tra)
        if not route.exists():
            raise RuntimeError("Nenalezena trasa: %s" % route)

        resolved.append((m, tra, route, nodes_file))

    print("R1 VISUM simple intersection corridor")
    print("SCRIPT_VERSION: %s" % SCRIPT_VERSION)
    print("SCRIPT_FILE: %s" % Path(__file__).resolve())
    print("CONFIG_FILE: %s" % CONFIG)
    print("mode: %s" % mode)
    print("base: %s" % base_ver)
    print("")
    print("Rampy / MUK vetve / turny / lanes: NEIMPORTUJI SE.")
    print("Velke kruhy = nove samostatne uzly.")
    print("Useky mezi kruhy = nove hlavni linky.")
    print("")

    print("PREFLIGHT:")
    for m, tra, route, nodes_file in resolved:
        npts = 0
        if nodes_file.exists():
            try:
                npts = len(load_geojson_points(nodes_file, load_geojson_line(route)))
            except Exception:
                npts = -1
        print(
            "  %s | TRA=%s | ROUTE=%s | NODES=%s | POINTS=%s"
            % (m["code"], tra, route, nodes_file, npts)
        )

    visum = get_visum()
    results = []
    all_nodes = []
    all_links = []

    for m, tra, route, nodes_file in resolved:
        result = process(visum, base_ver, tra, route, nodes_file, m, cfg, work)
        results.append(result)
        all_nodes.extend(result.pop("nodes", []))
        all_links.extend(result.pop("links", []))

    report = work / "report"
    save_csv(report / "new_nodes.csv", all_nodes)
    save_csv(report / "new_links.csv", all_links)

    statuses = [r["status"] for r in results]
    overall = "PASS" if all(x == "PASS" for x in statuses) else "PASS_WITH_SKIPS"

    summary = {
        "status": overall,
        "mode": mode,
        "timestamp": stamp,
        "script_version": SCRIPT_VERSION,
        "base_version": str(base_ver),
        "vpdb_modified": False,
        "ramps_imported": False,
        "results": results,
    }
    (report / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if mode == "dry-run":
        print("\nDRY-RUN %s. Puvodni TRA nebyly zmeneny." % overall)
        print("Report: %s" % report)
        return

    backup = backup_root / stamp
    backup.mkdir(parents=True, exist_ok=False)

    for m, tra, route, nodes_file in resolved:
        shutil.copy2(str(tra), str(backup / tra.name))

    try:
        for result in results:
            if result["status"] != "PASS":
                continue
            atomic_copy(Path(result["canonical"]), Path(result["tra"]))
    except Exception:
        for m, tra, route, nodes_file in resolved:
            src = backup / tra.name
            if src.exists():
                shutil.copy2(str(src), str(tra))
        raise

    print("\nAPPLY %s." % overall)
    print("Zaloha: %s" % backup)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        tb = traceback.format_exc()
        print("\nFAIL: %s" % exc)

        error_file = SCRIPT_DIR / "LAST_ERROR.txt"
        try:
            error_file.write_text(tb, encoding="utf-8")
        except Exception:
            pass

        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Verze: %s\nSkript: %s\n\nChyba:\n%s\n\nDetail: %s"
                % (SCRIPT_VERSION, Path(__file__).resolve(), exc, error_file),
                "R1 VISUM geometry import - ERROR",
                0x10,
            )
        except Exception:
            pass
