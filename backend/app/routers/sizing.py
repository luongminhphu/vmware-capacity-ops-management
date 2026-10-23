"""Host sizing API: calculate, import the Dev team's VM list, download a
template, and export the sizing result to Excel."""
import datetime as dt
import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import sizing
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/sizing", tags=["sizing"])

MAX_VMS = 5000
MAX_PROFILES = 8
MAX_UPLOAD = 5 * 1024 * 1024


class VmIn(BaseModel):
    name: str = ""
    qty: int = Field(default=1, ge=0, le=100000)
    vcpu: float = Field(default=0, ge=0, le=1024)
    ramGB: float = Field(default=0, ge=0, le=65536)
    diskGB: float = Field(default=0, ge=0)
    note: str = ""


class ProfileIn(BaseModel):
    name: str = ""
    sockets: int = Field(default=2, ge=1, le=8)
    coresPerSocket: int = Field(default=16, ge=1, le=256)
    ramGB: float = Field(default=512, ge=1, le=65536)
    diskGB: float = Field(default=0, ge=0)
    unitPrice: Optional[float] = Field(default=None, ge=0)


class ParamsIn(BaseModel):
    cpu_ratio: float = Field(default=3.0, gt=0, le=20)
    ht_factor: float = Field(default=2.0, ge=1, le=4)
    ram_ratio: float = Field(default=1.0, gt=0, le=4)
    max_util_pct: float = Field(default=80, ge=10, le=100)
    growth_pct: float = Field(default=20, ge=0, le=500)
    redundancy: int = Field(default=1, ge=0, le=4)
    min_hosts: int = Field(default=2, ge=1, le=64)
    reserve_cores: float = Field(default=2, ge=0, le=32)
    reserve_ram_gb: float = Field(default=16, ge=0, le=512)
    storage_mode: str = Field(default="san", pattern="^(san|local)$")
    storage_overhead_pct: float = Field(default=25, ge=0, le=300)
    storage_protection_factor: float = Field(default=1.0, ge=1, le=4)
    environment: str = Field(default="", max_length=120)


class SizingPayload(BaseModel):
    vms: List[VmIn]
    params: ParamsIn = ParamsIn()
    profiles: List[ProfileIn]


def _build(payload: SizingPayload) -> Dict[str, Any]:
    if len(payload.vms) > MAX_VMS:
        raise HTTPException(status_code=413, detail=f"Quá nhiều dòng VM ({len(payload.vms)}); giới hạn {MAX_VMS}.")
    if not payload.profiles:
        raise HTTPException(status_code=422, detail="Cần ít nhất một cấu hình host ứng viên.")
    if len(payload.profiles) > MAX_PROFILES:
        raise HTTPException(status_code=413, detail=f"Tối đa {MAX_PROFILES} cấu hình host ứng viên.")
    vms = [sizing.VmSpec(name=v.name or f"VM-{i+1:02d}", qty=v.qty, vcpu=v.vcpu, ram_gb=v.ramGB, disk_gb=v.diskGB, note=v.note)
           for i, v in enumerate(payload.vms)]
    profiles = [sizing.HostProfile(
        name=p.name or f"{p.sockets}×{p.coresPerSocket}C / {int(p.ramGB)} GB",
        sockets=p.sockets, cores_per_socket=p.coresPerSocket, ram_gb=p.ramGB, disk_gb=p.diskGB, unit_price=p.unitPrice,
    ) for p in payload.profiles]
    params = sizing.SizingParams(**payload.params.model_dump())
    return sizing.build_sizing(vms, params, profiles)


@router.get("/defaults")
def sizing_defaults(user: User = Depends(get_current_user)):
    return {"params": sizing.SizingParams().__dict__, "profiles": sizing.DEFAULT_PROFILES, "sampleVms": sizing.TEMPLATE_ROWS}


@router.post("/calc")
def sizing_calc(payload: SizingPayload, user: User = Depends(get_current_user)):
    return _build(payload)


