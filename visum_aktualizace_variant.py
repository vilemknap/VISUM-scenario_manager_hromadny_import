# -*- coding: utf-8 -*-
"""
R1 VARIANTY - AKTUALIZACE jednotlivych variant (samostatny skript)

Pouziti: kdyz uz je nahrada tras jednou provedena (apply) a pro nekterou variantu
prisla nova osa nebo nove velke kruhy.

Skript:
  1. vezme PUVODNI .tra dane varianty (geometrie + jeji S1/S2/S3) ze zalohy
     backup_modifications (automaticky najde zalohu s puvodnimi soubory),
  2. nahradi trasu novymi podklady stejnym postupem jako visum_bulk_replace_geometry.py,
  3. prepise JEN soubory teto varianty v Modifications (po zaloze aktualniho stavu).
Ostatni varianty ani R1_VARIANTY.vpdb nemeni.

Spusteni: PTV Visum 2026 -> Scripts -> Run script file... -> tento soubor.
Potrebuje vedle sebe: visum_bulk_replace_geometry.py, config.json (cesty a volby variant)
a config_aktualizace.json (co se aktualizuje).
"""
from __future__ import print_function

import copy
import datetime as dt
import importlib.util
import json
import os
import traceback
from pathlib import Path

try:
    SCRIPT_DIR = Path(__file__).resolve().parent
except NameError:
    SCRIPT_DIR = Path(os.getcwd())


def _load_main_module():
    path = SCRIPT_DIR / "visum_bulk_replace_geometry.py"
    if not path.exists():
        raise RuntimeError("Vedle skriptu chybi visum_bulk_replace_geometry.py")
    spec = importlib.util.spec_from_file_location("r1_route_replace", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCRIPT_DIR = SCRIPT_DIR
    mod.CONFIG = SCRIPT_DIR / "config.json"
    return mod


def _is_original(tra_path):
    """Puvodni soubor (ne vystup tohoto skriptu): v hlavicce neni '* From:'."""
    try:
        with open(str(tra_path), "r", encoding="utf-8-sig", errors="replace") as fh:
            head = [next(fh, "") for _ in range(12)]
    except Exception:
        return False
    return not any(line.startswith("* From:") for line in head)


def find_original_source(M, project, cfg, needed):
    backup_root = M.resolve(cfg.get("backup_dir", "backup_modifications"), project)
    if not backup_root.exists():
        raise M.Fail("Nenalezena slozka zaloh %s - nastavte source_tra_dir v config_aktualizace.json" % backup_root)
    for d in sorted(p for p in backup_root.iterdir() if p.is_dir()):
        if all((d / n).exists() and _is_original(d / n) for n in needed):
            return d
    raise M.Fail("V %s neni zadna zaloha, ktera by obsahovala puvodni soubory %s. "
                 "Nastavte source_tra_dir v config_aktualizace.json." % (backup_root, ", ".join(needed)))


def main():
    M = _load_main_module()
    upd_path = SCRIPT_DIR / "config_aktualizace.json"
    silent = True
    try:
        silent = bool(json.loads(upd_path.read_text(encoding="utf-8-sig")).get("silent", True))
    except Exception:
        pass
    log = M.RunLog(SCRIPT_DIR / "LAST_RUN_aktualizace.log", echo=not silent)
    status = SCRIPT_DIR / "LAST_STATUS_aktualizace.txt"
    for f in (status, SCRIPT_DIR / "LAST_ERROR_aktualizace.txt"):
        try:
            f.unlink()
        except Exception:
            pass
    try:
        if not upd_path.exists():
            raise M.Fail("Chybi config_aktualizace.json vedle skriptu (zkopirujte config_aktualizace.example.json)")
        upd = json.loads(M._repair_windows_paths(upd_path.read_text(encoding="utf-8-sig")))
        cfg = M.load_config()                       # config.json: cesty + volby variant
        cfg = copy.deepcopy(cfg)
        cfg["mode"] = upd.get("mode", "dry-run")
        if cfg["mode"] not in ("dry-run", "apply"):
            raise M.Fail('mode v config_aktualizace.json musi byt "dry-run" nebo "apply"')
        cfg["work_dir"] = upd.get("work_dir", "_route_update_work")
        project = M.resolve(cfg["project_dir"], SCRIPT_DIR)

        wanted = upd.get("variants") or {}
        wanted = dict((k, v) for k, v in wanted.items() if v.get("enabled", True))
        if not wanted:
            raise M.Fail("V config_aktualizace.json neni zapnuta zadna varianta (variants)")
        by_code = dict((m["code"], m) for m in cfg["geometry_modifications"])
        mods = []
        for code, ov in wanted.items():
            if code not in by_code:
                raise M.Fail("Varianta %s neni v config.json (geometry_modifications)" % code)
            m = copy.deepcopy(by_code[code])
            for k, v in ov.items():
                if k in ("parts", "enabled"):
                    continue
                m[k] = v
            if "parts" in ov:
                for i, p in enumerate(ov["parts"]):
                    m["parts"][i].update(p)
            mods.append(m)
        cfg["geometry_modifications"] = mods

        # zdroj puvodnich .tra
        src = upd.get("source_tra_dir", "auto")
        if src in (None, "", "auto"):
            needed = []
            vpdb = M.resolve(cfg["vpdb"], project)
            for m in mods:
                needed.append(m["tra"])
                ws = m.get("width_modifications") or M.width_mods_from_vpdb(
                    vpdb, m.get("no") or M.mod_no_from_tra(m["tra"])) or []
                needed += [w["tra"] for w in ws]
            src_dir = find_original_source(M, project, cfg, needed)
        else:
            src_dir = M.resolve(src, project)
        cfg["source_tra_dir"] = str(src_dir)
        log("AKTUALIZACE variant: %s" % ", ".join(m["code"] for m in mods))
        log("puvodni soubory ze zalohy: %s" % src_dir)

        visum = globals().get("Visum")
        if visum is None:
            try:
                import __main__
                visum = getattr(__main__, "Visum", None)
            except Exception:
                visum = None
        summary = M.run(cfg, visum=visum, log=log)
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
            (SCRIPT_DIR / "LAST_ERROR_aktualizace.txt").write_text("%s\n\n%s" % (exc, tb), encoding="utf-8")
            status.write_text("FAIL %s\n%s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), exc),
                              encoding="utf-8")
        except Exception:
            pass
        if not silent:
            raise
        return None


if __name__ == "__main__" or "Visum" in globals():
    main()
