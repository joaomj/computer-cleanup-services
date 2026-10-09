"""Drive CLI prompts through a pseudo-terminal with bounded waits."""

from __future__ import annotations

import errno
import os
import pty
import select
import subprocess
import termios
import time


class Terminal:
    def __init__(self, command: list[str], environment: dict[str, str], cwd: str):
        self.master, slave = pty.openpty()
        attributes = termios.tcgetattr(slave)
        attributes[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        try:
            self.process = subprocess.Popen(
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=environment,
                cwd=cwd,
                start_new_session=True,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self.output = bytearray()

    def read_until(self, marker: bytes | None = None, timeout: float = 20) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker is not None and marker in self.output:
                return self.output.decode(errors="replace")
            ready, _, _ = select.select(
                [self.master], [], [], max(0, deadline - time.monotonic())
            )
            if not ready:
                break
            try:
                chunk = os.read(self.master, 65536)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                chunk = b""
            if not chunk:
                if marker is not None:
                    raise AssertionError(
                        f"Prompt not reached: {self.output.decode(errors='replace')}"
                    )
                return self.output.decode(errors="replace")
            self.output.extend(chunk)
        raise TimeoutError(
            f"CLI did not finish: {self.output.decode(errors='replace')}"
        )

    def answer(self, value: bytes) -> tuple[int, str]:
        os.write(self.master, value)
        output = self.read_until()
        return self.process.wait(timeout=5), output

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        os.close(self.master)