@router.post("/import")
async def sizing_import(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File vượt quá 5 MB.")
    try:
        parsed = sizing.parse_vm_file(file.filename or "", content)
    except Exception as exc:  # noqa: BLE001 — surface a readable message to the UI
        raise HTTPException(status_code=400, detail=f"Không đọc được file: {exc}")
    return parsed


def _xlsx_response(wb, base: str) -> StreamingResponse:
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    filename = f"{base}_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _style_header(ws, row: int = 1):
    from openpyxl.styles import Alignment, Font, PatternFill
    for cell in ws[row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="074B9B")
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _autowidth(ws, min_w: int = 8, max_w: int = 60):
    from openpyxl.utils import get_column_letter
    for idx in range(1, ws.max_column + 1):
        longest = 0
        for r in range(1, ws.max_row + 1):
            v = ws.cell(row=r, column=idx).value
            longest = max(longest, len(str(v)) if v is not None else 0)
        ws.column_dimensions[get_column_letter(idx)].width = min(max_w, max(min_w, longest + 2))


@router.get("/template")
def sizing_template(user: User = Depends(get_current_user)):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "VM list"
    ws.append(["Tên VM", "Số lượng", "vCPU", "RAM (GB)", "Disk (GB)", "Ghi chú"])
    _style_header(ws)
    for r in sizing.TEMPLATE_ROWS:
        ws.append([r["name"], r["qty"], r["vcpu"], r["ramGB"], r["diskGB"], r["note"]])
    ws.freeze_panes = "A2"
    _autowidth(ws)
    guide = wb.create_sheet("Hướng dẫn")
    for line in [
        "Điền mỗi dòng một loại VM; cột 'Số lượng' cho phép khai nhiều VM cùng cấu hình.",
        "Đơn vị RAM và Disk là GB (có thể ghi '2 TB', '512 GB' — hệ thống tự quy đổi).",
        "Tên cột có thể viết tiếng Việt hoặc tiếng Anh (VM name / Qty / vCPU / RAM / Disk / Note).",
        "Lưu file .xlsx hoặc .csv rồi dùng nút 'Import Excel/CSV' trong mục Sizing máy chủ.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110
    return _xlsx_response(wb, "VCO_Sizing_mau_danh_sach_VM")


@router.post("/export")
def sizing_export(payload: SizingPayload, user: User = Depends(get_current_user)):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    result = _build(payload)
    demand = result["demand"]
    params = result["params"]
    wb = Workbook()

    # Sheet 1 — summary & recommendation
    ws = wb.active
    ws.title = "Tóm tắt"
    ws.append(["Sizing máy chủ vật lý — " + (params.get("environment") or "dự án mới")])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Ngày: {dt.date.today().strftime('%d/%m/%Y')}"])
    ws.append([])
    ws.append(["Đề xuất", result["recommendation"]["headline"]])
    ws.append(["Chi tiết", result["recommendation"]["detail"]])
    ws.append([])
    ws.append(["Nhu cầu từ team Dev", "Giá trị", "Sau dự phòng tăng trưởng"])
    _style_header(ws, ws.max_row)
    ws.append(["Số VM", demand["vmCount"], demand["vmCount"]])
    ws.append(["vCPU", demand["vcpu"], round(demand["vcpuPlanned"], 1)])
    ws.append(["RAM (GB)", demand["ramGB"], round(demand["ramPlannedGB"], 1)])
    ws.append(["Disk (GB)", demand["diskGB"], round(demand["diskPlannedGB"], 1)])
    ws.append(["Dung lượng lưu trữ cần cấp (GB, gồm overhead)", "", round(demand["storageNeededGB"], 1)])
    ws.append([])
    ws.append(["Giả định"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    for line in result["assumptions"]:
        ws.append([line])
    _autowidth(ws, max_w=90)

    # Sheet 2 — candidate comparison
    ws2 = wb.create_sheet("So sánh cấu hình")
    headers = ["Cấu hình host", "Sockets × cores", "RAM/host (GB)", "Số host theo CPU", "Số host theo RAM",
               "Dự phòng N+x", "Tổng số host", "Ràng buộc", "Tổng cores", "Tổng RAM (GB)",
               "Overcommit CPU sau", "Overcommit RAM sau", "CPU % khi mất host", "RAM % khi mất host",
               "Đạt N+x", "Thêm được ~VM", "Đơn giá", "Ngân sách ước tính", "Đề xuất"]
    ws2.append(headers)
    _style_header(ws2)
    for r in result["results"]:
        p = r["profile"]
        ws2.append([
            p["name"], f"{p['sockets']}×{p['coresPerSocket']}", p["ramGB"], r["hostsForCpu"], r["hostsForRam"],
            r["redundancy"], r["hostsTotal"], r["binding"], r["totals"]["cores"], r["totals"]["ramGB"],
            round(r["after"]["cpuOvercommit"], 2) if r["after"]["cpuOvercommit"] is not None else None,
            round(r["after"]["ramOvercommit"], 2) if r["after"]["ramOvercommit"] is not None else None,
            round(r["failure"]["cpuPct"], 1) if r["failure"]["cpuPct"] is not None else None,
            round(r["failure"]["ramPct"], 1) if r["failure"]["ramPct"] is not None else None,
            "Đạt" if r["failure"]["ok"] else "Chưa đạt",
            r["spare"]["extraAvgVms"], p["unitPrice"], r["budget"], "✓" if r["recommended"] else "",
        ])
        if r["recommended"]:
            for cell in ws2[ws2.max_row]:
                cell.fill = PatternFill("solid", fgColor="E6F4EA")
                cell.font = Font(bold=True)
    ws2.freeze_panes = "B2"
    _autowidth(ws2)

    # Sheet 3 — VM list as submitted
    ws3 = wb.create_sheet("Danh sách VM")
    ws3.append(["Tên VM", "Số lượng", "vCPU", "RAM (GB)", "Disk (GB)", "Tổng vCPU", "Tổng RAM (GB)", "Tổng Disk (GB)", "Ghi chú"])
    _style_header(ws3)
    for v in payload.vms:
        ws3.append([v.name, v.qty, v.vcpu, v.ramGB, v.diskGB, v.qty * v.vcpu, v.qty * v.ramGB, v.qty * v.diskGB, v.note])
    ws3.append(["TỔNG", demand["vmCount"], "", "", "", demand["vcpu"], demand["ramGB"], demand["diskGB"], ""])
    for cell in ws3[ws3.max_row]:
        cell.font = Font(bold=True)
    ws3.freeze_panes = "A2"
    _autowidth(ws3)

    base = "VCO_Sizing_" + "".join(ch for ch in (params.get("environment") or "may_chu") if ch.isalnum() or ch in "-_")[:40]
    return _xlsx_response(wb, base)
