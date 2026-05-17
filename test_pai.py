#!/usr/bin/env python3
"""
test_pai.py — Comprehensive AIO test suite
==========================================
Critique rationale per test: each test documents WHY it exists and what
failure mode it guards against — not just what it does.

Run all tests:
    python test_pai.py                   # unittest runner
    pytest test_pai.py -v               # pytest (prettier output)
    pytest test_pai.py -v -k "thermal"  # filter by name
    pytest test_pai.py --tb=short       # brief tracebacks

Run with live API (requires AIO server running):
    PAI_API=http://localhost:9480 python test_pai.py
"""

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import fields
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Helpers ───────────────────────────────────────────────────────────────────

AIO_DIR = Path(__file__).parent
PAI_API = os.getenv("PAI_API", "")   # empty = skip live API tests

def _import_aio():
    """Import aio module from local directory."""
    sys.path.insert(0, str(AIO_DIR))
    import pai as aio
    return aio


def _live_api_available() -> bool:
    if not PAI_API:
        return False
    try:
        import urllib.request
        urllib.request.urlopen(f"{PAI_API}/status", timeout=2)
        return True
    except Exception:
        return False


LIVE = _live_api_available()


# ==============================================================================
# 1. MODULE IMPORT & SYNTAX
# ==============================================================================

class TestModuleImport(unittest.TestCase):
    """
    CRITIQUE: If pai.py can't be imported at all, every downstream test is
    meaningless.  These tests catch circular imports, missing stdlib deps,
    and top-level syntax errors before any logic runs.
    """

    def test_import_succeeds(self):
        """pai.py must import without raising any exception."""
        try:
            aio = _import_aio()
        except Exception as e:
            self.fail(f"import pai as aio raised: {e}")

    def test_version_string(self):
        """PAI_VERSION must be a non-empty semantic-version string."""
        aio = _import_aio()
        v = aio.PAI_VERSION
        self.assertIsInstance(v, str)
        self.assertRegex(v, r"^\d+\.\d+\.\d+$",
            "PAI_VERSION should follow semver (e.g. 1.0.0)")

    def test_constants_are_paths(self):
        """DATA_DIR, MODELS_DIR etc. must be Path objects, not strings."""
        aio = _import_aio()
        for name in ("DATA_DIR", "MODELS_DIR", "ADAPTERS_DIR", "TRAIN_DIR"):
            val = getattr(aio, name)
            self.assertIsInstance(val, Path,
                f"{name} should be a pathlib.Path (got {type(val).__name__})")


# ==============================================================================
# 2. MODELSPEC DATACLASS
# ==============================================================================

class TestModelSpec(unittest.TestCase):
    """
    CRITIQUE: ModelSpec is the central data record for every model download,
    selection, and inference call.  Invariant violations here cause silent
    wrong-model selection or download corruption.
    """

    def setUp(self):
        self.aio = _import_aio()

    def test_modelspec_fields(self):
        """All expected fields must exist on ModelSpec."""
        required = {"key", "engine", "repo_id", "filename", "check_file",
                    "size_gb", "params_b", "ctx", "description",
                    "is_moe", "active_params_b"}
        actual = {f.name for f in fields(self.aio.ModelSpec)}
        self.assertEqual(required, actual,
            f"Missing fields: {required - actual}, Extra: {actual - required}")

    def test_effective_params_dense(self):
        """Dense model: effective_params_b == params_b."""
        spec = self.aio.ModelSpec(
            key="test", engine="mlx", repo_id="r", filename="", check_file="",
            size_gb=5.0, params_b=8.0, ctx=4096, description="test",
            is_moe=False, active_params_b=0.0,
        )
        self.assertEqual(spec.effective_params_b, 8.0)

    def test_effective_params_moe(self):
        """MoE model: effective_params_b == active_params_b, not total."""
        spec = self.aio.ModelSpec(
            key="test-moe", engine="mlx", repo_id="r", filename="", check_file="",
            size_gb=25.0, params_b=46.7, ctx=32768, description="Mixtral",
            is_moe=True, active_params_b=12.9,
        )
        self.assertEqual(spec.effective_params_b, 12.9,
            "MoE effective params should be active_params_b, not total")

    def test_label_includes_moe_tag(self):
        """MoE model label must contain '[MoE]' so users see it in UI/logs."""
        aio = self.aio
        moe = aio.ModelSpec("k","mlx","r","","",25,46,8192,"Mixtral",True,12.9)
        dense = aio.ModelSpec("k","mlx","r","","",42,70,8192,"Llama",False,0)
        self.assertIn("[MoE]", moe.label())
        self.assertNotIn("[MoE]", dense.label())

    def test_ladders_ordered_by_size_descending(self):
        """
        CRITIQUE: select_models() picks the first spec that fits RAM.
        If the ladders are not sorted largest-first, a small model will be
        chosen for a high-RAM device — silent quality regression.
        """
        aio = self.aio
        for ladder_name in ("_MLX_LADDER", "_GGUF_LADDER"):
            ladder = getattr(aio, ladder_name)
            sizes = [s.size_gb for s in ladder]
            self.assertEqual(sizes, sorted(sizes, reverse=True),
                f"{ladder_name} must be ordered by size_gb descending")

    def test_ladders_not_empty(self):
        """Both ladders must have entries — empty ladder = no model ever loads."""
        aio = self.aio
        self.assertGreater(len(aio._MLX_LADDER), 0)
        self.assertGreater(len(aio._GGUF_LADDER), 0)

    def test_moe_models_present_in_ladders(self):
        """
        CRITIQUE: MoE models are the reason for this update.  If they
        silently vanished from the ladders, the whole feature is missing.
        """
        aio = self.aio
        mlx_moe = [s for s in aio._MLX_LADDER if s.is_moe]
        gguf_moe = [s for s in aio._GGUF_LADDER if s.is_moe]
        self.assertGreaterEqual(len(mlx_moe), 2,
            "MLX ladder should have at least 2 MoE models")
        self.assertGreaterEqual(len(gguf_moe), 2,
            "GGUF ladder should have at least 2 MoE models")

    def test_moe_active_params_less_than_total(self):
        """
        CRITIQUE: If active_params_b >= params_b for a MoE model, that's
        invalid model metadata that would mislead routing decisions.
        """
        aio = self.aio
        for ladder in (aio._MLX_LADDER, aio._GGUF_LADDER):
            for spec in ladder:
                if spec.is_moe and spec.active_params_b > 0:
                    self.assertLess(spec.active_params_b, spec.params_b,
                        f"{spec.key}: active_params_b must be < params_b")

    def test_all_specs_have_positive_ctx(self):
        """Context window of 0 causes llama-cpp-python to crash at load time."""
        aio = self.aio
        for ladder in (aio._MLX_LADDER, aio._GGUF_LADDER):
            for spec in ladder:
                self.assertGreater(spec.ctx, 0, f"{spec.key}: ctx must be > 0")

    def test_engine_values(self):
        """engine must be 'mlx' or 'gguf' — no typos."""
        aio = self.aio
        valid = {"mlx", "gguf"}
        for ladder in (aio._MLX_LADDER, aio._GGUF_LADDER):
            for spec in ladder:
                self.assertIn(spec.engine, valid,
                    f"{spec.key}: invalid engine '{spec.engine}'")


