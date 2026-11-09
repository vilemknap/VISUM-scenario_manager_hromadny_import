# -*- coding: utf-8 -*-
"""
R1 VARIANTY - hromadna NAHRADA TRAS ve Visum Scenario Manageru (PTV Visum 2026)

Co skript dela pro kazdou geometry modification (G_W1_oprava, G_W2 ... G_W5):

  1. nacte R1_VARIANTYBase.ver + puvodni geometry .tra,
  2. SMAZE cely stary komplex trasy (hlavni linky TYP=hlavni_krivka + ENTITYHAND,
     jizdni pasy, rampy, vetve MUK a vsechny uzly, ktere stara modifikace vlozila),
  3. na VELKYCH KRUZICH VLOZI NOVE UZLY (urovnove krizovatky, nova cisla),
  4. mezi nimi VLOZI NOVE LINKY s polygonem podle nove osy (nova cisla),
  5. stavajici uzly site, na ktere se stara trasa napojovala, NAPOJI jednim linkem
     na nejblizsi novy uzel (zadne rampy ani MUK),
  6. necha Visum vygenerovat novou geometry .tra vuci base verzi,
  7. PREGENERUJE zavisle sirkove modifikace (S1/S2/S3),
  8. vse overi a v rezimu "apply" zazalohuje a prepise .tra soubory.
     R1_VARIANTY.vpdb se NIKDY nezapisuje (jen se z nej cte).

Spusteni: PTV Visum 2026 -> Scripts -> Run script file... -> tento soubor.
Konfigurace: config.json vedle skriptu (viz README.md).
"""
from __future__ import print_function

import collections
import datetime as dt
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import sys
import traceback
from pathlib import Path

try:
    SCRIPT_DIR = Path(__file__).resolve().parent
except NameError:  # Visum muze spoustet bez __file__
    SCRIPT_DIR = Path(os.getcwd())
CONFIG = SCRIPT_DIR / "config.json"

LINK_KEY_TABLES = ("LINK", "LANE", "LINKPOLY")
NODE_REF_COLUMNS = ("FROMNODENO", "TONODENO", "VIANODENO", "NODENO", "VIA1NODENO",
                    "VIA2NODENO")


