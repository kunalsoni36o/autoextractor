from __future__ import annotations

import subprocess
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ColmapCapabilities:
    executable: Path | None
    version: str | None
    commands: frozenset[str]
    has_cuda_build: bool
    error: str | None = None
    help_text: str = ""

    def supports_flag(self, flag: str) -> bool:
        return bool(re.search(rf"(?<![\w-]){re.escape(flag)}(?:\s|[=:]|$)", self.help_text))

    def supports_command(self, command: str) -> bool:
        return command in self.commands or command in self.help_text

    def supports_choice(self, flag: str, value: str) -> bool:
        """Check a flag and its advertised enum/value, independent of help formatting."""
        if not self.supports_flag(flag):
            return False
        return bool(re.search(rf"\{{[^}}]*\b{re.escape(value)}\b[^}}]*\}}", self.help_text, re.IGNORECASE)) or bool(re.search(rf"(?:^|\s){re.escape(value)}(?:\s|$|[,)])", self.help_text, re.IGNORECASE))


class ColmapAdapter:
    def __init__(self, executable: Path | None = None) -> None:
        self.executable = executable
        self._cached_key: tuple[str, int, int, str] | None = None
        self._cached_capabilities: ColmapCapabilities | None = None

    def discover(self, refresh: bool = False) -> ColmapCapabilities:
        candidates = [self.executable] if self.executable else []
        candidates += [Path("colmap"), Path(r"C:\Users\Parth\colmap\bin\colmap.exe")]
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                version = subprocess.run([str(candidate), "--version"], capture_output=True, text=True, timeout=10)
                if version.returncode != 0:
                    continue
                resolved = candidate.resolve()
                stat = resolved.stat() if resolved.is_file() else None
                help_result = subprocess.run([str(candidate), "help"], capture_output=True, text=True, timeout=10)
                output = help_result.stdout + help_result.stderr
                command_names = set(re.findall(r"\bcolmap\s+([a-z][a-z0-9_]*)\s+(?:-h|--help)", output, re.IGNORECASE))
                command_names.update(re.findall(r"^\s{0,2}([a-z][a-z0-9_]*)\s+-h\b", output, re.IGNORECASE | re.MULTILINE))
                commands = frozenset(command_names)
                help_parts = [output]
                for name in sorted(command_names):
                    try:
                        detail = subprocess.run([str(candidate), name, "-h"], capture_output=True, text=True, timeout=10)
                    except (OSError, subprocess.SubprocessError):
                        continue
                    help_parts.append(detail.stdout + detail.stderr)
                output = "\n".join(help_parts)
                version_text = version.stdout.strip().splitlines()[0]
                key = (str(resolved).lower(), stat.st_mtime_ns if stat else 0, stat.st_size if stat else 0, version_text)
                if not refresh and self._cached_key == key and self._cached_capabilities is not None:
                    return self._cached_capabilities
                capabilities = ColmapCapabilities(resolved, version_text, commands, "with CUDA" in version_text, None, output)
                self._cached_key = key; self._cached_capabilities = capabilities
                return capabilities
            except (OSError, subprocess.SubprocessError) as exc:
                last_error = str(exc)
        return ColmapCapabilities(None, None, frozenset(), False, locals().get("last_error", "COLMAP executable not found"))

    def refresh(self) -> ColmapCapabilities:
        return self.discover(refresh=True)

    def command(self, name: str, *args: str) -> list[str]:
        if not self.executable:
            raise RuntimeError("COLMAP is unavailable")
        return [str(self.executable), name, *args]