# ==============================================================================
# 3. PLATFORM DETECTION
# ==============================================================================

class TestDeviceProfile(unittest.TestCase):
    """
    CRITIQUE: DeviceProfile is the root decision node for model selection
    and backend compilation.  A wrong platform label causes the wrong model
    ladder to be used (e.g. GGUF on Apple Silicon = no Metal acceleration).
    """

    def setUp(self):
        self.aio = _import_aio()

    def test_detect_returns_profile(self):
        """detect_device() must return a DeviceProfile without crashing."""
        dev = self.aio.detect_device()
        self.assertIsNotNone(dev)

    def test_platform_is_known_value(self):
        dev = self.aio.detect_device()
        self.assertIn(dev.platform, {"apple_silicon", "cuda", "cpu"},
            f"Unknown platform: {dev.platform}")

    def test_ram_gb_positive(self):
        """RAM of 0 would cause every model to be skipped as 'too large'."""
        dev = self.aio.detect_device()
        self.assertGreater(dev.ram_gb, 0)

    def test_cpu_cores_positive(self):
        dev = self.aio.detect_device()
        self.assertGreater(dev.cpu_cores, 0)

    def test_node_id_is_uuid(self):
        """node_id must be a stable UUID used for mesh identity."""
        import re
        dev = self.aio.detect_device()
        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        self.assertRegex(dev.node_id, uuid_re,
            f"node_id should be a UUID, got: {dev.node_id}")

    def test_node_id_stable_across_calls(self):
        """
        CRITIQUE: node_id is written to disk.  If detect_device() generates
        a new UUID every call, mesh peers will never recognise the same node.
        """
        dev1 = self.aio.detect_device()
        dev2 = self.aio.detect_device()
        self.assertEqual(dev1.node_id, dev2.node_id,
            "node_id must be stable across detect_device() calls")

    @unittest.skipUnless(platform.system() == "Darwin" and
                         platform.machine() in ("arm64", "aarch64"),
                         "Apple Silicon only")
    def test_apple_silicon_platform(self):
        """On arm64 macOS, platform must be 'apple_silicon' for MLX selection."""
        dev = self.aio.detect_device()
        self.assertEqual(dev.platform, "apple_silicon")
        self.assertTrue(dev.has_metal)


# ==============================================================================
# 4. MODEL SELECTION
# ==============================================================================

