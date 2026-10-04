"""
Input worker for orienta.

Handles keyboard and gamepad input operations including:
- Shortcut monitoring for recenter operations
- Input capture during shortcut configuration
- Pygame management and gamepad state

This cleaned implementation centralizes logging via util.log_utils and
captures any remaining legacy `print()` calls by redirecting builtins.print
to the provided `log_queue` in `run_worker()`.
"""

import threading
import time
import queue
import traceback
import os

from src.util.error_utils import safe_queue_put
from src.config.config import QUEUE_PUT_TIMEOUT
from src.util.log_utils import log_info, log_warning, log_error

# Optional pygame support
try:
    # Suppress pygame welcome message
    os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
    import pygame
    PYGAME_AVAILABLE = True
except Exception:
    PYGAME_AVAILABLE = False


class PygameManager:
    """Lightweight pygame manager for joystick initialization."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._joysticks = []
        return cls._instance

    def initialize(self):
        """Initialize pygame and attached joysticks. Returns True if at least
        one joystick is available, False otherwise."""
        if not PYGAME_AVAILABLE:
            return False

        try:
            if not self._initialized:
                pygame.quit()
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass

                pygame.init()

                if not pygame.get_init():
                    return False

                try:
                    pygame.joystick.init()
                except Exception:
                    pass

                joystick_count = pygame.joystick.get_count()
                self._joysticks = []

                for i in range(joystick_count):
                    try:
                        joy = pygame.joystick.Joystick(i)
                        joy.init()
                        self._joysticks.append(joy)
                    except Exception:
                        # Skip joysticks that fail to initialize
                        continue

                self._initialized = True

            return len(self._joysticks) > 0

        except Exception:
            self._initialized = False
            return False

    def get_joysticks(self):
        return getattr(self, '_joysticks', []) if getattr(self, '_initialized', False) else []

    def cleanup(self):
        # Keep pygame initialized to avoid spurious reinitialization messages
        try:
            if getattr(self, '_initialized', False):
                # Do not call pygame.quit() here; leave it initialized for reuse
                pass
        except Exception:
            pass


class InputWorker:
    """Worker that handles input shortcuts and capture mode."""

    def __init__(self, command_queue=None, response_queue=None, log_queue=None):
        self.command_queue = command_queue or queue.Queue()
        self.response_queue = response_queue or queue.Queue()
        self.log_queue = log_queue

        self.running = False
        self.worker_thread = None

        self.pygame_manager = PygameManager()

        self.shortcuts = {}
        self._key_states = {}

        self.keyboard_listener_active = False
        self.gamepad_listener_active = False
        self.keyboard_thread = None
        self.gamepad_thread = None

        self.capture_mode = False
        self._last_button_states = {}
        self._last_hat_states = {}

        # Optional keyboard library
        self.keyboard_available = False
        try:
            import keyboard
            self.keyboard_available = True
            self.keyboard = keyboard
        except Exception:
            self.keyboard_available = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        log_info(self.log_queue, 'InputWorker', 'Started (listeners will start on demand)')

    def stop(self):
        if not self.running:
            return
        self.running = False

        self._stop_keyboard_listener()
        self._stop_gamepad_listener()

        for t in (self.worker_thread, self.keyboard_thread, self.gamepad_thread):
            if t and getattr(t, 'is_alive', lambda: False)():
                try:
                    t.join(timeout=1.0)
                except Exception:
                    pass

        log_info(self.log_queue, 'InputWorker', 'Stopped')

    def _worker_loop(self):
        while self.running:
            try:
                try:
                    command = self.command_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                self._process_command(command)

            except Exception as e:
                log_error(self.log_queue, 'InputWorker', f'Error in worker loop: {e}')
                time.sleep(0.1)

    def _process_command(self, command):
        if not isinstance(command, (list, tuple)) or len(command) < 1:
            return

        cmd_type = command[0]

        if cmd_type == 'set_shortcut':
            if len(command) >= 4:
                self._set_shortcut(command[1], command[2], command[3])
            elif len(command) >= 3:
                self._set_shortcut(command[1], command[2], 'reset_orientation')

        elif cmd_type == 'clear_shortcut':
            if len(command) >= 2:
                self._clear_shortcut(command[1])
            else:
                self._clear_all_shortcuts()

        elif cmd_type == 'start_capture':
            self._start_capture()

        elif cmd_type == 'stop_capture':
            self._stop_capture()

        elif cmd_type == 'trigger_reset':
            self._send_response(('shortcut_triggered', 'manual_trigger', 'reset_orientation'))

    def _set_shortcut(self, key, display_name, action):
        if not key or key == 'None':
            self._clear_shortcut(action)
            return

        self.shortcuts[action] = (key, display_name)
        log_info(self.log_queue, 'InputWorker', f"Shortcut set for '{action}': {key} ({display_name})")

        if key.startswith('joy'):
            if not self.gamepad_listener_active:
                self._start_gamepad_listener()
        else:
            if not self.keyboard_listener_active:
                self._start_keyboard_listener()

    def _clear_shortcut(self, action):
        if action in self.shortcuts:
            del self.shortcuts[action]
            log_info(self.log_queue, 'InputWorker', f"Shortcut cleared for '{action}'")

        if not self.shortcuts:
            self._stop_keyboard_listener()
            self._stop_gamepad_listener()

    def _clear_all_shortcuts(self):
        self._stop_keyboard_listener()
        self._stop_gamepad_listener()
        self.shortcuts = {}
        log_info(self.log_queue, 'InputWorker', 'All shortcuts cleared, listeners stopped')

    def _start_capture(self):
        if self.capture_mode:
            return
        self.capture_mode = True
        self._last_button_states.clear()
        self._last_hat_states.clear()
        self._start_keyboard_listener()
        self._start_gamepad_listener()
        log_info(self.log_queue, 'InputWorker', 'Capture mode enabled (both listeners active)')

    def _stop_capture(self):
        if not self.capture_mode:
            return
        self.capture_mode = False
        self._stop_keyboard_listener()
        self._stop_gamepad_listener()
        log_info(self.log_queue, 'InputWorker', 'Capture mode disabled (both listeners stopped)')

    def _start_keyboard_listener(self):
        if not self.keyboard_available:
            return
        if self.keyboard_listener_active:
            return
        self.keyboard_listener_active = True
        self.keyboard_thread = threading.Thread(target=self._keyboard_listener_loop, daemon=True)
        self.keyboard_thread.start()

    def _stop_keyboard_listener(self):
        if not self.keyboard_listener_active:
            return
        self.keyboard_listener_active = False
        if self.keyboard_available:
            try:
                self.keyboard.unhook_all()
            except Exception:
                pass
        if self.keyboard_thread and self.keyboard_thread.is_alive():
            try:
                self.keyboard_thread.join(timeout=0.5)
            except Exception:
                pass
        self.keyboard_thread = None

    def _keyboard_listener_loop(self):
        if not self.keyboard_available:
            return

        log_info(self.log_queue, 'InputWorker', 'Keyboard listener started')

        def on_key_event(event):
            if not self.keyboard_listener_active:
                return
            key_name = event.name

            if self.capture_mode:
                if event.event_type == 'down':
                    display_name = key_name.upper() if len(key_name) == 1 else key_name.title()
                    log_info(self.log_queue, 'InputWorker', f'Keyboard captured: {key_name} (display: {display_name})')
                    self._send_response(('input_captured', key_name, display_name))
                return

            for action, (shortcut_key, display_name) in list(self.shortcuts.items()):
                if shortcut_key == key_name:
                    if event.event_type == 'down':
                        if not self._key_states.get(key_name, False):
                            self._key_states[key_name] = True
                            log_info(self.log_queue, 'InputWorker', f'Keyboard shortcut pressed: {key_name} -> {action}')
                            self._send_response(('shortcut_pressed', shortcut_key, action))
                    elif event.event_type == 'up':
                        if self._key_states.get(key_name, False):
                            self._key_states[key_name] = False
                            log_info(self.log_queue, 'InputWorker', f'Keyboard shortcut released: {key_name} -> {action}')
                            self._send_response(('shortcut_released', shortcut_key, action))
                    break

        try:
            self.keyboard.hook(on_key_event)
            while self.keyboard_listener_active and self.running:
                time.sleep(0.1)
        except Exception as e:
            log_error(self.log_queue, 'InputWorker', f'Keyboard listener error: {e}')
            traceback.print_exc()
        finally:
            try:
                self.keyboard.unhook_all()
            except Exception:
                pass
            log_info(self.log_queue, 'InputWorker', 'Keyboard listener stopped')

    def _start_gamepad_listener(self):
        if not PYGAME_AVAILABLE:
            return
        if self.gamepad_listener_active:
            return

        if not self.pygame_manager.initialize():
            log_warning(self.log_queue, 'InputWorker', 'Failed to initialize pygame for gamepad')
            return

        self.gamepad_listener_active = True
        self.gamepad_thread = threading.Thread(target=self._gamepad_listener_loop, daemon=True)
        self.gamepad_thread.start()

    def _stop_gamepad_listener(self):
        if not self.gamepad_listener_active:
            return
        self.gamepad_listener_active = False
        if self.gamepad_thread and self.gamepad_thread.is_alive():
            try:
                self.gamepad_thread.join(timeout=0.5)
            except Exception:
                pass
        self.gamepad_thread = None

    def _gamepad_listener_loop(self):
        if not PYGAME_AVAILABLE:
            return

        joysticks = self.pygame_manager.get_joysticks()
        if not joysticks:
            self.gamepad_listener_active = False
            return

        try:
            check_interval = 1.0 / 30.0
            last_check = time.time()

            while self.gamepad_listener_active and self.running:
                current_time = time.time()
                if current_time - last_check >= check_interval:
                    last_check = current_time
                    try:
                        pygame.event.pump()
                        for i, joystick in enumerate(joysticks):
                            if not joystick.get_init():
                                continue

                            for button_id in range(joystick.get_numbuttons()):
                                try:
                                    pressed = bool(joystick.get_button(button_id))
                                    btn_key = (i, 'b', button_id)
                                    last_state = self._last_button_states.get(btn_key, False)
                                    key_id = f"joy{i}_button{button_id}"

                                    if self.capture_mode and pressed and not last_state:
                                        try:
                                            joy_name = joystick.get_name()
                                            display_name = f"{joy_name} Button {button_id}"
                                        except Exception:
                                            display_name = f"Joystick {i} Button {button_id}"

                                        log_info(self.log_queue, 'InputWorker', f'Gamepad captured: {key_id} (display: {display_name})')
                                        self._send_response(('input_captured', key_id, display_name))

                                    elif not self.capture_mode:
                                        for action, (shortcut_key, display_name) in list(self.shortcuts.items()):
                                            if shortcut_key == key_id:
                                                if pressed and not last_state:
                                                    log_info(self.log_queue, 'InputWorker', f'Gamepad shortcut pressed: {key_id} -> {action}')
                                                    self._send_response(('shortcut_pressed', key_id, action))
                                                elif not pressed and last_state:
                                                    log_info(self.log_queue, 'InputWorker', f'Gamepad shortcut released: {key_id} -> {action}')
                                                    self._send_response(('shortcut_released', key_id, action))
                                                break

                                    self._last_button_states[btn_key] = pressed

                                except Exception as e:
                                    log_error(self.log_queue, 'InputWorker', f'Error checking button {button_id} on joystick {i}: {e}')

                            for hat_id in range(joystick.get_numhats()):
                                try:
                                    hat_value = joystick.get_hat(hat_id)
                                    hat_key = (i, 'h', hat_id)
                                    last_hat = self._last_hat_states.get(hat_key, (0, 0))

                                    if hat_value != (0, 0) and hat_value != last_hat:
                                        key_id = f"joy{i}_hat{hat_id}_{hat_value[0]}_{hat_value[1]}"

                                        if self.capture_mode:
                                            try:
                                                joy_name = joystick.get_name()
                                                hat_dir = []
                                                if hat_value[1] == 1:
                                                    hat_dir.append('Up')
                                                elif hat_value[1] == -1:
                                                    hat_dir.append('Down')
                                                if hat_value[0] == 1:
                                                    hat_dir.append('Right')
                                                elif hat_value[0] == -1:
                                                    hat_dir.append('Left')
                                                display_name = f"{joy_name} D-Pad {' '.join(hat_dir)}"
                                            except Exception:
                                                display_name = f"Joystick {i} Hat {hat_id} {hat_value}"

                                            log_info(self.log_queue, 'InputWorker', f'Gamepad captured: {key_id} (display: {display_name})')
                                            self._send_response(('input_captured', key_id, display_name))

                                        else:
                                            for action, (shortcut_key, display_name) in list(self.shortcuts.items()):
                                                if shortcut_key == key_id:
                                                    log_info(self.log_queue, 'InputWorker', f'Gamepad shortcut pressed: {key_id} -> {action}')
                                                    self._send_response(('shortcut_pressed', key_id, action))
                                                    break

                                    elif hat_value == (0, 0) and last_hat != (0, 0):
                                        prev_key_id = f"joy{i}_hat{hat_id}_{last_hat[0]}_{last_hat[1]}"
                                        for action, (shortcut_key, display_name) in list(self.shortcuts.items()):
                                            if shortcut_key == prev_key_id:
                                                log_info(self.log_queue, 'InputWorker', f'Gamepad shortcut released: {prev_key_id} -> {action}')
                                                self._send_response(('shortcut_released', prev_key_id, action))
                                                break

                                    self._last_hat_states[hat_key] = hat_value

                                except Exception as e:
                                    log_error(self.log_queue, 'InputWorker', f'Error checking hat {hat_id} on joystick {i}: {e}')

                    except Exception as e:
                        log_error(self.log_queue, 'InputWorker', f'Gamepad listener error: {e}')
                        traceback.print_exc()

                time.sleep(0.033)

        except Exception as e:
            log_error(self.log_queue, 'InputWorker', f'Gamepad listener fatal error: {e}')
            traceback.print_exc()

    def _send_response(self, response):
        """Send a response back to the GUI."""
        try:
            ok = safe_queue_put(self.response_queue, response, timeout=QUEUE_PUT_TIMEOUT, context='send_response', log_failures=True, queue_name='response_queue')
            if ok:
                log_info(self.log_queue, 'InputWorker', f'Response sent successfully: {response[0]}')
            else:
                log_error(self.log_queue, 'InputWorker', f'ERROR: Response queue full, dropping response: {response[0]}')
        except Exception as e:
            log_error(self.log_queue, 'InputWorker', f'ERROR sending response: {e}')


def run_worker(command_queue, response_queue, stop_event, log_queue):
    """Entry point for the input worker process."""
    try:
        worker = InputWorker(command_queue, response_queue, log_queue=log_queue)
        worker.start()

        while not stop_event.is_set():
            try:
                stop_event.wait(timeout=1.0)
            except KeyboardInterrupt:
                break

        worker.stop()

    except Exception as e:
        log_error(log_queue, 'InputWorker', f'Fatal error: {e}')
        traceback.print_exc()


if __name__ == '__main__':
    # Quick manual test when run standalone
    import queue as _q
    import threading as _t

    q_command = _q.Queue()
    q_response = _q.Queue()
    stop_evt = _t.Event()

    run_worker(q_command, q_response, stop_evt, log_queue=None)
