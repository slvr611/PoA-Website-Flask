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

Query report (on by default, see --no-query-report): a pymongo
CommandListener counts every Mongo command issued during the run, grouped
by (database, collection, command name), and prints a table sorted by call
count at the end, flagging any group at or above --n-plus-one-threshold
(default 50) calls. This is a COUNT-based check, not a timing-based one —
it exists because a local mongod is fast enough that an N+1 query pattern
(one query per nation/market/route inside a tick loop) never gets slow
enough here to notice, even though the exact same pattern at 200+ real
nations and real network latency in production can turn into a
multi-hour tick (see the 2026-09-17 AI Decision Tick incident: 216 nations
at ~2 min/nation, zero errors, zero retries — pure per-nation query count).
A call count like "216" or "648" lining up with the number of nations/
markets/routes selected is the signature to look for; the listener has no
way to know what "too many" means for a given step, so treat the threshold
as a tripwire for "worth a look", not a hard failure.
"""
import os
import sys
import argparse
import time
import faulthandler
from urllib.parse import urlparse

from pymongo import monitoring

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


class _QueryCounter(monitoring.CommandListener):
    """pymongo CommandListener that tallies every command issued during the
    run by (database, collection, command name) — call count and total
    server-reported duration. Registered globally via
    pymongo.monitoring.register(), which MUST happen before the app's
    MongoClient is constructed (i.e. before `import app_core`) — pymongo
    reads the listener registry once, at MongoClient.__init__ time.

    started()/succeeded()/failed() are correlated via request_id because
    only the "started" event carries the actual command document (and thus
    which collection it targets); only "succeeded"/"failed" carry the
    duration.
    """

    def __init__(self):
        self._pending = {}
        self.stats = {}  # (db, collection, command_name) -> {"count": int, "total_micros": int}

    def started(self, event):
        collection = event.command.get(event.command_name)
        if not isinstance(collection, str):
            collection = "?"
        self._pending[event.request_id] = (event.database_name, collection, event.command_name)

    def succeeded(self, event):
        self._finish(event)

    def failed(self, event):
        self._finish(event)

    def _finish(self, event):
        key = self._pending.pop(event.request_id, None)
        if key is None:
            return
        entry = self.stats.setdefault(key, {"count": 0, "total_micros": 0})
        entry["count"] += 1
        entry["total_micros"] += getattr(event, "duration_micros", 0) or 0


def _print_query_report(counter, threshold):
    if not counter.stats:
        print("\nQuery report: no Mongo commands were observed.")
        return

    rows = sorted(counter.stats.items(), key=lambda kv: -kv[1]["count"])
    total_calls = sum(s["count"] for _, s in rows)

    print(f"\nQuery report ({total_calls} Mongo command(s) issued, by call count):")
    print(f"{'count':>7}  {'total ms':>10}  {'database.collection':<32}  command")
    flagged = []
    for (db, coll, cmd), s in rows:
        marker = ""
        if s["count"] >= threshold:
            marker = f"  <-- {s['count']} calls: possible N+1 (one query per item in a loop?)"
            flagged.append((db, coll, cmd, s["count"]))
        print(f"{s['count']:>7}  {s['total_micros'] / 1000:>10.1f}  {db + '.' + coll:<32}  {cmd}{marker}")

    if flagged:
        print(f"\n{len(flagged)} command shape(s) hit the N+1 threshold ({threshold}+ calls) — "
              f"if the count matches the number of nations/markets/routes/etc. this step "
              f"processed, that query is very likely running once per item instead of once "
              f"total (or once per some shared/cached set).")
    else:
        print(f"\nNo command shape reached the N+1 threshold ({threshold}+ calls).")


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
    # Nation/character names routinely contain non-ASCII characters, and
    # tick_status/query-report output includes them — Windows' default
    # console codepage (cp1252) can't encode most of them and would
    # otherwise crash the script after a real tick already finished
    # successfully. reconfigure() is a no-op failure risk only on very old
    # Pythons; this codebase already requires 3.7+ (f-strings elsewhere).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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
    parser.add_argument("--query-report", dest="query_report", action="store_true", default=True,
                         help="Print a per-(collection, command) Mongo call-count report at the end "
                              "(default: on)")
    parser.add_argument("--no-query-report", dest="query_report", action="store_false",
                         help="Skip the query-count report")
    parser.add_argument("--n-plus-one-threshold", type=int, default=50,
                         help="Flag a (collection, command) pair in the query report once its call "
                              "count reaches this many (default 50)")
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

    query_counter = None
    if args.query_report:
        # Must register before app_core (and its MongoClient) is imported —
        # pymongo.monitoring.register() only affects clients constructed
        # after the call.
        query_counter = _QueryCounter()
        monitoring.register(query_counter)

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

    if query_counter is not None:
        _print_query_report(query_counter, args.n_plus_one_threshold)


if __name__ == "__main__":
    main()
