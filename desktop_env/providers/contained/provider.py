import logging
import os
import platform
import time
import subprocess
import shlex
import psutil
import requests
from filelock import FileLock
from pathlib import Path

from desktop_env.providers.base import Provider

logger = logging.getLogger("desktopenv.providers.docker.ContainerdProvider")
logger.setLevel(logging.INFO)

WAIT_TIME = 3
RETRY_INTERVAL = 1
LOCK_TIMEOUT = 10


class PortAllocationError(Exception):
    pass


class ContainerdProvider(Provider):
    """
    使用 nerdctl 管理容器
    """

    def __init__(self, region: str, host_ip: str = None):
        self.server_port = None
        self.vnc_port = None
        self.chromium_port = None
        self.vlc_port = None

        # # nerdctl 容器标识（建议固定名，便于 stop/rm）
        # self.container_name = "osworld_vm"
        self.container_id = None

        # 与docker保持一致
        self.environment = {"DISK_SIZE": "32G", "RAM_SIZE": "4G", "CPU_CORES": "4"}

        temp_dir = Path(os.getenv('TEMP') if platform.system() == 'Windows' else '/tmp')
        self.lock_file = temp_dir / "docker_port_allocation.lck"
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.host_ip = host_ip

    # -----------------------------
    # Utils
    # -----------------------------
    def _run(self, cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
        """
        Run a nerdctl command.
        """
        logger.info("Running command: %s", " ".join(shlex.quote(x) for x in cmd))
        return subprocess.run(
            cmd,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )

    def _nerdctl_exists(self) -> None:
        try:
            self._run(["nerdctl", "version"], check=True, capture=True)
        except Exception as e:
            raise RuntimeError(
                "nerdctl not found or not working. Ensure nerdctl is installed and available in PATH."
            ) from e

    def _get_used_ports(self):
        """Get all currently used ports (system + containers via nerdctl port)."""
        # Get system ports
        system_ports = set(conn.laddr.port for conn in psutil.net_connections() if conn.laddr)

        # Get ports from nerdctl containers (best-effort)
        container_ports = set()
        try:
            # nerdctl ps -q -> list container IDs
            ps_out = self._run(["nerdctl", "ps", "-q"], check=True, capture=True).stdout.strip()
            if ps_out:
                for cid in ps_out.splitlines():
                    cid = cid.strip()
                    if not cid:
                        continue
                    # nerdctl port <cid> outputs like:
                    # 5000/tcp -> 0.0.0.0:5001
                    port_out = self._run(["nerdctl", "port", cid], check=False, capture=True).stdout or ""
                    for line in port_out.splitlines():
                        # parse last ":<hostport>"
                        if ":" in line:
                            host_port_str = line.rsplit(":", 1)[-1].strip()
                            if host_port_str.isdigit():
                                container_ports.add(int(host_port_str))
        except Exception:
            # 如果 nerdctl 不可用/权限不足，至少 system_ports 还在
            pass

        return system_ports | container_ports

    def _get_available_port(self, start_port: int) -> int:
        """Find next available port starting from start_port."""
        used_ports = self._get_used_ports()
        port = start_port
        while port < 65354:
            if port not in used_ports:
                return port
            port += 1
        raise PortAllocationError(f"No available ports found starting from {start_port}")

    def _wait_for_vm_ready(self, timeout: int = 300):
        """Wait for VM to be ready by checking screenshot endpoint."""
        start_time = time.time()

        def check_screenshot():
            try:
                response = requests.get(
                    f"http://{self.host_ip}:{self.server_port}/screenshot",
                    timeout=(10, 10)
                )
                return response.status_code == 200
            except Exception:
                return False

        while time.time() - start_time < timeout:
            if check_screenshot():
                return True
            logger.info("Checking if virtual machine is ready...")
            time.sleep(RETRY_INTERVAL)
        
        raise TimeoutError("VM failed to become ready within timeout period")

    # -----------------------------
    # Provider interface
    # -----------------------------
    def start_emulator(self, path_to_vm: str, headless: bool, os_type: str):
        """
        Start container using nerdctl:
          nerdctl run -d --rm --privileged --name osworld_vm \
            --cap-add=NET_ADMIN --device=/dev/kvm \
            -e DISK_SIZE=... -e RAM_SIZE=... -e CPU_CORES=... \
            -v <qcow2>:/storage/boot.qcow2:rw \
            -p <vnc_host>:8006 -p <server_host>:5000 -p <chrome_host>:9222 -p <vlc_host>:8080 \
            osworld-arm:latest
        """
        self._nerdctl_exists()

        lock = FileLock(str(self.lock_file), timeout=LOCK_TIMEOUT)

        try:
            with lock:
                # Allocate required ports
                self.vnc_port = self._get_available_port(8006)

                # 关键：server 容器内固定 5000，宿主侧你现在希望是 5001；
                # 这里用动态分配（优先从 5001 开始）来兼容你新命令风格
                self.server_port = self._get_available_port(5001)

                self.chromium_port = self._get_available_port(9222)

                # 关键：你新命令写的是 18080:8080，这里也从 18080 开始找
                self.vlc_port = self._get_available_port(18080)

                # Compose nerdctl run cmd
                cmd = [
                    "nerdctl", "run",
                    "-d",
                    "--privileged",
                    "--cap-add=NET_ADMIN",
                ]

                # KVM device
                if os.path.exists("/dev/kvm"):
                    cmd += ["--device=/dev/kvm"]
                    logger.info("KVM device found, using hardware acceleration")
                else:
                    # 无 kvm 时也能跑，只是慢
                    logger.warning("KVM device not found, running without hardware acceleration (will be slower)")

                # Env
                for k, v in self.environment.items():
                    cmd += ["-e", f"{k}={v}"]

                # Volume: qcow2 -> /storage/boot.qcow2:rw (与新 nerdctl 命令一致)
                qcow2_path = os.path.abspath(path_to_vm)
                cmd += ["-v", f"{qcow2_path}:/storage/boot.qcow2:rw"]

                # Ports: host:container
                cmd += [
                    "-p", f"{self.vnc_port}:8006",
                    "-p", f"{self.server_port}:5000",
                    "-p", f"{self.chromium_port}:9222",
                    "-p", f"{self.vlc_port}:8080",
                ]

                # Image
                cmd += ["osworld-arm:latest"]

                # Run
                out = self._run(cmd, check=True, capture=True)

                # nerdctl run -d 输出一般为 container id
                self.container_id = out.stdout.strip()


            logger.info(
                "Started container (id=%s) with ports - VNC: %s, Server: %s, Chrome: %s, VLC: %s",
                self.container_id,
                self.vnc_port, self.server_port, self.chromium_port, self.vlc_port
            )

            # Wait until VM is ready
            self._wait_for_vm_ready()

        except Exception as e:
            # Clean up if anything goes wrong
            try:
                self.stop_emulator(path_to_vm)
            except Exception as stop_err:
                logger.warning("Cleanup after start_emulator failure failed: %s", stop_err)
            raise e

    def get_ip_address(self, path_to_vm: str) -> str:
        if not all([self.server_port, self.chromium_port, self.vnc_port, self.vlc_port]):
            raise RuntimeError("VM not started - ports not allocated")
        # DesktopEnv 假定顺序：server, chromium, vnc, vlc
        return f"{self.host_ip}:{self.server_port}:{self.chromium_port}:{self.vnc_port}:{self.vlc_port}"

    def save_state(self, path_to_vm: str, snapshot_name: str):
        raise NotImplementedError("Snapshots not available for container provider")

    def revert_to_snapshot(self, path_to_vm: str, snapshot_name: str):
        # 容器模式下 revert 本质就是重启干净实例
        self.stop_emulator(path_to_vm)

    def stop_emulator(self, path_to_vm: str):
        """
        Stop and remove container.
        If started with --rm, stop should auto-remove, but we still rm -f best-effort.
        """
        
        if self.container_id:
            logger.info("Stopping VM (container %s)...", self.container_id)
            # stop
            self._run(["nerdctl", "stop", self.container_id], check=False, capture=True)
            # rm
            self._run(["nerdctl", "rm", "-f", self.container_id], check=False, capture=True)

            time.sleep(WAIT_TIME)

        # Reset state
        self.container_id = None
        self.server_port = None
        self.vnc_port = None
        self.chromium_port = None
        self.vlc_port = None