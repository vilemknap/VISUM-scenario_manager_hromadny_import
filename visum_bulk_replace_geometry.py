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



def load_geojson_points(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    out = []
    for f in feats:
        g = f.get("geometry", f)
        if not g or g.get("type") != "Point":
            continue
        c = g.get("coordinates", [])
        if len(c) >= 2:
            props = f.get("properties", {}) if isinstance(f, dict) else {}
            out.append({
                "xy": (float(c[0]), float(c[1])),
                "order": props.get("poradi_po_trase"),
                "station": props.get("stacionaz_m"),
            })
    return out


def parse_tra_touched_nodes(path):
    touched = set()
    lines = Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines()
    for i, line in enumerate(lines):
        if not (line.startswith("$+NODE:") or line.startswith("$*NODE:")):
            continue
        hdr = line.split(":", 1)[1].split(";")
        try:
            no_idx = hdr.index("NO")
        except ValueError:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].startswith("$"):
            row = lines[j].strip()
            if row and not row.startswith("*"):
                vals = row.split(";")
                if no_idx < len(vals) and vals[no_idx].strip():
                    try:
                        touched.add(int(float(vals[no_idx])))
                    except Exception:
                        pass
            j += 1
    return touched


def selected_physical_groups(visum, cfg, target):
    links = visum.Net.Links
    type_attr = cfg["route_type_attribute"]
    id_attr = cfg["route_identity_attribute"]
    for a in (type_attr, id_attr):
        if not attr_exists(links, a):
            raise RuntimeError("Chybi link atribut %s" % a)

    attrs = ["NO", "FROMNODENO", "TONODENO", type_attr, id_attr]
    rows = links.GetMultipleAttributes(attrs)
    groups = {}
    for r in rows:
        if norm(r[3]) != norm(cfg["route_type_value"]):
            continue
        if norm(r[4]) != norm(cfg["identity"]):
            continue
        no = int(r[0])
        groups.setdefault(no, {"no": no, "dirs": []})
        groups[no]["dirs"].append((int(r[1]), int(r[2])))

    if not groups:
        raise RuntimeError(
            "Nenalezeny hlavni linky pro %s=%s, %s=%s"
            % (type_attr, cfg["route_type_value"], id_attr, cfg["identity"])
        )

    tcum = cumlen(target)
    for g in groups.values():
        a, b = g["dirs"][0]
        try:
            pts = parse_wkt_line(visum.Net.Links.ItemByKey(a, b).AttValue("WKTPOLY"))
        except Exception:
            pts = []
        if len(pts) < 2:
            pts = [node_xy(visum, a), node_xy(visum, b)]
        pcum = cumlen(pts)
        mid = point_at(pts, pcum, pcum[-1] / 2.0)
        station, _q, off = project(target, tcum, mid)
        g["old_mid"] = mid
        g["station"] = station
        g["offset"] = off

    out = sorted(groups.values(), key=lambda g: g["station"])
    return out


def split_target_by_authoritative_points(target, points, merge_tol=1.0):
    tcum = cumlen(target)
    total = tcum[-1]
    stations = []
    for p in points:
        st, _q, _off = project(target, tcum, p["xy"])
        if merge_tol < st < total - merge_tol:
            stations.append(st)
        elif st <= merge_tol:
            stations.append(0.0)
        else:
            stations.append(total)

    cuts = [0.0]
    for st in sorted(stations):
        if abs(st - cuts[-1]) > merge_tol:
            cuts.append(st)
    if abs(total - cuts[-1]) > merge_tol:
        cuts.append(total)
    else:
        cuts[-1] = total

    segs = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:]), 1):
        course = subline(target, tcum, a, b)
        segs.append({
            "candidate_id": i,
            "s0": a,
            "s1": b,
            "course": course,
            "mid": point_at(target, tcum, (a + b) / 2.0),
            "length_m": line_len(course),
        })
    return segs


