"""Tests for selecting and routing Orienta's output protocols."""

import os
import sys
import ctypes
import math
import struct
import tempfile
import threading
import types
import unittest
from pathlib import Path
from queue import Empty, Queue
from unittest.mock import Mock, patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from src.config.config import (
    OUTPUT_PROTOCOL_FREETRACK,
    OUTPUT_PROTOCOL_NPCLIENT,
    OUTPUT_PROTOCOL_UDP,
)
from src.util.game_client_registration import (
    FreeTrackClientRegistration,
    NPCLIENT_DLLS,
    NPClientRegistration,
)
from src.workers.output_wrk import OUTPUT_PROTOCOLS
from src.util.tracking_sample import TrackingSample
from src.outputs.shared_memory_output import (
    FTData,
    FTHeap,
    FreeTrackSharedMemory,
    freetrack_pose_from_euler,
    load_freetrack_game_response_tables,
)
from src.workers.gui_qt.panels.output_panel import OutputPanelQt
from src.outputs.udp_output import UDPOutput


def _pose_batch(pose, timestamp=1.0):
    rates = tuple(pose[3:6]) if len(pose) >= 6 else (0.0, 0.0, 0.0)
    return (TrackingSample(timestamp, tuple(pose[:3]), rates),)


class OutputProtocolSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.output_control_queue = Queue()
        self.messages = []
        self.panel = OutputPanelQt(
            None,
            self.output_control_queue,
            self.messages.append,
        )
        self.addCleanup(self.panel.close)

    def test_udp_is_default_and_legacy_network_preferences_still_load(self):
        self.assertEqual(self.panel.get_output_protocol(), OUTPUT_PROTOCOL_UDP)
        self.assertEqual(
            self.panel.output_toggle_btn.text(),
            "Start Tracking",
        )
        self.assertEqual(
            [
                self.panel.output_protocol_combo.itemText(index)
                for index in range(self.panel.output_protocol_combo.count())
            ],
            [
                "UDP (opentrack)",
                "FreeTrack 2.0 (Direct)",
                "TrackIR-compatible",
            ],
        )
        self.panel.set_prefs({
            "network": {
                "udp_ip": "127.0.0.2",
                "udp_port": "5555",
            },
        })
        self.assertEqual(self.panel.get_output_protocol(), OUTPUT_PROTOCOL_UDP)
        self.assertEqual(self.panel.get_udp_config(), ("127.0.0.2", 5555))
        self.assertEqual(
            self.panel.get_prefs()["output_protocol"],
            OUTPUT_PROTOCOL_UDP,
        )

    def test_direct_selection_routes_and_enables_only_freetrack(self):
        self.panel.output_protocol_combo.setCurrentIndex(1)
        self.assertEqual(self.panel.get_output_protocol(), OUTPUT_PROTOCOL_FREETRACK)
        self.assertEqual(
            self.output_control_queue.get_nowait(),
            ("set_protocol", OUTPUT_PROTOCOL_FREETRACK),
        )
        self.assertTrue(self.panel.udp_settings_widget.isHidden())

        self.panel.output_toggle_btn.click()
        self.assertTrue(self.panel.output_enabled)
        self.assertEqual(self.panel.output_toggle_btn.text(), "Stop Tracking")
        self.assertEqual(
            self.output_control_queue.get_nowait(),
            ("set_enabled", True),
        )

    def test_npclient_selection_routes_shared_memory_and_registers_npclient(self):
        registration = Mock()
        panel = OutputPanelQt(
            None,
            Queue(),
            self.messages.append,
            npclient_registration=registration,
        )
        self.addCleanup(panel.close)

        panel.output_protocol_combo.setCurrentIndex(2)
        self.assertEqual(panel.get_output_protocol(), OUTPUT_PROTOCOL_NPCLIENT)
        self.assertEqual(
            panel.output_control_queue.get_nowait(),
            ("set_protocol", OUTPUT_PROTOCOL_NPCLIENT),
        )
        panel.toggle_output()
        registration.register.assert_called_once_with()
        self.assertEqual(panel.output_toggle_btn.text(), "Stop Tracking")
        self.assertEqual(
            panel.output_control_queue.get_nowait(),
            ("set_enabled", True),
        )

        panel.toggle_output()
        registration.restore.assert_called_once_with()
        self.assertEqual(
            panel.output_control_queue.get_nowait(),
            ("set_enabled", False),
        )

    def test_switching_active_direct_protocols_switches_client_registration(self):
        freetrack_registration = Mock()
        npclient_registration = Mock()
        panel = OutputPanelQt(
            None,
            Queue(),
            self.messages.append,
            freetrack_client_registration=freetrack_registration,
            npclient_registration=npclient_registration,
        )
        self.addCleanup(panel.close)

        panel.output_protocol_combo.setCurrentIndex(1)
        panel.toggle_output()
        panel.output_protocol_combo.setCurrentIndex(2)

        self.assertEqual(panel.get_output_protocol(), OUTPUT_PROTOCOL_NPCLIENT)
        freetrack_registration.register.assert_called_once_with()
        freetrack_registration.restore.assert_called_once_with()
        npclient_registration.register.assert_called_once_with()
        self.assertEqual(
            [panel.output_control_queue.get_nowait() for _ in range(5)],
            [
                ("set_protocol", OUTPUT_PROTOCOL_FREETRACK),
                ("set_enabled", True),
                ("set_enabled", False),
                ("set_protocol", OUTPUT_PROTOCOL_NPCLIENT),
                ("set_enabled", True),
            ],
        )

    def test_direct_output_registers_then_restores_client_path(self):
        registration = Mock()
        panel = OutputPanelQt(
            None,
            Queue(),
            self.messages.append,
            freetrack_client_registration=registration,
        )
        self.addCleanup(panel.close)
        panel.output_protocol_combo.setCurrentIndex(1)
        panel.output_toggle_btn.click()
        registration.register.assert_called_once_with()
        panel.output_toggle_btn.click()
        registration.restore.assert_called_once_with()

    def test_switching_active_output_disables_old_target_before_enabling_new(self):
        self.panel.output_protocol_combo.setCurrentIndex(1)
        self.assertEqual(
            self.output_control_queue.get_nowait(),
            ("set_protocol", OUTPUT_PROTOCOL_FREETRACK),
        )
        self.panel.output_toggle_btn.click()
        self.output_control_queue.get_nowait()

        self.panel.output_protocol_combo.setCurrentIndex(0)

        self.assertEqual(
            self.output_control_queue.get_nowait(),
            ("set_enabled", False),
        )
        self.assertEqual(
            self.output_control_queue.get_nowait(),
            ("set_protocol", OUTPUT_PROTOCOL_UDP),
        )
        self.assertEqual(
            self.output_control_queue.get_nowait(),
            ("set_udp", "127.0.0.1", 4243),
        )
        self.assertEqual(
            self.output_control_queue.get_nowait(),
            ("set_enabled", True),
        )
        self.assertTrue(self.panel.output_enabled)
        self.assertTrue(self.panel.is_udp_enabled())
        self.assertFalse(self.panel.udp_settings_widget.isHidden())

    def test_output_worker_error_returns_output_button_to_stopped_state(self):
        self.panel.output_protocol_combo.setCurrentIndex(1)
        self.panel.output_toggle_btn.click()
        self.panel.update_output_status("error")

        self.assertFalse(self.panel.output_enabled)
        self.assertEqual(self.panel.output_toggle_btn.property("status"), "error")
        self.assertTrue(any("output failed" in message.lower() for message in self.messages))

    def test_selected_protocol_is_persisted_and_restored(self):
        self.panel.output_protocol_combo.setCurrentIndex(1)
        saved = self.panel.get_prefs()
        self.assertEqual(
            saved["output_protocol"],
            OUTPUT_PROTOCOL_FREETRACK,
        )

        restored = OutputPanelQt(
            None,
            Queue(),
            self.messages.append,
        )
        self.addCleanup(restored.close)
        restored.set_prefs(saved)
        self.assertEqual(
            restored.get_output_protocol(),
            OUTPUT_PROTOCOL_FREETRACK,
        )

    def test_npclient_protocol_is_persisted_and_restored(self):
        self.panel.output_protocol_combo.setCurrentIndex(2)
        saved = self.panel.get_prefs()
        self.assertEqual(
            saved["output_protocol"],
            OUTPUT_PROTOCOL_NPCLIENT,
        )

        restored = OutputPanelQt(
            None,
            Queue(),
            self.messages.append,
        )
        self.addCleanup(restored.close)
        restored.set_prefs(saved)
        self.assertEqual(restored.get_output_protocol(), OUTPUT_PROTOCOL_NPCLIENT)


