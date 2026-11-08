"""Run install.sh inside Alpine on a real pseudo-terminal and answer the password prompt."""
import os
import pty
import select
import sys
import time

bundle, logfile = sys.argv[1], sys.argv[2]
argv = [
    "docker", "run", "--rm", "-it", "--network", "host",
    "-v", "/var/run/docker.sock:/var/run/docker.sock",
    "-v", f"{bundle}:{bundle}", "-w", bundle,
    "docker:cli", "sh", "./install.sh",
]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(argv[0], argv)

out = b""
sent = False
end = time.time() + 300
while time.time() < end:
    ready, _, _ = select.select([fd], [], [], 1)
    if ready:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if not sent and b"generate a random one" in out:
            time.sleep(0.5)
            os.write(fd, b"\n")
            sent = True
_, status = os.waitpid(pid, 0)
with open(logfile, "wb") as f:
    f.write(out)
print("exit", os.waitstatus_to_exitcode(status), "| prompt answered:", sent)