def fallback_segments_from_old_topology(visum, groups, target):
    # Pouziva se jen tehdy, kdy pro variantu nejsou autoritativni velke kruhy
    # (v dodanem DXF je to aktualne oranzova). Zachova stare cleneni, ale uzly
    # promita na novou osu.
    tcum = cumlen(target)
    total = tcum[-1]
    raw = []
    for g in groups:
        for a, b in g["dirs"]:
            for n in (a, b):
                st, _q, _off = project(target, tcum, node_xy(visum, n))
                raw.append(st)

    raw = sorted(raw)
    clustered = []
    tol = 50.0
    for st in raw:
        if not clustered or abs(st - clustered[-1][-1]) > tol:
            clustered.append([st])
        else:
            clustered[-1].append(st)
    cuts = [sum(x) / len(x) for x in clustered]

    nseg = len(groups)
    if len(cuts) != nseg + 1:
        # Bezpecny fallback: hranice odvodime z projekci stredu starych fyzickych
        # linku. Je to stale lepsi nez vymyslet krizovatky, ale v reportu je tato
        # varianta oznacena jako fallback.
        mids = sorted(g["station"] for g in groups)
        cuts = [max(0.0, mids[0] - (mids[1] - mids[0]) / 2.0) if len(mids) > 1 else 0.0]
        for a, b in zip(mids, mids[1:]):
            cuts.append((a + b) / 2.0)
        cuts.append(min(total, mids[-1] + (mids[-1] - mids[-2]) / 2.0) if len(mids) > 1 else total)

    cuts[0] = max(0.0, cuts[0])
    cuts[-1] = min(total, cuts[-1])
    segs = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:]), 1):
        course = subline(target, tcum, a, b)
        segs.append({
            "candidate_id": i, "s0": a, "s1": b,
            "course": course,
            "mid": point_at(target, tcum, (a + b) / 2.0),
            "length_m": line_len(course),
        })
    return segs


def choose_candidate_window(groups, candidates):
    n = len(groups)
    m = len(candidates)
    if m < n:
        raise RuntimeError(
            "Nova topologie ma jen %d useku, ale stara geometry modification ma %d fyzickych hlavnih linku."
            % (m, n)
        )

    best = None
    for start in range(m - n + 1):
        win = candidates[start:start + n]
        cost = sum(d(g["old_mid"], seg["mid"]) for g, seg in zip(groups, win))
        if best is None or cost < best[0]:
            best = (cost, start, win)
    return best[2], best[1], best[0]


def assign_main_nodes(visum, groups, segments, touched_nodes):
    desired = {}
    link_binding = []

    for g, seg in zip(groups, segments):
        a, b = g["dirs"][0]
        pa = node_xy(visum, a)
        pb = node_xy(visum, b)
        s0 = tuple(seg["course"][0])
        s1 = tuple(seg["course"][-1])

        forward_cost = d(pa, s0) + d(pb, s1)
        reverse_cost = d(pa, s1) + d(pb, s0)
        if forward_cost <= reverse_cost:
            start_node, end_node = a, b
        else:
            start_node, end_node = b, a

        for n, q in ((start_node, s0), (end_node, s1)):
            if n in desired and d(desired[n], q) > 5.0:
                raise RuntimeError(
                    "Uzel %s dostal dve neslucitelne nove polohy (rozdil %.1f m)."
                    % (n, d(desired[n], q))
                )
            desired[n] = q

        link_binding.append({
            "group": g,
            "segment": seg,
            "start_node": start_node,
            "end_node": end_node,
        })

    old_xy = {n: node_xy(visum, n) for n in desired}
    final_xy = {}
    for n, q in desired.items():
        # Uzly pridane/menene puvodni geometry modification prebiraji polohu z
        # noveho CAD. Base/interface uzly zustavaji na svem miste a nova krivka
        # se na ne napoji.
        final_xy[n] = q if n in touched_nodes else old_xy[n]

    mapped = []
    for n in sorted(final_xy):
        old = old_xy[n]
        new = final_xy[n]
        mapped.append({
            "node_no": n,
            "old_x": old[0], "old_y": old[1],
            "new_x": new[0], "new_y": new[1],
            "snap_m": d(old, new),
            "role": "main",
            "is_touched_node": n in touched_nodes,
        })

    return link_binding, final_xy, mapped


