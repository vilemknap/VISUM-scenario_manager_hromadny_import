# -*- coding: utf-8 -*-
"""
R1 VARIANTY - PRENOS NOVE BASE VERZE do projektu s modifikacemi (rebase modifikaci)

Situace: projekt ZDROJ ma upravenou base verzi (napr. obchvaty), projekt CIL ma aktualni
modifikace (nove trasy, rucni upravy). Cil ma dostat novou base verzi a jeho modifikace
se maji "prestehovat" tak, aby na ni sedely.

Pro kazdou geometrickou modifikaci cile (a jeji zavisle sirkove modifikace) skript:
  * uzly stare base, ktere v nove base nejsou, ale na stejnem miste (<= node_map_tolerance_m)
    je novy uzel, prepise na tento novy uzel (napr. 1002957 -> 99800074),
  * vkladane linky (a uzly), jejichz cislo uz v nove base existuje, precisluje na volna cisla
    a stejne precislovani provede v zavislych sirkovych modifikacich,
  * mazani starych linku, ktere nova base rozdelila novymi uzly, prevede na smazani
    vsech nove vzniklych useku (nove uzly a ostatni linky - obchvat - zustanou),
  * uzly ze seznamu keep_nodes nemaze (smaze jen jejich puvodni linky, ktere modifikace
    mazala a ktere v nove base stale existuji),
  * vypusti radky odboceni, pruhu a linku, ktere v nove base nemaji odpovidajici objekt,
  * necha Visum modifikaci vygenerovat znovu vuci nove base a overi, ze se propsala cela.
V rezimu apply zazalohuje cil (base + .tra) a nahradi base verzi i modifikace. .vpdb se nemeni.

Spusteni: PTV Visum 2026 -> Scripts -> Run script file... -> tento soubor.
Vedle skriptu: visum_bulk_replace_geometry.py a config_prenos_base.json.
Vystup: LAST_STATUS_prenos_base.txt, LAST_RUN_prenos_base.log, slozka _base_transfer_work v cili.
"""
from __future__ import print_function

import collections
import datetime as dt
import importlib.util
import json
import math
import os
import shutil
import sqlite3
import traceback
from pathlib import Path

try:
    SCRIPT_DIR = Path(__file__).resolve().parent
except NameError:
    SCRIPT_DIR = Path(os.getcwd())

CFG_NAME = "config_prenos_base.json"
NODE_COLS = ("FROMNODENO", "TONODENO", "VIANODENO", "NODENO")


