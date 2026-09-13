"""Shared fixtures. Adds the project root to sys.path so the flat modules import."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.specs import SystemSpecs  # noqa: E402


@pytest.fixture
def modern_pc() -> SystemSpecs:
    return SystemSpecs(
        os_name="Windows 11", os_family="windows", os_version=11.0,
        arch_bits=64,
        cpu_name="AMD Ryzen 7 5800X 8-Core Processor",
        cpu_cores=8, cpu_threads=16, cpu_freq_ghz=3.8,
        ram_gb=32.0, gpus=["NVIDIA GeForce RTX 3070"],
        free_disk_gb=500.0, disk_label="D:\\")


@pytest.fixture
def old_pc() -> SystemSpecs:
    return SystemSpecs(
        os_name="Windows 7", os_family="windows", os_version=7.0,
        arch_bits=64,
        cpu_name="Intel(R) Core(TM)2 Duo CPU E8400 @ 3.00GHz",
        cpu_cores=2, cpu_threads=2, cpu_freq_ghz=3.0,
        ram_gb=4.0, gpus=["NVIDIA GeForce 9800 GT"],
        free_disk_gb=20.0, disk_label="C:\\")