def move_auxiliary_nodes(visum, touched_nodes, final_xy, mapped, radius):
    direct = {r["node_no"]: r for r in mapped}
    movers = [r for r in mapped if r["is_touched_node"] and r["snap_m"] > 1e-6]
    if not movers:
        return mapped

    old_direct = {r["node_no"]: (r["old_x"], r["old_y"]) for r in movers}
    delta = {
        r["node_no"]: (r["new_x"] - r["old_x"], r["new_y"] - r["old_y"])
        for r in movers
    }

    for n in sorted(touched_nodes):
        if n in direct:
            continue
        try:
            old = node_xy(visum, n)
        except Exception:
            continue
        nearest = min(movers, key=lambda r: d(old, old_direct[r["node_no"]]))
        dist0 = d(old, old_direct[nearest["node_no"]])
        if dist0 > radius:
            continue
        dx, dy = delta[nearest["node_no"]]
        new = (old[0] + dx, old[1] + dy)
        final_xy[n] = new
        mapped.append({
            "node_no": n,
            "old_x": old[0], "old_y": old[1],
            "new_x": new[0], "new_y": new[1],
            "snap_m": d(old, new),
            "role": "aux",
            "is_touched_node": True,
            "cluster_master": nearest["node_no"],
            "cluster_distance_m": dist0,
        })
    return mapped


def orient_course_for_nodes(course, from_node, to_node, start_node, end_node):
    if from_node == start_node and to_node == end_node:
        return list(course)
    if from_node == end_node and to_node == start_node:
        return list(reversed(course))
    # U neobvykle topologie rozhodneme podle koncovych souradnic.
    return list(course)