def _load_main_module():
    path = SCRIPT_DIR / "visum_bulk_replace_geometry.py"
    if not path.exists():
        raise RuntimeError("Vedle skriptu chybi visum_bulk_replace_geometry.py")
    spec = importlib.util.spec_from_file_location("r1_route_replace", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCRIPT_DIR = SCRIPT_DIR
    return mod


def read_mods(vpdb):
    con = sqlite3.connect("file:%s?mode=ro" % Path(vpdb).resolve().as_posix(), uri=True)
    try:
        mods = collections.OrderedDict()
        for no, code, lo, dep in con.execute(
                "SELECT NO, CODE, LOADORDER, DEPENDENTON FROM MODIFICATION ORDER BY LOADORDER, NO"):
            deps = [int(d) for d in str(dep or "").replace(";", ",").split(",") if d.strip()]
            mods[int(no)] = {"no": int(no), "code": code, "order": int(lo or 0), "deps": deps}
        hide = bool(con.execute("SELECT MODIFICATIONSHIDEDEFAULTVALUES FROM PROJECTPARAMETERS").fetchone()[0])
    finally:
        con.close()
    return mods, hide


class Ctx(object):
    pass


def node_of(M, t, r, c):
    v = t.get(r, c)
    return M.to_int(v) if v != "" else None


def rebase_geometry(M, ctx, tra, log):
    """Prevede geometrickou modifikaci na novou base. Vraci (tabulky, mapa linku, mapa uzlu, varovani)."""
    warn = []
    nmap = dict(ctx.node_map)                    # stary uzel -> novy uzel
    lmap = {}                                    # stare cislo vkladaneho linku -> nove
    new_links = ctx.new_links                    # set (no,f,t)
    new_pairs = ctx.new_pairs                    # (f,t) -> no
    new_nodes = ctx.new_nodes
    old_nodes = ctx.old_nodes

    # vkladane uzly kolidujici s novou base
    tn = tra.table("+NODE")
    ins_nodes = set()
    if tn is not None:
        for r in tn.rows:
            n = M.to_int(tn.get(r, "NO"))
            if n in new_nodes:
                nmap[n] = ctx.next_node
                warn.append("vkladany uzel %d uz v nove base existuje -> precislovan na %d" % (n, ctx.next_node))
                ctx.next_node += 1
            ins_nodes.add(nmap.get(n, n))

    def nm(n):
        return nmap.get(n, n)

    # mazane uzly
    del_nodes, keep_node_links = set(), set()
    t = tra.table("-NODE")
    if t is not None:
        for r in t.rows:
            n = nm(M.to_int(t.get(r, "NO")))
            if n not in new_nodes:
                warn.append("-NODE %d: uzel v nove base neni - vypusteno" % n)
            elif n in ctx.keep_nodes:
                warn.append("-NODE %d: ponechan (keep_nodes); smazou se jen jeho puvodni linky" % n)
                for (no, f, to) in new_links:
                    if n in (f, to) and no in ctx.old_link_nos:
                        keep_node_links.add(no)
            else:
                del_nodes.add(n)

    # mazane linky
    del_link_keys = collections.OrderedDict()   # no -> (f,t) v nove base
    t = tra.table("-LINK")
    if t is not None:
        for r in t.rows:
            no, f, to = M.to_int(t.get(r, "NO")), nm(M.to_int(t.get(r, "FROMNODENO"))), nm(M.to_int(t.get(r, "TONODENO")))
            if (no, f, to) in new_links or (no, to, f) in new_links:
                del_link_keys[no] = (f, to)
                continue
            if f in ctx.keep_nodes or to in ctx.keep_nodes:
                warn.append("-LINK %d (%d-%d): u ponechaneho uzlu a v nove base neexistuje - vypusteno" % (no, f, to))
                continue
            if (f, to) in new_pairs:
                no2 = new_pairs[(f, to)]
                del_link_keys[no2] = (f, to)
                warn.append("-LINK %d (%d-%d): v nove base ma cislo %d" % (no, f, to, no2))
                continue
            chain = ctx.find_chain(f, to)
            if chain:
                for k in chain:
                    del_link_keys[k[0]] = (k[1], k[2])
                warn.append("-LINK %d (%d-%d): v nove base rozdelen -> mazou se useky %s (uzly obchvatu zustavaji)"
                            % (no, f, to, [k[0] for k in chain]))
                continue
            warn.append("-LINK %d (%d-%d): v nove base neni ani jeho nahrada - vypusteno" % (no, f, to))
    for no in keep_node_links:
        if no not in del_link_keys:
            k = [x for x in new_links if x[0] == no][0]
            del_link_keys[no] = (k[1], k[2])

    deleted_nos = set(del_link_keys)
    newly = collections.defaultdict(set)
    for (no, f, to) in new_links:
        if f in del_nodes or to in del_nodes:
            deleted_nos.add(no)
            if no not in ctx.old_link_nos:
                newly[f if f in del_nodes else to].add(no)
    for n, nos in sorted(newly.items()):
        warn.append("POZOR: -NODE %d smaze v nove base i nove linky %s (pripojene novou base)" % (n, sorted(nos)))

    # vkladane linky - kolize cisel
    tl = tra.table("+LINK")
    ins_pairs = set()
    if tl is not None:
        for no in sorted(set(M.to_int(tl.get(r, "NO")) for r in tl.rows)):
            if no in ctx.new_link_nos and no not in deleted_nos:
                lmap[no] = ctx.next_link
                ctx.next_link += 1
        if lmap:
            warn.append("precislovano %d vkladanych linku (cislo uz v nove base): %s"
                        % (len(lmap), ", ".join("%d->%d" % kv for kv in sorted(lmap.items()))))
        for r in tl.rows:
            ins_pairs.add((nm(M.to_int(tl.get(r, "FROMNODENO"))), nm(M.to_int(tl.get(r, "TONODENO")))))

    avail_nodes = (new_nodes - del_nodes) | ins_nodes
    del_pairs = set()
    for (no, f, to) in new_links:
        if no in deleted_nos:
            del_pairs.add((f, to))
    avail_pairs = (set(new_pairs) - del_pairs) | ins_pairs

    out = []
    dropped = collections.Counter()
    for t in tra.tables:
        if t.name == "VERSION":
            continue
        tag = t.tag()
        if tag == "-LINK":
            nt = M.Table("-", "LINK", ["NO", "FROMNODENO", "TONODENO"])
            nt.rows = [[str(no), str(f), str(to)] for no, (f, to) in del_link_keys.items()]
            out.append(nt)
            continue
        if tag == "-NODE":
            nt = M.Table("-", "NODE", ["NO"])
            nt.rows = [[str(n)] for n in sorted(del_nodes)]
            out.append(nt)
            continue
        nt = M.Table(t.op, t.name, t.header)
        for r in t.rows:
            r2 = list(r)
            for c in NODE_COLS:
                if c in t.idx and r2[t.idx[c]] != "":
                    r2[t.idx[c]] = str(nm(M.to_int(r2[t.idx[c]])))
            if t.name == "NODE" and "NO" in t.idx:
                r2[t.idx["NO"]] = str(nm(M.to_int(r2[t.idx["NO"]])))
            if t.name == "LINK" and "NO" in t.idx and t.op == "+":
                no = M.to_int(r2[t.idx["NO"]])
                r2[t.idx["NO"]] = str(lmap.get(no, no))
            if "LINKNO" in t.idx and r2[t.idx["LINKNO"]] != "":
                no = M.to_int(r2[t.idx["LINKNO"]])
                r2[t.idx["LINKNO"]] = str(lmap.get(no, no))
            g = lambda c: M.to_int(r2[t.idx[c]]) if c in t.idx and r2[t.idx[c]] != "" else None
            ok = True
            if t.name == "TURN":
                f, v, to = g("FROMNODENO"), g("VIANODENO"), g("TONODENO")
                ok = (f, v) in avail_pairs and (v, to) in avail_pairs
            elif t.name in ("LANE", "LINKPOLY") and t.op != "+" or (t.name == "LANE" and t.op == "+"):
                ok = (g("FROMNODENO"), g("TONODENO")) in avail_pairs
            elif t.name == "LINK" and t.op == "*":
                no, f, to = g("NO"), g("FROMNODENO"), g("TONODENO")
                if (no, f, to) not in new_links:
                    if (f, to) in avail_pairs and (f, to) in new_pairs:
                        r2[t.idx["NO"]] = str(new_pairs[(f, to)])
                    else:
                        ok = False
            elif t.name == "LINK" and t.op == "+":
                ok = g("FROMNODENO") in avail_nodes and g("TONODENO") in avail_nodes
                if not ok:
                    raise M.Fail("%s: vkladany link %s vede do uzlu, ktery v nove base neni (%s-%s)"
                                 % (tra.path.name, r2[t.idx["NO"]], r2[t.idx["FROMNODENO"]], r2[t.idx["TONODENO"]]))
            elif t.name == "COUNTLOCATION" and t.op == "-":
                ok = M.to_int(r2[t.idx["NO"]]) in ctx.new_countlocations if ctx.new_countlocations is not None else True
            elif t.name == "COUNTLOCATION" and t.op == "+":
                ok = (g("FROMNODENO"), g("TONODENO")) in avail_pairs
            elif t.op in ("*", "-") and t.name not in ("USERATTDEF",):
                for c in NODE_COLS:
                    n = g(c)
                    if n is not None and n not in avail_nodes:
                        ok = False
            if ok:
                nt.rows.append(r2)
            else:
                dropped[tag] += 1
        out.append(nt)
    for tag, n in sorted(dropped.items()):
        warn.append("vypusteno %d radku %s (objekt v nove base neexistuje)" % (n, tag))
    return out, lmap, nmap, warn


def rebase_width(M, ctx, tra, lmap, nmap, net_links, log):
    warn = []
    pairs = {}
    for (no, f, to) in net_links:
        pairs[(f, to)] = no
    nodes_ok = set()
    for (no, f, to) in net_links:
        nodes_ok.update((f, to))
    out = []
    dropped = collections.Counter()
    for t in tra.tables:
        if t.name == "VERSION":
            continue
        nt = M.Table(t.op, t.name, t.header)
        for r in t.rows:
            r2 = list(r)
            for c in NODE_COLS:
                if c in t.idx and r2[t.idx[c]] != "":
                    n = M.to_int(r2[t.idx[c]])
                    r2[t.idx[c]] = str(nmap.get(n, n))
            if "NO" in t.idx and t.name == "LINK" and r2[t.idx["NO"]] != "":
                no = M.to_int(r2[t.idx["NO"]])
                r2[t.idx["NO"]] = str(lmap.get(no, no))
            if "LINKNO" in t.idx and r2[t.idx["LINKNO"]] != "":
                no = M.to_int(r2[t.idx["LINKNO"]])
                r2[t.idx["LINKNO"]] = str(lmap.get(no, no))
            g = lambda c: M.to_int(r2[t.idx[c]]) if c in t.idx and r2[t.idx[c]] != "" else None
            ok = True
            if t.name == "LINK":
                key = (g("NO"), g("FROMNODENO"), g("TONODENO"))
                if key not in net_links:
                    if (key[1], key[2]) in pairs:
                        r2[t.idx["NO"]] = str(pairs[(key[1], key[2])])
                    else:
                        ok = False
            elif t.name in ("LANE", "LINKPOLY", "COUNTLOCATION") and "FROMNODENO" in t.idx:
                ok = (g("FROMNODENO"), g("TONODENO")) in pairs
            elif t.name == "TURN":
                ok = (g("FROMNODENO"), g("VIANODENO")) in pairs and (g("VIANODENO"), g("TONODENO")) in pairs
            else:
                for c in NODE_COLS:
                    n = g(c)
                    if n is not None and n not in nodes_ok:
                        ok = False
            if ok:
                nt.rows.append(r2)
            else:
                dropped[t.tag()] += 1
        out.append(nt)
    for tag, n in sorted(dropped.items()):
        warn.append("vypusteno %d radku %s (link v nove siti neexistuje)" % (n, tag))
    return out, warn


def main():
    M = _load_main_module()
    cfg_path = SCRIPT_DIR / CFG_NAME
    silent = True
    try:
        silent = bool(json.loads(M._repair_windows_paths(cfg_path.read_text(encoding="utf-8-sig"))).get("silent", True))
    except Exception:
        pass
    log = M.RunLog(SCRIPT_DIR / "LAST_RUN_prenos_base.log", echo=not silent)
    status = SCRIPT_DIR / "LAST_STATUS_prenos_base.txt"
    for f in (status, SCRIPT_DIR / "LAST_ERROR_prenos_base.txt"):
        try:
            f.unlink()
        except Exception:
            pass
    try:
        if not cfg_path.exists():
            raise M.Fail("Chybi %s vedle skriptu (zkopirujte config_prenos_base.example.json)" % CFG_NAME)
        raw = cfg_path.read_text(encoding="utf-8-sig")
        try:
            cfg = json.loads(raw)
        except ValueError:
            cfg = json.loads(raw.replace("\\", "/"))
        mode = cfg.get("mode", "dry-run")
        if mode not in ("dry-run", "apply"):
            raise M.Fail('mode musi byt "dry-run" nebo "apply"')
        base_name = cfg.get("base_version", "R1_VARIANTYBase.ver")
        src_proj = Path(cfg["source_project_dir"].replace("\\", "/"))
        tgt_proj = Path(cfg["target_project_dir"].replace("\\", "/"))
        src_base = src_proj / base_name
        tgt_base = tgt_proj / base_name
        tgt_tra_dir = M.resolve(cfg.get("target_tra_dir", "Modifications"), tgt_proj)
        tgt_vpdb = M.resolve(cfg.get("target_vpdb", "R1_VARIANTY.vpdb"), tgt_proj)
        # varianta "jen modifikace": cil uz novou base ma, modifikace (proti stare base) se berou odjinud
        replace_base = bool(cfg.get("replace_base", True))
        ob = cfg.get("old_base_version")
        if ob == "auto":
            broot = M.resolve(cfg.get("backup_dir", "backup_pred_prenosem_base"), tgt_proj)
            cands = sorted(broot.glob("*/" + base_name)) if broot.exists() else []
            if not cands:
                raise M.Fail("old_base_version=auto: v %s neni zadna zaloha base (prenos base jeste neprobehl?)" % broot)
            old_base = cands[-1]
        elif ob:
            old_base = M.resolve(ob.replace("\\", "/"), tgt_proj)
        else:
            old_base = tgt_base
        mods_dir = M.resolve(cfg["source_tra_dir"].replace("\\", "/"), tgt_proj) if cfg.get("source_tra_dir") else tgt_tra_dir
        for p, what in ((src_base, "zdrojova (nova) base"), (old_base, "stara base"), (tgt_vpdb, "cilovy .vpdb"),
                        (tgt_tra_dir, "slozka modifikaci cile"), (mods_dir, "slozka s modifikacemi ke prenosu")):
            if not p.exists():
                raise M.Fail("Nenalezena %s: %s" % (what, p))
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        work = M.resolve(cfg.get("work_dir", "_base_transfer_work"), tgt_proj) / (stamp + "_" + mode.replace("-", "_"))
        work.mkdir(parents=True, exist_ok=True)
        log("PRENOS NOVE BASE + PRESTEHOVANI MODIFIKACI (rezim %s)" % mode)
        log("nova base (zdroj): %s" % src_base)
        log("cil: %s" % tgt_proj)
        log("stara base: %s | modifikace ze: %s | nahradit base v cili: %s"
            % (old_base, mods_dir, "ano" if replace_base else "ne"))
        log("work: %s" % work)

        visum = globals().get("Visum")
        if visum is None:
            try:
                import __main__
                visum = getattr(__main__, "Visum", None)
            except Exception:
                visum = None
        if visum is None:
            visum = M.get_visum()

        mods, hide = read_mods(tgt_vpdb)
        only = cfg.get("only_geometry_modifications")
        ctx = Ctx()
        ctx.keep_nodes = set(int(x) for x in cfg.get("keep_nodes", []))

        # stara a nova base
        M.v_load(visum, old_base)
        old_xy = M.v_nodes(visum)
        old_links = M.v_links(visum)
        M.v_load(visum, src_base)
        new_xy = M.v_nodes(visum)
        new_links = M.v_links(visum)
        try:
            rows = visum.Net.CountLocations.GetMultipleAttributes(["NO"])
            ctx.new_countlocations = set(int(r[0]) for r in rows)
        except Exception:
            ctx.new_countlocations = None
        ctx.old_nodes = set(old_xy)
        ctx.new_nodes = set(new_xy)
        ctx.new_links = new_links
        ctx.new_link_nos = set(no for no, _f, _t in new_links)
        ctx.old_link_nos = set(no for no, _f, _t in old_links)
        ctx.new_pairs = dict(((f, t), no) for no, f, t in new_links)
        log("stara base: %d uzlu, %d smeru linku | nova base: %d uzlu, %d smeru linku"
            % (len(old_xy), len(old_links), len(new_xy), len(new_links)))

        tol = float(cfg.get("node_map_tolerance_m", 1.0))
        brand_new = [n for n in new_xy if n not in old_xy]
        ctx.node_map = {}
        for n in old_xy:
            if n in new_xy:
                continue
            p = old_xy[n]
            best = None
            for m in brand_new:
                q = new_xy[m]
                if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol:
                    d = math.hypot(q[0] - p[0], q[1] - p[1])
                    if d <= tol and (best is None or d < best[1]):
                        best = (m, d)
            if best:
                ctx.node_map[n] = best[0]
        if ctx.node_map:
            log("uzly nahrazene v nove base uzlem na stejnem miste: %s"
                % ", ".join("%d->%d" % kv for kv in sorted(ctx.node_map.items())))

        adj = collections.defaultdict(set)
        for no, f, t in new_links:
            adj[f].add((t, no))

        def find_chain(a, b, max_mid=4):
            # cesta a -> b pres uzly, ktere ve stare base nebyly
            from collections import deque
            q = deque([(a, [])])
            seen = {a}
            while q:
                n, path = q.popleft()
                for (m, no) in adj[n]:
                    if m == b and path is not None and len(path) >= 1:
                        return path + [(no, n, m)]
                    if m in seen or m in ctx.old_nodes or len(path) >= max_mid:
                        continue
                    seen.add(m)
                    q.append((m, path + [(no, n, m)]))
            return None
        ctx.find_chain = find_chain

        max_link = max(ctx.new_link_nos | ctx.old_link_nos)
        max_node = max(ctx.new_nodes | ctx.old_nodes)
        for p in list(tgt_tra_dir.glob("M*.tra")) + list(mods_dir.glob("M*.tra")):
            try:
                tr = M.Tra(p)
            except Exception:
                continue
            for tag, kind in (("+LINK", "l"), ("+NODE", "n")):
                t = tr.table(tag)
                if t is None:
                    continue
                for r in t.rows:
                    v = M.to_int(t.get(r, "NO"))
                    if kind == "l":
                        max_link = max(max_link, v)
                    else:
                        max_node = max(max_node, v)
        ctx.next_link = max_link + 1
        ctx.next_node = max_node + 1
        log("volna cisla: linky od %d, uzly od %d" % (ctx.next_link, ctx.next_node))

        geoms = [m for m in mods.values() if not m["deps"]]
        if only:
            geoms = [m for m in geoms if m["code"] in only]
        outputs = []
        all_warn = []
        for g in geoms:
            gpath = mods_dir / ("M%06d.tra" % g["no"])
            if not gpath.exists():
                raise M.Fail("Chybi %s (%s)" % (gpath.name, g["code"]))
            log("\n=== %s (%s) ===" % (g["code"], gpath.name))
            gtra = M.Tra(gpath)
            tables, lmap, nmap, warn = rebase_geometry(M, ctx, gtra, log)
            for w in warn:
                log("  - " + w)
                all_warn.append("%s: %s" % (g["code"], w))
            gdir = work / g["code"].replace("+", "_")
            patch = gdir / ("rebased_" + gpath.name)
            M.write_tra(patch, gtra.version_line, tables, gtra.newline, False, "rebase %s na novou base" % g["code"])
            M.v_load(visum, src_base)
            M.v_apply(visum, patch)
            gver = gdir / "geom_new_base.ver"
            M.v_save(visum, gver)
            net_links = M.v_links(visum)
            new_g = gdir / "canonical" / gpath.name
            M.v_diff(visum, src_base, gver, new_g, hide)
            M.v_load(visum, src_base)
            M.v_apply(visum, new_g)
            outputs.append((new_g, tgt_tra_dir / gpath.name))
            log("  nova %s OK" % gpath.name)
            for w in [m for m in mods.values() if g["no"] in m["deps"]]:
                wpath = mods_dir / ("M%06d.tra" % w["no"])
                if not wpath.exists():
                    raise M.Fail("Chybi %s (%s)" % (wpath.name, w["code"]))
                wtra = M.Tra(wpath)
                wtables, wwarn = rebase_width(M, ctx, wtra, lmap, nmap, net_links, log)
                for x in wwarn:
                    log("  - %s: %s" % (w["code"], x))
                    all_warn.append("%s: %s" % (w["code"], x))
                wpatch = gdir / ("rebased_" + wpath.name)
                M.write_tra(wpatch, wtra.version_line, wtables, wtra.newline, False, "rebase %s" % w["code"])
                M.v_load(visum, gver)
                M.v_apply(visum, wpatch)
                wver = gdir / ("width_%s.ver" % wpath.stem)
                M.v_save(visum, wver)
                new_w = gdir / "canonical" / wpath.name
                M.v_diff(visum, gver, wver, new_w, hide)
                M.v_load(visum, src_base)
                M.v_apply(visum, new_g)
                M.v_apply(visum, new_w)
                outputs.append((new_w, tgt_tra_dir / wpath.name))
                log("  nova %s (%s) OK" % (wpath.name, w["code"]))
                try:
                    wver.unlink()
                except Exception:
                    pass

        summary = {"status": "PASS", "mode": mode, "node_map": ctx.node_map, "warnings": all_warn,
                   "outputs": [str(d) for _s, d in outputs]}
        (work / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if mode == "dry-run":
            log("\nDRY-RUN PASS. Cil nebyl zmenen. Nove soubory: %s\\<kod>\\canonical" % work)
        else:
            backup = M.resolve(cfg.get("backup_dir", "backup_pred_prenosem_base"), tgt_proj) / stamp
            (backup / "Modifications").mkdir(parents=True, exist_ok=False)
            if replace_base:
                shutil.copy2(str(tgt_base), str(backup / base_name))
            for _src, dst in outputs:
                if dst.exists():
                    shutil.copy2(str(dst), str(backup / "Modifications" / dst.name))
            try:
                if replace_base:
                    tmp = Path(str(tgt_base) + ".tmp_base_transfer")
                    shutil.copy2(str(src_base), str(tmp))
                    os.replace(str(tmp), str(tgt_base))
                for src, dst in outputs:
                    tmp = Path(str(dst) + ".tmp_base_transfer")
                    shutil.copy2(str(src), str(tmp))
                    os.replace(str(tmp), str(dst))
            except Exception:
                if replace_base:
                    shutil.copy2(str(backup / base_name), str(tgt_base))
                for _src, dst in outputs:
                    b = backup / "Modifications" / dst.name
                    if b.exists():
                        shutil.copy2(str(b), str(dst))
                raise
            log("\nAPPLY PASS. %s%d modifikaci. Zaloha: %s"
                % ("Nahrazena base a " if replace_base else "Base beze zmeny, nahrazeno ", len(outputs), backup))
        log("VYSLEDEK: PASS (%s)" % mode)
        status.write_text("PASS %s %s\n" % (mode, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")), encoding="utf-8")
        log.copy_to(work / "run.log")
        return summary
    except Exception as exc:
        tb = traceback.format_exc()
        log("FAIL: %s" % exc)
        log(tb)
        log("VYSLEDEK: FAIL")
        try:
            (SCRIPT_DIR / "LAST_ERROR_prenos_base.txt").write_text("%s\n\n%s" % (exc, tb), encoding="utf-8")
            status.write_text("FAIL %s\n%s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), exc),
                              encoding="utf-8")
        except Exception:
            pass
        if not silent:
            raise
        return None


if __name__ == "__main__" or "Visum" in globals():
    main()
