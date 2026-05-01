import os
import subprocess  # nosec
import time
from glob import glob
from random import randrange
from shutil import which
from threading import Lock
from typing import List, Optional

from camoufox.exceptions import (
    CannotExecuteXvfb,
    CannotFindXvfb,
    VirtualDisplayError,
    VirtualDisplayNotSupported,
)
from camoufox.pkgman import OS_NAME

DISPLAY_LOCK = Lock()


class VirtualDisplay:
    """
    A minimal virtual display implementation for Linux.
    """

    def __init__(self, debug: Optional[bool] = False) -> None:
        """
        Constructor for the VirtualDisplay class (singleton object).
        """
        self.debug = debug
        self.proc: Optional[subprocess.Popen] = None
        self._display: Optional[int] = None
        self._lock = Lock()

    _MAX_DISPLAY_ATTEMPTS = 10
    _STARTUP_GRACE_PERIOD = 0.2
    _TERMINATE_TIMEOUT = 5

    xvfb_args = (
        # fmt: off
        "-screen", "0", "1x1x24",
        "-ac",
        "-nolisten", "tcp",
        "-extension", "RENDER",
        "+extension", "GLX",
        "-extension", "COMPOSITE",
        "-extension", "XVideo",
        "-extension", "XVideo-MotionCompensation",
        "-extension", "XINERAMA",
        "-shmem",
        "-fp", "built-ins",
        "-nocursor",
        "-br",
        # fmt: on
    )

    @property
    def xvfb_path(self) -> str:
        """
        Get the path to the xvfb executable
        """
        path = which("Xvfb")
        if not path:
            raise CannotFindXvfb("Please install Xvfb to use headless mode.")
        if not os.access(path, os.X_OK):
            raise CannotExecuteXvfb(f"I do not have permission to execute Xvfb: {path}")
        return path

    def xvfb_cmd(self, display: int) -> List[str]:
        """
        Get the xvfb command
        """
        return [self.xvfb_path, f':{display}', *self.xvfb_args]

    def execute_xvfb(self) -> None:
        """
        Spawn Xvfb and retry on display collisions.
        """
        last_error: Optional[CannotExecuteXvfb] = None
        for _ in range(self._MAX_DISPLAY_ATTEMPTS):
            display = self._free_display()
            cmd = self.xvfb_cmd(display)
            if self.debug:
                print('Starting virtual display:', ' '.join(cmd))

            proc = subprocess.Popen(  # nosec
                cmd,
                stdout=None if self.debug else subprocess.DEVNULL,
                stderr=None if self.debug else subprocess.DEVNULL,
            )
            time.sleep(self._STARTUP_GRACE_PERIOD)

            if proc.poll() is None:
                self.proc = proc
                self._display = display
                return

            last_error = CannotExecuteXvfb(
                f"Xvfb exited before becoming ready on display :{display}."
            )

        raise last_error or CannotExecuteXvfb("Failed to allocate a working Xvfb display.")

    def get(self) -> str:
        """
        Get the display number
        """
        self.assert_linux()

        with self._lock:
            if self.proc is None:
                with DISPLAY_LOCK:
                    self.execute_xvfb()
            elif self.debug:
                print(f'Using virtual display: {self.display}')
            return f':{self.display}'

    def kill(self):
        """
        Terminate the xvfb process
        """
        with self._lock:
            proc = self.proc
            display = self._display
            self.proc = None
            self._display = None

            if proc and proc.poll() is None:
                if self.debug:
                    print('Terminating virtual display:', display)
                proc.terminate()
                try:
                    proc.wait(timeout=self._TERMINATE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    if self.debug:
                        print('Killing hung virtual display:', display)
                    proc.kill()
                    proc.wait(timeout=self._TERMINATE_TIMEOUT)

    def __del__(self):
        """
        Kill and delete the VirtualDisplay object
        """
        try:
            self.kill()
        except Exception:
            pass

    @staticmethod
    def _get_lock_files() -> List[str]:
        """
        Get list of lock files in /tmp
        """
        tmpd = os.environ.get('TMPDIR', '/tmp')  # nosec
        try:
            lock_files = glob(os.path.join(tmpd, ".X*-lock"))
        except FileNotFoundError:
            return []
        return [p for p in lock_files if os.path.isfile(p)]

    @staticmethod
    def _free_display() -> int:
        """
        Search for free display
        """
        ls = list(
            map(lambda x: int(x.split("X")[1].split("-")[0]), VirtualDisplay._get_lock_files())
        )
        return max(99, max(ls) + randrange(3, 20)) if ls else 99  # nosec

    @property
    def display(self) -> int:
        """
        Get the display number
        """
        if self._display is None:
            raise VirtualDisplayError("Virtual display has not been started yet.")
        return self._display

    @staticmethod
    def assert_linux():
        """
        Assert that the current OS is Linux
        """
        if OS_NAME != 'lin':
            raise VirtualDisplayNotSupported("Virtual display is only supported on Linux.")