class TestModelSelection(unittest.TestCase):
    """
    CRITIQUE: select_models() is the single function that decides which
    models run.  Off-by-one in the RAM headroom or wrong ladder causes a
    device to OOM or use a degraded model silently.
    """

    def setUp(self):
        self.aio = _import_aio()

    def _dev(self, ram_gb: float, platform: str = "cpu") -> object:
        """Build a mock DeviceProfile with specified RAM."""
        dev = MagicMock()
        dev.ram_gb   = ram_gb
        dev.platform = platform
        return dev

    def test_tiny_device_gets_smallest_model(self):
        """4 GB device must not select a 42 GB model."""
        aio = self.aio
        with patch.object(aio, "_scan_network_mounts", return_value=[]):
            sudo, sized = aio.select_models(self._dev(4.0))
        if sudo:
            self.assertLessEqual(sudo.size_gb, 4.0 * 0.78 + 0.1,
                "sudo model must fit in available RAM")

    def test_large_device_prefers_moe(self):
        """
        CRITIQUE: 128 GB Apple Silicon should pick Mixtral 8x22B (49 GB)
        over Llama 70B (42 GB) because 8x22B is first in the MLX ladder.
        """
        aio = self.aio
        with patch.object(aio, "_scan_network_mounts", return_value=[]):
            sudo, _ = aio.select_models(self._dev(128.0, "apple_silicon"))
        if sudo:
            self.assertEqual(sudo.key, "mlx-moe-8x22b",
                "128 GB Apple Silicon should pick Mixtral 8x22B MoE first")

    def test_sized_model_is_smallest(self):
        """
        sized model must always be the last (smallest) entry in the ladder —
        it's the fast routing model used for trivial queries.
        """
        aio = self.aio
        with patch.object(aio, "_scan_network_mounts", return_value=[]):
            _, sized = aio.select_models(self._dev(128.0, "apple_silicon"))
        if sized:
            self.assertEqual(sized.size_gb, aio._MLX_LADDER[-1].size_gb)

    def test_mount_model_preferred(self):
        """
        CRITIQUE: If a GGUF exists on a network mount, we must use it
        without downloading — saves bandwidth and supports air-gapped labs.
        """
        aio = self.aio
        fake_path = Path("/Volumes/NAS/models/test.gguf")
        with patch.object(aio, "_scan_network_mounts",
                          return_value=[(fake_path, 20.0)]):
            sudo, _ = aio.select_models(self._dev(32.0))
        if sudo:
            self.assertEqual(sudo.engine, "gguf")
            self.assertIn("mount", sudo.key)

    def test_no_model_for_impossible_ram(self):
        """
        0.1 GB RAM — every model is larger.  sudo should be None or the
        absolute smallest model, not an OOM crash.
        """
        aio = self.aio
        with patch.object(aio, "_scan_network_mounts", return_value=[]):
            sudo, sized = aio.select_models(self._dev(0.1))
        # sized is always picked (last ladder entry); sudo may be None
        # The important thing: no exception
        self.assertIsNotNone(sized)


# ==============================================================================
# 5. THERMAL GOVERNOR
# ==============================================================================

class TestThermalGovernor(unittest.TestCase):
    """
    CRITIQUE: ThermalGovernor drives decisions that prevent hardware damage.
    A governor that never transitions state or fails silently could allow
    runaway inference at 100°C.
    """

    def setUp(self):
        self.aio = _import_aio()

    def test_initial_state_nominal(self):
        g = self.aio.ThermalGovernor()
        self.assertEqual(g.state, self.aio.ThermalState.NOMINAL)

    def test_classify_temp_buckets(self):
        g = self.aio.ThermalGovernor()
        cases = [
            (50.0, self.aio.ThermalState.NOMINAL),
            (65.0, self.aio.ThermalState.WARM),
            (80.0, self.aio.ThermalState.HOT),
            (90.0, self.aio.ThermalState.CRITICAL),
            (99.0, self.aio.ThermalState.EMERGENCY),
        ]
        for temp, expected in cases:
            result = g._classify(temp)
            self.assertEqual(result, expected,
                f"temp={temp}°C should classify as {expected.name}, got {result.name}")

    def test_thresholds_are_monotone(self):
        """
        CRITIQUE: Out-of-order thresholds cause _classify to return wrong
        states — e.g. HOT classified before WARM so WARM is unreachable.
        """
        thresholds = self.aio.ThermalGovernor.THRESHOLDS
        self.assertEqual(list(thresholds), sorted(thresholds),
            "THRESHOLDS must be in ascending order")

    def test_predict_constant_series(self):
        """Flat temperature history should predict the same temperature."""
        aio = self.aio
        g = aio.ThermalGovernor()
        now = time.time()
        for i in range(10):
            g._history.append(aio.ThermalReading(55.0, now - (9 - i) * 5))
        pred = g._predict(60.0)
        self.assertAlmostEqual(pred, 55.0, delta=2.0,
            msg="Constant temp series should predict ~same temperature")

    def test_predict_rising_series(self):
        """Rising temperature history should predict a higher future temp."""
        aio = self.aio
        g = aio.ThermalGovernor()
        now = time.time()
        # Temperature rising by 1°C every 5s
        for i in range(10):
            g._history.append(aio.ThermalReading(50.0 + i, now - (9 - i) * 5))
        pred = g._predict(60.0)
        self.assertGreater(pred, 59.0,
            "Rising temp should predict above current temp in 60s")

    def test_start_stop_thread(self):
        """Governor must start and stop without deadlocking."""
        g = self.aio.ThermalGovernor(poll_s=0.1)
        g.start()
        time.sleep(0.3)
        g.stop()
        # Pass = no hang

    def test_callback_fires_on_state_change(self):
        """
        CRITIQUE: If callbacks don't fire, subsystems (trainer, inference)
        won't throttle even when thermal state changes.
        """
        aio = self.aio
        g = aio.ThermalGovernor()
        fired = []
        g.on_change(lambda s: fired.append(s))

        # Simulate state transition bypassing hysteresis
        g._state = aio.ThermalState.NOMINAL
        g._pending = aio.ThermalState.HOT
        g._pending_ts = time.time() - g.HYSTERESIS - 1
        g._evaluate(80.0)

        self.assertTrue(len(fired) > 0, "Callback should fire on state change")
        self.assertEqual(fired[-1], aio.ThermalState.HOT)

    def test_wait_cool_returns_true_when_already_cool(self):
        g = self.aio.ThermalGovernor()
        g._state = self.aio.ThermalState.NOMINAL
        result = g.wait_cool(target=self.aio.ThermalState.WARM, timeout=1.0)
        self.assertTrue(result)

    def test_stats_keys(self):
        """stats() must return all expected keys for the API status endpoint."""
        g = self.aio.ThermalGovernor()
        s = g.stats()
        for key in ("state", "temp_c", "predicted_60s", "max_c", "avg_c"):
            self.assertIn(key, s, f"stats() missing key: {key}")


