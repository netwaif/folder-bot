"""테스트 안전장치 — launchctl·systemctl·loginctl 실호출 차단.

하네스 6차(2026-09-07) 때 리눅스 분기 테스트가 맥의 실제 LaunchAgent를 내린 사고 재발 방지:
PATH 맨 앞에 가짜 바이너리를 두어 어떤 테스트든 실호출하면 표식 파일이 남고 즉시 실패(exit 99)한다.
botctl의 정상 테스트 경로는 HARNESS_FAKE_SYSTEMCTL=1(무접촉)이라 표식이 남지 않아야 한다.
"""
import os
import pytest


@pytest.fixture(autouse=True)
def block_service_managers(tmp_path_factory, monkeypatch):
    shim = tmp_path_factory.mktemp("shim")
    marker = shim / "CALLED"
    for name in ("launchctl", "systemctl", "loginctl"):
        p = shim / name
        p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{marker}"\necho "BLOCKED: {name} 실호출" >&2\nexit 99\n')
        p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HARNESS_FAKE_SYSTEMCTL", "1")
    yield
    assert not marker.exists(), f"서비스 관리자 실호출 감지:\n{marker.read_text()}"
