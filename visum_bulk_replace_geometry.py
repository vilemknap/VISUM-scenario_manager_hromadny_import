# -*- coding: utf-8 -*-
"""
PTV Visum 2026 - hromadna vymena geometrie hlavni trasy v geometry modifications.

Bezpecnost:
- nikdy neupravuje .vpdb
- vychozi mode = dry-run
- zachovava NodeNo a Link keys
- pred APPLY vytvari zalohu puvodnich .tra
- po vytvoreni nove .tra ji znovu aplikuje na cistou base version a overi geometrii

Spusteni:
PTV Visum 2026 -> Scripts -> Run script file...
Konfigurace: config.json ve stejne slozce jako tento skript.
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


def norm(v):
    return "" if v is None else str(v).strip().casefold()


def d(a, b):
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def line_len(c):
    return sum(d(a, b) for a, b in zip(c, c[1:]))


def cumlen(c):
    out = [0.0]
    s = 0.0
    for a, b in zip(c, c[1:]):
        s += d(a, b)
        out.append(s)
    return out


def point_at(c, cum, s):
    if s <= 0:
        return tuple(c[0])
    if s >= cum[-1]:
        return tuple(c[-1])
    for i in range(len(c) - 1):
        if cum[i] <= s <= cum[i + 1]:
            den = cum[i + 1] - cum[i]
            t = 0.0 if den == 0 else (s - cum[i]) / den
            ax, ay = c[i]
            bx, by = c[i + 1]
            return (ax + t * (bx - ax), ay + t * (by - ay))
    return tuple(c[-1])


def project(c, cum, p):
    px, py = p
    best = (1e100, 0.0, tuple(c[0]))
    for i, (a, b) in enumerate(zip(c, c[1:])):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else ((px - ax) * dx + (py - ay) * dy) / den
        t = max(0.0, min(1.0, t))
        q = (ax + t * dx, ay + t * dy)
        dd = d(p, q)
        if dd < best[0]:
            best = (dd, cum[i] + t * math.sqrt(den), q)
    return best[1], best[2], best[0]


def subline(c, cum, s0, s1):
    rev = s1 < s0
    a, b = (s1, s0) if rev else (s0, s1)
    pts = [point_at(c, cum, a)]
    for i in range(1, len(c) - 1):
        if a < cum[i] < b:
            pts.append(tuple(c[i]))
    pts.append(point_at(c, cum, b))
    clean = []
    for p in pts:
        if not clean or d(clean[-1], p) > 1e-7:
            clean.append(p)
    if rev:
        clean.reverse()
    return clean


def parse_wkt_line(s):
    s = str(s or "").strip()
    if "(" not in s or ")" not in s:
        return []
    body = s[s.find("(") + 1:s.rfind(")")]
    pts = []
    for token in body.split(","):
        p = token.strip().split()
        if len(p) >= 2:
            try:
                pts.append((float(p[0]), float(p[1])))
            except Exception:
                return []
    return pts


def load_geojson(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    lines = []
    for f in feats:
        g = f.get("geometry", f)
        if not g:
            continue
        if g.get("type") == "LineString":
            lines.append([(float(x), float(y)) for x, y, *rest in g["coordinates"]])
        elif g.get("type") == "MultiLineString":
            for part in g["coordinates"]:
                lines.append([(float(x), float(y)) for x, y, *rest in part])
    lines = [x for x in lines if len(x) >= 2]
    if not lines:
        raise RuntimeError("GeoJSON nema LineString: %s" % path)
    return max(lines, key=line_len)


DEFAULT_IDENTITIES = {
    "G_W1_oprava": "14AF",
    "G_W2": "1E11",
    "G_W3": "26B3",
    "G_W4": "2E29",
    "G_W5": "3156",
}


def _repair_windows_path_values(raw):
    # Compatibility for configs written as C:\\folder\\file instead of valid JSON
    # C:/folder/file or C:\\\\folder\\\\file.
    import re
    keys = ("project_dir", "tra_dir", "routes_dir", "work_dir", "backup_dir", "base_version")
    out = raw
    for key in keys:
        pattern = r'("' + re.escape(key) + r'"\\s*:\\s*")([^"]*)(")'
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
        repaired = _repair_windows_path_values(raw)
        try:
            cfg = json.loads(repaired)
            print("WARNING: config.json obsahoval Windows cesty s jednoduchym \\. "
                  "Skript je automaticky prevedl na /.")
        except Exception:
            raise RuntimeError(
                "config.json neni platny JSON. U Windows cest pouzij C:/R1_VARIANTY "
                "nebo C:\\\\R1_VARIANTY. Puvodni chyba: %s" % first_exc
            )

    mods = cfg.get("modifications", [])
    if not isinstance(mods, list) or not mods:
        raise RuntimeError("config.json neobsahuje pole modifications.")

    for m in mods:
        if "identity" not in m or not str(m.get("identity", "")).strip():
            code = m.get("code")
            if code in DEFAULT_IDENTITIES:
                m["identity"] = DEFAULT_IDENTITIES[code]
                print("WARNING: %s nema identity; doplnuji %s."
                      % (code, m["identity"]))
            else:
                raise RuntimeError(
                    "Chybi identity u modification %r a neni znamy bezpecny default." % m
                )
    return cfg


def path_resolve(v, base):
    p = Path(str(v))
    return p if p.is_absolute() else (Path(base) / p).resolve()


def get_visum():
    if globals().get("Visum") is not None:
        return globals()["Visum"]
    try:
        import win32com.client
        return win32com.client.Dispatch("Visum.Visum")
    except Exception as exc:
        raise RuntimeError(
            "Nelze ziskat Visum COM. Spust skript z PTV Visum 2026 -> Scripts -> Run script file..."
        ) from exc


def attr_exists(container, name):
    try:
        return bool(container.AttrExists(name))
    except Exception:
        try:
            container.GetMultipleAttributes([name])
            return True
        except Exception:
            return False


def selected_links(visum, cfg):
    links = visum.Net.Links
    type_attr = cfg["route_type_attribute"]
    id_attr = cfg["route_identity_attribute"]
    color_attr = cfg.get("route_color_attribute", "BARVA")

    for a in (type_attr, id_attr):
        if not attr_exists(links, a):
            raise RuntimeError("Chybi link atribut %s" % a)

    attrs = ["FROMNODENO", "TONODENO", type_attr, id_attr]
    use_color = attr_exists(links, color_attr)
    if use_color:
        attrs.append(color_attr)

    rows = links.GetMultipleAttributes(attrs)
    out = []
    for r in rows:
        if norm(r[2]) != norm(cfg["route_type_value"]):
            continue
        if norm(r[3]) != norm(cfg["identity"]):
            continue
        out.append((int(r[0]), int(r[1])))

    if not out:
        raise RuntimeError(
            "Nenalezeny linky pro %s=%s, %s=%s"
            % (type_attr, cfg["route_type_value"], id_attr, cfg["identity"])
        )
    return out


def physical_edges(directed):
    x = {}
    for a, b in directed:
        x.setdefault((min(a, b), max(a, b)), []).append((a, b))
    return x


def order_path(edges):
    adj = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    branch = {n: len(v) for n, v in adj.items() if len(v) > 2}
    if branch:
        raise RuntimeError("Hlavni trasa je rozvetvena: %r" % branch)

    ends = [n for n, v in adj.items() if len(v) == 1]
    if len(ends) != 2:
        raise RuntimeError("Ocekavany 2 koncove uzly, nalezeno %d: %r" % (len(ends), ends))

    start = sorted(ends)[0]
    out = [start]
    prev = None
    cur = start
    used = set()
    while True:
        opts = [n for n in adj[cur] if n != prev]
        if not opts:
            break
        if len(opts) != 1:
            raise RuntimeError("Nejednoznacne pokracovani v uzlu %s" % cur)
        nxt = opts[0]
        e = (min(cur, nxt), max(cur, nxt))
        if e in used:
            raise RuntimeError("Cyklus v hlavni trase")
        used.add(e)
        out.append(nxt)
        prev, cur = cur, nxt

    if len(used) != len(edges):
        raise RuntimeError("Hlavni trasa neni souvisla")
    return out


def node_xy(visum, n):
    o = visum.Net.Nodes.ItemByKey(int(n))
    return float(o.AttValue("XCOORD")), float(o.AttValue("YCOORD"))


def orient(target, a, b):
    f = d(a, target[0]) + d(b, target[-1])
    r = d(a, target[-1]) + d(b, target[0])
    return list(reversed(target)) if r < f else list(target)


def map_nodes(visum, nodes, target, max_move, endpoint_limit, min_spacing, backtrack_tol):
    old = {n: node_xy(visum, n) for n in nodes}
    target = orient(target, old[nodes[0]], old[nodes[-1]])
    cum = cumlen(target)
    total = cum[-1]

    mapped = []
    for i, n in enumerate(nodes):
        if i == 0:
            s, q, move = 0.0, target[0], d(old[n], target[0])
        elif i == len(nodes) - 1:
            s, q, move = total, target[-1], d(old[n], target[-1])
        else:
            s, q, move = project(target, cum, old[n])
        mapped.append({
            "node_no": n, "old_x": old[n][0], "old_y": old[n][1],
            "new_x": q[0], "new_y": q[1], "station_m": s, "snap_m": move
        })

    if mapped[0]["snap_m"] > endpoint_limit or mapped[-1]["snap_m"] > endpoint_limit:
        raise RuntimeError("Koncove body nove trasy jsou prilis daleko od stavajici trasy")

    worst = max(mapped, key=lambda x: x["snap_m"])
    if worst["snap_m"] > max_move:
        raise RuntimeError(
            "Uzel %s by se posunul %.1f m, limit je %.1f m"
            % (worst["node_no"], worst["snap_m"], max_move)
        )

    for a, b in zip(mapped, mapped[1:]):
        if b["station_m"] + backtrack_tol < a["station_m"]:
            raise RuntimeError("Mapovani uzlu neni monotoni na nove ose")

    for i in range(1, len(mapped) - 1):
        minimum = mapped[i - 1]["station_m"] + min_spacing
        if mapped[i]["station_m"] < minimum:
            mapped[i]["station_m"] = minimum
            mapped[i]["new_x"], mapped[i]["new_y"] = point_at(target, cum, minimum)

    for a, b in zip(mapped, mapped[1:]):
        if b["station_m"] - a["station_m"] < min_spacing:
            raise RuntimeError("Dva uzly se na nove trase prekryvaji")

    return target, cum, mapped


def set_nodes(visum, mapped):
    for r in mapped:
        n = visum.Net.Nodes.ItemByKey(int(r["node_no"]))
        n.SetAttValue("XCOORD", float(r["new_x"]))
        n.SetAttValue("YCOORD", float(r["new_y"]))


def choose_dir(edge_map, a, b):
    dirs = edge_map[(min(a, b), max(a, b))]
    return (a, b) if (a, b) in dirs else (b, a)


def build_plan(nodes, edge_map, mapped, target, cum):
    by = {r["node_no"]: r for r in mapped}
    out = []
    for a, b in zip(nodes, nodes[1:]):
        s0, s1 = by[a]["station_m"], by[b]["station_m"]
        course = subline(target, cum, s0, s1)
        wf, wt = choose_dir(edge_map, a, b)
        if (wf, wt) != (a, b):
            course = list(reversed(course))
        interior = course[1:-1]
        if not interior:
            p0, p1 = course[0], course[-1]
            interior = [((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)]
        out.append({
            "path_from": a, "path_to": b,
            "write_from": wf, "write_to": wt,
            "course": course, "interior": interior,
            "length_m": line_len(course),
            "directions": edge_map[(min(a, b), max(a, b))]
        })
    return out


def write_net(path, plan):
    lines = [
        "$VISION",
        "* generated by visum_bulk_replace_geometry.py",
        "$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT;VISUMVERSIONNO",
        "19;Net;ENG;KM;2026.01",
        "",
        "$LINKPOLY:FROMNODENO;TONODENO;INDEX;XCOORD;YCOORD;ZCOORD",
    ]
    for x in plan:
        for i, (px, py) in enumerate(x["interior"], 1):
            lines.append("%d;%d;%d;%.6f;%.6f;0" % (
                x["write_from"], x["write_to"], i, px, py
            ))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def set_lengths(visum, plan, scale):
    for x in plan:
        val = x["length_m"] * scale
        for a, b in x["directions"]:
            visum.Net.Links.ItemByKey(int(a), int(b)).SetAttValue("LENGTH", val)


def verify_nodes(visum, mapped, tol):
    for r in mapped:
        err = d(node_xy(visum, r["node_no"]), (r["new_x"], r["new_y"]))
        if err > tol:
            raise RuntimeError("Nesedi poloha uzlu %s (chyba %.3f m)" % (r["node_no"], err))


def verify_links(visum, plan, tol):
    rows = []
    for x in plan:
        link = visum.Net.Links.ItemByKey(int(x["write_from"]), int(x["write_to"]))
        actual = parse_wkt_line(link.AttValue("WKTPOLY"))
        if len(actual) < 2:
            raise RuntimeError("Nelze precist WKTPOLY %s->%s" % (x["write_from"], x["write_to"]))
        exp = x["course"]
        le = abs(line_len(actual) - x["length_m"])
        se = d(actual[0], exp[0])
        ee = d(actual[-1], exp[-1])
        ok = se <= tol and ee <= tol and le <= max(tol, x["length_m"] * 0.002)
        rows.append({
            "from_node": x["write_from"], "to_node": x["write_to"],
            "expected_length_m": x["length_m"], "actual_length_m": line_len(actual),
            "length_error_m": le, "start_error_m": se, "end_error_m": ee, "ok": ok
        })
        if not ok:
            raise RuntimeError("Validace WKTPOLY selhala %s->%s" % (x["write_from"], x["write_to"]))
    return rows


def save_csv(path, rows, fields):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def atomic_copy(src, dst):
    tmp = Path(str(dst) + ".tmp_geometry")
    shutil.copy2(str(src), str(tmp))
    os.replace(str(tmp), str(dst))


def process(visum, base_ver, tra, route, m, global_cfg, work):
    print("\n=== %s ===" % m["code"])
    visum.IO.LoadVersion(str(base_ver))
    visum.ApplyModelTransferFile(str(tra))

    sel_cfg = {
        "route_type_attribute": global_cfg.get("route_type_attribute", "TYP"),
        "route_type_value": global_cfg.get("route_type_value", "hlavni_krivka"),
        "route_identity_attribute": global_cfg.get("route_identity_attribute", "ENTITYHAND"),
        "route_color_attribute": global_cfg.get("route_color_attribute", "BARVA"),
        "identity": m["identity"],
    }
    directed = selected_links(visum, sel_cfg)
    edges = physical_edges(directed)
    nodes = order_path(edges)
    print("trasa: %d fyzickych linku, %d uzlu" % (len(edges), len(nodes)))

    target = load_geojson(route)
    target, cum, mapped = map_nodes(
        visum, nodes, target,
        float(global_cfg.get("max_route_node_snap_m", 3000.0)),
        float(global_cfg.get("max_endpoint_snap_m", 3000.0)),
        float(global_cfg.get("minimum_route_node_spacing_m", 0.25)),
        float(global_cfg.get("max_station_backtrack_m", 50.0)),
    )
    print("max posun uzlu: %.1f m" % max(x["snap_m"] for x in mapped))

    set_nodes(visum, mapped)
    plan = build_plan(nodes, edges, mapped, target, cum)

    patch = work / "linkpoly_patch" / (m["code"] + ".net")
    write_net(patch, plan)
    visum.IO.LoadNet(str(patch), True)

    if global_cfg.get("set_link_length_from_geometry", True):
        set_lengths(visum, plan, float(global_cfg.get("coordinate_m_to_length_unit", 0.001)))

    tol = float(global_cfg.get("geometry_verification_tolerance_m", 2.0))
    verify_nodes(visum, mapped, tol)
    verify_links(visum, plan, tol)

    canonical = work / "canonical_tra" / Path(tra).name
    canonical.parent.mkdir(parents=True, exist_ok=True)
    visum.GenerateModelTransferFile(str(base_ver), str(canonical), "", False, False, True, True)
    if not canonical.exists() or canonical.stat().st_size < 100:
        raise RuntimeError("Nevznikla platna canonical TRA: %s" % canonical)

    # Nezavisla kontrola nove TRA na ciste base version.
    visum.IO.LoadVersion(str(base_ver))
    visum.ApplyModelTransferFile(str(canonical))
    verify_nodes(visum, mapped, tol)
    link_rows = verify_links(visum, plan, tol)

    for r in mapped:
        r["modification"] = m["code"]
        r["color"] = m["color"]
    for r in link_rows:
        r["modification"] = m["code"]
        r["color"] = m["color"]

    return {
        "code": m["code"], "tra": str(tra), "canonical": str(canonical),
        "route": str(route), "nodes": mapped, "links": link_rows,
        "max_node_move_m": max(x["snap_m"] for x in mapped)
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
        if not tra.exists():
            raise RuntimeError("Nenalezena TRA: %s" % tra)
        if not route.exists():
            raise RuntimeError("Nenalezena trasa: %s" % route)
        resolved.append((m, tra, route))

    print("R1 VISUM bulk geometry replacement")
    print("mode: %s" % mode)
    print("base: %s" % base_ver)
    print("work: %s" % work)
    print("VPDB se timto skriptem nikdy nemeni.")

    visum = get_visum()
    results = []
    all_nodes = []
    all_links = []

    for m, tra, route in resolved:
        r = process(visum, base_ver, tra, route, m, cfg, work)
        results.append(r)
        all_nodes.extend(r.pop("nodes"))
        all_links.extend(r.pop("links"))

    report = work / "report"
    save_csv(report / "route_node_moves.csv", all_nodes,
             ["modification", "color", "node_no", "old_x", "old_y",
              "new_x", "new_y", "station_m", "snap_m"])
    save_csv(report / "link_geometry_validation.csv", all_links,
             ["modification", "color", "from_node", "to_node",
              "expected_length_m", "actual_length_m", "length_error_m",
              "start_error_m", "end_error_m", "ok"])

    summary = {
        "status": "PASS", "mode": mode, "timestamp": stamp,
        "base_version": str(base_ver), "vpdb_modified": False,
        "results": results
    }
    (report / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if mode == "dry-run":
        print("\nDRY-RUN PASS. Puvodni TRA nebyly zmeneny.")
        print("Report: %s" % report)
        return

    backup = backup_root / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for _m, tra, _route in resolved:
        shutil.copy2(str(tra), str(backup / tra.name))

    try:
        for r in results:
            atomic_copy(Path(r["canonical"]), Path(r["tra"]))
    except Exception:
        for _m, tra, _route in resolved:
            src = backup / tra.name
            if src.exists():
                shutil.copy2(str(src), str(tra))
        raise

    print("\nAPPLY PASS. Nove geometry modifications byly ulozeny.")
    print("Zaloha: %s" % backup)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nFAIL: %s" % exc)
        traceback.print_exc()
        raise