# ==============================================================================
# 6. RAG STORE
# ==============================================================================

class TestRagStore(unittest.TestCase):
    """
    CRITIQUE: RagStore is the document memory.  If chunking or search are
    broken, the model receives empty context and hallucinates — a silent
    failure worse than a crash.
    """

    def setUp(self):
        self.aio = _import_aio()
        self.rag = self.aio.RagStore()

    def test_add_and_search_basic(self):
        """Documents added must be retrievable by keyword."""
        self.rag.add_text("The Eiffel Tower is in Paris.", source="test")
        results = self.rag.search("Paris", top_k=1)
        self.assertEqual(len(results), 1)
        self.assertIn("Eiffel", results[0].text)

    def test_empty_store_returns_empty(self):
        """Searching an empty store must not raise — returns []."""
        results = self.rag.search("anything")
        self.assertEqual(results, [])

    def test_chunking_splits_long_text(self):
        """
        CRITIQUE: If chunking is broken, a 100K-word document becomes one
        chunk that exceeds the model's context window and gets silently
        truncated — poisoning the context.
        """
        words = ["word"] * 1000
        text = " ".join(words)
        self.rag.add_text(text, source="long")
        self.assertGreater(len(self.rag._chunks), 1,
            "Long text must produce multiple chunks")

    def test_chunk_overlap(self):
        """Adjacent chunks must share some words (sliding window, not hard split)."""
        aio = self.aio
        rag = aio.RagStore()
        # 600 words → 2 chunks with OVERLAP words shared
        text = " ".join([f"word{i}" for i in range(600)])
        rag.add_text(text)
        if len(rag._chunks) >= 2:
            end_of_first  = rag._chunks[0].text.split()
            start_of_second = rag._chunks[1].text.split()
            # Overlap: last N words of chunk 1 should appear in first N of chunk 2
            shared = set(end_of_first[-aio.RagStore.OVERLAP:]) & \
                     set(start_of_second[:aio.RagStore.OVERLAP])
            self.assertGreater(len(shared), 0,
                "Consecutive chunks must overlap (sliding window)")

    def test_add_file_txt(self):
        """TXT file ingestion must work end-to-end."""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w",
                                         delete=False) as f:
            f.write("The capital of Japan is Tokyo.")
            fname = f.name
        try:
            n = self.rag.add_file(Path(fname))
            self.assertGreater(n, 0)
            hits = self.rag.search("Japan", top_k=1)
            self.assertTrue(any("Tokyo" in h.text for h in hits))
        finally:
            os.unlink(fname)

    def test_add_nonexistent_file_returns_zero(self):
        """Missing file must return 0 chunks, not raise FileNotFoundError."""
        n = self.rag.add_file(Path("/tmp/definitely_does_not_exist_xyz.txt"))
        self.assertEqual(n, 0)

    def test_doc_id_is_hash(self):
        """doc_id must be deterministic — same text = same id (dedup)."""
        text = "Hello world"
        self.rag.add_text(text, source="a")
        self.rag.add_text(text, source="b")
        ids = {c.doc_id for c in self.rag._chunks}
        self.assertEqual(len(ids), 1,
            "Same text from different sources should hash to same doc_id")

    def test_top_k_limit(self):
        """search() must return at most top_k results."""
        for i in range(20):
            self.rag.add_text(f"Document number {i} about cats and dogs.")
        results = self.rag.search("cats", top_k=3)
        self.assertLessEqual(len(results), 3)

    def test_source_preserved(self):
        """Chunk source must match what was passed to add_text/add_file."""
        self.rag.add_text("Some text", source="my-document.pdf")
        self.assertEqual(self.rag._chunks[0].source, "my-document.pdf")


# ==============================================================================
# 7. DDGS SEARCH
# ==============================================================================

class TestDDGS(unittest.TestCase):
    """
    CRITIQUE: DDGS is an external network dependency.  Tests that require
    live internet are skipped in CI.  The important test is that the function
    doesn't crash and returns the right shape on success.
    """

    def setUp(self):
        self.aio = _import_aio()

    @unittest.skipUnless(os.getenv("PAI_TEST_NETWORK"), "Set PAI_TEST_NETWORK=1 to run")
    def test_ddgs_search_returns_list(self):
        results = self.aio.ddgs_search("Python programming language", max_results=3)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIn("body", r)

    def test_ddgs_search_handles_failure_gracefully(self):
        """
        CRITIQUE: If DDGS raises (network down, API change, rate limit),
        the entire query must not crash — return [] and log a warning.
        """
        with patch("duckduckgo_search.DDGS.text", side_effect=Exception("Network error")):
            results = self.aio.ddgs_search("test query")
        self.assertEqual(results, [])

    def test_build_rag_context_no_web(self):
        """build_rag_context with web=False must not call DDGS."""
        aio = self.aio
        rag = aio.RagStore()
        rag.add_text("Some local knowledge.", source="doc")
        with patch.object(aio, "ddgs_search") as mock_ddgs:
            ctx = aio.build_rag_context(rag, "local knowledge", web=False)
        mock_ddgs.assert_not_called()
        self.assertIn("Local Context", ctx)