class OutputBackendTests(unittest.TestCase):
    def test_output_worker_supports_three_protocols_through_one_router(self):
        self.assertEqual(
            OUTPUT_PROTOCOLS,
            (
                OUTPUT_PROTOCOL_UDP,
                OUTPUT_PROTOCOL_FREETRACK,
                OUTPUT_PROTOCOL_NPCLIENT,
            ),
        )

    def test_udp_backend_sends_translation_first_packet(self):
        with patch("src.outputs.udp_output.socket.socket") as socket_factory:
            backend = UDPOutput("127.0.0.2", 5555)
            backend.send_pose([10, -5, 2])
            backend.set_target("127.0.0.3", 6666)
            backend.send_pose([1, 2, 3])
            backend.close()

        sock = socket_factory.return_value
        self.assertEqual(
            sock.sendto.call_args_list[0].args,
            (struct.pack("<6d", 0, 0, 0, 10, -5, 2), ("127.0.0.2", 5555)),
        )
        self.assertEqual(
            sock.sendto.call_args_list[1].args,
            (struct.pack("<6d", 0, 0, 0, 1, 2, 3), ("127.0.0.3", 6666)),
        )
        sock.close.assert_called_once_with()

    def test_udp_backend_rejects_non_finite_pose(self):
        with patch("src.outputs.udp_output.socket.socket"):
            backend = UDPOutput()
        with self.assertRaisesRegex(ValueError, "finite"):
            backend.send_pose([0, float("nan"), 0])
        backend.close()

    def test_output_worker_uses_one_pose_queue_and_selected_backend(self):
        from src.workers.output_wrk import run_worker
        from src.outputs.native_output_processing import default_native_output_settings

        stop_event = threading.Event()
        pose_queue = Queue()
        control_queue = Queue()
        status_queue = Queue()
        pose = [25.0, -10.0, 3.0]
        pose_queue.put(_pose_batch(pose))
        native_settings = default_native_output_settings()
        native_settings["filter_enabled"] = False
        native_settings["input_limits"]["yaw"] = 30.0
        native_settings["curves"]["yaw"] = [
            [0, 0],
            [15, 90],
            [25, 100],
            [30, 180],
        ]
        native_settings["offsets"]["x_mm"] = 20.0
        control_queue.put(("set_native_processing", native_settings))
        control_queue.put(("set_protocol", OUTPUT_PROTOCOL_NPCLIENT))
        control_queue.put(("set_enabled", True))
        writes = []
        closes = []

        class SharedMemoryBackend:
            mapping_already_exists = False

            def send_pose(self, actual_pose):
                writes.append(actual_pose)
                stop_event.set()

            def close(self):
                closes.append(True)

        udp_module = types.SimpleNamespace(UDPOutput=Mock())
        shared_module = types.SimpleNamespace(
            FreeTrackSharedMemory=Mock(return_value=SharedMemoryBackend())
        )
        with patch.dict(
            sys.modules,
            {
                "src.outputs.udp_output": udp_module,
                "src.outputs.shared_memory_output": shared_module,
            },
        ):
            run_worker(pose_queue, control_queue, stop_event, status_queue)

        for actual, expected in zip(
            writes[0],
            (100.0, -20.0, 6.0, 20.0, 0.0, 0.0),
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(closes, [True])
        udp_module.UDPOutput.assert_not_called()
        shared_module.FreeTrackSharedMemory.assert_called_once_with()
        status_names = []
        while not status_queue.empty():
            status_names.append(status_queue.get_nowait()[0])
        self.assertIn("output_status", status_names)

    def test_output_worker_forwards_gyro_rates_to_native_processing(self):
        from src.outputs.native_output_processing import (
            NativeOutputProcessor,
            default_native_output_settings,
        )
        from src.workers.output_wrk import run_worker

        stop_event = threading.Event()
        pose_queue = Queue()
        control_queue = Queue()
        pose_queue.put(_pose_batch([1.0, 2.0, 3.0, -10.0, 5.0, 0.5], 12.004))
        native_settings = default_native_output_settings()
        native_settings["filter_type"] = "gyro_adaptive"
        control_queue.put(("set_native_processing", native_settings))
        control_queue.put(("set_protocol", OUTPUT_PROTOCOL_FREETRACK))
        control_queue.put(("set_enabled", True))
        received_rates = []
        received_times = []
        original_process = NativeOutputProcessor.process

        def spy(processor, pose, timestamp, rates=None):
            received_rates.append(rates)
            received_times.append(timestamp)
            return original_process(processor, pose, timestamp, rates)

        class SharedMemoryBackend:
            mapping_already_exists = False

            def send_pose(self, actual_pose):
                stop_event.set()

            def close(self):
                pass

        shared_module = types.SimpleNamespace(
            FreeTrackSharedMemory=Mock(return_value=SharedMemoryBackend())
        )
        with patch.dict(
            sys.modules,
            {
                "src.outputs.udp_output": types.SimpleNamespace(UDPOutput=Mock()),
                "src.outputs.shared_memory_output": shared_module,
            },
        ), patch.object(NativeOutputProcessor, "process", spy):
            run_worker(pose_queue, control_queue, stop_event, Queue())

        self.assertEqual(received_rates[0], (-10.0, 5.0, 0.5))
        self.assertEqual(received_times, [12.004])

    def test_native_filtering_is_identical_across_batches_and_host_timing(self):
        from src.outputs.native_output_processing import (
            NativeOutputProcessor,
            default_native_output_settings,
        )
        from src.workers.output_wrk import run_worker

        samples = tuple(
            TrackingSample(
                index / 250,
                (3.0 * math.sin(index / 80), 0.0, 0.0),
                (9.375 * math.cos(index / 80), 0.0, 0.0),
                reset_filter=index == 250,
            )
            for index in range(501)
        )
        for filter_id in ("one_euro", "gyro_adaptive"):
            settings = default_native_output_settings()
            settings["filter_type"] = filter_id
            expected_processor = NativeOutputProcessor(settings)
            expected = []
            for sample in samples:
                if sample.reset_filter:
                    expected_processor.reset()
                expected.append(expected_processor.process(
                    sample.angles, sample.timestamp, sample.rates
                ))
            for batch_size, drain_backlog, host_step in (
                (1, False, 0.004),
                (17, False, 0.035),
                (80, True, 0.001),
            ):
                with self.subTest(filter=filter_id, batch_size=batch_size):
                    pose_queue = Queue()
                    control_queue = Queue()
                    stop_event = threading.Event()
                    pending = [
                        samples[index:index + batch_size]
                        for index in range(0, len(samples), batch_size)
                    ]
                    control_queue.put(("set_native_processing", settings))
                    control_queue.put(("set_protocol", OUTPUT_PROTOCOL_FREETRACK))
                    control_queue.put(("set_enabled", True))
                    observed = []
                    sent = []
                    host_time = 1000.0
                    original_process = NativeOutputProcessor.process

                    def process(processor, angles, timestamp, rates=None):
                        result = original_process(processor, angles, timestamp, rates)
                        observed.append((timestamp, result))
                        return result

                    def get_sample(queue, timeout=0.0, default=None):
                        if queue is pose_queue:
                            if pending and (timeout > 0.0 or drain_backlog):
                                return pending.pop(0)
                            return default
                        try:
                            return queue.get_nowait()
                        except Empty:
                            return default

                    def clock():
                        nonlocal host_time
                        host_time += host_step
                        return host_time

                    class Backend:
                        mapping_already_exists = False

                        def send_pose(self, pose):
                            sent.append(pose)
                            if not pending:
                                stop_event.set()

                        def close(self):
                            pass

                    with patch(
                        "src.outputs.shared_memory_output.FreeTrackSharedMemory",
                        return_value=Backend(),
                    ), patch(
                        "src.workers.output_wrk.safe_queue_get", side_effect=get_sample
                    ), patch(
                        "src.workers.output_wrk.time.perf_counter", side_effect=clock
                    ), patch.object(NativeOutputProcessor, "process", process):
                        run_worker(pose_queue, control_queue, stop_event)

                    self.assertEqual([t for t, _ in observed], [s.timestamp for s in samples])
                    self.assertEqual([pose for _, pose in observed], expected)
                    self.assertEqual(sent[-1], expected[-1])
                    if drain_backlog:
                        self.assertEqual(len(sent), 1)

    def test_output_worker_switches_native_filter_while_output_is_active(self):
        from src.filters import FILTER_REGISTRY
        from src.filters.base import FilterDefinition
        from src.outputs.native_output_processing import default_native_output_settings
        from src.workers.output_wrk import run_worker

        filter_id = "test_live_filter_switch"

        class HalfPoseFilter:
            def reset(self):
                pass

            def process(self, angles, timestamp):
                return tuple(value / 2.0 for value in angles)

        FILTER_REGISTRY[filter_id] = FilterDefinition(
            identifier=filter_id,
            label="Live Switch Test",
            parameters=(),
            factory=lambda settings: HalfPoseFilter(),
        )
        try:
            stop_event = threading.Event()
            pose_queue = Queue()
            control_queue = Queue()
            initial_settings = default_native_output_settings()
            initial_settings["filter_enabled"] = False
            switched_settings = default_native_output_settings()
            switched_settings["filter_type"] = filter_id
            switched_settings["filter_enabled"] = True
            control_queue.put(("set_native_processing", initial_settings))
            control_queue.put(("set_protocol", OUTPUT_PROTOCOL_FREETRACK))
            control_queue.put(("set_enabled", True))
            pose_queue.put(_pose_batch([0.0, 0.0, 0.0]))
            writes = []

            class SharedMemoryBackend:
                mapping_already_exists = False

                def send_pose(self, actual_pose):
                    writes.append(actual_pose)
                    if len(writes) == 1:
                        control_queue.put((
                            "set_native_processing",
                            switched_settings,
                        ))
                        pose_queue.put(_pose_batch([60.0, 0.0, 0.0], 1.004))
                    else:
                        stop_event.set()

                def close(self):
                    pass

            udp_module = types.SimpleNamespace(UDPOutput=Mock())
            shared_module = types.SimpleNamespace(
                FreeTrackSharedMemory=lambda: SharedMemoryBackend()
            )
            with patch.dict(
                sys.modules,
                {
                    "src.outputs.udp_output": udp_module,
                    "src.outputs.shared_memory_output": shared_module,
                },
            ):
                run_worker(pose_queue, control_queue, stop_event)

            self.assertEqual(len(writes), 2)
            self.assertEqual(writes[0][0], 0.0)
            self.assertAlmostEqual(writes[1][0], 60.0)
            udp_module.UDPOutput.assert_not_called()
        finally:
            FILTER_REGISTRY.pop(filter_id, None)

    def test_output_worker_routes_udp_poses_to_udp_backend(self):
        from src.workers.output_wrk import run_worker
        from src.outputs.native_output_processing import default_native_output_settings

        stop_event = threading.Event()
        pose_queue = Queue()
        control_queue = Queue()
        pose = [4.0, -2.0, 1.0]
        pose_queue.put(_pose_batch(pose))
        native_settings = default_native_output_settings()
        native_settings["filter_enabled"] = False
        native_settings["offsets"]["yaw_deg"] = 90.0
        control_queue.put(("set_native_processing", native_settings))
        control_queue.put(("set_udp", "127.0.0.2", 5555))
        control_queue.put(("set_enabled", True))
        sent = []
        closes = []

        class UDPBackend:
            def __init__(self, host, port):
                self.target = (host, port)

            def send_pose(self, actual_pose):
                sent.append((actual_pose, self.target))
                stop_event.set()

            def close(self):
                closes.append(True)

        udp_module = types.SimpleNamespace(UDPOutput=UDPBackend)
        shared_module = types.SimpleNamespace(FreeTrackSharedMemory=Mock())
        with patch.dict(
            sys.modules,
            {
                "src.outputs.udp_output": udp_module,
                "src.outputs.shared_memory_output": shared_module,
            },
        ):
            run_worker(pose_queue, control_queue, stop_event)

        self.assertEqual(sent, [(tuple(pose), ("127.0.0.2", 5555))])
        self.assertEqual(closes, [True])
        shared_module.FreeTrackSharedMemory.assert_not_called()


class FreeTrackProtocolTests(unittest.TestCase):
    def test_shared_memory_layout_matches_free_track_structure(self):
        self.assertEqual(FTData.DataID.offset, 0)
        self.assertEqual(FTData.Yaw.offset, 12)
        self.assertEqual(FTData.RawYaw.offset, 36)
        self.assertEqual(FTHeap.GameID.offset, 92)
        self.assertEqual(FTHeap.table.offset, 96)
        self.assertEqual(FTHeap.GameID2.offset, 104)
        self.assertEqual(ctypes.sizeof(FTData), 92)
        self.assertEqual(ctypes.sizeof(FTHeap), 108)

    def test_face_track_noir_catalog_loads_response_tables(self):
        tables = load_freetrack_game_response_tables()

        self.assertGreater(len(tables), 500)
        self.assertEqual(
            tables[1003],
            (0x5F, 0xD1, 0xDC, 0xB6, 0x65, 0x2F, 0x57, 0x5A),
        )
        self.assertEqual(
            tables[1006],
            (0x9B, 0xFC, 0x88, 0xF6, 0x8F, 0x86, 0x56, 0x05),
        )
        self.assertEqual(tables[13601], (0,) * 8)
        self.assertNotIn(999999, tables)

    def test_face_track_catalog_keeps_first_duplicate_id_and_v160_table_zero(self):
        catalog_path = Path(tempfile.gettempdir()) / f"{uuid4().hex}.csv"
        self.addCleanup(
            lambda: catalog_path.unlink(missing_ok=True)
        )
        catalog_path.write_text(
            "No;Game Name;Game protocol;Supported since;Verified;By;INTERNATIONAL_ID;FTN_ID\n"
            "1;Legacy game;FreeTrack20;V160;;;77;0073B6DCD15F5A572F6500\n"
            "2;Later duplicate;FreeTrack20;V170;;;77;0073B6DCD15F5A572F6500\n"
            "3;Encoded game;FreeTrack20;V170;;;88;0073B6DCD15F5A572F6500\n",
            encoding="utf-8",
        )

        tables = load_freetrack_game_response_tables(catalog_path)

        self.assertEqual(tables[77], (0,) * 8)
        self.assertEqual(
            tables[88],
            (0x5F, 0xD1, 0xDC, 0xB6, 0x65, 0x2F, 0x57, 0x5A),
        )

    def test_face_track_catalog_rejects_an_unexpected_schema(self):
        catalog_path = Path(tempfile.gettempdir()) / f"{uuid4().hex}.csv"
        self.addCleanup(
            lambda: catalog_path.unlink(missing_ok=True)
        )
        catalog_path.write_text("Game;ID\nDCS;1003\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Unexpected FaceTrackNoIR catalog columns"):
            load_freetrack_game_response_tables(catalog_path)

    def test_face_track_catalog_rejects_malformed_rows(self):
        catalog_path = Path(tempfile.gettempdir()) / f"{uuid4().hex}.csv"
        self.addCleanup(
            lambda: catalog_path.unlink(missing_ok=True)
        )
        catalog_path.write_text(
            "No;Game Name;Game protocol;Supported since;Verified;By;INTERNATIONAL_ID;FTN_ID\n"
            "1;Game;FreeTrack20;V170;;;1003;0073B6DCD15F5A572F6500;extra\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "Malformed FaceTrackNoIR catalog row"):
            load_freetrack_game_response_tables(catalog_path)

    def test_orientation_conversion_uses_radians_and_protocol_axis_signs(self):
        yaw, pitch, roll = freetrack_pose_from_euler(90, 45, -180)
        self.assertAlmostEqual(yaw, -1.57079632679)
        self.assertAlmostEqual(pitch, -0.78539816339)
        self.assertAlmostEqual(roll, -3.14159265359)
        self.assertEqual(freetrack_pose_from_euler(0, 0, 0), (0.0, 0.0, 0.0))

    def test_orientation_conversion_rejects_non_finite_values(self):
        with self.assertRaises(ValueError):
            freetrack_pose_from_euler(float("nan"), 0, 0)

    @unittest.skipUnless(sys.platform == "win32", "FreeTrack uses Windows shared memory")
    def test_shared_memory_worker_writes_pose_and_closes_handles(self):
        suffix = uuid4().hex
        shared = FreeTrackSharedMemory(
            f"Local\\OrientaTest_{suffix}",
            f"Local\\OrientaTestMutex_{suffix}",
            f"Local\\OrientaTestPublisher_{suffix}",
        )
        self.addCleanup(shared.close)

        data = FTHeap.from_address(int(shared._view)).data
        shared.write_pose([90, 45, -180])
        shared.send_pose([91, 46, -179, 12.5, -25.0, 40.0])

        expected_yaw, expected_pitch, expected_roll = freetrack_pose_from_euler(
            91,
            46,
            -179,
        )
        self.assertEqual(data.DataID, 3)
        self.assertAlmostEqual(data.Yaw, expected_yaw, places=6)
        self.assertAlmostEqual(data.Pitch, expected_pitch, places=6)
        self.assertAlmostEqual(data.Roll, expected_roll, places=6)
        self.assertEqual((data.X, data.Y, data.Z), (12.5, -25.0, 40.0))
        self.assertEqual(
            (data.RawX, data.RawY, data.RawZ),
            (12.5, -25.0, 40.0),
        )

    @unittest.skipUnless(sys.platform == "win32", "FreeTrack uses Windows shared memory")
    def test_shared_memory_worker_echoes_game_id_and_clears_response_table(self):
        suffix = uuid4().hex
        shared = FreeTrackSharedMemory(
            f"Local\\OrientaGameIdTest_{suffix}",
            f"Local\\OrientaGameIdMutex_{suffix}",
            f"Local\\OrientaGameIdPublisher_{suffix}",
        )
        self.addCleanup(shared.close)
        heap = FTHeap.from_address(int(shared._view))

        heap.GameID = 1003
        for index in range(8):
            heap.table[index] = 0xA5
        shared.write_pose([10, 5, -2])

        self.assertEqual(heap.GameID2, 1003)
        self.assertEqual(
            list(heap.table),
            [0x5F, 0xD1, 0xDC, 0xB6, 0x65, 0x2F, 0x57, 0x5A],
        )
        self.assertEqual(heap.data.DataID, 0)

        shared.write_pose([11, 6, -1])
        self.assertEqual(heap.GameID2, 1003)
        self.assertEqual(heap.data.DataID, 1)

        heap.GameID = 9999
        shared.write_pose([12, 7, 0])
        self.assertEqual(heap.GameID2, 9999)
        self.assertEqual(list(heap.table), [0] * 8)
        self.assertEqual(heap.data.DataID, 0)

    @unittest.skipUnless(sys.platform == "win32", "FreeTrack uses Windows shared memory")
    def test_existing_reader_mapping_is_reused_without_resetting_data_id(self):
        suffix = uuid4().hex
        mapping_name = f"Local\\OrientaReaderTest_{suffix}"
        mutex_name = f"Local\\OrientaReaderMutex_{suffix}"
        publisher_mutex_name = f"Local\\OrientaReaderPublisher_{suffix}"
        shared = FreeTrackSharedMemory(
            mapping_name,
            mutex_name,
            publisher_mutex_name,
        )
        shared.write_pose([12, -8, 3])

        kernel32 = shared._kernel32
        kernel32.OpenFileMappingW.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p,
        ]
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        reader_handle = kernel32.OpenFileMappingW(
            0x000F001F,
            False,
            mapping_name,
        )
        self.assertTrue(reader_handle)
        reader_view = kernel32.MapViewOfFile(
            reader_handle,
            0x000F001F,
            0,
            0,
            ctypes.sizeof(FTHeap),
        )
        self.assertTrue(reader_view)
        self.addCleanup(kernel32.UnmapViewOfFile, reader_view)
        self.addCleanup(kernel32.CloseHandle, reader_handle)

        shared.close()
        reopened = FreeTrackSharedMemory(
            mapping_name,
            mutex_name,
            publisher_mutex_name,
        )
        self.addCleanup(reopened.close)

        self.assertTrue(reopened._mapping_already_exists)
        data = FTHeap.from_address(int(reopened._view)).data
        self.assertEqual(data.DataID, 2)
        self.assertEqual((data.CamWidth, data.CamHeight), (100, 250))
        reopened.write_pose([15, -10, 4])
        self.assertEqual(data.DataID, 3)
        self.assertAlmostEqual(data.Yaw, -0.2617993878, places=6)

    @unittest.skipUnless(sys.platform == "win32", "FreeTrack uses Windows shared memory")
    def test_publisher_initializes_a_mapping_created_by_a_game(self):
        suffix = uuid4().hex
        mapping_name = f"Local\\OrientaClientFirst_{suffix}"
        mutex_name = f"Local\\OrientaClientFirstMutex_{suffix}"
        publisher_name = f"Local\\OrientaClientFirstPublisher_{suffix}"
        first = FreeTrackSharedMemory(mapping_name, mutex_name, publisher_name)
        kernel32 = first._kernel32
        reader = kernel32.OpenFileMappingW(0x0004, False, mapping_name)
        self.assertTrue(reader)
        self.addCleanup(kernel32.CloseHandle, reader)
        heap = FTHeap.from_address(int(first._view))
        heap.data.CamWidth = 0
        heap.data.CamHeight = 0
        heap.data.DataID = 42
        first.close()

        publisher = FreeTrackSharedMemory(mapping_name, mutex_name, publisher_name)
        self.addCleanup(publisher.close)
        data = FTHeap.from_address(int(publisher._view)).data
        self.assertEqual(data.DataID, 42)
        self.assertEqual((data.CamWidth, data.CamHeight), (100, 250))

    @unittest.skipUnless(sys.platform == "win32", "FreeTrack uses Windows shared memory")
    def test_second_orienta_publisher_is_rejected(self):
        suffix = uuid4().hex
        mapping_name = f"Local\\OrientaExclusiveTest_{suffix}"
        mutex_name = f"Local\\OrientaExclusiveMutex_{suffix}"
        publisher_mutex_name = f"Local\\OrientaExclusivePublisher_{suffix}"
        shared = FreeTrackSharedMemory(
            mapping_name,
            mutex_name,
            publisher_mutex_name,
        )
        self.addCleanup(shared.close)

        with self.assertRaisesRegex(RuntimeError, "Another Orienta FreeTrack publisher"):
            FreeTrackSharedMemory(
                mapping_name,
                mutex_name,
                publisher_mutex_name,
            )


class FreeTrackClientRegistrationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Client registration uses the Windows registry")
    def test_another_session_cannot_restore_or_take_over_registered_client(self):
        import winreg

        root_key = f"Software\\Orienta\\RegistrationOwnerTests\\{uuid4().hex}"
        client_key = root_key + "\\Client"
        state_key = root_key + "\\State"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            for filename in NPCLIENT_DLLS:
                (source / filename).write_bytes(b"client")
            arguments = dict(
                source_directory=str(source),
                app_data_directory=str(Path(directory) / "appdata"),
                client_registry_key=client_key,
                state_registry_key=state_key,
            )
            owner = NPClientRegistration(**arguments)
            other = NPClientRegistration(**arguments)
            try:
                path = owner.register()
                self.assertFalse(other.restore())
                with self.assertRaisesRegex(RuntimeError, "another Orienta session"):
                    other.register()
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, client_key) as key:
                    self.assertEqual(winreg.QueryValueEx(key, "Path")[0], path)
                self.assertTrue(owner.restore())
            finally:
                owner.restore()
                for key_name in (state_key, client_key, root_key):
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)

    @unittest.skipUnless(sys.platform == "win32", "Client registration uses the Windows registry")
    def test_npclient_registration_stages_both_dlls_and_restores_registry(self):
        import winreg

        suffix = uuid4().hex
        root_key = f"Software\\Orienta\\NPClientTests\\{suffix}"
        client_key = root_key + "\\Client"
        state_key = root_key + "\\State"
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        source_dir = Path(temp_dir.name) / "source"
        source_dir.mkdir()
        data_dir = Path(temp_dir.name) / "appdata"
        for filename in NPCLIENT_DLLS:
            (source_dir / filename).write_bytes(filename.encode("ascii"))

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                "Path",
                0,
                winreg.REG_SZ,
                r"C:\ExistingNPClient\modules\\",
            )

        registration = NPClientRegistration(
            source_directory=str(source_dir),
            app_data_directory=str(data_dir),
            client_registry_key=client_key,
            state_registry_key=state_key,
        )

        def cleanup_registry():
            for key_name in (state_key, client_key, root_key):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
                except FileNotFoundError:
                    pass

        self.addCleanup(cleanup_registry)
        registered_path = registration.register()
        self.assertTrue(registered_path.endswith("NPClient\\"))
        for filename in NPCLIENT_DLLS:
            self.assertEqual(
                (Path(registered_path) / filename).read_bytes(),
                filename.encode("ascii"),
            )

        self.assertTrue(registration.restore())
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            restored_path, _ = winreg.QueryValueEx(key, "Path")
        self.assertEqual(restored_path, r"C:\ExistingNPClient\modules\\")

    @unittest.skipUnless(sys.platform == "win32", "Client registration uses the Windows registry")
    def test_registration_stages_dlls_and_restores_previous_registry_type(self):
        import winreg

        suffix = uuid4().hex
        root_key = f"Software\\Orienta\\FreeTrackClientTests\\{suffix}"
        client_key = root_key + "\\Client"
        state_key = root_key + "\\State"
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        source_dir = Path(temp_dir.name) / "source"
        data_dir = Path(temp_dir.name) / "appdata"
        source_dir.mkdir()
        for filename in ("FreeTrackClient.dll", "FreeTrackClient64.dll"):
            (source_dir / filename).write_bytes(filename.encode("ascii"))

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                "Path",
                0,
                winreg.REG_EXPAND_SZ,
                r"C:\OpenTrack\modules\\",
            )

        registration = FreeTrackClientRegistration(
            source_directory=str(source_dir),
            app_data_directory=str(data_dir),
            client_registry_key=client_key,
            state_registry_key=state_key,
        )

        def cleanup_registry():
            for key_name in (state_key, client_key, root_key):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
                except FileNotFoundError:
                    pass

        self.addCleanup(cleanup_registry)
        registered_path = registration.register()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            current_path, current_type = winreg.QueryValueEx(key, "Path")
        self.assertEqual(current_path, registered_path)
        self.assertEqual(current_type, winreg.REG_SZ)
        for filename in ("FreeTrackClient.dll", "FreeTrackClient64.dll"):
            self.assertEqual(
                (Path(registered_path) / filename).read_bytes(),
                filename.encode("ascii"),
            )

        self.assertTrue(registration.restore())
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            restored_path, restored_type = winreg.QueryValueEx(key, "Path")
        self.assertEqual(restored_path, r"C:\OpenTrack\modules\\")
        self.assertEqual(restored_type, winreg.REG_EXPAND_SZ)

    @unittest.skipUnless(sys.platform == "win32", "Client registration uses the Windows registry")
    def test_restore_does_not_overwrite_a_path_changed_by_another_app(self):
        import winreg

        suffix = uuid4().hex
        root_key = f"Software\\Orienta\\FreeTrackClientTests\\{suffix}"
        client_key = root_key + "\\Client"
        state_key = root_key + "\\State"
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        source_dir = Path(temp_dir.name) / "source"
        source_dir.mkdir()
        for filename in ("FreeTrackClient.dll", "FreeTrackClient64.dll"):
            (source_dir / filename).write_bytes(b"client")

        registration = FreeTrackClientRegistration(
            source_directory=str(source_dir),
            app_data_directory=str(Path(temp_dir.name) / "appdata"),
            client_registry_key=client_key,
            state_registry_key=state_key,
        )

        def cleanup_registry():
            for key_name in (state_key, client_key, root_key):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
                except FileNotFoundError:
                    pass

        self.addCleanup(cleanup_registry)
        registration.register()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                "Path",
                0,
                winreg.REG_SZ,
                r"C:\OtherTracker\modules\\",
            )

        self.assertFalse(registration.restore())
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            current_path, _ = winreg.QueryValueEx(key, "Path")
        self.assertEqual(current_path, r"C:\OtherTracker\modules\\")

    @unittest.skipUnless(sys.platform == "win32", "Client registration uses the Windows registry")
    def test_new_session_recovers_a_stale_registration(self):
        import winreg

        suffix = uuid4().hex
        root_key = f"Software\\Orienta\\FreeTrackClientTests\\{suffix}"
        client_key = root_key + "\\Client"
        state_key = root_key + "\\State"
        publisher_mutex_name = f"Local\\OrientaRecoveryTest_{suffix}"
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        source_dir = Path(temp_dir.name) / "source"
        source_dir.mkdir()
        for filename in ("FreeTrackClient.dll", "FreeTrackClient64.dll"):
            (source_dir / filename).write_bytes(b"client")

        first_session = FreeTrackClientRegistration(
            source_directory=str(source_dir),
            app_data_directory=str(Path(temp_dir.name) / "appdata"),
            client_registry_key=client_key,
            state_registry_key=state_key,
            publisher_mutex_name=publisher_mutex_name,
        )
        next_session = FreeTrackClientRegistration(
            source_directory=str(source_dir),
            app_data_directory=str(Path(temp_dir.name) / "appdata"),
            client_registry_key=client_key,
            state_registry_key=state_key,
            publisher_mutex_name=publisher_mutex_name,
        )

        def cleanup_registry():
            for key_name in (state_key, client_key, root_key):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
                except FileNotFoundError:
                    pass

        self.addCleanup(cleanup_registry)
        first_session.register()

        self.assertTrue(next_session.recover_previous_registration())
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            client_key,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            with self.assertRaises(FileNotFoundError):
                winreg.QueryValueEx(key, "Path")


if __name__ == "__main__":
    unittest.main()