class Fail(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# geometrie
# ---------------------------------------------------------------------------

def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def line_len(c):
    return sum(dist(c[i], c[i + 1]) for i in range(len(c) - 1))


def cumlen(c):
    out = [0.0]
    for i in range(len(c) - 1):
        out.append(out[-1] + dist(c[i], c[i + 1]))
    return out


def project(c, cum, p):
    """Projekce bodu na lomenou caru -> (staniceni, vzdalenost, bod)."""
    best = (float("inf"), 0.0, c[0])
    for i in range(len(c) - 1):
        a, b = c[i], c[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
        q = (a[0] + t * dx, a[1] + t * dy)
        dd = dist(p, q)
        if dd < best[0]:
            best = (dd, cum[i] + t * math.sqrt(l2), q)
    return best[1], best[0], best[2]


def point_at(c, cum, s):
    if s <= 0:
        return c[0]
    if s >= cum[-1]:
        return c[-1]
    for i in range(len(c) - 1):
        if cum[i + 1] >= s:
            seg = cum[i + 1] - cum[i]
            t = 0.0 if seg == 0 else (s - cum[i]) / seg
            return (c[i][0] + t * (c[i + 1][0] - c[i][0]), c[i][1] + t * (c[i + 1][1] - c[i][1]))
    return c[-1]


def interior_vertices(c, cum, s0, s1, eps=0.05):
    """Vrcholy krivky striktne mezi staniceni s0 < s1."""
    return [c[i] for i in range(len(c)) if s0 + eps < cum[i] < s1 - eps]


def warp_polyline(pts, new_start, new_end):
    """Posune konce lomene cary a vnitrni body linearne dotvaruje podle delky."""
    if len(pts) < 2:
        return [new_start, new_end]
    cum = cumlen(pts)
    total = cum[-1] or 1.0
    ds = (new_start[0] - pts[0][0], new_start[1] - pts[0][1])
    de = (new_end[0] - pts[-1][0], new_end[1] - pts[-1][1])
    out = []
    for p, s in zip(pts, cum):
        t = s / total
        out.append((p[0] + (1 - t) * ds[0] + t * de[0], p[1] + (1 - t) * ds[1] + t * de[1]))
    out[0], out[-1] = new_start, new_end
    return out


def chain_lines(lines, tol):
    """Spoji LineStringy do souvislych retezcu (konce do tol metru)."""
    lines = [list(l) for l in lines if len(l) >= 2]
    chains = []
    while lines:
        cur = lines.pop(0)
        changed = True
        while changed:
            changed = False
            for i, l in enumerate(lines):
                if dist(cur[-1], l[0]) <= tol:
                    cur = cur + l[1:]
                elif dist(cur[-1], l[-1]) <= tol:
                    cur = cur + l[::-1][1:]
                elif dist(cur[0], l[-1]) <= tol:
                    cur = l[:-1] + cur
                elif dist(cur[0], l[0]) <= tol:
                    cur = l[::-1][:-1] + cur
                else:
                    continue
                lines.pop(i)
                changed = True
                break
        # odstranit duplicitni po sobe jdouci body
        clean = [cur[0]]
        for p in cur[1:]:
            if dist(p, clean[-1]) > 1e-6:
                clean.append(p)
        if len(clean) >= 2:
            chains.append(clean)
    return chains


def parse_wkt_line(s):
    s = str(s or "")
    if "(" not in s:
        return []
    body = s[s.find("(") + 1:s.rfind(")")].replace("(", "").replace(")", "")
    pts = []
    for tok in body.split(","):
        p = tok.split()
        if len(p) >= 2:
            pts.append((float(p[0]), float(p[1])))
    return pts


# ---------------------------------------------------------------------------
# vstupy: GeoJSON / SHP
# ---------------------------------------------------------------------------

def _ring_centroid(ring):
    pts = ring[:-1] if len(ring) > 1 and dist(ring[0], ring[-1]) < 1e-9 else ring
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _geojson_geoms(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("type") == "FeatureCollection":
        feats = data.get("features", [])
    elif data.get("type") == "Feature":
        feats = [data]
    else:
        feats = [{"geometry": data}]
    for f in feats:
        g = f.get("geometry") or {}
        yield g.get("type"), g.get("coordinates"), f.get("properties") or {}


def _xy(c):
    return (float(c[0]), float(c[1]))


def read_shp(path):
    """Minimalni ctecka ESRI Shapefile (Point, PolyLine, Polygon, i Z/M)."""
    data = Path(path).read_bytes()
    out = []
    pos = 100
    while pos + 8 <= len(data):
        _rec, clen = struct.unpack(">ii", data[pos:pos + 8])
        content = data[pos + 8:pos + 8 + clen * 2]
        pos += 8 + clen * 2
        if len(content) < 4:
            continue
        st = struct.unpack("<i", content[:4])[0]
        if st in (1, 11, 21):
            x, y = struct.unpack("<dd", content[4:20])
            out.append(("Point", (x, y)))
        elif st in (3, 13, 23, 5, 15, 25):
            nparts, npts = struct.unpack("<ii", content[36:44])
            parts = list(struct.unpack("<%di" % nparts, content[44:44 + 4 * nparts]))
            off = 44 + 4 * nparts
            pts = [struct.unpack("<dd", content[off + 16 * i:off + 16 * i + 16]) for i in range(npts)]
            parts.append(npts)
            rings = [pts[parts[i]:parts[i + 1]] for i in range(nparts)]
            kind = "LineString" if st in (3, 13, 23) else "Polygon"
            for r in rings:
                out.append((kind, [tuple(p) for p in r]))
    return out


def load_lines(path):
    path = Path(path)
    lines = []
    if path.suffix.lower() == ".shp":
        for kind, g in read_shp(path):
            if kind == "LineString":
                lines.append(g)
    else:
        for t, c, _p in _geojson_geoms(path):
            if t == "LineString":
                lines.append([_xy(p) for p in c])
            elif t == "MultiLineString":
                lines.extend([[_xy(p) for p in part] for part in c])
    lines = [l for l in lines if len(l) >= 2 and line_len(l) > 0]
    if not lines:
        raise Fail("Soubor s osou neobsahuje zadnou caru (LineString): %s" % path)
    return lines


def load_points(path):
    """Velke kruhy: Point, nebo kruznice jako Polygon / uzavrena cara -> stred."""
    path = Path(path)
    pts = []
    if path.suffix.lower() == ".shp":
        for kind, g in read_shp(path):
            if kind == "Point":
                pts.append(tuple(g))
            elif len(g) >= 3:
                pts.append(_ring_centroid(g))
    else:
        for t, c, _p in _geojson_geoms(path):
            if t == "Point":
                pts.append(_xy(c))
            elif t == "MultiPoint":
                pts.extend(_xy(p) for p in c)
            elif t == "Polygon":
                pts.append(_ring_centroid([_xy(p) for p in c[0]]))
            elif t == "MultiPolygon":
                pts.extend(_ring_centroid([_xy(p) for p in poly[0]]) for poly in c)
            elif t == "LineString" and len(c) >= 4 and dist(_xy(c[0]), _xy(c[-1])) < 0.01:
                pts.append(_ring_centroid([_xy(p) for p in c]))
    return pts


# ---------------------------------------------------------------------------
# .tra soubory
# ---------------------------------------------------------------------------

class Table(object):
    def __init__(self, op, name, header):
        self.op = op            # '+', '-', '*', '' (VERSION)
        self.name = name        # LINK, NODE, ...
        self.header = header    # list sloupcu
        self.rows = []
        self.idx = dict((c, i) for i, c in enumerate(header))

    def get(self, row, col, default=""):
        i = self.idx.get(col)
        return row[i] if i is not None and i < len(row) else default

    def tag(self):
        return self.op + self.name


class Tra(object):
    def __init__(self, path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig", errors="replace")
        self.newline = "\r\n" if "\r\n" in text[:5000] else "\n"
        self.tables = []
        self.version_line = None
        cur = None
        for line in text.splitlines():
            if line.startswith("$"):
                if ":" not in line:
                    cur = None
                    continue
                tag, hdr = line[1:].split(":", 1)
                if tag == "VERSION":
                    self.version_line = line
                op = tag[0] if tag[0] in "+-*" else ""
                cur = Table(op, tag[len(op):], hdr.split(";"))
                self.tables.append(cur)
            elif not line.strip():
                cur = None
            elif line.startswith("*"):
                continue
            elif cur is not None:
                cur.rows.append(line.split(";"))

    def table(self, tag):
        for t in self.tables:
            if t.tag() == tag:
                return t
        return None


def write_tra(path, version_line, tables, newline="\r\n", bom=False, comment=None):
    out = ["$VISION", "* " + (comment or "R1 VARIANTY - generovano skriptem"),
           "* " + dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "* "]
    out += ["* Table: Version block", "* ",
            version_line or "$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT;VISUMVERSIONNO",
            ]
    if not version_line:
        out.append("19;Trans;ENG;KM;2026.01")
    else:
        out.append(_VERSION_ROW.get(str(path), "19;Trans;ENG;KM;2026.01"))
    out.append("")
    for t in tables:
        if not t.rows:
            continue
        out += ["* ", "* Table: %s" % t.tag(), "* ",
                "$%s:%s" % (t.tag(), ";".join(t.header))]
        out += [";".join(str(v) for v in r) for r in t.rows]
        out.append("")
    data = newline.join(out) + newline
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes((b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8"))


_VERSION_ROW = {}


def version_row_of(tra):
    t = tra.table("VERSION")
    return ";".join(t.rows[0]) if t and t.rows else "19;Trans;ENG;KM;2026.01"


def fnum(v):
    s = repr(float(v))
    return s


def km(m):
    return ("%.9f" % (m / 1000.0)).rstrip("0").rstrip(".") + "km"


def to_int(v):
    return int(float(str(v).strip()))


# ---------------------------------------------------------------------------
# konfigurace
# ---------------------------------------------------------------------------

DEFAULTS = {
    "mode": "dry-run",
    "base_version": "R1_VARIANTYBase.ver",
    "vpdb": "R1_VARIANTY.vpdb",
    "tra_dir": ".",
    "routes_dir": "routes",
    "work_dir": "_route_replace_work",
    "backup_dir": "backup_modifications",
    "route_type_attribute": "TYP",
    "route_type_value": "hlavni_krivka",
    "route_identity_attribute": "ENTITYHAND",
    "line_join_tolerance_m": 2.0,
    "circle_max_offset_m": 50.0,
    "circle_offset_action": "fail",
    "circle_merge_tolerance_m": 5.0,
    "circle_snap_to_existing_node_m": 2.0,
    "endpoint_snap_to_old_anchor_m": 500.0,
    "trim_to_old_extent": True,
    "trim_anchor_window_m": 1000.0,
    "circle_merge_into_anchor_m": 150.0,
    "junction_fallback_when_no_circles": True,
    "junction_match_station_m": 1500.0,
    "junction_cluster_m": 600.0,
    "endpoint_connect_existing_m": 300.0,
    "existing_node_on_axis_m": 20.0,
    "extra_connectors": [],
    "endpoint_snap_to_any_node_m": 5.0,
    "max_old_route_offset_median_m": 5000.0,
    "keep_links": [],
    "new_node_no_start": "auto",
    "new_link_no_start": "auto",
    "non_default_only": "auto",
}


def _repair_windows_paths(raw):
    keys = ("project_dir", "tra_dir", "routes_dir", "work_dir", "backup_dir",
            "base_version", "vpdb")
    for key in keys:
        raw = re.sub(r'("%s"\s*:\s*")([^"]*)(")' % re.escape(key),
                     lambda m: m.group(1) + m.group(2).replace("\\", "/") + m.group(3), raw)
    return raw


def load_config(path=None):
    path = path or CONFIG
    if not Path(path).exists():
        raise Fail("Chybi config.json vedle skriptu: %s" % path)
    raw = Path(path).read_text(encoding="utf-8-sig")
    try:
        cfg = json.loads(raw)
    except ValueError:
        try:
            cfg = json.loads(_repair_windows_paths(raw))
        except ValueError as exc:
            raise Fail("config.json neni platny JSON (%s). Cesty piste jako C:/R1_VARIANTY." % exc)
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    if cfg["mode"] not in ("dry-run", "apply"):
        raise Fail('mode musi byt "dry-run" nebo "apply"')
    if not cfg.get("project_dir"):
        raise Fail("V config.json chybi project_dir")
    mods = cfg.get("geometry_modifications") or cfg.get("modifications")
    if not mods:
        raise Fail("V config.json chybi geometry_modifications")
    norm_mods = []
    for m in mods:
        if m.get("enabled", True) is False:
            continue
        m = dict(m)
        if "parts" not in m:  # zpetna kompatibilita se starym configem
            m["parts"] = [{"identity": m.get("identity"), "route": m.get("route"),
                           "nodes": m.get("nodes")}]
        for p in m["parts"]:
            for k in ("identity", "route", "nodes"):
                if not p.get(k):
                    raise Fail("Modifikace %s: v parts chybi '%s'" % (m.get("code"), k))
        if not m.get("tra"):
            raise Fail("Modifikace %s: chybi 'tra'" % m.get("code"))
        norm_mods.append(m)
    cfg["geometry_modifications"] = norm_mods
    return cfg


def resolve(p, base):
    p = Path(str(p))
    return p if p.is_absolute() else (Path(base) / p)


def mod_no_from_tra(name):
    m = re.search(r"M0*(\d+)\.tra$", str(name), re.I)
    return int(m.group(1)) if m else None


def width_mods_from_vpdb(vpdb, geom_no):
    """Precte (jen pro cteni) zavisle modifikace z R1_VARIANTY.vpdb."""
    if not vpdb or not Path(vpdb).exists():
        return None
    uri = "file:%s?mode=ro" % Path(vpdb).resolve().as_posix()
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute("SELECT NO, CODE, DEPENDENTON FROM MODIFICATION").fetchall()
    finally:
        con.close()
    out = []
    for no, code, dep in rows:
        deps = [d.strip() for d in str(dep or "").replace(";", ",").split(",") if d.strip()]
        if str(geom_no) in deps:
            out.append({"no": int(no), "code": code, "tra": "M%06d.tra" % int(no)})
    return sorted(out, key=lambda r: r["no"])


# ---------------------------------------------------------------------------
# PLANOVANI nove topologie (cisty Python, bez Visumu)
# ---------------------------------------------------------------------------

class OldModel(object):
    """Stara geometry modifikace: linky, polygony, vlozene uzly."""

    def __init__(self, tra, node_xy):
        self.tra = tra
        self.node_xy = node_xy  # dict NO -> (x,y) kompletni sit base+stara modifikace
        lt = tra.table("+LINK")
        nt = tra.table("+NODE")
        if lt is None or nt is None:
            raise Fail("%s neobsahuje vlozene linky/uzly (+LINK/+NODE) - neni to geometry modifikace?"
                       % tra.path.name)
        self.link_table = lt
        self.node_table = nt
        self.inserted_nodes = set(to_int(r[nt.idx["NO"]]) for r in nt.rows)
        self.node_rows = dict((to_int(r[nt.idx["NO"]]), r) for r in nt.rows)
        self.dir_rows = {}            # (f,t) -> row
        self.links = collections.OrderedDict()  # NO -> [(f,t), ...]
        for r in lt.rows:
            no, f, t = to_int(r[lt.idx["NO"]]), to_int(r[lt.idx["FROMNODENO"]]), to_int(r[lt.idx["TONODENO"]])
            self.dir_rows[(f, t)] = r
            self.links.setdefault(no, []).append((f, t))
        self.polys = {}
        pt = tra.table("+LINKPOLY")
        if pt is not None:
            tmp = collections.defaultdict(list)
            for r in pt.rows:
                key = (to_int(pt.get(r, "FROMNODENO")), to_int(pt.get(r, "TONODENO")))
                tmp[key].append((to_int(pt.get(r, "INDEX")), float(pt.get(r, "XCOORD")),
                                 float(pt.get(r, "YCOORD"))))
            for k, v in tmp.items():
                self.polys[k] = [(x, y) for _i, x, y in sorted(v)]
        self.linkpoly_header = pt.header if pt is not None else \
            ["FROMNODENO", "TONODENO", "INDEX", "XCOORD", "YCOORD", "ZCOORD"]

    def link_nodes(self, no):
        f, t = self.links[no][0]
        return f, t

    def geometry(self, f, t):
        """Plna lomena cara linku ve smeru f->t vcetne uzlu."""
        if (f, t) in self.polys:
            mid = self.polys[(f, t)]
        elif (t, f) in self.polys:
            mid = self.polys[(t, f)][::-1]
        else:
            mid = []
        return [self.node_xy[f]] + mid + [self.node_xy[t]]

    def attr(self, no, col):
        vals = [self.link_table.get(self.dir_rows[k], col) for k in self.links[no]]
        return [v for v in vals if v != ""]


class Plan(object):
    pass


def _matches(val, wanted):
    return str(val).strip().lower() == str(wanted).strip().lower()


def detect_old_main(model, cfg, identities):
    """Stare hlavni linky trasy: TYP=hlavni_krivka a ENTITYHAND=identita."""
    ta, ia = cfg["route_type_attribute"], cfg["route_identity_attribute"]
    lt = model.link_table
    for col in (ta, ia):
        if col not in lt.idx:
            raise Fail("%s: tabulka +LINK nema sloupec %s" % (model.tra.path.name, col))
    main = collections.OrderedDict()
    for ident in identities:
        nos = [no for no, dirs in model.links.items()
               if any(_matches(lt.get(model.dir_rows[k], ta), cfg["route_type_value"]) and
                      _matches(lt.get(model.dir_rows[k], ia), ident) for k in dirs)]
        if not nos:
            found = sorted(set(lt.get(r, ia) for r in lt.rows
                               if _matches(lt.get(r, ta), cfg["route_type_value"])))
            raise Fail("%s: nenalezeny linky %s=%s s %s=%s. V souboru jsou identity: %s"
                       % (model.tra.path.name, ta, cfg["route_type_value"], ia, ident, found))
        for no in nos:
            main[no] = ident
    return main


def old_route_complex(model, main):
    """Cely stary komplex trasy: hlavni linky + vse, co na ne navazuje pres vlozene uzly
    (jizdni pasy, rampy, vnitrni uzly MUK). Vraci (linky, vlozene uzly)."""
    at_node = collections.defaultdict(set)
    for no in model.links:
        for n in model.link_nodes(no):
            at_node[n].add(no)
    links = set(main)
    nodes = set()
    stack = [n for no in main for n in model.link_nodes(no) if n in model.inserted_nodes]
    while stack:
        n = stack.pop()
        if n in nodes:
            continue
        nodes.add(n)
        for no in at_node[n]:
            links.add(no)
            for m in model.link_nodes(no):
                if m in model.inserted_nodes and m not in nodes:
                    stack.append(m)
    return links, nodes


def build_plan(model, mod_cfg, cfg, width_tras, inputs, alloc, all_node_xy, net_links=None):
    """
    Nova topologie: stary komplex trasy se smaze, na velkych kruzich vzniknou uzly
    (urovnove krizovatky), mezi nimi nove linky po nove ose a stavajici uzly site, ktere
    byly napojene na starou trasu, se napoji primo na nejblizsi novy uzel.
    """
    plan = Plan()
    plan.code = mod_cfg["code"]
    plan.warnings = []
    identities = [p["identity"] for p in inputs]
    main = detect_old_main(model, cfg, identities)
    route_links, deleted_nodes = old_route_complex(model, main)
    for no in cfg.get("keep_links", []) + list(mod_cfg.get("keep_links", [])):
        if int(no) in route_links and int(no) not in main:
            route_links.discard(int(no))
    plan.old_main = main
    plan.route_links = route_links
    plan.deleted_nodes = deleted_nodes
    xy0 = all_node_xy

    # stavajici uzly site dotcene starym komplexem = mista napojeni
    contacts = collections.OrderedDict()   # base node -> list of (link no, inserted node on other side)
    anchors = set()
    for no in route_links:
        f, t = model.link_nodes(no)
        for b, x in ((f, t), (t, f)):
            if b not in model.inserted_nodes:
                contacts.setdefault(b, []).append((no, x))
                if no in main:
                    anchors.add(b)
    plan.anchors = anchors

    nid = {}
    for no, ident in main.items():
        for n in model.link_nodes(no):
            nid.setdefault(n, ident)

    tolA = float(cfg["endpoint_snap_to_old_anchor_m"])
    tolAny = float(cfg["endpoint_snap_to_any_node_m"])
    anchor_merge = float(cfg["circle_merge_into_anchor_m"])
    any_pool = [n for n in xy0 if n not in deleted_nodes]

    def nearest_existing(p, tol):
        best = None
        for n in any_pool:
            q = xy0[n]
            if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol:
                d = dist(p, q)
                if d <= tol and (best is None or d < best[1]):
                    best = (n, d)
        return best

    new_nodes = collections.OrderedDict()
    new_links = []
    part_curves = []
    plan.trim_rows = []
    plan.dead_end_candidates = []

    trim = bool(mod_cfg.get("trim_to_old_extent", cfg.get("trim_to_old_extent", True)))
    plan.trim = trim
    plan.replace_existing_route = bool(mod_cfg.get("replace_old_route_on_existing_network", False))
    plan.open_closed_route_links = bool(mod_cfg.get("open_closed_route_links", False))
    plan.fill_empty_route_type = bool(mod_cfg.get("fill_empty_route_type", False))
    plan.drop_width_rows_for_links = set(int(x) for x in mod_cfg.get("drop_width_rows_for_links", []))
    for part in inputs:
        ident = part["identity"]
        chains = chain_lines(part["lines"], float(cfg["line_join_tolerance_m"]))
        if len(chains) > 1:
            plan.warnings.append("%s / %s: osa se sklada z %d nesouvislych casti - kazda se zpracuje zvlast"
                                 % (plan.code, ident, len(chains)))
        part_main = [no for no, i2 in main.items() if i2 == ident]
        offs = sorted(min(project(c, cumlen(c), model.node_xy[n])[1] for c in chains)
                      for no in part_main for n in model.link_nodes(no) if n in model.node_xy)
        if offs and offs[len(offs) // 2] > float(cfg["max_old_route_offset_median_m"]):
            raise Fail("%s / %s: nova osa je od stare trasy median %.0f m daleko - spatny soubor nebo "
                       "souradnicovy system (ocekava se EPSG:25834 jako ve Visumu)."
                       % (plan.code, ident, offs[len(offs) // 2]))
        pts = list(part["points"])
        if not pts:
            plan.warnings.append("%s / %s: soubor velkych kruhu je PRAZDNY" % (plan.code, ident))
        for ci, c in enumerate(chains):
            cum = cumlen(c)
            L = cum[-1]
            # --- oriznuti osy na stavajici napojovaci uzly stare trasy --------
            on_anchors = []
            for a in anchors:
                if a in xy0 and nid.get(a) in (None, ident):
                    s, off, _q = project(c, cum, xy0[a])
                    if off <= tolA:
                        on_anchors.append((s, off, a))
            old_st = [project(c, cum, p)[0] for no in part_main for p in model.geometry(*model.link_nodes(no))]
            s0, s1, term0, term1 = 0.0, L, None, None
            if trim and old_st and on_anchors:
                lo, hi = min(old_st), max(old_st)
                win = float(cfg["trim_anchor_window_m"])
                cand = [x for x in on_anchors if abs(x[0] - lo) <= win]
                if cand:
                    best = min(cand, key=lambda x: abs(x[0] - lo))
                    s0, term0 = best[0], best[2]
                cand = [x for x in on_anchors if abs(x[0] - hi) <= win and x[2] != term0]
                if cand:
                    best = min(cand, key=lambda x: abs(x[0] - hi))
                    s1, term1 = best[0], best[2]
            if s1 < s0:
                s0, s1, term0, term1 = s1, s0, term1, term0
            on_axis_tol = float(cfg["existing_node_on_axis_m"])
            axis_anchors = [(s, a) for (s, off, a) in on_anchors
                            if off <= on_axis_tol and s0 + 1.0 < s < s1 - 1.0 and a not in (term0, term1)]
            if s0 > 1.0 or s1 < L - 1.0:
                plan.trim_rows.append({"modification": plan.code, "identity": ident, "axis_length_m": round(L, 1),
                                       "used_from_m": round(s0, 1), "used_to_m": round(s1, 1),
                                       "start_node": term0, "end_node": term1})
                plan.warnings.append(
                    "%s / %s: nova osa (%.1f km) pouzita v useku %.1f-%.1f km (od %s do %s) - jen tam stara "
                    "modifikace mela novou trasu, zbytek je ve scenarich stavajici sit."
                    % (plan.code, ident, L / 1000.0, s0 / 1000.0, s1 / 1000.0,
                       ("uzlu %d" % term0) if term0 else "zacatku osy", ("uzlu %d" % term1) if term1 else "konce osy"))
            # --- velke kruhy -> uzly -------------------------------------------
            stops = []
            for p in pts:
                s, off, q = project(c, cum, p)
                if len(chains) > 1 and off > min(project(c2, cumlen(c2), p)[1] for c2 in chains) + 1e-6:
                    continue
                if off > float(cfg["circle_max_offset_m"]):
                    msg = ("%s / %s: velky kruh [%.1f, %.1f] je %.1f m od osy (limit circle_max_offset_m=%.0f)"
                           % (plan.code, ident, p[0], p[1], off, float(cfg["circle_max_offset_m"])))
                    if cfg["circle_offset_action"] == "fail":
                        raise Fail(msg)
                    plan.warnings.append(msg + " - ignorovan")
                    continue
                if term0 is not None and abs(s - s0) <= anchor_merge:
                    plan.warnings.append("%s: velky kruh (staniceni %.0f m) = stavajici uzel %d" % (plan.code, s, term0))
                    continue
                if term1 is not None and abs(s - s1) <= anchor_merge:
                    plan.warnings.append("%s: velky kruh (staniceni %.0f m) = stavajici uzel %d" % (plan.code, s, term1))
                    continue
                if s < s0 - 1e-6 or s > s1 + 1e-6:
                    plan.warnings.append("%s: velky kruh (staniceni %.0f m) je mimo nahrazovany usek - vynechan"
                                         % (plan.code, s))
                    continue
                near_ax = [a for (sa, a) in axis_anchors if abs(sa - s) <= anchor_merge]
                if near_ax:
                    plan.warnings.append("%s: velky kruh (staniceni %.0f m) = stavajici uzel %d lezici na ose"
                                         % (plan.code, s, near_ax[0]))
                    continue
                stops.append([s, q, "velky_kruh", None])
            # mista napojeni stavajici site, u kterych neni velky kruh -> novy uzel v jejich prumetu
            if cfg.get("junction_fallback_when_no_circles", True):
                lim_ = float(cfg["junction_match_station_m"])
                proj = []
                for b_, lst in contacts.items():
                    if b_ in (term0, term1) or b_ not in xy0 or b_ in [a for (_s, a) in axis_anchors]:
                        continue
                    lno, x = lst[0]
                    p = xy0[b_] if lno in main else model.node_xy.get(x, xy0[b_])
                    if main.get(lno, nid.get(x, ident)) != ident:
                        continue
                    s, off, _q = project(c, cum, p)
                    if not (s0 + anchor_merge < s < s1 - anchor_merge):
                        continue
                    if any(abs(st[0] - s) <= lim_ for st in stops):
                        continue
                    proj.append((s, b_))
                proj.sort()
                cl = []
                for s, b_ in proj:
                    if cl and s - cl[-1][-1][0] <= float(cfg["junction_cluster_m"]):
                        cl[-1].append((s, b_))
                    else:
                        cl.append([(s, b_)])
                for g in cl:
                    s = sum(x[0] for x in g) / len(g)
                    stops.append([s, point_at(c, cum, s), "novy_uzel_bez_kruhu", None])
                    plan.warnings.append(
                        "%s / %s: stavajici uzly %s byly napojeny na starou trasu a do %.0f m neni velky kruh -> "
                        "novy uzel v prumetu na novou osu (staniceni %.0f m)"
                        % (plan.code, ident, ",".join(str(x[1]) for x in g), lim_, s))
            for (sa, a) in axis_anchors:
                stops.append([sa, xy0[a], "stavajici_uzel_na_ose", a])
                plan.warnings.append("%s / %s: stavajici uzel %d lezi na nove ose (staniceni %.0f m) - trasa jim prochazi"
                                     % (plan.code, ident, a, sa))
            stops.append([s0, xy0[term0] if term0 else point_at(c, cum, s0), "zacatek", term0])
            stops.append([s1, xy0[term1] if term1 else point_at(c, cum, s1), "konec", term1])
            stops.sort(key=lambda x: x[0])
            merged = []
            for st in stops:
                if merged and st[0] - merged[-1][0] <= float(cfg["circle_merge_tolerance_m"]):
                    if st[2] in ("zacatek", "konec") or st[3] is not None:
                        merged[-1] = st if merged[-1][2] != "zacatek" else merged[-1]
                    continue
                merged.append(st)
            for k, st in enumerate(merged):
                if st[3] is not None:
                    continue
                is_end = k in (0, len(merged) - 1)
                hit = nearest_existing(st[1], tolAny if is_end else float(cfg["circle_snap_to_existing_node_m"]))
                if hit is None and is_end:
                    for nno, nd in new_nodes.items():
                        if dist(nd["xy"], st[1]) <= float(cfg["line_join_tolerance_m"]) * 5:
                            hit = (nno, 0.0)
                            break
                if hit is not None:
                    st[3] = hit[0]
                    st[1] = new_nodes[hit[0]]["xy"] if hit[0] in new_nodes else xy0[hit[0]]
                    continue
                st[3] = alloc["node"]
                alloc["node"] += 1
                new_nodes[st[3]] = {"xy": st[1], "source": st[2], "identity": ident, "station": st[0], "chain": ci}
                if is_end:
                    plan.dead_end_candidates.append(st[3])
            for k in range(len(merged) - 1):
                a, b = merged[k], merged[k + 1]
                if a[3] == b[3]:
                    continue
                new_links.append({"no": alloc["link"], "a": a[3], "b": b[3], "kind": "trasa", "identity": ident,
                                  "pts": [a[1]] + interior_vertices(c, cum, a[0], b[0]) + [b[1]],
                                  "sa": a[0], "sb": b[0], "chain": ci})
                alloc["link"] += 1
            part_curves.append({"identity": ident, "curve": c, "cum": cum,
                                "stops": [(st[0], st[3], st[2]) for st in merged]})

    plan.new_nodes = new_nodes
    plan.part_curves = part_curves
    xy_all = dict(xy0)
    for n, d in new_nodes.items():
        xy_all[n] = d["xy"]
    plan.xy_all = xy_all
    stop_nodes = set(st[1] for pc in part_curves for st in pc["stops"])

    def locate(p, identity):
        best = None
        for pc in [x for x in part_curves if x["identity"] == identity] or part_curves:
            s, off, _q = project(pc["curve"], pc["cum"], p)
            if best is None or off < best[2]:
                best = (pc, s, off)
        return best

    # ---------- napojeni stavajici site na nove uzly --------------------------
    lim = float(cfg["junction_match_station_m"])
    used_pairs = set(frozenset((nl["a"], nl["b"])) for nl in new_links)
    for (no, f, t) in (net_links or ()):
        if no not in route_links:
            used_pairs.add(frozenset((f, t)))
    remapped, plan.junction_rows, plan.dropped_contacts = [], [], []
    for b, lst in contacts.items():
        if b in stop_nodes or b not in xy_all:
            continue
        cands = []
        for (lno, x) in lst:
            p = model.node_xy.get(x, xy_all[b]) if lno not in main else xy_all[b]
            pc, s, off = locate(p, main.get(lno) or nid.get(x) or identities[0])
            st = min(pc["stops"], key=lambda q: abs(q[0] - s))
            cands.append((abs(st[0] - s), lno, x, st, s))
        cands.sort(key=lambda c: (c[0], c[1] in main))
        ds, lno, x, st, s = cands[0]
        target = st[1]
        row = {"modification": plan.code, "existing_node": b, "via_old_link": lno, "old_attach_node": x,
               "old_station_m": round(s, 1), "new_node": target, "new_node_station_m": round(st[0], 1),
               "station_diff_m": round(ds, 1), "link_length_m": round(dist(xy_all[b], xy_all[target]), 1)}
        if ds > lim:
            row["result"] = "NEPRIPOJENO - zadny velky kruh do %.0f m" % lim
            plan.junction_rows.append(row)
            plan.dropped_contacts.append(b)
            plan.warnings.append("%s: stavajici uzel %d byl na starou trasu napojen (link %d), ale do %.0f m "
                                 "neni zadny velky kruh -> NEPRIPOJEN" % (plan.code, b, lno, lim))
            continue
        if frozenset((b, target)) in used_pairs:
            row["result"] = "uz propojeno"
            plan.junction_rows.append(row)
            continue
        used_pairs.add(frozenset((b, target)))
        if lno in main:
            # stavajici koncovy uzel, ktery nova osa nepouziva -> novy link se sablonou hlavniho linku
            f, t = model.link_nodes(lno)
            other = t if f == b else f
            nl = {"no": alloc["link"], "a": b, "b": target, "kind": "napojeni_stavajiciho_uzlu",
                  "identity": main[lno], "pts": [xy_all[b], xy_all[target]], "sa": s, "sb": s, "chain": -1,
                  "tmpl": {(b, target): (b, other), (target, b): (other, b)}}
            alloc["link"] += 1
            new_links.append(nl)
            row["result"] = "novy link %d (atributy ze stare trasy %d)" % (nl["no"], lno)
        else:
            old_pts = model.geometry(b, x)
            pts = warp_polyline(old_pts, xy_all[b], xy_all[target])
            dirs = [(df, dt_, target if df == x else b, target if dt_ == x else b) for (df, dt_) in model.links[lno]]
            remapped.append({"no": lno, "f": b, "t": x, "nf": b, "nt": target, "pts": pts, "dirs": dirs,
                             "old_len": line_len(old_pts), "new_len": line_len(pts)})
            row["result"] = "link %d prepojen %d-%d" % (lno, b, target)
        plan.junction_rows.append(row)
    plan.remapped = remapped
    plan.new_links = new_links
    reused = set(r["no"] for r in remapped)
    plan.old_carriage = set(no for no in route_links if no not in main and no not in reused)
    plan.dropped_connectors = []
    plan.merged_dirs = {}
    plan.remap_dir = {}
    for r in remapped:
        for (df, dt_, nf, nt) in r["dirs"]:
            plan.remap_dir[(df, dt_)] = (r["no"], nf, nt)

    ends = {}
    for pc in part_curves:
        ends.setdefault("zacatek", pc["stops"][0][1])
        ends["konec"] = pc["stops"][-1][1]
    for ec in list(cfg.get("extra_connectors") or []) + list(mod_cfg.get("extra_connectors") or []):
        a = int(ec["node"])
        if a not in xy_all:
            raise Fail("%s: extra_connectors %r - uzel %d v siti neexistuje" % (plan.code, ec, a))
        to = ec.get("to")
        if to in ("konec", "zacatek", "end", "start"):
            target = ends[{"end": "konec", "start": "zacatek"}.get(to, to)]
        elif to:
            target = int(to)
        else:
            target = min([n for n in stop_nodes if n != a], key=lambda n: dist(xy_all[n], xy_all[a]))
        tmpl_no = int(ec["template_link"])
        if ec.get("template_tra"):
            src_tra = Tra(resolve(ec["template_tra"], model.tra.path.parent))
            src_lt = src_tra.table("+LINK")
            rows = [r for r in (src_lt.rows if src_lt else []) if to_int(src_lt.get(r, "NO")) == tmpl_no]
            if not rows:
                raise Fail("%s: extra_connectors - link %d neni v %s" % (plan.code, tmpl_no, ec["template_tra"]))
            dict_rows = [dict(zip(src_lt.header, r)) for r in rows]
            lane_t = src_tra.table("+LANE")
            lanes = {}
            for d in dict_rows:
                key = (to_int(d["FROMNODENO"]), to_int(d["TONODENO"]))
                lanes[key] = [dict(zip(lane_t.header, r)) for r in (lane_t.rows if lane_t else [])
                              if (to_int(lane_t.get(r, "FROMNODENO")), to_int(lane_t.get(r, "TONODENO"))) == key]
            d0 = dict_rows[0]
            d1 = dict_rows[1] if len(dict_rows) > 1 else d0
            k0 = (to_int(d0["FROMNODENO"]), to_int(d0["TONODENO"]))
            k1 = (to_int(d1["FROMNODENO"]), to_int(d1["TONODENO"]))
            nl = {"no": alloc["link"], "a": a, "b": target, "pts": [xy_all[a], xy_all[target]],
                  "kind": "extra_connector", "identity": identities[0], "sa": 0, "sb": 0, "chain": -1,
                  "rows": {(a, target): d0, (target, a): d1},
                  "lanes": {(a, target): lanes.get(k0, []), (target, a): lanes.get(k1, [])},
                  "old_no": tmpl_no}
        else:
            if tmpl_no not in model.links:
                raise Fail("%s: extra_connectors %r - sablonovy link %d neni v %s (pouzijte template_tra)"
                           % (plan.code, ec, tmpl_no, model.tra.path.name))
            tf, tt = model.link_nodes(tmpl_no)
            nl = {"no": alloc["link"], "a": a, "b": target, "pts": [xy_all[a], xy_all[target]],
                  "kind": "extra_connector", "identity": identities[0], "sa": 0, "sb": 0, "chain": -1,
                  "tmpl": {(a, target): (tf, tt), (target, a): (tt, tf) if (tt, tf) in model.dir_rows else (tf, tt)}}
        alloc["link"] += 1
        new_links.append(nl)
        plan.warnings.append("%s: extra_connectors - novy link %d mezi %d a %d (%.0f m)"
                             % (plan.code, nl["no"], a, target, dist(xy_all[a], xy_all[target])))

    # ---------- atributy novych linku trasy podle staniceni starych ----------
    old_intervals = []
    for no, ident in main.items():
        f, t = model.link_nodes(no)
        g = model.geometry(f, t)
        best = None
        for pc in [x for x in part_curves if x["identity"] == ident] or part_curves:
            a = project(pc["curve"], pc["cum"], g[0])
            b = project(pc["curve"], pc["cum"], g[-1])
            if best is None or a[1] + b[1] < best[0]:
                best = (a[1] + b[1], a[0], b[0])
        _o, sf, st_ = best
        old_intervals.append({"identity": ident, "s0": min(sf, st_), "s1": max(sf, st_), "no": no,
                              "inc": (f, t) if sf <= st_ else (t, f)})
    plan.link_match, plan.link_rows = {}, []
    for nl in new_links:
        nl["len"] = line_len(nl["pts"])
        if nl.get("rows"):
            plan.link_rows.append({"modification": plan.code, "new_link": nl["no"], "kind": nl["kind"],
                                   "from_node": nl["a"], "to_node": nl["b"], "length_m": round(nl["len"], 1),
                                   "station_from_m": "", "station_to_m": "",
                                   "attributes_from_old_link": nl["old_no"], "identity": nl["identity"]})
            continue
        if nl.get("tmpl"):
            plan.link_match.update(nl["tmpl"])
            nl["old_no"] = to_int(model.link_table.get(
                model.dir_rows[list(nl["tmpl"].values())[0]], "NO"))
        else:
            mid = 0.5 * (nl["sa"] + nl["sb"])
            cand = [iv for iv in old_intervals if iv["identity"] == nl["identity"]] or old_intervals
            best = min(cand, key=lambda iv: (0, -(iv["s1"] - iv["s0"])) if iv["s0"] <= mid <= iv["s1"]
                       else (1, min(abs(mid - iv["s0"]), abs(mid - iv["s1"]))))
            inc = best["inc"]
            dec = (inc[1], inc[0]) if (inc[1], inc[0]) in model.dir_rows else inc
            plan.link_match[(nl["a"], nl["b"])] = inc
            plan.link_match[(nl["b"], nl["a"])] = dec
            nl["old_no"] = best["no"]
        plan.link_rows.append({"modification": plan.code, "new_link": nl["no"], "kind": nl["kind"],
                               "from_node": nl["a"], "to_node": nl["b"], "length_m": round(nl["len"], 1),
                               "station_from_m": round(nl["sa"], 1), "station_to_m": round(nl["sb"], 1),
                               "attributes_from_old_link": nl["old_no"], "identity": nl["identity"]})
    plan.old_dir_to_new = collections.defaultdict(list)
    def fill_old_dir_to_new():
        plan.old_dir_to_new = collections.defaultdict(list)
        for nl in new_links:
            if nl.get("rows"):
                continue   # sablona z jine modifikace - sirkove radky nema
            for (a, b) in ((nl["a"], nl["b"]), (nl["b"], nl["a"])):
                plan.old_dir_to_new[plan.link_match[(a, b)]].append((nl["no"], a, b))
    fill_old_dir_to_new()

    # ---------- slepe konce osy -> napojit na nejblizsi stavajici uzel -------
    def degree():
        d = collections.Counter()
        for nl in new_links:
            d[nl["a"]] += 1
            d[nl["b"]] += 1
        for r in remapped:
            d[r["nf"]] += 1
            d[r["nt"]] += 1
        return d
    deg = degree()
    tolEnd = float(cfg["endpoint_connect_existing_m"])
    for pc in part_curves:
        for idx in (0, -1):
            n = pc["stops"][idx][1]
            if n not in new_nodes or deg[n] > 1:
                continue
            near = nearest_existing(xy_all[n], tolEnd)
            if near is None or frozenset((n, near[0])) in used_pairs:
                continue
            adj = [nl for nl in new_links if n in (nl["a"], nl["b"]) and not nl.get("rows")][0]
            other = adj["b"] if adj["a"] == n else adj["a"]
            e = {"no": alloc["link"], "a": n, "b": near[0], "pts": [xy_all[n], xy_all[near[0]]],
                 "kind": "napojeni_konce_osy", "identity": adj["identity"], "sa": 0, "sb": 0, "chain": -1,
                 "tmpl": {(n, near[0]): plan.link_match[(other, n)], (near[0], n): plan.link_match[(n, other)]}}
            alloc["link"] += 1
            e["len"] = line_len(e["pts"])
            e["old_no"] = adj.get("old_no")
            plan.link_match.update(e["tmpl"])
            new_links.append(e)
            used_pairs.add(frozenset((n, near[0])))
            plan.link_rows.append({"modification": plan.code, "new_link": e["no"], "kind": e["kind"],
                                   "from_node": n, "to_node": near[0], "length_m": round(e["len"], 1),
                                   "station_from_m": "", "station_to_m": "",
                                   "attributes_from_old_link": e["old_no"], "identity": e["identity"]})
            plan.warnings.append("%s: konec nove osy (uzel %d) napojen na nejblizsi stavajici uzel %d (%.0f m) linkem %d"
                                 % (plan.code, n, near[0], near[1], e["no"]))
    fill_old_dir_to_new()
    deg = degree()
    for n in new_nodes:
        if deg[n] <= 1:
            plan.warnings.append("%s: novy uzel %d [%.1f, %.1f] je SLEPY KONEC (zadne napojeni na stavajici sit). "
                                 "Napojeni doplnte v config.json pres extra_connectors."
                                 % (plan.code, n, xy_all[n][0], xy_all[n][1]))
        elif deg[n] == 2 and new_nodes[n]["source"] == "velky_kruh":
            plan.warnings.append("%s: na velky kruh / uzel %d se zatim nenapojuje zadna stavajici sit"
                                 % (plan.code, n))

    # ---------- objekty (scitaci profily) na starych linkach ------------------
    plan.extra_tables = []
    for t in model.tra.tables:
        if t.op != "+" or t.name in ("LINK", "NODE", "LINKPOLY", "LANE", "TURN", "USERATTDEF",
                                     "LINKTYPE") or "FROMNODENO" not in t.idx:
            continue
        keep = Table("+", t.name, t.header)
        for r in t.rows:
            ln = t.get(r, "LINKNO")
            ln = to_int(ln) if ln != "" else None
            key = (to_int(t.get(r, "FROMNODENO")), to_int(t.get(r, "TONODENO")))
            if key in plan.remap_dir:
                no2, nf, nt = plan.remap_dir[key]
                r2 = list(r)
                r2[t.idx["FROMNODENO"]], r2[t.idx["TONODENO"]] = str(nf), str(nt)
                if "LINKNO" in t.idx:
                    r2[t.idx["LINKNO"]] = str(no2)
                keep.rows.append(r2)
            elif ln in route_links:
                plan.warnings.append("%s: %s %s lezel na smazanem linku %s - po nahrade nebude existovat"
                                     % (plan.code, t.name, r[0], ln))
        if keep.rows:
            plan.extra_tables.append(keep)

    # ---------- sit po nahrade (pro odboceni) --------------------------------
    after = set()
    for (no, f, t) in (net_links or set((no, f, t) for no, dirs in model.links.items() for (f, t) in dirs)):
        if no in route_links or f in deleted_nodes or t in deleted_nodes:
            continue
        after.add((no, f, t))
    new_dirs = set()
    for nl in new_links:
        new_dirs.add((nl["no"], nl["a"], nl["b"]))
        new_dirs.add((nl["no"], nl["b"], nl["a"]))
    for r in remapped:
        for (_df, _dt, nf, nt) in r["dirs"]:
            new_dirs.add((r["no"], nf, nt))
    plan.after_links = after | new_dirs
    plan.new_dirs = new_dirs
    plan.connectors = sorted(reused)
    return plan



def plan_patch_tables(plan, model):
    """Tabulky patch .tra aplikovane na (base + stara modifikace)."""
    tables = []
    lt, nt = model.link_table, model.node_table
    # mazani objektu na prepojovanych linkach (vlozi se znovu)
    for et in plan.extra_tables:
        if "NO" in et.idx:
            d = Table("-", et.name, ["NO"])
            d.rows = [[r[et.idx["NO"]]] for r in et.rows]
            tables.append(d)
    dl = Table("-", "LINK", ["NO", "FROMNODENO", "TONODENO"])
    for no in sorted(plan.route_links | set(r["no"] for r in plan.remapped) | set(plan.dropped_connectors)):
        f, t = model.link_nodes(no)
        dl.rows.append([str(no), str(f), str(t)])
    tables.append(dl)
    dn = Table("-", "NODE", ["NO"])
    dn.rows = [[str(n)] for n in sorted(plan.deleted_nodes)]
    tables.append(dn)

    an = Table("+", "NODE", nt.header)
    tmpl_no = sorted(n for n in plan.deleted_nodes if n in model.node_rows)
    tmpl = model.node_rows[tmpl_no[0]] if tmpl_no else nt.rows[0]
    for n, d in plan.new_nodes.items():
        r = list(tmpl)
        r[nt.idx["NO"]] = str(n)
        r[nt.idx["XCOORD"]] = fnum(d["xy"][0])
        r[nt.idx["YCOORD"]] = fnum(d["xy"][1])
        for c in ("CODE", "NAME"):
            if c in nt.idx:
                r[nt.idx[c]] = ""
        an.rows.append(r)
    tables.append(an)

    al = Table("+", "LINK", lt.header)
    ap = Table("+", "LINKPOLY", model.linkpoly_header)
    pi = ap.idx

    def add_poly(f, t, pts):
        for k, p in enumerate(pts[1:-1]):
            row = [""] * len(ap.header)
            row[pi["FROMNODENO"]], row[pi["TONODENO"]], row[pi["INDEX"]] = str(f), str(t), str(k + 1)
            row[pi["XCOORD"]], row[pi["YCOORD"]] = fnum(p[0]), fnum(p[1])
            if "ZCOORD" in pi:
                row[pi["ZCOORD"]] = "0"
            ap.rows.append(row)

    for nl in plan.new_links:
        if nl.get("rows"):
            for (a, b) in ((nl["a"], nl["b"]), (nl["b"], nl["a"])):
                d = nl["rows"][(a, b)]
                r = [d.get(c, "") for c in lt.header]
                r[lt.idx["NO"]], r[lt.idx["FROMNODENO"]], r[lt.idx["TONODENO"]] = str(nl["no"]), str(a), str(b)
                if "LENGTH" in lt.idx:
                    r[lt.idx["LENGTH"]] = km(nl["len"])
                al.rows.append(r)
            add_poly(nl["a"], nl["b"], nl["pts"])
            continue
        for (a, b) in ((nl["a"], nl["b"]), (nl["b"], nl["a"])):
            src = model.dir_rows[plan.link_match[(a, b)]]
            r = list(src)
            r[lt.idx["NO"]], r[lt.idx["FROMNODENO"]], r[lt.idx["TONODENO"]] = str(nl["no"]), str(a), str(b)
            if "LENGTH" in lt.idx:
                r[lt.idx["LENGTH"]] = km(nl["len"])
            al.rows.append(r)
        add_poly(nl["a"], nl["b"], nl["pts"])
    for rc in plan.remapped:
        for (df, dt_, nf, nt_) in rc["dirs"]:
            r = list(model.dir_rows[(df, dt_)])
            r[lt.idx["NO"]], r[lt.idx["FROMNODENO"]], r[lt.idx["TONODENO"]] = str(rc["no"]), str(nf), str(nt_)
            if "LENGTH" in lt.idx:
                r[lt.idx["LENGTH"]] = km(rc["new_len"])
            al.rows.append(r)
        add_poly(rc["nf"], rc["nt"], rc["pts"])
    tables += [al]
    # jizdni pruhy - kopie z odpovidajiciho stareho linku (Visum je v .tra uvadi vzdy)
    lane_t = model.tra.table("+LANE")
    if lane_t is not None:
        lanes_by = collections.defaultdict(list)
        for r in lane_t.rows:
            lanes_by[(to_int(lane_t.get(r, "FROMNODENO")), to_int(lane_t.get(r, "TONODENO")))].append(r)
        lt2 = Table("+", "LANE", lane_t.header)

        def add_lanes(src_key, f, t):
            for r in lanes_by.get(src_key, []):
                r2 = list(r)
                r2[lane_t.idx["FROMNODENO"]], r2[lane_t.idx["TONODENO"]] = str(f), str(t)
                lt2.rows.append(r2)
        for nl in plan.new_links:
            for (a, b) in ((nl["a"], nl["b"]), (nl["b"], nl["a"])):
                if nl.get("rows"):
                    for d in nl["lanes"][(a, b)]:
                        r2 = [d.get(c, "") for c in lane_t.header]
                        r2[lane_t.idx["FROMNODENO"]], r2[lane_t.idx["TONODENO"]] = str(a), str(b)
                        lt2.rows.append(r2)
                else:
                    add_lanes(plan.link_match[(a, b)], a, b)
        for rc in plan.remapped:
            for (df, dt_, nf, nt_) in rc["dirs"]:
                add_lanes((df, dt_), nf, nt_)
        tables.append(lt2)
    tables.append(ap)
    # odboceni na uzlech, kterych se nove/prepojene linky dotykaji
    turn_t = model.tra.table("+TURN")
    if turn_t is not None and turn_t.rows:
        tsys_mode = collections.Counter(turn_t.get(r, "TSYSSET") for r in turn_t.rows).most_common(1)[0][0]
        tmpl = list(turn_t.rows[0])
        tt = Table("+", "TURN", turn_t.header)
        into = collections.defaultdict(list)
        outof = collections.defaultdict(list)
        for k in plan.after_links:
            into[k[2]].append(k)
            outof[k[1]].append(k)
        xy = plan.xy_all
        geo = {}
        for nl in plan.new_links:
            geo[(nl["no"], nl["a"], nl["b"])] = nl["pts"]
            geo[(nl["no"], nl["b"], nl["a"])] = nl["pts"][::-1]
        for rc in plan.remapped:
            geo[(rc["no"], rc["nf"], rc["nt"])] = rc["pts"]
            geo[(rc["no"], rc["nt"], rc["nf"])] = rc["pts"][::-1]

        def heading(k, at_end):
            pts = geo.get(k) or [xy.get(k[1]), xy.get(k[2])]
            if None in pts:
                return None
            a, b = (pts[-2], pts[-1]) if at_end else (pts[0], pts[1])
            return math.atan2(b[1] - a[1], b[0] - a[0])
        done = set()
        nodes = set()
        for k in plan.new_dirs:
            nodes.update((k[1], k[2]))
        for v in sorted(nodes):
            for kin in into[v]:
                for kout in outof[v]:
                    if kin not in plan.new_dirs and kout not in plan.new_dirs:
                        continue
                    key = (kin[1], v, kout[2])
                    if key in done:
                        continue
                    done.add(key)
                    if kin[1] == kout[2]:
                        typ = 4
                    else:
                        h1, h2 = heading(kin, True), heading(kout, False)
                        if h1 is None or h2 is None:
                            typ = 2
                        else:
                            d = math.degrees((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)
                            typ = 2 if abs(d) <= 45 else (3 if d > 0 else 1)
                    r = list(tmpl)
                    r[turn_t.idx["FROMNODENO"]], r[turn_t.idx["VIANODENO"]], r[turn_t.idx["TONODENO"]] = \
                        str(kin[1]), str(v), str(kout[2])
                    if "TYPENO" in turn_t.idx:
                        r[turn_t.idx["TYPENO"]] = str(typ)
                    if "TSYSSET" in turn_t.idx:
                        r[turn_t.idx["TSYSSET"]] = tsys_mode
                    tt.rows.append(r)
        tables.append(tt)
    tables += plan.extra_tables
    return tables


# ---------------------------------------------------------------------------
# SIRKOVE modifikace - premapovani na nove linky
# ---------------------------------------------------------------------------

def map_width_tables(wtra, plan, model):
    out_tables = []
    stats = collections.Counter()
    log = []
    deleted_nodes = plan.deleted_nodes
    carriage_dirs = set()
    for no in plan.old_carriage:
        carriage_dirs.update(model.links[no])
    main_dirs = set()
    for no in plan.old_main:
        main_dirs.update(model.links[no])
    dropped_dirs = set()
    for no in plan.dropped_connectors:
        dropped_dirs.update(model.links[no])

    # stara trasa vedena po STAVAJICI siti (upgrade + uzavreni okolnich silnic) - pri uplne
    # nahrade trasy se tyto radky vypusti, silnice se vrati do stavu v base siti.
    old_on_existing = set()
    if getattr(plan, "replace_existing_route", False):
        lk = wtra.table("*LINK")
        if lk is not None:
            mtypes = collections.Counter(lk.get(r, "TYPENO") for r in lk.rows
                                         if to_int(lk.get(r, "NO")) in plan.old_main and lk.get(r, "TYPENO") != "")
            mtype = mtypes.most_common(1)[0][0] if mtypes else None
            base_rows = [(to_int(lk.get(r, "NO")), to_int(lk.get(r, "FROMNODENO")), to_int(lk.get(r, "TONODENO")),
                          lk.get(r, "TYPENO"), lk.get(r, "TSYSSET")) for r in lk.rows
                         if to_int(lk.get(r, "NO")) not in model.links]
            parent = {}

            def find(x):
                while parent.setdefault(x, x) != x:
                    x = parent[x]
                return x
            for (_no, f, t2, _ty, _ts) in base_rows:
                parent[find(f)] = find(t2)
            route_nodes = set(n for no in plan.route_links for n in model.link_nodes(no))
            good = set()
            for (_no, f, t2, ty, ts) in base_rows:
                if (mtype is not None and ty == mtype and ts != "") or f in route_nodes or t2 in route_nodes:
                    good.add(find(f))
            for (_no, f, t2, _ty, _ts) in base_rows:
                if find(f) in good:
                    old_on_existing.add((f, t2))
                    old_on_existing.add((t2, f))

    # volitelne: zavrene useky hlavni trasy otevrit profilem zbytku trasy v teze sirkove modifikaci
    open_profile = None
    if getattr(plan, "open_closed_route_links", False):
        lk = wtra.table("*LINK")
        if lk is not None and "TSYSSET" in lk.idx:
            cols = [c for c in ("TYPENO", "TSYSSET", "CAPPRT", "V0PRT") if c in lk.idx]
            prof = collections.Counter(tuple(lk.get(r, c) for c in cols) for r in lk.rows
                                       if to_int(lk.get(r, "NO")) in plan.old_main and lk.get(r, "TSYSSET") != "")
            if prof:
                open_profile = dict(zip(cols, prof.most_common(1)[0][0]))
    type_profile = None
    if getattr(plan, "fill_empty_route_type", False):
        lk = wtra.table("*LINK")
        if lk is not None and "TYPENO" in lk.idx:
            tp = collections.Counter(lk.get(r, "TYPENO") for r in lk.rows
                                     if to_int(lk.get(r, "NO")) in plan.old_main and lk.get(r, "TYPENO") != "")
            if tp:
                type_profile = tp.most_common(1)[0][0]
    drop_nos = getattr(plan, "drop_width_rows_for_links", set())
    opened_keys = set()
    if open_profile:
        lk = wtra.table("*LINK")
        for r in lk.rows:
            if to_int(lk.get(r, "NO")) in plan.old_main and lk.get(r, "TSYSSET") == "":
                opened_keys.add((to_int(lk.get(r, "FROMNODENO")), to_int(lk.get(r, "TONODENO"))))

    for t in wtra.tables:
        if t.name == "VERSION":
            continue
        nt = Table(t.op, t.name, t.header)
        is_linkish = t.name in LINK_KEY_TABLES and "FROMNODENO" in t.idx and "TONODENO" in t.idx
        for r in t.rows:
            if is_linkish:
                key = (to_int(t.get(r, "FROMNODENO")), to_int(t.get(r, "TONODENO")))
                if key in main_dirs and t.tag() == "-LANE" and key in opened_keys:
                    stats["zachovany pruhy otevreneho useku (%s)" % t.tag()] += 1
                    log.append((t.tag(), "%d-%d" % key, "usek se otevira - pruhy se nemazou"))
                    continue
                if key in main_dirs:
                    targets = plan.old_dir_to_new.get(key, [])
                    if type_profile and t.tag() == "*LINK" and t.get(r, "TYPENO") == "" and t.get(r, "TSYSSET") != "":
                        r = list(r)
                        r[t.idx["TYPENO"]] = type_profile
                        stats["doplnen chybejici typ trasy (%s)" % t.tag()] += len(targets)
                        log.append((t.tag(), "%d-%d" % key, "doplnen TYPENO %s" % type_profile))
                    if open_profile and t.tag() == "*LINK" and t.get(r, "TSYSSET") == "":
                        r = list(r)
                        for c, v in open_profile.items():
                            r[t.idx[c]] = v
                        stats["zavreny usek trasy otevren (%s)" % t.tag()] += len(targets)
                        log.append((t.tag(), "%d-%d" % key, "zavreny usek otevren: %s" % open_profile))
                    for (nno, a, b) in targets:
                        r2 = list(r)
                        r2[t.idx["FROMNODENO"]], r2[t.idx["TONODENO"]] = str(a), str(b)
                        if "NO" in t.idx:
                            r2[t.idx["NO"]] = str(nno)
                        nt.rows.append(r2)
                    stats["hlavni->nove (%s)" % t.tag()] += len(targets)
                    if not targets:
                        log.append((t.tag(), "%d-%d" % key, "VAROVANI: stary hlavni smer nema zadny novy link"))
                    log.append((t.tag(), "%d-%d" % key, "hlavni trasa -> %d novych smerovych linku" % len(targets)))
                    continue
                if key in carriage_dirs:
                    stats["vypusteno smazany stary link MUK/rampy (%s)" % t.tag()] += 1
                    log.append((t.tag(), "%d-%d" % key, "stary link MUK/rampy/pasu smazan - radek vypusten"))
                    continue
                rno = to_int(t.get(r, "NO")) if "NO" in t.idx and t.get(r, "NO") != "" else None
                lnos = set(no for no, dirs in model.links.items() if key in dirs) if drop_nos else set()
                if drop_nos and ((rno in drop_nos) or (lnos & drop_nos)):
                    stats["vypusteno dle drop_width_rows_for_links (%s)" % t.tag()] += 1
                    log.append((t.tag(), "%d-%d" % key, "vypusteno dle configu - vraceno do stavu geometrie"))
                    continue
                if key in old_on_existing:
                    stats["vypusteno stara trasa po stavajici siti (%s)" % t.tag()] += 1
                    log.append((t.tag(), "%d-%d" % key, "stara trasa po stavajici siti - vraceno do base"))
                    continue
                if key in plan.merged_dirs:
                    stats["vypusteno slouceny smer rampy (%s)" % t.tag()] += 1
                    log.append((t.tag(), "%d-%d" % key, "rampa sloucena s jinou - radek vypusten"))
                    continue
                if key in dropped_dirs and key not in plan.remap_dir:
                    stats["vypusteno zruseny link (%s)" % t.tag()] += 1
                    log.append((t.tag(), "%d-%d" % key, "link zrusen - radek vypusten"))
                    continue
                if key in plan.remap_dir:
                    no, nf, nt_ = plan.remap_dir[key]
                    r2 = list(r)
                    r2[t.idx["FROMNODENO"]], r2[t.idx["TONODENO"]] = str(nf), str(nt_)
                    if "NO" in t.idx:
                        r2[t.idx["NO"]] = str(no)
                    nt.rows.append(r2)
                    stats["rampa prepojena (%s)" % t.tag()] += 1
                    log.append((t.tag(), "%d-%d" % key, "rampa -> %d-%d" % (nf, nt_)))
                    continue
                nt.rows.append(list(r))
                stats["beze zmeny (%s)" % t.tag()] += 1
                continue
            refs = []
            for c in NODE_REF_COLUMNS:
                if c in t.idx and t.get(r, c) != "":
                    refs.append(to_int(t.get(r, c)))
            if t.name == "NODE" and "NO" in t.idx:
                refs.append(to_int(t.get(r, "NO")))
            if any(n in deleted_nodes for n in refs):
                stats["vypusteno (%s na smazanem uzlu)" % t.tag()] += 1
                log.append((t.tag(), ";".join(r[:4]), "odkazuje na smazany uzel - vypusteno"))
                continue
            nt.rows.append(list(r))
            stats["beze zmeny (%s)" % t.tag()] += 1
        out_tables.append(nt)
    return out_tables, stats, log


# ---------------------------------------------------------------------------
# Visum
# ---------------------------------------------------------------------------

def get_visum():
    v = globals().get("Visum")
    if v is not None:
        return v
    try:
        import __main__
        v = getattr(__main__, "Visum", None)
        if v is not None:
            return v
    except Exception:
        pass
    try:
        import win32com.client
        return win32com.client.Dispatch("Visum.Visum.26")
    except Exception:
        try:
            import win32com.client
            return win32com.client.Dispatch("Visum.Visum")
        except Exception:
            raise Fail("Nelze ziskat Visum. Spustte skript z PTV Visum 2026: Scripts -> Run script file...")


def _call(visum, name, *args):
    fn = getattr(visum, name, None)
    if fn is None:
        fn = getattr(visum.IO, name)
    return fn(*args)


def v_load(visum, ver):
    _call(visum, "LoadVersion", str(ver))


def v_save(visum, ver):
    Path(ver).parent.mkdir(parents=True, exist_ok=True)
    _call(visum, "SaveVersion", str(ver))


def v_apply(visum, tra):
    try:
        visum.ApplyModelTransferFile(str(tra))
    except Exception as exc:
        raise Fail("Visum odmitl aplikovat %s: %s" % (tra, exc))


def v_diff(visum, ver_from, ver_to, out_tra, non_default_only):
    Path(out_tra).parent.mkdir(parents=True, exist_ok=True)
    if Path(out_tra).exists():
        Path(out_tra).unlink()
    visum.GenerateModelTransferFileBetweenVersionFiles(
        str(ver_from), str(ver_to), str(out_tra), "", bool(non_default_only), True, False)
    if not Path(out_tra).exists():
        raise Fail("Visum nevytvoril %s" % out_tra)


def v_nodes(visum):
    rows = visum.Net.Nodes.GetMultipleAttributes(["NO", "XCOORD", "YCOORD"])
    return dict((int(r[0]), (float(r[1]), float(r[2]))) for r in rows)


def v_links(visum):
    rows = visum.Net.Links.GetMultipleAttributes(["NO", "FROMNODENO", "TONODENO"])
    return set((int(r[0]), int(r[1]), int(r[2])) for r in rows)


def v_turn_vianodes(visum):
    try:
        rows = visum.Net.Turns.GetMultipleAttributes(["VIANODENO"])
        return collections.Counter(int(r[0]) for r in rows)
    except Exception:
        return None


def verify_network(visum, plan, label):
    errs = []
    nodes = v_nodes(visum)
    links = v_links(visum)
    for n, d in plan.new_nodes.items():
        if n not in nodes:
            errs.append("chybi novy uzel %d" % n)
        elif dist(nodes[n], d["xy"]) > 0.05:
            errs.append("novy uzel %d ma polohu %s misto %s" % (n, nodes[n], d["xy"]))
    for n in plan.deleted_nodes:
        if n in nodes:
            errs.append("stary uzel trasy %d stale existuje" % n)
    for nl in plan.new_links:
        for (a, b) in ((nl["a"], nl["b"]), (nl["b"], nl["a"])):
            if (nl["no"], a, b) not in links:
                errs.append("chybi novy link %d %d->%d" % (nl["no"], a, b))
    for rc in plan.remapped:
        for (_df, _dt, nf, nt) in rc["dirs"]:
            if (rc["no"], nf, nt) not in links:
                errs.append("chybi prepojena rampa %d %d->%d" % (rc["no"], nf, nt))
    turns = v_turn_vianodes(visum)
    if turns is not None:
        for n in plan.new_nodes:
            if n in nodes and turns.get(n, 0) == 0:
                deg = sum(1 for nl in plan.new_links if n in (nl["a"], nl["b"]))
                if deg > 1:
                    errs.append("uzel %d nema zadna odboceni (turns)" % n)
    if errs:
        raise Fail("%s - kontrola site selhala:\n  %s" % (label, "\n  ".join(errs[:40])))


# ---------------------------------------------------------------------------
# reporty
# ---------------------------------------------------------------------------

def save_csv(path, rows, fields=None):
    import csv
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
    with open(str(path), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def save_preview_geojson(path, plan, model):
    feats = []
    for n, d in plan.new_nodes.items():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(d["xy"])},
                      "properties": {"kind": "novy_uzel", "no": n, "source": d["source"],
                                     "station_m": round(d["station"], 1)}})
    for nl in plan.new_links:
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(p) for p in nl["pts"]]},
                      "properties": {"kind": "novy_link_trasy", "no": nl["no"], "from": nl["a"], "to": nl["b"],
                                     "attr_from_old_link": nl.get("old_no")}})
    for rc in plan.remapped:
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(p) for p in rc["pts"]]},
                      "properties": {"kind": "prepojena_rampa", "no": rc["no"], "from": rc["nf"], "to": rc["nt"],
                                     "old_from": rc["f"], "old_to": rc["t"]}})
    for no in sorted(plan.route_links):
        f, t = model.link_nodes(no)
        feats.append({"type": "Feature", "geometry": {"type": "LineString",
                                                      "coordinates": [list(p) for p in model.geometry(f, t)]},
                      "properties": {"kind": "smazany_stary_link" if no in plan.old_main else "smazany_stary_jizdni_pas",
                                     "no": no, "from": f, "to": t}})
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")


# ---------------------------------------------------------------------------
# hlavni beh
# ---------------------------------------------------------------------------

def collect_max_numbers(tra_dir, net_nodes, net_links):
    max_node = max(net_nodes) if net_nodes else 0
    max_link = max(net_links) if net_links else 0
    rx_node = re.compile(r"^\$\+NODE:")
    for p in Path(tra_dir).glob("*.tra"):
        try:
            tra = Tra(p)
        except Exception:
            continue
        for tag, cur in (("+NODE", "node"), ("+LINK", "link")):
            t = tra.table(tag)
            if t is None or "NO" not in t.idx:
                continue
            for r in t.rows:
                try:
                    v = to_int(r[t.idx["NO"]])
                except Exception:
                    continue
                if cur == "node":
                    max_node = max(max_node, v)
                else:
                    max_link = max(max_link, v)
    return max_node, max_link


def run(cfg, visum=None, log=None):
    if log is None:
        log = print
    mode = cfg["mode"]
    project = resolve(cfg["project_dir"], SCRIPT_DIR)
    base_ver = resolve(cfg["base_version"], project)
    tra_dir = resolve(cfg["tra_dir"], project)
    routes_dir = resolve(cfg["routes_dir"], SCRIPT_DIR)
    vpdb = resolve(cfg["vpdb"], project) if cfg.get("vpdb") else None
    if not base_ver.exists():
        raise Fail("Nenalezena base verze: %s" % base_ver)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = resolve(cfg["work_dir"], project) / (stamp + "_" + mode.replace("-", "_"))
    work.mkdir(parents=True, exist_ok=True)
    report_dir = work / "report"
    non_default = cfg["non_default_only"]
    if non_default == "auto":
        non_default = False
        if vpdb and vpdb.exists():
            con = sqlite3.connect("file:%s?mode=ro" % vpdb.resolve().as_posix(), uri=True)
            try:
                non_default = bool(con.execute("SELECT MODIFICATIONSHIDEDEFAULTVALUES FROM PROJECTPARAMETERS")
                                   .fetchone()[0])
            finally:
                con.close()

    log("R1 VARIANTY - nahrada tras (rezim %s)" % mode)
    log("base: %s" % base_ver)
    log("tra:  %s" % tra_dir)
    if cfg.get("source_tra_dir"):
        log("zdroj puvodnich .tra: %s" % resolve(cfg["source_tra_dir"], project))
    log("work: %s" % work)

    # --- priprava vstupu a seznamu modifikaci ---------------------------------
    jobs = []
    for m in cfg["geometry_modifications"]:
        src_dir = resolve(cfg["source_tra_dir"], project) if cfg.get("source_tra_dir") else tra_dir
        tra_path = resolve(m["tra"], src_dir)
        tra_dst = resolve(m["tra"], tra_dir)
        if not tra_path.exists():
            raise Fail("Nenalezena geometry modifikace %s: %s" % (m["code"], tra_path))
        gno = m.get("no") or mod_no_from_tra(tra_path.name)
        widths = m.get("width_modifications")
        if widths is None:
            widths = width_mods_from_vpdb(vpdb, gno)
            if widths is None:
                raise Fail("Nelze zjistit sirkove modifikace pro %s: chybi %s a v configu neni "
                           "width_modifications." % (m["code"], vpdb))
        widths = [dict(w, path=resolve(w["tra"], src_dir), dst=resolve(w["tra"], tra_dir)) for w in widths]
        for w in widths:
            if not w["path"].exists():
                raise Fail("Nenalezena sirkova modifikace %s (%s) zavisla na %s" % (w.get("code"), w["path"], m["code"]))
        parts = []
        for p in m["parts"]:
            rp, np_ = resolve(p["route"], routes_dir), resolve(p["nodes"], routes_dir)
            for f in (rp, np_):
                if not f.exists():
                    raise Fail("Nenalezen vstupni soubor %s" % f)
            parts.append({"identity": p["identity"], "lines": load_lines(rp), "points": load_points(np_),
                          "route_file": str(rp), "nodes_file": str(np_)})
        jobs.append({"cfg": m, "tra": tra_path, "dst": tra_dst, "widths": widths, "parts": parts, "no": gno})
        log("%s (M%06d): %s | sirkove: %s" % (m["code"], gno or 0, ", ".join(
            "%s:%d bodu" % (p["identity"], len(p["points"])) for p in parts),
            ", ".join("%s" % w.get("code", w["tra"]) for w in widths) or "zadne"))

    if visum is None:
        visum = get_visum()

    # --- cislovani -------------------------------------------------------------
    v_load(visum, base_ver)
    base_nodes = v_nodes(visum)
    base_links = set(no for no, _f, _t in v_links(visum))
    mx_node, mx_link = collect_max_numbers(tra_dir, base_nodes, base_links)
    alloc = {
        "node": int(cfg["new_node_no_start"]) if cfg["new_node_no_start"] != "auto" else mx_node + 1,
        "link": int(cfg["new_link_no_start"]) if cfg["new_link_no_start"] != "auto" else mx_link + 1,
    }
    if alloc["node"] <= mx_node or alloc["link"] <= mx_link:
        raise Fail("new_node_no_start/new_link_no_start koliduje s existujicimi cisly (max uzel %d, max link %d)"
                   % (mx_node, mx_link))
    log("nova cisla uzlu od %d, linku od %d" % (alloc["node"], alloc["link"]))

    results = []
    all_junction, all_links, all_width_log, warnings = [], [], [], []
    outputs = []   # (src_canonical, target_path)

    for job in jobs:
        m = job["cfg"]
        code = m["code"]
        log("\n=== %s ===" % code)
        old_tra = Tra(job["tra"])
        width_tras = [Tra(w["path"]) for w in job["widths"]]

        v_load(visum, base_ver)
        v_apply(visum, job["tra"])
        node_xy = v_nodes(visum)
        net_links = v_links(visum)
        model = OldModel(old_tra, node_xy)
        plan = build_plan(model, m, cfg, width_tras, job["parts"], alloc, node_xy, net_links)
        warnings.extend(plan.warnings)
        log("smazano: %d linku stareho komplexu trasy (z toho %d hlavnich), %d vlozenych uzlu"
            % (len(plan.route_links), len(plan.old_main), len(plan.deleted_nodes)))
        log("vlozeno: %d novych uzlu, %d novych linku trasy, %d napojeni stavajici site"
            % (len(plan.new_nodes), len([x for x in plan.new_links if x["kind"] == "trasa"]),
               len(plan.remapped) + len([x for x in plan.new_links if x["kind"] != "trasa"])))

        gdir = work / code
        patch = gdir / ("patch_" + job["tra"].name)
        write_tra(patch, old_tra.version_line, plan_patch_tables(plan, model), old_tra.newline, False,
                  "patch nahrady trasy %s" % code)
        save_preview_geojson(gdir / "nahled_nova_trasa.geojson", plan, model)
        v_apply(visum, patch)
        verify_network(visum, plan, "%s (base + stara modifikace + patch)" % code)
        g_after = gdir / "geom_after.ver"
        v_save(visum, g_after)
        new_geom_tra = gdir / "canonical" / job["tra"].name
        v_diff(visum, base_ver, g_after, new_geom_tra, non_default)

        # nezavisla kontrola: base + nova .tra
        v_load(visum, base_ver)
        v_apply(visum, new_geom_tra)
        verify_network(visum, plan, "%s (base + nova geometry .tra)" % code)
        g_canon = gdir / "geom_canonical.ver"
        v_save(visum, g_canon)
        outputs.append((new_geom_tra, job["dst"]))
        log("nova geometry .tra: %s" % new_geom_tra)

        wres = []
        for w, wt in zip(job["widths"], width_tras):
            tables, stats, wlog = map_width_tables(wt, plan, model)
            wpatch = gdir / ("patch_" + w["path"].name)
            write_tra(wpatch, wt.version_line, tables, wt.newline, False,
                      "patch sirkove modifikace %s" % w.get("code"))
            v_load(visum, g_canon)
            v_apply(visum, wpatch)
            w_after = gdir / ("width_after_%s.ver" % w["path"].stem)
            v_save(visum, w_after)
            new_w_tra = gdir / "canonical" / w["path"].name
            v_diff(visum, g_canon, w_after, new_w_tra, non_default)
            # kontrola: base + geom + width
            v_load(visum, base_ver)
            v_apply(visum, new_geom_tra)
            v_apply(visum, new_w_tra)
            outputs.append((new_w_tra, w["dst"]))
            for k, (tag, key, what) in enumerate(wlog):
                all_width_log.append({"geometry": code, "width": w.get("code"), "file": w["path"].name,
                                      "table": tag, "key": key, "action": what})
            wres.append({"code": w.get("code"), "tra": str(w["path"]), "canonical": str(new_w_tra),
                         "stats": dict(stats)})
            log("  %s (%s): %s" % (w.get("code"), w["path"].name,
                                   ", ".join("%s=%d" % kv for kv in sorted(stats.items()))))
            for p in (w_after,):
                try:
                    p.unlink()
                except Exception:
                    pass

        all_junction.extend(plan.junction_rows)
        all_links.extend(plan.link_rows)
        results.append({
            "code": code, "tra": str(job["tra"]), "canonical": str(new_geom_tra),
            "old_main_links": sorted(plan.old_main), "old_carriageway_links": sorted(plan.old_carriage),
            "deleted_nodes": sorted(plan.deleted_nodes), "kept_anchor_nodes": sorted(plan.anchors),
            "new_nodes": dict((str(k), {"x": v["xy"][0], "y": v["xy"][1], "source": v["source"]})
                              for k, v in plan.new_nodes.items()),
            "new_links": [nl["no"] for nl in plan.new_links],
            "remapped_connectors": [{"no": r["no"], "old": [r["f"], r["t"]], "new": [r["nf"], r["nt"]],
                                     "old_len_m": round(r["old_len"], 1), "new_len_m": round(r["new_len"], 1)}
                                    for r in plan.remapped],
            "width_modifications": wres,
        })

    save_csv(report_dir / "napojeni_stavajici_site.csv", all_junction)
    save_csv(report_dir / "nove_linky.csv", all_links)
    save_csv(report_dir / "sirkove_modifikace_radky.csv", all_width_log)
    summary = {"status": "PASS", "mode": mode, "timestamp": stamp, "vpdb_modified": False,
               "report_dir": str(report_dir),
               "warnings": warnings, "results": results}
    (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log("\nUPOZORNENI (%d):" % len(warnings))
    for w in warnings:
        log("  - " + w)

    if mode == "dry-run":
        log("\nDRY-RUN PASS. Puvodni .tra nebyly zmeneny. Kandidati: %s\\<kod>\\canonical" % work)
        log("Report: %s" % report_dir)
        return summary

    backup = resolve(cfg["backup_dir"], project) / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for _src, dst in outputs:
        shutil.copy2(str(dst), str(backup / dst.name))
    try:
        for src, dst in outputs:
            tmp = Path(str(dst) + ".tmp_route_replace")
            shutil.copy2(str(src), str(tmp))
            os.replace(str(tmp), str(dst))
    except Exception:
        for _src, dst in outputs:
            b = backup / dst.name
            if b.exists():
                shutil.copy2(str(b), str(dst))
        raise
    log("\nAPPLY PASS. Nahrazeno %d .tra souboru. Zaloha: %s" % (len(outputs), backup))
    summary["backup"] = str(backup)
    (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


class RunLog(object):
    """Zapis prubehu do LAST_RUN.log vedle skriptu (a na konci i do report/run.log).
    V tichem rezimu (silent) se nic nevypisuje do Visumu -> zadna vyskakovaci okna."""

    def __init__(self, path, echo):
        self.path = Path(path)
        self.echo = echo
        self.lines = []
        self.path.write_text("", encoding="utf-8")

    def __call__(self, msg=""):
        line = "%s  %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg)
        self.lines.append(line)
        with open(str(self.path), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.echo:
            print(msg)

    def copy_to(self, path):
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(self.path), str(path))
        except Exception:
            pass


def main():
    silent = True
    try:
        raw = json.loads(_repair_windows_paths(CONFIG.read_text(encoding="utf-8-sig")))
        silent = bool(raw.get("silent", True))
    except Exception:
        pass
    log = RunLog(SCRIPT_DIR / "LAST_RUN.log", echo=not silent)
    status = SCRIPT_DIR / "LAST_STATUS.txt"
    for f in (SCRIPT_DIR / "LAST_ERROR.txt", status):
        try:
            f.unlink()
        except Exception:
            pass
    try:
        cfg = load_config()
        summary = run(cfg, log=log)
        log("VYSLEDEK: PASS (%s)" % cfg["mode"])
        status.write_text("PASS %s %s\n" % (cfg["mode"], dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                          encoding="utf-8")
        if summary and summary.get("report_dir"):
            log.copy_to(Path(summary["report_dir"]) / "run.log")
        return summary
    except Exception as exc:
        tb = traceback.format_exc()
        log("FAIL: %s" % exc)
        log(tb)
        log("VYSLEDEK: FAIL")
        try:
            (SCRIPT_DIR / "LAST_ERROR.txt").write_text("%s\n\n%s" % (exc, tb), encoding="utf-8")
            status.write_text("FAIL %s\n%s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), exc),
                              encoding="utf-8")
        except Exception:
            pass
        if not silent:
            raise
        return None


if __name__ == "__main__" or "Visum" in globals():
    main()
