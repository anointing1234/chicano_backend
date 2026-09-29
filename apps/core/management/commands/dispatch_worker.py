"""
python manage.py dispatch_worker [--interval 2] [--keep-demo-fresh]

Background loop for production (run it as its own process/container next to the API):
every `interval` seconds it calls `apps.rides.dispatch.tick()`, which
    * expires unanswered 15 s offers and offers the ride to the next driver/rider,
    * starts scheduled rides 15 minutes before pickup,
    * retries rides still searching, and
    * marks searches older than 10 minutes as `no_provider`.

The API also calls tick() lazily while apps poll, so local development works without this,
but in production run exactly ONE worker so offers expire on time even when nobody polls.

--keep-demo-fresh (DEBUG only): refreshes the GPS timestamp of online demo drivers/riders
from `seed_demo` so they stay eligible for dispatch without a phone sending locations.
"""
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from apps.providers.models import ProviderProfile
from apps.rides import dispatch

log = logging.getLogger(__name__)

DEMO_PHONE_PREFIXES = ("+23480500000", "+23480700000")   # phones created by seed_demo


class Command(BaseCommand):
    help = "Run the dispatch loop (offer expiry, scheduled rides, search timeouts)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=2.0, help="Seconds between cycles (default 2).")
        parser.add_argument("--once", action="store_true", help="Run a single cycle and exit (useful for cron/tests).")
        parser.add_argument("--keep-demo-fresh", action="store_true", help="DEBUG only: keep seeded demo providers' GPS fresh.")

    def handle(self, *args, **opts):
        keep_fresh = opts["keep_demo_fresh"]
        if keep_fresh and not settings.DEBUG:
            self.stderr.write("--keep-demo-fresh is ignored because DEBUG is off.")
            keep_fresh = False
        self.stdout.write(self.style.SUCCESS(f"dispatch_worker running every {opts['interval']}s (Ctrl+C to stop)"))
        while True:
            close_old_connections()          # long-running process: drop dead DB connections between cycles
            try:
                if keep_fresh:
                    for prefix in DEMO_PHONE_PREFIXES:
                        ProviderProfile.objects.filter(user__phone__startswith=prefix, is_online=True).update(last_location_at=timezone.now())
                dispatch.tick()
            except Exception:                 # never let one bad cycle kill the worker
                log.exception("dispatch tick failed")
            if opts["once"]:
                return
            time.sleep(opts["interval"])
