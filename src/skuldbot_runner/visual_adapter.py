# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Real desktop automation adapter for graphical runner actions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import VisualActionKind


class VisualAdapterError(RuntimeError):
    """Raised when the desktop adapter cannot execute a visual action."""


@dataclass(frozen=True)
class VisualArtifact:
    """Evidence metadata for artifacts created by visual actions."""

    path: str
    size_bytes: int
    checksum_sha256: str

    @classmethod
    def from_file(cls, path: Path) -> "VisualArtifact":
        if not path.is_file():
            raise VisualAdapterError(f"Visual artifact was not created: {path}")

        digest = hashlib.sha256()
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)

        return cls(
            path=str(path),
            size_bytes=path.stat().st_size,
            checksum_sha256=digest.hexdigest(),
        )


class RpaDesktopVisualAdapter:
    """Executes visual actions against the current graphical desktop."""

    def __init__(self) -> None:
        try:
            from RPA.Desktop import Desktop
        except ImportError as exc:
            raise VisualAdapterError(
                "RPA.Desktop is required for graphical visual actions."
            ) from exc

        self._desktop = Desktop()

    def screenshot(self, output_path: Path) -> VisualArtifact:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._call(
            VisualActionKind.SCREENSHOT,
            ("take_screenshot", "screenshot"),
            str(output_path),
        )
        return VisualArtifact.from_file(output_path)

    def type_text(self, text: str) -> None:
        self._call(VisualActionKind.TYPE_TEXT, ("type_text", "typewrite"), text)

    def hotkey(self, keys: tuple[str, ...]) -> None:
        if not keys:
            raise VisualAdapterError("At least one key is required.")
        self._call(VisualActionKind.HOTKEY, ("press_keys", "hotkey"), *keys)

    def image_click(self, image_path: Path) -> None:
        self._call(
            VisualActionKind.IMAGE_CLICK,
            ("click",),
            self._image_locator(image_path),
        )

    def wait_image(self, image_path: Path, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise VisualAdapterError("Timeout must be greater than zero seconds.")
        self._call(
            VisualActionKind.WAIT_IMAGE,
            ("wait_for_element",),
            self._image_locator(image_path),
            timeout=timeout_seconds,
        )

    def _call(
        self,
        action: VisualActionKind,
        method_names: tuple[str, ...],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        method = self._resolve_method(action, method_names)
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            raise VisualAdapterError(
                f"Visual action {action.value} failed: {exc}"
            ) from exc

    def _resolve_method(
        self,
        action: VisualActionKind,
        method_names: tuple[str, ...],
    ) -> Any:
        for method_name in method_names:
            method = getattr(self._desktop, method_name, None)
            if callable(method):
                return method
        raise VisualAdapterError(
            f"RPA.Desktop does not expose a method for {action.value}."
        )

    @staticmethod
    def _image_locator(image_path: Path) -> str:
        return f"image:{image_path.expanduser().resolve()}"