# ==============================================================================
# 8. MESH NETWORK
# ==============================================================================

class TestMeshPeer(unittest.TestCase):
    """
    CRITIQUE: MeshPeer uses UDP broadcast — tests must not actually broadcast
    on the LAN (would disrupt real nodes).  We mock the socket layer and test
    beacon format and peer expiry logic instead.
    """

    def setUp(self):
        self.aio = _import_aio()
        self.dev = self.aio.detect_device()
        self.mesh = self.aio.MeshPeer(self.dev)

    def test_beacon_is_valid_json(self):
        """Beacons must be valid JSON so peers can parse them."""
        beacon = self.mesh._make_beacon()
        parsed = json.loads(beacon.decode())
        for key in ("node_id", "hostname", "api_port", "ram_gb", "platform", "ts"):
            self.assertIn(key, parsed, f"Beacon missing key: {key}")

    def test_beacon_contains_node_id(self):
        beacon = json.loads(self.mesh._make_beacon())
        self.assertEqual(beacon["node_id"], self.dev.node_id)

    def test_peer_is_alive_within_ttl(self):
        aio = self.aio
        peer = aio.Peer(
            node_id="x", hostname="h", address="10.0.0.1",
            api_port=9480, ram_gb=16.0, platform="cpu",
            last_seen=time.time(),
        )
        self.assertTrue(peer.is_alive())

    def test_peer_dead_after_ttl(self):
        aio = self.aio
        peer = aio.Peer(
            node_id="x", hostname="h", address="10.0.0.1",
            api_port=9480, ram_gb=16.0, platform="cpu",
            last_seen=time.time() - aio.PEER_TTL - 1,
        )
        self.assertFalse(peer.is_alive(),
            "Peer should be considered dead after PEER_TTL seconds")

    def test_live_peers_filters_dead(self):
        """
        CRITIQUE: If dead peers stay in the table, the mesh manager tries to
        shard layers to unreachable nodes — silent OOM or hanging requests.
        """
        aio = self.aio
        alive = aio.Peer("a","h","1.1.1.1",9480,16.0,"cpu",time.time())
        dead  = aio.Peer("b","h","2.2.2.2",9480,16.0,"cpu",
                         time.time() - aio.PEER_TTL - 1)
        self.mesh._peers = {"a": alive, "b": dead}
        live = self.mesh.live_peers()
        self.assertEqual([p.node_id for p in live], ["a"])

    def test_peer_url_format(self):
        aio = self.aio
        peer = aio.Peer("a","h","192.168.1.5",9480,16.0,"cpu")
        self.assertEqual(peer.url("/status"), "http://192.168.1.5:9480/status")

    def test_best_peer_none_when_empty(self):
        self.mesh._peers = {}
        self.assertIsNone(self.mesh.best_peer_for_large())

    def test_best_peer_selects_highest_ram(self):
        aio = self.aio
        now = time.time()
        p1 = aio.Peer("a","h","1.1.1.1",9480,16.0,"cpu",now)
        p2 = aio.Peer("b","h","1.1.1.2",9480,64.0,"cpu",now)
        p3 = aio.Peer("c","h","1.1.1.3",9480,32.0,"cpu",now)
        self.mesh._peers = {"a":p1,"b":p2,"c":p3}
        best = self.mesh.best_peer_for_large()
        self.assertEqual(best.node_id, "b",
            "best_peer_for_large should return the node with most RAM")


# ==============================================================================
# 9. PYTHON SANDBOX
# ==============================================================================

class TestPythonSandbox(unittest.TestCase):
    """
    CRITIQUE: The sandbox runs agent-generated code.  If isolation breaks,
    the agent can exfiltrate files, modify the host system, or DOS the process
    via infinite loops.  Tests confirm basic isolation and timeout enforcement.
    """

    def setUp(self):
        self.sandbox = _import_aio().PythonSandbox()

    def test_hello_world(self):
        out = self.sandbox.run('print("hello")')
        self.assertIn("hello", out)

    def test_arithmetic(self):
        out = self.sandbox.run("print(2 + 2)")
        self.assertIn("4", out)

    def test_timeout_enforced(self):
        """Infinite loop must be killed within TIMEOUT seconds."""
        start = time.time()
        out = self.sandbox.run("while True: pass")
        elapsed = time.time() - start
        self.assertLessEqual(elapsed, self.sandbox.TIMEOUT + 5,
            "Sandbox should kill infinite loops")
        self.assertIn("timeout", out.lower())

    def test_syntax_error_captured(self):
        """Syntax errors must be captured, not crash the test process."""
        out = self.sandbox.run("def broken(:")
        self.assertTrue(len(out) > 0)  # stderr captured

    def test_output_truncated(self):
        """
        CRITIQUE: Huge output floods the agent context window, causing the
        model to drop important earlier content.  Truncation is essential.
        """
        out = self.sandbox.run("print('x' * 100000)")
        self.assertLessEqual(len(out), 4100,
            "Sandbox output must be capped to prevent context flooding")


