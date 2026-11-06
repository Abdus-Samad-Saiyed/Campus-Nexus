"""Tests for Prioritized Performance & Infrastructure Optimizations.

Validates:
1. Cached DB Session Engine (django.contrib.sessions.backends.cached_db).
2. Session caching, write-through persistence to DB, and cache eviction on delete.
3. PBKDF2 password hasher iteration tuning in dev vs. full security in prod.
4. Multi-worker Daphne cluster supervisor configuration and worker port allocation.
"""

import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password, PBKDF2PasswordHasher
from django.contrib.sessions.backends.cached_db import SessionStore as CachedDBSessionStore
from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings

User = get_user_model()


class PerformanceOptimizationsTests(TestCase):
    def test_session_engine_is_cached_db(self):
        """SESSION_ENGINE is configured to use cached_db."""
        self.assertEqual(settings.SESSION_ENGINE, "django.contrib.sessions.backends.cached_db")

    def test_cached_db_session_lifecycle_and_eviction(self):
        """Sessions write to both DB and Cache; deleting evicts from both layers."""
        store = CachedDBSessionStore()
        store["test_token"] = "fast_cache_value_123"
        store.save()
        key = store.session_key
        self.assertTrue(key)

        # 1. Verify session exists in DB
        self.assertTrue(Session.objects.filter(session_key=key).exists())

        # 2. Verify session exists in Cache
        cache_key = store.cache_key
        self.assertIsNotNone(store._cache.get(cache_key))

        # 3. Read session via independent SessionStore instance (reads from Cache)
        read_store = CachedDBSessionStore(session_key=key)
        self.assertEqual(read_store.get("test_token"), "fast_cache_value_123")

        # 4. Deleting session evicts from both Cache and DB
        read_store.delete()
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertIsNone(store._cache.get(cache_key))

    def test_dev_pbkdf2_hasher_configuration_and_compatibility(self):
        """FastPBKDF2PasswordHasher uses tuned iterations (default 10,000) while verifying 720k hashes."""
        from campus.settings.dev import FastPBKDF2PasswordHasher

        dev_hasher = FastPBKDF2PasswordHasher()
        self.assertEqual(dev_hasher.iterations, 10000)

        # Encode a password with standard 720,000 iterations
        std_hasher = PBKDF2PasswordHasher()
        high_iter_hash = std_hasher.encode("StudentPass@123", "fixed_salt", iterations=720000)
        self.assertIn("$720000$", high_iter_hash)

        # Fast hasher must verify the high-iteration hash seamlessly
        self.assertTrue(dev_hasher.verify("StudentPass@123", high_iter_hash))

        # When creating a new hash in dev, it uses 10,000 iterations for speed
        dev_hash = dev_hasher.encode("StudentPass@123", "fixed_salt")
        self.assertIn("$10000$", dev_hash)
        self.assertTrue(dev_hasher.verify("StudentPass@123", dev_hash))

    def test_multiworker_cluster_initialization_and_port_calculation(self):
        """MultiWorkerCluster calculates non-conflicting sequential worker ports."""
        from scripts.run_multiworker_cluster import MultiWorkerCluster

        cluster = MultiWorkerCluster(
            num_workers=4,
            bind_host="0.0.0.0",
            proxy_port=8000,
            worker_base_port=8001,
            early_launch=True,
            settings_module="campus.settings.dev",
        )
        self.assertEqual(cluster.worker_ports, [8001, 8002, 8003, 8004])
        self.assertEqual(cluster.proxy_port, 8000)
        env = cluster._get_worker_env()
        self.assertEqual(env.get("CAMPUS_EARLY_LAUNCH_MODE"), "True")
        self.assertEqual(env.get("DJANGO_SETTINGS_MODULE"), "campus.settings.dev")

    def test_run_cluster_management_command_arguments(self):
        """manage.py run_cluster is registered and has appropriate default worker count."""
        from django.core.management import get_commands, load_command_class

        commands = get_commands()
        self.assertIn("run_cluster", commands)

        cmd = load_command_class(commands["run_cluster"], "run_cluster")
        parser = cmd.create_parser("manage.py", "run_cluster")
        actions = {a.dest: a for a in parser._actions}

        self.assertIn("workers", actions)
        self.assertIn("port", actions)
        self.assertIn("bind", actions)
        self.assertIn("early_launch", actions)
        self.assertGreaterEqual(actions["workers"].default, 4)
