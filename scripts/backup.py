#!/usr/bin/env python3
"""Consistent SQLite online backup; never copies a live .sqlite file directly."""
import argparse
from pathlib import Path
import sqlite3
import os
import sys
import hashlib
from urllib.parse import quote
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from trade_pricing.config import read_config


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',default='private/server.json');ap.add_argument('--out',required=True);args=ap.parse_args()
    cfg=read_config(args.config);source=Path(cfg.database).resolve();out=Path(args.out).resolve()
    if not source.exists():raise SystemExit('Source database does not exist')
    if out.exists():raise SystemExit('Refusing to overwrite an existing backup')
    out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        src=sqlite3.connect('file:'+quote(str(source),safe='/')+'?mode=ro',uri=True)
        dst=sqlite3.connect(out)
        try:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup failed integrity check')
        finally:src.close();dst.close()
    except Exception:
        out.unlink(missing_ok=True);raise
    print(f'Backup verified: {out}\nSHA256: {hashlib.sha256(out.read_bytes()).hexdigest()}\nContains private business data. Protect this file.')


if __name__=='__main__':main()