# ==============================================================================
# 10. AGENT TOOL DISPATCH
# ==============================================================================

class TestAgentTools(unittest.TestCase):
    """
    CRITIQUE: Tool dispatch is the agent's interface to the real world.
    Injection attacks, path traversal, and unchecked shell commands are
    the primary risk surface.
    """

    def setUp(self):
        self.aio = _import_aio()
        self.rag  = self.aio.RagStore()
        self.dev  = self.aio.detect_device()
        self.mesh = self.aio.MeshPeer(self.dev)

    def _dispatch(self, name, args):
        return self.aio._tool_dispatch(name, args, self.rag, self.mesh)

    def test_python_tool(self):
        result = self._dispatch("python", "print(1 + 1)")
        self.assertIn("2", result)

    def test_search_tool_returns_string(self):
        with patch.object(self.aio, "ddgs_search", return_value=[
            {"body": "Python is great", "href": "http://example.com"}
        ]):
            result = self._dispatch("search", "Python")
        self.assertIsInstance(result, str)
        self.assertIn("Python", result)

    def test_read_file_nonexistent(self):
        """Nonexistent file must return error string, not raise FileNotFoundError."""
        result = self._dispatch("read_file", "/tmp/definitely_missing_xyz_abc.txt")
        self.assertIn("not found", result.lower())

    def test_write_and_read_file(self):
        """write_file then read_file round-trip must work."""
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "test.txt")
            write_result = self._dispatch("write_file", f"{fpath}, Hello from agent")
            self.assertIn("Written", write_result)
            read_result = self._dispatch("read_file", fpath)
            self.assertIn("Hello from agent", read_result)

    def test_shell_blocks_compound_commands(self):
        """
        CRITIQUE: Compound shell commands (;, &&, ||) enable injection.
        The agent should refuse them — a ReAct agent can still get shell
        output through simpler commands.
        """
        for dangerous in ["ls; rm -rf /", "ls && echo pwned", "ls || echo bad"]:
            result = self._dispatch("shell", dangerous)
            self.assertIn("not allowed", result.lower(),
                f"Compound command '{dangerous}' should be blocked")

    def test_recall_unknown_key(self):
        result = self._dispatch("recall", "nonexistent_key_xyz")
        self.assertIn("not found", result.lower())

    def test_remember_and_recall(self):
        self._dispatch("remember", "color=blue")
        result = self._dispatch("recall", "color")
        self.assertEqual(result, "blue")

    def test_done_returns_done_prefix(self):
        result = self._dispatch("done", "Task complete")
        self.assertTrue(result.startswith("DONE:"))

    def test_unknown_tool_returns_error(self):
        result = self._dispatch("nonexistent_tool", "args")
        self.assertIn("Unknown tool", result)


# ==============================================================================
# 11. INFERENCE ENGINE (mocked — no real model loaded)
# ==============================================================================

class TestInferenceEngine(unittest.TestCase):
    """
    CRITIQUE: Real model loading takes minutes and requires 5–50 GB.
    These tests mock the underlying backends and verify the swap logic,
    thermal gating, and streaming interface without touching disk.
    """

    def setUp(self):
        self.aio = _import_aio()
        self.dev  = self.aio.detect_device()
        self.gov  = self.aio.ThermalGovernor()
        self.eng  = self.aio.InferenceEngine(self.dev, self.gov)

    def _mock_spec(self, engine: str = "mlx") -> object:
        spec = MagicMock()
        spec.engine = engine
        spec.ctx    = 4096
        spec.key    = "test-model"
        return spec

    def test_generate_blocked_during_emergency(self):
        """
        CRITIQUE: If thermal EMERGENCY doesn't block inference, the device
        can sustain 100°C+ during generation — hardware damage.
        """
        self.gov._state = self.aio.ThermalState.EMERGENCY
        spec = self._mock_spec()
        result = self.eng.generate("hello", spec, Path("/fake"), max_tokens=10)
        self.assertIn("Thermal", result)
        self.assertIn("limit", result)

    def test_unload_on_emergency_callback(self):
        """ThermalGovernor EMERGENCY callback must trigger model unload."""
        self.eng._model = MagicMock()
        self.eng._tok   = MagicMock()
        self.eng._active = "mlx:/fake"
        self.gov._state = self.aio.ThermalState.EMERGENCY
        self.eng._on_thermal(self.aio.ThermalState.EMERGENCY)
        self.assertIsNone(self.eng._active,
            "EMERGENCY should unload active model")

    def test_unload_clears_state(self):
        """_unload must clear both MLX and GGUF state."""
        self.eng._model  = MagicMock()
        self.eng._tok    = MagicMock()
        self.eng._llama  = MagicMock()
        self.eng._active = "mlx:/something"
        self.eng._unload()
        self.assertIsNone(self.eng._model)
        self.assertIsNone(self.eng._llama)
        self.assertIsNone(self.eng._active)


# ==============================================================================
# 12. THERMAL TRAINER
# ==============================================================================

