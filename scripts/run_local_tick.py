"""Run a real tick() or era_tick() against a LOCAL MongoDB copy, for
reproducing and debugging problems (hangs, slowness, exceptions) with zero
risk to production. This is step 2 — clone production data first with
scripts/clone_prod_to_local.py.

Usage:
    python scripts/run_local_tick.py --all
    python scripts/run_local_tick.py --steps "AI Decision Tick,Nation Income Tick"
    python scripts/run_local_tick.py --era --all
    python scripts/run_local_tick.py --list-steps

Runs in the foreground (not a background thread, unlike the real admin
"Run Tick" button) so Ctrl+C and a debugger both work normally. If it
hangs, every thread's Python stack trace is dumped to the console every
--watchdog-seconds (default 60) via faulthandler — check the console
output to see exactly which line it's stuck on, no debugger needed. This
is what to reach for when "I can't tell where the tick was when it got
stuck" (see the ~7.5-hour stuck-tick incident this was built for).

Safety: MONGO_URI is forced to --local-uri before app_core is ever
imported, and AWS/S3/email credentials are stripped from the environment
so nothing — including the real "Backup Database" and "Give Tick Summary"
steps, if selected — can touch production S3, email, or the production
database. A hard check right after import re-verifies app_core actually
ended up pointed at a local address before anything runs.
"""
import os
import sys
import argparse
import time
import faulthandler
from urllib.parse import urlparse

# Running as `python scripts/run_local_tick.py` only puts scripts/ on
# sys.path, not the project root — app_core.py and helpers/ live there, and
# app_core also loads schema JSON via paths relative to the working
# directory, so both need to point at the project root regardless of where
# this script is invoked from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

DEFAULT_LOCAL_URI = "mongodb://localhost:27017/poa_local_debug"

# These have real-world side effects (email, S3) even though we neutralize
# the credentials below — excluded from --all by default since a local,
# disposable copy gets no debugging value from them, only slower runs.
DEFAULT_EXCLUDED_FROM_ALL = {"Backup Database"}

# Env vars that would let a selected step reach a real external service.
# Cleared unconditionally so choosing "Backup Database" or "Give Tick
# Summary" (or anything else that happens to read these) is always safe.
_EXTERNAL_SERVICE_ENV_VARS = (
    "S3_BUCKET_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "EMAIL_FROM", "EMAIL_TO", "EMAIL_PASSWORD",
)


