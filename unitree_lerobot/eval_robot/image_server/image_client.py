import cv2
import zmq
import numpy as np
import time
import struct
from collections import deque
from multiprocessing import shared_memory
from pathlib import Path
from threading import Event, Lock, Thread


class ImageClient:
    """ZMQ network image receiver (original, unchanged)."""

    def __init__(
        self,
        tv_img_shape=None,
        tv_img_shm_name=None,
        wrist_img_shape=None,
        wrist_img_shm_name=None,
        image_show=False,
        server_address="192.168.123.164",
        port=5555,
        Unit_Test=False,
    ):
        """
        tv_img_shape: User's expected head camera resolution shape (H, W, C). It should match the output of the image service terminal.
        tv_img_shm_name: Shared memory is used to easily transfer images across processes to the Vuer.
        wrist_img_shape: User's expected wrist camera resolution shape (H, W, C). It should maintain the same shape as tv_img_shape.
        wrist_img_shm_name: Shared memory is used to easily transfer images.
        image_show: Whether to display received images in real time.
        server_address: The ip address to execute the image server script.
        port: The port number to bind to. It should be the same as the image server.
        Unit_Test: When both server and client are True, it can be used to test the image transfer latency, \
                   network jitter, frame loss rate and other information.
        """
        self.running = True
        self._image_show = image_show
        self._server_address = server_address
        self._port = port

        self.tv_img_shape = tv_img_shape
        self.wrist_img_shape = wrist_img_shape

        self.tv_enable_shm = False
        if self.tv_img_shape is not None and tv_img_shm_name is not None:
            self.tv_image_shm = shared_memory.SharedMemory(name=tv_img_shm_name)
            self.tv_img_array = np.ndarray(tv_img_shape, dtype=np.uint8, buffer=self.tv_image_shm.buf)
            self.tv_enable_shm = True

        self.wrist_enable_shm = False
        if self.wrist_img_shape is not None and wrist_img_shm_name is not None:
            self.wrist_image_shm = shared_memory.SharedMemory(name=wrist_img_shm_name)
            self.wrist_img_array = np.ndarray(wrist_img_shape, dtype=np.uint8, buffer=self.wrist_image_shm.buf)
            self.wrist_enable_shm = True

        # Performance evaluation parameters
        self._enable_performance_eval = Unit_Test
        if self._enable_performance_eval:
            self._init_performance_metrics()

    def _init_performance_metrics(self):
        self._frame_count = 0  # Total frames received
        self._last_frame_id = -1  # Last received frame ID

        # Real-time FPS calculation using a time window
        self._time_window = 1.0  # Time window size (in seconds)
        self._frame_times = deque()  # Timestamps of frames received within the time window

        # Data transmission quality metrics
        self._latencies = deque()  # Latencies of frames within the time window
        self._lost_frames = 0  # Total lost frames
        self._total_frames = 0  # Expected total frames based on frame IDs

    def _update_performance_metrics(self, timestamp, frame_id, receive_time):
        # Update latency
        latency = receive_time - timestamp
        self._latencies.append(latency)

        # Remove latencies outside the time window
        while self._latencies and self._frame_times and self._latencies[0] < receive_time - self._time_window:
            self._latencies.popleft()

        # Update frame times
        self._frame_times.append(receive_time)
        # Remove timestamps outside the time window
        while self._frame_times and self._frame_times[0] < receive_time - self._time_window:
            self._frame_times.popleft()

        # Update frame counts for lost frame calculation
        expected_frame_id = self._last_frame_id + 1 if self._last_frame_id != -1 else frame_id
        if frame_id != expected_frame_id:
            lost = frame_id - expected_frame_id
            if lost < 0:
                print(f"[Image Client] Received out-of-order frame ID: {frame_id}")
            else:
                self._lost_frames += lost
                print(
                    f"[Image Client] Detected lost frames: {lost}, Expected frame ID: {expected_frame_id}, Received frame ID: {frame_id}"
                )
        self._last_frame_id = frame_id
        self._total_frames = frame_id + 1

        self._frame_count += 1

    def _print_performance_metrics(self, receive_time):
        if self._frame_count % 30 == 0:
            # Calculate real-time FPS
            real_time_fps = len(self._frame_times) / self._time_window if self._time_window > 0 else 0

            # Calculate latency metrics
            if self._latencies:
                avg_latency = sum(self._latencies) / len(self._latencies)
                max_latency = max(self._latencies)
                min_latency = min(self._latencies)
                jitter = max_latency - min_latency
            else:
                avg_latency = max_latency = min_latency = jitter = 0

            # Calculate lost frame rate
            lost_frame_rate = (self._lost_frames / self._total_frames) * 100 if self._total_frames > 0 else 0

            print(
                f"[Image Client] Real-time FPS: {real_time_fps:.2f}, Avg Latency: {avg_latency * 1000:.2f} ms, Max Latency: {max_latency * 1000:.2f} ms, \
                  Min Latency: {min_latency * 1000:.2f} ms, Jitter: {jitter * 1000:.2f} ms, Lost Frame Rate: {lost_frame_rate:.2f}%"
            )

    def _close(self):
        self._socket.close()
        self._context.term()
        if self._image_show:
            cv2.destroyAllWindows()
        print("Image client has been closed.")

    def receive_process(self):
        # Set up ZeroMQ context and socket
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.connect(f"tcp://{self._server_address}:{self._port}")
        self._socket.setsockopt_string(zmq.SUBSCRIBE, "")

        print("\nImage client has started, waiting to receive data...")
        try:
            while self.running:
                # Receive message
                message = self._socket.recv()
                receive_time = time.time()

                if self._enable_performance_eval:
                    header_size = struct.calcsize("dI")
                    try:
                        # Attempt to extract header and image data
                        header = message[:header_size]
                        jpg_bytes = message[header_size:]
                        timestamp, frame_id = struct.unpack("dI", header)
                    except struct.error as e:
                        print(f"[Image Client] Error unpacking header: {e}, discarding message.")
                        continue
                else:
                    # No header, entire message is image data
                    jpg_bytes = message
                # Decode image
                np_img = np.frombuffer(jpg_bytes, dtype=np.uint8)
                current_image = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
                current_image = current_image[:, :, ::-1]
                if current_image is None:
                    print("[Image Client] Failed to decode image.")
                    continue

                if self.tv_enable_shm:
                    np.copyto(self.tv_img_array, np.array(current_image[:, : self.tv_img_shape[1]]))

                if self.wrist_enable_shm:
                    np.copyto(self.wrist_img_array, np.array(current_image[:, -self.wrist_img_shape[1] :]))

                if self._image_show:
                    height, width = current_image.shape[:2]
                    resized_image = cv2.resize(current_image, (width // 2, height // 2))
                    cv2.imshow("Image Client Stream", resized_image)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        self.running = False

                if self._enable_performance_eval:
                    self._update_performance_metrics(timestamp, frame_id, receive_time)
                    self._print_performance_metrics(receive_time)

        except KeyboardInterrupt:
            print("Image client interrupted by user.")
        except Exception as e:
            print(f"[Image Client] An error occurred while receiving data: {e}")
        finally:
            self._close()


class LocalCamera:
    """Local USB camera with background frame capture.

    Usage:
        cam = LocalCamera("/dev/video0")
        cam.connect()
        frame = cam.read()    # RGB numpy array, non-blocking
        cam.disconnect()

    Batch creation:
        cameras = LocalCamera.from_config(["head:2", "wrist:0"])
    """

    def __init__(
        self,
        device: int | str = 0,
        fps: int = 30,
        width: int = 640,
        height: int = 480,
        fourcc: str | None = "MJPG",
        warmup_s: float = 1.0,
    ):
        if isinstance(device, Path):
            device = str(device)
        self._device = device
        self._fps = fps
        self._width = width
        self._height = height
        self._fourcc = fourcc
        self._warmup_s = warmup_s

        self._cap: cv2.VideoCapture | None = None
        self._thread: Thread | None = None
        self._stop_event: Event | None = None
        self._frame_lock: Lock = Lock()
        self._latest_frame: np.ndarray | None = None
        self._new_frame_event: Event = Event()

    def __str__(self) -> str:
        return f"LocalCamera({self._device})"

    @property
    def is_connected(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> int:
        return self._fps

    def connect(self) -> None:
        """Open the camera, apply settings, and warm up."""
        if self.is_connected:
            return

        self._cap = cv2.VideoCapture(self._device)
        if not self._cap.isOpened():
            self._cap.release()
            self._cap = None
            raise ConnectionError(f"Failed to open {self}")

        if self._fourcc is not None:
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self._fourcc))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        t0 = time.time()
        while time.time() - t0 < self._warmup_s:
            self._cap.read()
            time.sleep(0.05)

    def read(self, timeout_ms: float = 500) -> np.ndarray:
        """Return the latest RGB frame (non-blocking)."""
        if not self.is_connected:
            raise RuntimeError(f"{self} is not connected.")

        if self._thread is None or not self._thread.is_alive():
            self._start_thread()

        if not self._new_frame_event.wait(timeout=timeout_ms / 1000.0):
            raise TimeoutError(f"Timed out waiting for frame from {self}")

        with self._frame_lock:
            frame = self._latest_frame
            self._new_frame_event.clear()

        if frame is None:
            raise RuntimeError(f"No frame available from {self}")
        return frame

    # Alias for backward compatibility with teleop_runner
    async_read = read

    def disconnect(self) -> None:
        """Stop the background thread and release the camera."""
        self._stop_thread()
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @staticmethod
    def from_config(camera_strs: list[str] | None) -> dict[str, "LocalCamera"]:
        """Create cameras from ["name:device_id", ...] strings."""
        if not camera_strs:
            return {}
        cameras: dict[str, LocalCamera] = {}
        for cam_str in camera_strs:
            if ":" not in cam_str:
                continue
            name, dev = cam_str.split(":", 1)
            if dev.isdigit():
                device = f"/dev/video{dev}"
            else:
                device = dev
            cameras[name] = LocalCamera(device=device, fourcc="MJPG")
        return cameras

    # -- internals --

    def _start_thread(self) -> None:
        self._stop_thread()
        self._stop_event = Event()
        self._thread = Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _stop_thread(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._stop_event = None

    def _read_loop(self) -> None:
        while self._stop_event and not self._stop_event.is_set():
            if self._cap is None:
                break
            ret, frame = self._cap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._frame_lock:
                self._latest_frame = rgb
            self._new_frame_event.set()


Camera = LocalCamera



if __name__ == "__main__":
    # example1
    # tv_img_shape = (480, 1280, 3)
    # img_shm = shared_memory.SharedMemory(create=True, size=np.prod(tv_img_shape) * np.uint8().itemsize)
    # img_array = np.ndarray(tv_img_shape, dtype=np.uint8, buffer=img_shm.buf)
    # img_client = ImageClient(tv_img_shape = tv_img_shape, tv_img_shm_name = img_shm.name)
    # img_client.receive_process()

    # example2
    # Initialize the client with performance evaluation enabled
    # client = ImageClient(image_show = True, server_address='127.0.0.1', Unit_Test=True) # local test
    client = ImageClient(image_show=True, server_address="192.168.123.164", Unit_Test=False)  # deployment test
    client.receive_process()