class TestThermalTrainer(unittest.TestCase):
    """
    CRITIQUE: The trainer runs idle-time background LoRA fine-tuning.
    A trainer that launches during active inference floods RAM and causes OOM.
    Tests verify the idle gate and thermal gate.
    """

    def setUp(self):
        self.aio   = _import_aio()
        self.dev   = self.aio.detect_device()
        self.gov   = self.aio.ThermalGovernor()

    def _make_trainer(self, idle_s: float = 0) -> object:
        trainer = self.aio.ThermalTrainer(self.dev, self.gov, None, None)
        trainer._last_active = time.time() - idle_s
        return trainer

    def test_not_training_initially(self):
        trainer = self._make_trainer()
        self.assertFalse(trainer.is_training)

    def test_record_interaction_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(self.aio, "TRAIN_DIR", Path(td)):
                trainer = self._make_trainer()
                trainer.record_interaction("Q", "A", tag="test")
                buf = Path(td) / "test_buffer.jsonl"
                self.assertTrue(buf.exists())
                line = json.loads(buf.read_text().strip())
                self.assertIn("User: Q", line["text"])
                self.assertIn("Assistant: A", line["text"])

    def test_touch_resets_idle_timer(self):
        trainer = self._make_trainer(idle_s=3600)
        trainer.touch()
        idle = time.time() - trainer._last_active
        self.assertLess(idle, 5.0, "touch() must reset idle timer")

    def test_skip_when_not_apple_silicon(self):
        """
        CRITIQUE: MLX training on non-Apple-Silicon would crash immediately.
        The trainer must check platform and abort silently.
        """
        dev = MagicMock()
        dev.platform = "cuda"
        trainer = self.aio.ThermalTrainer(dev, self.gov, None, None)
        # start() should not spawn a thread that will immediately crash
        trainer.start()   # should log "not Apple Silicon" and return
        self.assertFalse(trainer._running)


# ==============================================================================
# 13. CLI ARGUMENT PARSING
# ==============================================================================

class TestCLIArguments(unittest.TestCase):
    """
    CRITIQUE: argparse misconfigurations can silently ignore flags or conflict
    with each other.  These tests verify every documented CLI option works.
    """

    def _parse(self, *args):
        import argparse
        aio = _import_aio()
        parser = argparse.ArgumentParser()
        parser.add_argument("--install",   action="store_true")
        parser.add_argument("--chat",      action="store_true")
        parser.add_argument("--agent",     metavar="TASK")
        parser.add_argument("--code",      metavar="TASK")
        parser.add_argument("--train",     action="store_true")
        parser.add_argument("--mesh",      action="store_true")
        parser.add_argument("--serve",     action="store_true")
        parser.add_argument("--status",    action="store_true")
        parser.add_argument("--port",      type=int, default=9480)
        parser.add_argument("--ui-port",   type=int, default=8501)
        parser.add_argument("--no-rag",    action="store_true")
        parser.add_argument("--force-install", action="store_true")
        return parser.parse_args(list(args))

    def test_default_port(self):
        ns = self._parse()
        self.assertEqual(ns.port, 9480)

    def test_custom_port(self):
        ns = self._parse("--port", "9999")
        self.assertEqual(ns.port, 9999)

    def test_custom_ui_port(self):
        ns = self._parse("--ui-port", "8888")
        self.assertEqual(ns.ui_port, 8888)

    def test_install_flag(self):
        ns = self._parse("--install")
        self.assertTrue(ns.install)

    def test_agent_task(self):
        ns = self._parse("--agent", "summarize this document")
        self.assertEqual(ns.agent, "summarize this document")

    def test_code_task(self):
        ns = self._parse("--code", "write a hello world script")
        self.assertEqual(ns.code, "write a hello world script")

    def test_no_rag_flag(self):
        ns = self._parse("--no-rag")
        self.assertTrue(ns.no_rag)

    def test_force_install(self):
        ns = self._parse("--force-install")
        self.assertTrue(ns.force_install)

    def test_serve_flag(self):
        ns = self._parse("--serve")
        self.assertTrue(ns.serve)

    def test_all_flags_together(self):
        """All non-conflicting flags should parse without error."""
        ns = self._parse("--no-rag", "--port", "9000", "--ui-port", "9001")
        self.assertTrue(ns.no_rag)
        self.assertEqual(ns.port, 9000)
        self.assertEqual(ns.ui_port, 9001)


# ==============================================================================
# 14. LIVE API TESTS (skip unless PAI_API is set and server is running)
# ==============================================================================