def _looks_local(uri):
    host = (urlparse(uri).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _force_local_environment(local_uri):
    """Point the app at the local database and strip external-service
    credentials, WITHOUT touching the real .env file.

    app_core.py calls load_dotenv(override=True) at import time, which
    would otherwise clobber our MONGO_URI override with the real
    (production) value the moment app_core is imported — python-dotenv's
    default load_dotenv() resolves .env relative to the CALLING file, not
    the current directory, so even running this script from elsewhere
    doesn't dodge it. Fill in everything else from .env first (override=False,
    so it won't stomp what we set after), then set our overrides, then
    neutralize app_core's own load_dotenv call before importing it.
    """
    from dotenv import load_dotenv
    load_dotenv(override=False)

    os.environ["MONGO_URI"] = local_uri
    for var in _EXTERNAL_SERVICE_ENV_VARS:
        os.environ.pop(var, None)

    import dotenv
    dotenv.load_dotenv = lambda *a, **kw: False


def _all_labels(th, era):
    if era:
        d = dict(th.ERA_GENERAL_TICK_FUNCTIONS)
        d.update(th.ERA_NATION_TICK_FUNCTIONS)
        d.update(th.ERA_CHARACTER_TICK_FUNCTIONS)
    else:
        d = dict(th.GENERAL_TICK_FUNCTIONS)
        d.update(th.CHARACTER_TICK_FUNCTIONS)
        d.update(th.ARTIFACT_TICK_FUNCTIONS)
        d.update(th.MERCHANT_TICK_FUNCTIONS)
        d.update(th.MERCENARY_TICK_FUNCTIONS)
        d.update(th.FACTION_TICK_FUNCTIONS)
        d.update(th.MARKET_TICK_FUNCTIONS)
        d.update(th.NATION_TICK_FUNCTIONS)
        d.update(th.NATION_CROSS_TICK_FUNCTIONS)
    return list(d.keys())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-uri", default=os.getenv("LOCAL_MONGO_URI", DEFAULT_LOCAL_URI),
                         help=f"Local MongoDB URI to run against (default: {DEFAULT_LOCAL_URI})")
    parser.add_argument("--steps", default=None,
                         help="Comma-separated exact step labels to run (see --list-steps)")
    parser.add_argument("--all", action="store_true",
                         help="Run every step for the chosen tick type, except 'Backup Database' "
                              "(see --include-backup)")
    parser.add_argument("--include-backup", action="store_true",
                         help="Include 'Backup Database' when used with --all (safe — it backs up "
                              "the local copy to a local file only — just slow and not useful for debugging)")
    parser.add_argument("--era", action="store_true", help="Run era_tick() instead of tick()")
    parser.add_argument("--list-steps", action="store_true", help="Print available step labels and exit")
    parser.add_argument("--watchdog-seconds", type=int, default=60,
                         help="Dump every thread's stack trace this often while running (default 60s), "
                              "so a hang is diagnosable without attaching a debugger")
    args = parser.parse_args()

    local_uri = args.local_uri
    if not _looks_local(local_uri):
        print(f"ERROR: --local-uri ({local_uri}) doesn't look local (expected localhost/127.0.0.1). "
              f"Refusing to run a test tick against what might be a real database.")
        sys.exit(1)

    _force_local_environment(local_uri)

    # Fail fast with a clear message if there's no local server, rather than
    # letting app_core's own ensure_mongo_indexes() discover that: it retries
    # per-collection with no shared short-circuit, so it can take several
    # minutes of identical connection-refused timeouts before surfacing
    # anything.
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    try:
        MongoClient(local_uri, serverSelectionTimeoutMS=3000).admin.command("ping")
    except PyMongoError:
        print(f"ERROR: can't reach a local MongoDB server at {local_uri}.")
        print("Start one first, e.g.: mongod --dbpath <some_local_folder>")
        sys.exit(1)

    import app_core  # noqa: E402  (must be imported only after the env is forced above)

    effective_uri = app_core.app.config.get("MONGO_URI") or ""
    if not _looks_local(effective_uri):
        print(f"ERROR: safety check failed — app_core ended up configured with "
              f"MONGO_URI={effective_uri!r}, which isn't local. Aborting before running "
              f"anything against it.")
        sys.exit(1)
    print(f"Connected to (confirmed local): {effective_uri}")

    import helpers.tick_helpers as th  # noqa: E402

    if args.list_steps:
        for label in _all_labels(th, args.era):
            print(label)
        return

    if args.steps:
        selected = [s.strip() for s in args.steps.split(",") if s.strip()]
        valid = set(_all_labels(th, args.era))
        unknown = [s for s in selected if s not in valid]
        if unknown:
            print(f"ERROR: unknown step label(s): {unknown}\nRun with --list-steps to see valid labels.")
            sys.exit(1)
    elif args.all:
        selected = _all_labels(th, args.era)
        if not args.include_backup:
            selected = [s for s in selected if s not in DEFAULT_EXCLUDED_FROM_ALL]
    else:
        print("ERROR: pass --steps \"Label1,Label2\" or --all (see --list-steps for valid labels).")
        sys.exit(1)

    form_data = {f"run_{label}": "on" for label in selected}
    print(f"\nRunning {'era_tick' if args.era else 'tick'} with {len(selected)} step(s):")
    for label in selected:
        print(f"  - {label}")
    print()

    faulthandler.enable()
    faulthandler.dump_traceback_later(args.watchdog_seconds, repeat=True, file=sys.stdout)

    target = th.era_tick if args.era else th.tick
    label = "Era tick" if args.era else "Tick"

    start = time.time()
    try:
        th._run_tick_guarded(target, form_data, label)
    except KeyboardInterrupt:
        print("\nInterrupted — dumping current stack before exiting:")
        faulthandler.dump_traceback()
        raise
    finally:
        faulthandler.cancel_dump_traceback_later()

    elapsed = time.time() - start
    status = app_core.mongo.db.tick_status.find_one({"_id": "current"})
    print(f"\nFinished in {elapsed:.1f}s. tick_status: {status}")


if __name__ == "__main__":
    main()
