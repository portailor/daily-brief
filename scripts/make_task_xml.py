"""Windows 작업 스케줄러 등록용 XML 생성.

schtasks 는 UTF-16LE(BOM 포함) XML 을 기대한다.
경로는 하드코딩하지 않고 이 파일 위치에서 역산한다.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "scripts" / "daily_brief.bat"

TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>매일 오전 7시 경제 브리핑을 생성해 카카오톡으로 보냅니다.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT10M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def build(start: str = "2026-09-12T07:00:00") -> Path:
    xml = TEMPLATE.format(start=start, command=str(BAT), workdir=str(ROOT))
    out = ROOT / "scripts" / "DailyEconomicBrief.xml"
    out.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))
    return out


if __name__ == "__main__":
    p = build()
    print(f"생성: {p} ({p.stat().st_size:,} bytes)")
    print(f"  실행 파일: {BAT}")
    print(f"  작업 폴더: {ROOT}")