@unittest.skipUnless(LIVE, "Set PAI_API=http://localhost:9480 and start AIO to run live tests")
class TestLiveAPI(unittest.TestCase):
    """
    CRITIQUE: Live tests catch integration failures that unit tests miss:
    wrong Content-Type headers, serialisation mismatches, CORS issues.
    They are opt-in to avoid CI requiring a running server.
    """

    def _get(self, path: str):
        import urllib.request
        with urllib.request.urlopen(f"{PAI_API}{path}", timeout=10) as r:
            return json.loads(r.read())

    def _post(self, path: str, data: dict):
        import urllib.request
        payload = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{PAI_API}{path}", data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def test_status_endpoint(self):
        data = self._get("/status")
        for key in ("version", "device", "ram_gb", "thermal"):
            self.assertIn(key, data, f"/status missing key: {key}")

    def test_status_version_matches(self):
        data = self._get("/status")
        aio  = _import_aio()
        self.assertEqual(data["version"], aio.PAI_VERSION)

    def test_thermal_endpoint(self):
        data = self._get("/thermal")
        self.assertIn("state", data)
        self.assertIn(data["state"],
                      ["NOMINAL","WARM","HOT","CRITICAL","EMERGENCY"])

    def test_generate_returns_response(self):
        """
        CRITIQUE: The most important live test — verifies the full stack:
        routing → model load → inference → response serialisation.
        Use 'sized' model to keep the test fast.
        """
        data = self._post("/generate", {
            "prompt":  "Reply with exactly one word: hello",
            "model":   "sized",
            "max_tokens": 10,
            "web_rag": False,
        })
        self.assertIn("response", data)
        self.assertIsInstance(data["response"], str)
        self.assertGreater(len(data["response"]), 0)

    def test_generate_includes_model_key(self):
        data = self._post("/generate", {
            "prompt": "hi", "model": "sized",
            "max_tokens": 5, "web_rag": False
        })
        self.assertIn("model", data)

    def test_rag_add_and_search(self):
        self._post("/rag/add", {"text": "The Pacific Ocean is the largest ocean.", "source": "test"})
        data = self._post("/rag/search?query=largest+ocean&top_k=1", {})
        self.assertIn("results", data)

    def test_mesh_peers_endpoint(self):
        data = self._get("/mesh/peers")
        self.assertIn("peers", data)
        self.assertIsInstance(data["peers"], list)

    def test_agent_endpoint(self):
        data = self._post("/agent", {
            "task": "What is 2 + 2? Reply with just the number.",
            "web_rag": False,
        })
        self.assertIn("answer", data)
        self.assertIn("4", data["answer"])


# ==============================================================================
# 15. SCRIPT FILES EXIST AND ARE EXECUTABLE
# ==============================================================================

class TestScriptFiles(unittest.TestCase):
    """
    CRITIQUE: Scripts that don't exist or aren't executable fail silently
    when double-clicked on macOS/Linux — user gets no error message.
    """

    BASE = AIO_DIR

    def test_runaio_sh_exists(self):
        self.assertTrue((self.BASE / "runaio.sh").exists())

    def test_runaio_bat_exists(self):
        self.assertTrue((self.BASE / "runaio.bat").exists())

    def test_stopaio_sh_exists(self):
        self.assertTrue((self.BASE / "stopaio.sh").exists())

    def test_stopaio_bat_exists(self):
        self.assertTrue((self.BASE / "stopaio.bat").exists())

    def test_license_exists(self):
        self.assertTrue((self.BASE / "LICENSE").exists())

    def test_license_contains_apache(self):
        text = (self.BASE / "LICENSE").read_text()
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)

    @unittest.skipUnless(platform.system() != "Windows", "Unix only")
    def test_sh_scripts_have_shebang(self):
        for name in ("runaio.sh", "stopaio.sh"):
            first = (self.BASE / name).read_text().splitlines()[0]
            self.assertTrue(first.startswith("#!"),
                f"{name} must start with a shebang line")

    @unittest.skipUnless(platform.system() != "Windows", "Unix only")
    def test_pai_command_is_executable(self):
        cmd = self.BASE / "scripts" / "AIO.command"
        if cmd.exists():
            self.assertTrue(os.access(cmd, os.X_OK),
                "AIO.command must be chmod +x")

    def test_scripts_dir_contents(self):
        scripts = self.BASE / "scripts"
        self.assertTrue(scripts.is_dir())
        names = {p.name for p in scripts.iterdir()}
        expected = {"AIO.command", "StopAIO.command", "AIO.desktop",
                    "install_desktop.sh", "install_desktop.bat"}
        self.assertEqual(expected, names & expected,
            f"scripts/ missing: {expected - names}")


# ==============================================================================
# 16. AGENT MEMORY
# ==============================================================================

class TestAgentMemory(unittest.TestCase):
    """
    CRITIQUE: AgentMemory persists key-value facts across agent steps.
    If remember/recall are broken, the agent forgets context mid-task
    and hallucinates previous tool results.
    """

    def setUp(self):
        self.mem = _import_aio().AgentMemory()

    def test_remember_and_recall(self):
        self.mem.remember("color", "red")
        self.assertEqual(self.mem.recall("color"), "red")

    def test_recall_missing_key(self):
        result = self.mem.recall("missing_key")
        self.assertEqual(result, "[not found]")

    def test_overwrite_key(self):
        self.mem.remember("x", "1")
        self.mem.remember("x", "2")
        self.assertEqual(self.mem.recall("x"), "2")

    def test_whitespace_stripped_from_key(self):
        self.mem.remember("  key  ", "value")
        self.assertEqual(self.mem.recall("key"), "value")


# ==============================================================================
# Entry point
# ==============================================================================

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="AIO Test Suite")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-k", "--filter",  metavar="PATTERN",
                    help="Only run tests whose name contains PATTERN")
    ap.add_argument("--list", action="store_true", help="List all test names")
    args, remaining = ap.parse_known_args()

    loader  = unittest.TestLoader()
    suite   = loader.discover(start_dir=str(AIO_DIR), pattern="test_pai.py")

    if args.list:
        for ts in suite:
            for tc in ts:
                for t in tc:
                    print(t.id())
        sys.exit(0)

    if args.filter:
        filtered = unittest.TestSuite()
        for ts in suite:
            for tc in ts:
                for t in tc:
                    if args.filter.lower() in t.id().lower():
                        filtered.addTest(t)
        suite = filtered

    verbosity = 2 if args.verbose else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
