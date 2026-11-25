#!/usr/bin/env python3
"""Sync main Jenkins job `android-pack` (安卓打包) to per-user child jobs.

Convention
----------
- Main pipeline : /job/android-pack/
- Child pipelines: /job/<user>/job/android-pack/  (under personal folders)

Workflow: change + verify main first, then run this script to push the same
build/notify logic to all children. Child-only fields are preserved/adapted:
  - displayName → 「安卓打包 · <user>」
  - Active Choices projectFullName / projectFullNameCache → 「<user>/android-pack」
  - AuthorizationMatrixProperty for that user (kept from existing child when present)

Auth / proxy (env)
------------------
  JENKINS_URL      default https://jenkins.njyz999.com
  JENKINS_USER     default admin
  JENKINS_PASS     required (or JENKINS_TOKEN)
  ALL_PROXY / HTTPS_PROXY optional, e.g. socks5h://127.0.0.1:1080

Examples
--------
  # dry-run: show which children would be updated
  JENKINS_PASS=*** python3 sync-android-pack-to-children.py --dry-run

  # sync all children
  JENKINS_PASS=*** ALL_PROXY=socks5h://127.0.0.1:1080 \\
    python3 sync-android-pack-to-children.py

  # sync one child only
  JENKINS_PASS=*** python3 sync-android-pack-to-children.py --only chenweili
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote


DEFAULT_URL = "https://jenkins.njyz999.com"
MAIN_JOB = "android-pack"


def env_pass() -> str:
    p = os.environ.get("JENKINS_PASS") or os.environ.get("JENKINS_TOKEN") or ""
    if not p:
        sys.exit("Set JENKINS_PASS (or JENKINS_TOKEN) in the environment.")
    return p


def curl_base(user: str, password: str) -> List[str]:
    cmd = ["curl", "-sS", "-u", f"{user}:{password}", "--connect-timeout", "20"]
    proxy = (
        os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    )
    # Prefer SOCKS hostname resolution when socks5h is set; for socks5:// still pass through.
    if proxy.startswith("socks5h://") or proxy.startswith("socks5://"):
        # curl --socks5-hostname wants host:port without scheme
        hostport = proxy.split("://", 1)[1]
        # strip userinfo if any
        if "@" in hostport:
            hostport = hostport.rsplit("@", 1)[-1]
        cmd += ["--socks5-hostname", hostport]
    elif proxy.startswith("http://") or proxy.startswith("https://"):
        cmd += ["-x", proxy]
    return cmd


def run_curl(args: List[str], user: str, password: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = curl_base(user, password) + args
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"curl failed ({r.returncode}): {r.stderr.strip() or r.stdout[:300]}")
    return r


def list_child_users(base_url: str, user: str, password: str) -> List[str]:
    """Folders at root that contain a job named android-pack."""
    r = run_curl(
        ["--get", f"{base_url.rstrip('/')}/api/json", "--data-urlencode", "tree=jobs[name,_class]"],
        user,
        password,
    )
    root = json.loads(r.stdout)
    children: List[str] = []
    for j in root.get("jobs") or []:
        if j.get("_class") != "com.cloudbees.hudson.plugins.folder.Folder":
            continue
        name = j["name"]
        rr = run_curl(
            [
                "--get",
                f"{base_url.rstrip('/')}/job/{quote(name)}/api/json",
                "--data-urlencode",
                "tree=jobs[name]",
            ],
            user,
            password,
        )
        jobs = [x.get("name") for x in (json.loads(rr.stdout).get("jobs") or [])]
        if MAIN_JOB in jobs:
            children.append(name)
    return sorted(children)


def download_config(base_url: str, job_path: str, user: str, password: str) -> str:
    """job_path like 'android-pack' or 'chenweili/job/android-pack'."""
    url = f"{base_url.rstrip('/')}/job/{job_path}/config.xml"
    with tempfile.NamedTemporaryFile(prefix="jk-cfg-", suffix=".xml", delete=False) as f:
        path = f.name
    try:
        r = run_curl(["-o", path, "-w", "%{http_code}", url], user, password, check=False)
        code = (r.stdout or "").strip()
        if r.returncode != 0 or code != "200":
            raise RuntimeError(f"download {url} failed http={code} err={r.stderr}")
        return Path(path).read_text(encoding="utf-8")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def extract_auth_block(xml: str) -> Optional[str]:
    m = re.search(
        r"<hudson\.security\.AuthorizationMatrixProperty>.*?</hudson\.security\.AuthorizationMatrixProperty>",
        xml,
        re.S,
    )
    return m.group(0) if m else None


def default_auth_block(owner: str) -> str:
    perms = [
        "Item.Build",
        "Item.Cancel",
        "Item.Discover",
        "Item.Read",
        "Item.Workspace",
        "Run.Update",
    ]
    lines = [
        "<hudson.security.AuthorizationMatrixProperty>",
        '      <inheritanceStrategy class="org.jenkinsci.plugins.matrixauth.inheritance.NonInheritingStrategy"/>',
    ]
    for who in ("admin", owner):
        for p in perms:
            lines.append(f"      <permission>hudson.model.{p}:{who}</permission>")
    lines.append("    </hudson.security.AuthorizationMatrixProperty>")
    return "\n".join(lines)


def adapt_main_to_child(main_xml: str, owner: str, existing_child_xml: Optional[str]) -> str:
    """Build child config from main, adapting path/displayName/auth."""
    xml = main_xml

    # displayName
    xml = re.sub(
        r"<displayName>安卓打包(?: · [^<]*)?</displayName>",
        f"<displayName>安卓打包 · {owner}</displayName>",
        xml,
        count=1,
    )
    if "<displayName>" not in xml[:800]:
        # insert after description if missing
        xml = re.sub(
            r"(</description>\s*)",
            rf"\1  <displayName>安卓打包 · {owner}</displayName>\n",
            xml,
            count=1,
        )

    # Active Choices / cascade project identity
    full = f"{owner}/{MAIN_JOB}"
    xml = xml.replace(
        f"<projectFullName>{MAIN_JOB}</projectFullName>",
        f"<projectFullName>{full}</projectFullName>",
    )
    xml = xml.replace(
        f"<projectFullNameCache>{MAIN_JOB}</projectFullNameCache>",
        f"<projectFullNameCache>{full}</projectFullNameCache>",
    )

    # Auth: prefer existing child matrix; else generate for owner
    auth = None
    if existing_child_xml:
        auth = extract_auth_block(existing_child_xml)
    if not auth:
        auth = default_auth_block(owner)

    # Remove any auth already present in main (usually none), then insert ours.
    xml = re.sub(
        r"\s*<hudson\.security\.AuthorizationMatrixProperty>.*?</hudson\.security\.AuthorizationMatrixProperty>\s*",
        "\n    ",
        xml,
        count=1,
        flags=re.S,
    )
    if "<hudson.security.AuthorizationMatrixProperty>" not in xml:
        if not re.search(r"<properties>\s*", xml):
            raise RuntimeError("main config missing <properties>")
        xml = re.sub(r"(<properties>\s*)", rf"\1  {auth}\n    ", xml, count=1)

    return xml


def post_config(base_url: str, job_path: str, xml: str, user: str, password: str) -> None:
    cookie = tempfile.NamedTemporaryFile(prefix="jk-cookie-", delete=False)
    cookie.close()
    cfg = tempfile.NamedTemporaryFile(prefix="jk-post-", suffix=".xml", delete=False)
    cfg.write(xml.encode("utf-8"))
    cfg.close()
    try:
        crumb_raw = run_curl(
            ["-c", cookie.name, "-b", cookie.name, f"{base_url.rstrip('/')}/crumbIssuer/api/json"],
            user,
            password,
        ).stdout
        crumb = json.loads(crumb_raw)
        field, val = crumb["crumbRequestField"], crumb["crumb"]
        url = f"{base_url.rstrip('/')}/job/{job_path}/config.xml"
        r = run_curl(
            [
                "-c",
                cookie.name,
                "-b",
                cookie.name,
                "-X",
                "POST",
                "-H",
                "Content-Type: text/xml; charset=utf-8",
                "-H",
                f"{field}: {val}",
                "--data-binary",
                f"@{cfg.name}",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                url,
            ],
            user,
            password,
            check=False,
        )
        code = (r.stdout or "").strip()
        if r.returncode != 0 or code != "200":
            raise RuntimeError(f"POST {url} failed http={code} err={r.stderr}")
    finally:
        for p in (cookie.name, cfg.name):
            try:
                os.remove(p)
            except OSError:
                pass


def verify_child(xml: str, owner: str) -> Tuple[bool, str]:
    if f"<displayName>安卓打包 · {owner}</displayName>" not in xml:
        return False, "displayName mismatch"
    if f"<projectFullName>{owner}/{MAIN_JOB}</projectFullName>" not in xml:
        return False, "projectFullName not adapted"
    if "<hudson.security.AuthorizationMatrixProperty>" not in xml:
        return False, "missing auth matrix"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync main android-pack to child folder jobs")
    ap.add_argument("--url", default=os.environ.get("JENKINS_URL", DEFAULT_URL))
    ap.add_argument("--user", default=os.environ.get("JENKINS_USER", "admin"))
    ap.add_argument("--only", action="append", default=[], help="Only sync these folder names (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="Download/adapt only; do not POST")
    ap.add_argument("--list", action="store_true", help="List child folders and exit")
    args = ap.parse_args()

    password = env_pass()
    children = list_child_users(args.url, args.user, password)
    if args.only:
        want = set(args.only)
        missing = sorted(want - set(children))
        if missing:
            sys.exit(f"Unknown child folders (no android-pack inside): {missing}")
        children = [c for c in children if c in want]

    print(f"Jenkins: {args.url}")
    print(f"Main:    /job/{MAIN_JOB}/")
    print(f"Children ({len(children)}): {', '.join(children) or '(none)'}")
    if args.list:
        return

    main_xml = download_config(args.url, MAIN_JOB, args.user, password)
    print(f"Downloaded main config ({len(main_xml)} bytes)")

    ok_n = 0
    for owner in children:
        job_path = f"{owner}/job/{MAIN_JOB}"
        print(f"\n==> {owner}  ({job_path})")
        try:
            existing = download_config(args.url, job_path, args.user, password)
        except Exception as e:
            print(f"  skip (download child failed): {e}")
            continue
        adapted = adapt_main_to_child(main_xml, owner, existing)
        good, reason = verify_child(adapted, owner)
        if not good:
            print(f"  abort adapt: {reason}")
            continue
        print(f"  adapted size={len(adapted)} (was {len(existing)})")
        if args.dry_run:
            print("  dry-run: not posted")
            ok_n += 1
            continue
        post_config(args.url, job_path, adapted, args.user, password)
        # re-download verify displayName
        after = download_config(args.url, job_path, args.user, password)
        good, reason = verify_child(after, owner)
        print(f"  posted: {reason}")
        if good:
            ok_n += 1

    print(f"\nDone: {ok_n}/{len(children)} {'(dry-run) ' if args.dry_run else ''}succeeded")
    if ok_n != len(children):
        sys.exit(1)


if __name__ == "__main__":
    main()