def make_plan_entry(from_node, to_node, course, link_no=None, kind="main"):
    if len(course) < 2:
        raise RuntimeError("Link %s->%s ma neplatnou geometrii." % (from_node, to_node))
    interior = list(course[1:-1])
    if not interior:
        p0, p1 = course[0], course[-1]
        interior = [((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)]
    return {
        "write_from": int(from_node),
        "write_to": int(to_node),
        "course": list(course),
        "interior": interior,
        "length_m": line_len(course),
        "directions": [(int(from_node), int(to_node))],
        "link_no": link_no,
        "kind": kind,
    }


def build_authoritative_main_plan(bindings, final_xy):
    plan = []
    main_pairs = set()
    main_nos = set()

    for b in bindings:
        g = b["group"]
        seg = b["segment"]
        start_node = b["start_node"]
        end_node = b["end_node"]
        base_course = list(seg["course"])
        base_course[0] = final_xy[start_node]
        base_course[-1] = final_xy[end_node]

        main_nos.add(g["no"])
        for a, c in g["dirs"]:
            course = orient_course_for_nodes(
                base_course, a, c, start_node, end_node
            )
            course[0] = final_xy.get(a, node_xy_cached_fallback(a, final_xy))
            course[-1] = final_xy.get(c, node_xy_cached_fallback(c, final_xy))
            plan.append(make_plan_entry(a, c, course, g["no"], "main"))
            main_pairs.add((a, c))
    return plan, main_pairs, main_nos


def node_xy_cached_fallback(node_no, final_xy):
    # Placeholder overwritten in process by ensuring every main endpoint exists
    # in final_xy. This branch should never be reached.
    if node_no in final_xy:
        return final_xy[node_no]
    raise RuntimeError("Chybi nova poloha uzlu %s." % node_no)


def warp_polyline(points, delta_from, delta_to):
    if len(points) < 2:
        return points
    pcum = cumlen(points)
    total = max(pcum[-1], 1e-9)
    out = []
    for p, s0 in zip(points, pcum):
        t = s0 / total
        dx = (1.0 - t) * delta_from[0] + t * delta_to[0]
        dy = (1.0 - t) * delta_from[1] + t * delta_to[1]
        out.append((p[0] + dx, p[1] + dy))
    return out


def build_adjacent_link_warp_plan(visum, mapped, main_pairs):
    move = {
        r["node_no"]: (
            r["new_x"] - r["old_x"],
            r["new_y"] - r["old_y"],
        )
        for r in mapped
        if r["snap_m"] > 1e-6
    }
    if not move:
        return []

    rows = visum.Net.Links.GetMultipleAttributes(["NO", "FROMNODENO", "TONODENO"])
    out = []
    seen = set()
    for no, a0, b0 in rows:
        a, b = int(a0), int(b0)
        if (a, b) in main_pairs:
            continue
        if a not in move and b not in move:
            continue
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        try:
            pts = parse_wkt_line(visum.Net.Links.ItemByKey(a, b).AttValue("WKTPOLY"))
        except Exception:
            pts = []
        if len(pts) < 2:
            pts = [node_xy(visum, a), node_xy(visum, b)]
        da = move.get(a, (0.0, 0.0))
        db = move.get(b, (0.0, 0.0))
        course = warp_polyline(pts, da, db)
        out.append(make_plan_entry(a, b, course, int(no), "adjacent"))
    return out


def physical_edges(directed):
    x = {}
    for a, b in directed:
        x.setdefault((min(a, b), max(a, b)), []).append((a, b))
    return x


def _edge_geometry_length(visum, edge_map, u, v):
    dirs = edge_map[(min(u, v), max(u, v))]
    a, b = dirs[0]
    try:
        wkt = visum.Net.Links.ItemByKey(int(a), int(b)).AttValue("WKTPOLY")
        pts = parse_wkt_line(wkt)
        if len(pts) >= 2:
            return line_len(pts)
    except Exception:
        pass
    return d(node_xy(visum, u), node_xy(visum, v))


def _dijkstra_main_path(visum, edge_map, target, start, end, max_backtrack_m):
    import heapq

    nodes = sorted({n for e in edge_map for n in e})
    xy = {n: node_xy(visum, n) for n in nodes}
    tcum = cumlen(target)
    proj = {}
    for n in nodes:
        st, _q, off = project(target, tcum, xy[n])
        proj[n] = (st, off)

    adj = {n: [] for n in nodes}
    for u, v in edge_map:
        mid = ((xy[u][0] + xy[v][0]) / 2.0, (xy[u][1] + xy[v][1]) / 2.0)
        _sm, _qm, off_mid = project(target, tcum, mid)
        geom_len = max(_edge_geometry_length(visum, edge_map, u, v), 0.01)

        for a, b in ((u, v), (v, u)):
            sa, off_a = proj[a]
            sb, off_b = proj[b]
            ds = sb - sa

            # Hlavni osa ma jit prevazne dopredu po nove trase.
            # Rampy/odbocky typicky maji velky bocni odstup nebo maly/negativni
            # postup ve staniceni. Nezakazujeme je natvrdo, jen je silne penalizujeme.
            backtrack = max(0.0, -ds - max_backtrack_m)
            station_mismatch = abs(abs(ds) - geom_len)
            off_penalty = 4.0 * (off_a + off_b + 2.0 * off_mid)
            mismatch_penalty = 2.0 * station_mismatch
            backtrack_penalty = 100.0 * backtrack
            weight = geom_len + off_penalty + mismatch_penalty + backtrack_penalty
            adj[a].append((b, weight))

    dist = {start: 0.0}
    prev = {}
    heap = [(0.0, start)]
    while heap:
        cur_d, u = heapq.heappop(heap)
        if cur_d != dist.get(u):
            continue
        if u == end:
            break
        for v, w in adj.get(u, []):
            nd = cur_d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if end not in dist:
        raise RuntimeError("Nenalezena souvisla hlavni cesta mezi krajnimi body nove trasy.")

    path = [end]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path, proj


def order_main_path_by_target(visum, edge_map, target, endpoint_limit, max_backtrack_m):
    nodes = sorted({n for e in edge_map for n in e})
    if len(nodes) < 2:
        raise RuntimeError("Vyber hlavni trasy obsahuje mene nez 2 uzly.")

    xy = {n: node_xy(visum, n) for n in nodes}

    start = min(nodes, key=lambda n: d(xy[n], target[0]))
    end = min(nodes, key=lambda n: d(xy[n], target[-1]))

    # Kdyby byl GeoJSON orientovan obracene, toto je stale v poradku:
    # start/end se berou z jeho skutecnych koncu a dalsi mapovani si orientaci
    # jeste jednou nezavisle overi.
    start_err = d(xy[start], target[0])
    end_err = d(xy[end], target[-1])

    if start == end:
        raise RuntimeError("Oba konce nove trasy se mapuji na stejny uzel %s." % start)
    if start_err > endpoint_limit or end_err > endpoint_limit:
        raise RuntimeError(
            "Konce nove trasy jsou prilis daleko od site: start %.1f m, end %.1f m, limit %.1f m"
            % (start_err, end_err, endpoint_limit)
        )

    path, proj = _dijkstra_main_path(
        visum, edge_map, target, start, end, max_backtrack_m
    )

    selected_edges = {
        (min(a, b), max(a, b)) for a, b in zip(path, path[1:])
    }
    excluded_edges = sorted(set(edge_map.keys()) - selected_edges)

    # Kontrola, ze vybrana cesta skutecne postupuje po nove ose a neskace
    # mezi vzdalenymi rampami.
    stations = [proj[n][0] for n in path]
    worst_backtrack = 0.0
    for a, b in zip(stations, stations[1:]):
        worst_backtrack = max(worst_backtrack, a - b)
    if worst_backtrack > max_backtrack_m:
        raise RuntimeError(
            "Nejlepsi nalezena cesta se vraci ve staniceni o %.1f m; limit je %.1f m."
            % (worst_backtrack, max_backtrack_m)
        )

    return path, selected_edges, excluded_edges


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


def process(visum, base_ver, tra, route, nodes_file, m, global_cfg, work):
    print("\n=== %s ===" % m["code"])
    visum.IO.LoadVersion(str(base_ver))
    visum.ApplyModelTransferFile(str(tra))

    target = load_geojson(route)
    touched_nodes = parse_tra_touched_nodes(tra)

    sel_cfg = {
        "route_type_attribute": global_cfg.get("route_type_attribute", "TYP"),
        "route_type_value": global_cfg.get("route_type_value", "hlavni_krivka"),
        "route_identity_attribute": global_cfg.get("route_identity_attribute", "ENTITYHAND"),
        "route_color_attribute": global_cfg.get("route_color_attribute", "BARVA"),
        "identity": m["identity"],
    }

    groups = selected_physical_groups(visum, sel_cfg, target)
    print("stara geometry modification: %d fyzickych hlavnih linku" % len(groups))

    points = []
    if nodes_file is not None and Path(nodes_file).exists():
        points = load_geojson_points(nodes_file)

    if points:
        candidates = split_target_by_authoritative_points(
            target,
            points,
            float(global_cfg.get("authoritative_point_merge_tolerance_m", 1.0)),
        )
        node_source = "large_circles"
        print(
            "nova topologie: %d autoritativnich velkych kruhu -> %d kandidatnich useku"
            % (len(points), len(candidates))
        )
    else:
        candidates = fallback_segments_from_old_topology(visum, groups, target)
        node_source = "old_topology_projection"
        print(
            "WARNING: pro %s nejsou velke kruhy; pouzivam fallback projekce stareho cleneni na novou osu."
            % m["code"]
        )

    segments, window_start, window_cost = choose_candidate_window(groups, candidates)
    print(
        "vybrano %d novych useku (okno od %d, match cost %.1f m)"
        % (len(segments), window_start + 1, window_cost)
    )

    bindings, final_xy, mapped = assign_main_nodes(
        visum, groups, segments, touched_nodes
    )

    mapped = move_auxiliary_nodes(
        visum,
        touched_nodes,
        final_xy,
        mapped,
        float(global_cfg.get("junction_cluster_radius_m", 2000.0)),
    )

    max_move = max((r["snap_m"] for r in mapped), default=0.0)
    if max_move > float(global_cfg.get("max_route_node_snap_m", 3000.0)):
        worst = max(mapped, key=lambda r: r["snap_m"])
        raise RuntimeError(
            "Uzel %s by se posunul %.1f m, limit je %.1f m"
            % (
                worst["node_no"],
                worst["snap_m"],
                float(global_cfg.get("max_route_node_snap_m", 3000.0)),
            )
        )

    # Rampy/navazujici linky deformujeme z puvodni geometrie, proto plan
    # sestavime jeste pred presunem uzlu.
    main_plan, main_pairs, _main_nos = build_authoritative_main_plan(
        bindings, final_xy
    )
    adjacent_plan = build_adjacent_link_warp_plan(visum, mapped, main_pairs)
    plan = main_plan + adjacent_plan

    set_nodes(visum, mapped)

    print(
        "vkladam: %d smerovych hlavnih krivek + %d navazujicich/rampovych krivek; "
        "%d uzlu ma novou polohu"
        % (len(main_plan), len(adjacent_plan), len(mapped))
    )

    patch = work / "linkpoly_patch" / (m["code"] + ".net")
    write_net(patch, plan)
    visum.IO.LoadNet(str(patch), True)

    if global_cfg.get("set_link_length_from_geometry", True):
        set_lengths(
            visum,
            plan,
            float(global_cfg.get("coordinate_m_to_length_unit", 0.001)),
        )

    tol = float(global_cfg.get("geometry_verification_tolerance_m", 2.0))
    verify_nodes(visum, mapped, tol)
    verify_links(visum, plan, tol)

    canonical = work / "canonical_tra" / Path(tra).name
    canonical.parent.mkdir(parents=True, exist_ok=True)
    visum.GenerateModelTransferFile(
        str(base_ver), str(canonical), "", False, False, True, True
    )
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
        "code": m["code"],
        "tra": str(tra),
        "canonical": str(canonical),
        "route": str(route),
        "nodes_file": str(nodes_file) if nodes_file else None,
        "node_source": node_source,
        "nodes": mapped,
        "links": link_rows,
        "max_node_move_m": max_move,
        "physical_main_links": len(groups),
        "candidate_segments": len(candidates),
        "selected_segment_window_start": window_start + 1,
        "selected_segment_window_cost_m": window_cost,
        "warped_adjacent_directional_links": len(adjacent_plan),
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
        nodes_file = None
        if m.get("nodes"):
            nodes_file = path_resolve(m["nodes"], routes_dir)
        if not tra.exists():
            raise RuntimeError("Nenalezena TRA: %s" % tra)
        if not route.exists():
            raise RuntimeError("Nenalezena trasa: %s" % route)
        if nodes_file is not None and not nodes_file.exists():
            raise RuntimeError("Nenalezen soubor autoritativnich uzlu: %s" % nodes_file)
        resolved.append((m, tra, route, nodes_file))

    print("R1 VISUM bulk geometry replacement")
    print("mode: %s" % mode)
    print("base: %s" % base_ver)
    print("work: %s" % work)
    print("VPDB se timto skriptem nikdy nemeni.")

    visum = get_visum()
    results = []
    all_nodes = []
    all_links = []

    for m, tra, route, nodes_file in resolved:
        r = process(visum, base_ver, tra, route, nodes_file, m, cfg, work)
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
    for _m, tra, _route, _nodes in resolved:
        shutil.copy2(str(tra), str(backup / tra.name))

    try:
        for r in results:
            atomic_copy(Path(r["canonical"]), Path(r["tra"]))
    except Exception:
        for _m, tra, _route, _nodes in resolved:
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
        tb = traceback.format_exc()
        print("\nFAIL: %s" % exc)
        print(tb)
        error_file = SCRIPT_DIR / "LAST_ERROR.txt"
        try:
            error_file.write_text(tb, encoding="utf-8")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Skript skoncil chybou:\n\n%s\n\nDetail: %s" % (exc, error_file),
                "R1 VISUM geometry import - ERROR",
                0x10,
            )
        except Exception:
            pass
