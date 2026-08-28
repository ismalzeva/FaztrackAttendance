"""
HRD Import System — Bulk worker + schedule upload with AI-assisted shift rule parsing.

Flow:
1. HRD uploads CSV/Excel with employee data
2. Optionally describes shift rules in natural language (AI chat)
3. AI parses rules into structured ShiftRule
4. System generates WorkSchedule entries per worker
5. Preview → Confirm → Import
"""
import csv
import io
import json
import uuid
from datetime import date, datetime, time, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import RequestContext, tenant_context
from app.models import (
    Assignment,
    HRDImportBatch,
    HRDImportStatus,
    Project,
    ShiftRule,
    ShiftRuleStatus,
    Worker,
    WorkSchedule,
)

router = APIRouter(prefix="/api/v1/hrd", tags=["HRD Import"])

# ── Schemas ────────────────────────────────────────────────────────

class ShiftDefinition(BaseModel):
    name: str
    start_time: str
    end_time: str
    break_start: Optional[str] = None
    break_end: Optional[str] = None

class RotationRule(BaseModel):
    type: str
    params: dict

class ConstraintRule(BaseModel):
    type: str
    params: dict

class ShiftRuleset(BaseModel):
    shifts: list[ShiftDefinition]
    rotation: list[RotationRule]
    constraints: list[ConstraintRule]
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = None
    shift_rule_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    ruleset: Optional[ShiftRuleset] = None
    confidence: Optional[float] = None

class ColumnMapping(BaseModel):
    name: str
    maps_to: str

class ImportPreviewRequest(BaseModel):
    import_id: str
    column_mapping: list[ColumnMapping]
    shift_rule_id: Optional[str] = None

class ImportConfirmRequest(BaseModel):
    import_id: str
    project_id: str
    shift_rule_id: Optional[str] = None
    generate_schedule: bool = True
    schedule_days: int = 90

# ── AI Helper ──────────────────────────────────────────────────────

SHIFT_RULE_SYSTEM_PROMPT = """Kamu adalah asisten HRD untuk sistem absensi Faztrack. Tugasmu adalah mengubah deskripsi aturan shift kerja dari HRD menjadi format JSON terstruktur.

OUTPUT FORMAT (JSON only, no markdown):
{
  "shifts": [
    {"name": "Pagi", "start_time": "07:00", "end_time": "19:00", "break_start": "12:00", "break_end": "13:00"},
    {"name": "Malam", "start_time": "19:00", "end_time": "07:00", "break_start": "00:00", "break_end": "01:00"}
  ],
  "rotation": [
    {"type": "on_off_cycle", "params": {"on_weeks": 12, "off_weeks": 2}},
    {"type": "consecutive_days", "params": {"work_days": 12, "rest_days": 1}}
  ],
  "constraints": [
    {"type": "max_consecutive_same_shift", "params": {"max_days": 7}}
  ],
  "effective_from": null,
  "effective_to": null
}

RULES:
1. shifts: daftar shift dengan jam mulai, selesai, dan istirahat
2. rotation: pola rotasi kerja (on/off cycle untuk roster site, consecutive days untuk pola harian)
3. constraints: batasan (maks hari berturut-turut shift sama, min jam istirahat, dll)
4. Jika ada informasi yang tidak jelas, tetap buat best guess dan sertakan di reply
5. Selalu output JSON yang valid
"""

async def call_ai(prompt: str, system: str = SHIFT_RULE_SYSTEM_PROMPT) -> dict:
    settings = get_settings()
    api_key = settings.sumopod_api_key
    base_url = settings.sumopod_base_url
    model = settings.sumopod_model

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 2000,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())

# ── CSV Parser ─────────────────────────────────────────────────────

def parse_csv_content(content: bytes, filename: str) -> list[dict]:
    rows = []
    if filename.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            cleaned = {k.strip(): v.strip() if v else "" for k, v in row.items()}
            if any(cleaned.values()):
                rows.append(cleaned)
    elif filename.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
            ws = wb.active
            headers = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c).strip() if c else f"col_{j}" for j, c in enumerate(row)]
                else:
                    row_dict = {}
                    for j, cell in enumerate(row):
                        if j < len(headers):
                            row_dict[headers[j]] = str(cell).strip() if cell is not None else ""
                    if any(row_dict.values()):
                        rows.append(row_dict)
            wb.close()
        except ImportError:
            raise HTTPException(400, "Excel support requires openpyxl")
    return rows

# ── Column Auto-Detection ──────────────────────────────────────────

COLUMN_ALIASES = {
    "worker_name": ["nama", "name", "nama karyawan", "nama lengkap", "full name", "employee name"],
    "position": ["jabatan", "posisi", "position", "title", "role", "job title"],
    "phone": ["no hp", "no. hp", "nomor hp", "telepon", "phone", "wa", "whatsapp", "mobile", "no telp"],
    "shift": ["shift", "jadwal", "schedule", "sift", "jadwal shift"],
    "location": ["lokasi", "pos", "location", "site", "project", "lokasi kerja", "pos kerja"],
    "latitude": ["latitude", "lat", "lintang", "koordinat lat"],
    "longitude": ["longitude", "lng", "lon", "bujur", "koordinat lng"],
    "worker_code": ["kode", "code", "id", "nip", "nrp", "employee id", "nik"],
}

def auto_detect_columns(headers: list[str]) -> list[ColumnMapping]:
    mappings = []
    for header in headers:
        h_lower = header.lower().strip()
        mapped_to = None
        for target, aliases in COLUMN_ALIASES.items():
            if h_lower in aliases:
                mapped_to = target
                break
        mappings.append(ColumnMapping(name=header, maps_to=mapped_to or "ignore"))
    return mappings

# ── Schedule Generator ─────────────────────────────────────────────

def generate_schedules(
    workers: list[dict],
    ruleset: ShiftRuleset,
    project: Project,
    start_date: date,
    days: int,
) -> list[dict]:
    schedules = []
    shifts = ruleset.shifts
    if not shifts:
        return schedules

    on_weeks = 12
    off_weeks = 2
    work_days = 12
    rest_days = 1
    max_consecutive = 7

    for rule in ruleset.rotation:
        if rule.type == "on_off_cycle":
            on_weeks = rule.params.get("on_weeks", 12)
            off_weeks = rule.params.get("off_weeks", 2)
        elif rule.type == "consecutive_days":
            work_days = rule.params.get("work_days", 12)
            rest_days = rule.params.get("rest_days", 1)

    for constraint in ruleset.constraints:
        if constraint.type == "max_consecutive_same_shift":
            max_consecutive = constraint.params.get("max_days", 7)

    cycle_days = work_days + rest_days

    for worker in workers:
        worker_name = worker.get("worker_name", "")
        shift_name = worker.get("shift", "").strip()

        assigned_shift = None
        if shift_name:
            for s in shifts:
                if s.name.lower() == shift_name.lower() or shift_name.lower() in s.name.lower():
                    assigned_shift = s
                    break
        if not assigned_shift and shifts:
            assigned_shift = shifts[0]
        if not assigned_shift:
            continue

        consecutive_same = 0
        current_shift = assigned_shift

        for day_offset in range(days):
            current_date = start_date + timedelta(days=day_offset)
            total_cycle = (on_weeks + off_weeks) * 7
            day_in_cycle = day_offset % total_cycle
            is_off_site = day_in_cycle >= on_weeks * 7

            if is_off_site:
                continue

            day_in_work_cycle = day_offset % cycle_days
            is_rest_day = day_in_work_cycle >= work_days

            if is_rest_day:
                schedules.append({
                    "worker_name": worker_name,
                    "work_date": current_date.isoformat(),
                    "start_time": None,
                    "end_time": None,
                    "is_working_day": False,
                    "shift_name": current_shift.name,
                })
                consecutive_same = 0
                continue

            if consecutive_same >= max_consecutive and len(shifts) > 1:
                idx = shifts.index(current_shift)
                current_shift = shifts[(idx + 1) % len(shifts)]
                consecutive_same = 0

            schedules.append({
                "worker_name": worker_name,
                "work_date": current_date.isoformat(),
                "start_time": current_shift.start_time,
                "end_time": current_shift.end_time,
                "is_working_day": True,
                "shift_name": current_shift.name,
                "break_start": current_shift.break_start,
                "break_end": current_shift.break_end,
            })
            consecutive_same += 1

    return schedules

# ── Endpoints ──────────────────────────────────────────────────────

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "No file provided")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 10MB)")

    rows = parse_csv_content(content, file.filename)
    if not rows:
        raise HTTPException(400, "No data rows found in file")

    headers = list(rows[0].keys())
    auto_mapping = auto_detect_columns(headers)

    import_batch = HRDImportBatch(
        id=str(uuid.uuid4()),
        tenant_id=ctx.membership.tenant_id,
        filename=file.filename,
        status=HRDImportStatus.UPLOADED,
        total_rows=len(rows),
        preview_json=json.dumps(rows[:50]),
        mapping_json=json.dumps([m.model_dump() for m in auto_mapping]),
        created_by=ctx.user.id,
    )
    db.add(import_batch)
    db.commit()
    db.refresh(import_batch)

    return {
        "import_id": import_batch.id,
        "filename": file.filename,
        "total_rows": len(rows),
        "headers": headers,
        "auto_mapping": [m.model_dump() for m in auto_mapping],
        "preview": rows[:20],
    }

@router.get("/imports/{import_id}")
async def get_import(
    import_id: str,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(HRDImportBatch).where(
            HRDImportBatch.id == import_id,
            HRDImportBatch.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not batch:
        raise HTTPException(404, "Import batch not found")
    return {
        "import_id": batch.id,
        "filename": batch.filename,
        "status": batch.status.value,
        "total_rows": batch.total_rows,
        "valid_rows": batch.valid_rows,
        "error_rows": batch.error_rows,
        "imported_rows": batch.imported_rows,
        "preview": json.loads(batch.preview_json) if batch.preview_json else None,
        "mapping": json.loads(batch.mapping_json) if batch.mapping_json else None,
        "errors": json.loads(batch.error_json) if batch.error_json else None,
        "shift_rule_id": batch.shift_rule_id,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }

@router.post("/preview")
async def preview_import(
    req: ImportPreviewRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(HRDImportBatch).where(
            HRDImportBatch.id == req.import_id,
            HRDImportBatch.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not batch:
        raise HTTPException(404, "Import batch not found")

    rows = json.loads(batch.preview_json) if batch.preview_json else []
    mapping = {m["name"]: m["maps_to"] for m in [cm.model_dump() for cm in req.column_mapping]}

    valid_rows = []
    errors = []
    for i, row in enumerate(rows):
        mapped = {}
        row_errors = []
        for col, value in row.items():
            target = mapping.get(col, "ignore")
            if target != "ignore":
                mapped[target] = value
        if not mapped.get("worker_name"):
            row_errors.append(f"Row {i+1}: Missing worker name")
        if not mapped.get("phone") and not mapped.get("worker_code"):
            row_errors.append(f"Row {i+1}: Missing phone or worker code")
        if row_errors:
            errors.extend(row_errors)
        else:
            valid_rows.append(mapped)

    batch.mapping_json = json.dumps([cm.model_dump() for cm in req.column_mapping])
    batch.valid_rows = len(valid_rows)
    batch.error_rows = len(errors)
    batch.status = HRDImportStatus.PREVIEWING
    batch.error_json = json.dumps(errors) if errors else None
    db.commit()

    return {
        "import_id": batch.id,
        "valid_rows": len(valid_rows),
        "errors": errors,
        "preview": valid_rows[:20],
    }

@router.post("/chat")
async def ai_chat(
    req: ChatRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    prompt = req.message
    if req.context:
        prompt = f"Konteks percakapan sebelumnya:\n{req.context}\n\nPesan baru: {req.message}"

    try:
        ruleset_json = await call_ai(prompt)
        ruleset = ShiftRuleset(**ruleset_json)

        if req.shift_rule_id:
            rule = db.scalar(
                select(ShiftRule).where(
                    ShiftRule.id == req.shift_rule_id,
                    ShiftRule.tenant_id == ctx.membership.tenant_id,
                )
            )
            if rule:
                rule.rules_json = json.dumps(ruleset_json)
                rule.natural_language_input = req.message
                rule.ai_model = get_settings().sumopod_model
                rule.ai_confidence = 0.85
                db.commit()
        else:
            rule = ShiftRule(
                id=str(uuid.uuid4()),
                tenant_id=ctx.membership.tenant_id,
                name=f"Shift Rule {datetime.now().strftime('%Y%m%d_%H%M')}",
                description=req.message[:500],
                rules_json=json.dumps(ruleset_json),
                natural_language_input=req.message,
                ai_model=get_settings().sumopod_model,
                ai_confidence=0.85,
                status=ShiftRuleStatus.DRAFT,
            )
            db.add(rule)
            db.commit()
            db.refresh(rule)

        return ChatResponse(
            reply=f"Saya sudah memahami aturan shift Anda. Berikut hasil parsing:\n\n"
                  f"**Shift:** {', '.join(s.name + ' (' + s.start_time + '-' + s.end_time + ')' for s in ruleset.shifts)}\n"
                  f"**Rotasi:** {', '.join(r.type + ': ' + json.dumps(r.params) for r in ruleset.rotation)}\n"
                  f"**Batasan:** {', '.join(c.type + ': ' + json.dumps(c.params) for c in ruleset.constraints)}\n\n"
                  f"Apakah sudah sesuai? Jika ada yang perlu diubah, silakan jelaskan.",
            ruleset=ruleset,
            confidence=0.85,
        )
    except json.JSONDecodeError:
        return ChatResponse(
            reply="Maaf, saya tidak bisa memparse aturan shift dari deskripsi Anda. "
                  "Bisa tolong jelaskan lebih detail? Contoh: 'Shift pagi jam 07.00-19.00, shift malam jam 19.00-07.00. "
                  "Karyawan 12 hari kerja, 1 hari libur. Maksimal 7 hari berturut-turut shift sama.'",
            ruleset=None,
            confidence=0.0,
        )
    except Exception as e:
        raise HTTPException(500, f"AI error: {str(e)}")

@router.get("/shift-rules")
async def list_shift_rules(
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    rules = db.scalars(
        select(ShiftRule)
        .where(ShiftRule.tenant_id == ctx.membership.tenant_id)
        .order_by(ShiftRule.created_at.desc())
    ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "status": r.status.value,
            "rules": json.loads(r.rules_json),
            "natural_language_input": r.natural_language_input,
            "ai_confidence": r.ai_confidence,
            "created_at": r.created_at.isoformat(),
        }
        for r in rules
    ]

@router.get("/shift-rules/{rule_id}")
async def get_shift_rule(
    rule_id: str,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    rule = db.scalar(
        select(ShiftRule).where(
            ShiftRule.id == rule_id,
            ShiftRule.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not rule:
        raise HTTPException(404, "Shift rule not found")
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "status": rule.status.value,
        "rules": json.loads(rule.rules_json),
        "natural_language_input": rule.natural_language_input,
        "ai_model": rule.ai_model,
        "ai_confidence": rule.ai_confidence,
        "created_at": rule.created_at.isoformat(),
    }

@router.put("/shift-rules/{rule_id}")
async def update_shift_rule(
    rule_id: str,
    body: dict,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    rule = db.scalar(
        select(ShiftRule).where(
            ShiftRule.id == rule_id,
            ShiftRule.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not rule:
        raise HTTPException(404, "Shift rule not found")
    if "name" in body:
        rule.name = body["name"]
    if "status" in body:
        rule.status = ShiftRuleStatus(body["status"])
    if "rules" in body:
        rule.rules_json = json.dumps(body["rules"])
    db.commit()
    return {"ok": True}

@router.post("/generate-schedule")
async def generate_schedule(
    req: ImportConfirmRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(HRDImportBatch).where(
            HRDImportBatch.id == req.import_id,
            HRDImportBatch.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not batch:
        raise HTTPException(404, "Import batch not found")
    if not req.shift_rule_id:
        raise HTTPException(400, "shift_rule_id required")

    rule = db.scalar(
        select(ShiftRule).where(
            ShiftRule.id == req.shift_rule_id,
            ShiftRule.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not rule:
        raise HTTPException(404, "Shift rule not found")

    project = db.scalar(
        select(Project).where(
            Project.id == req.project_id,
            Project.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not project:
        raise HTTPException(404, "Project not found")

    ruleset = ShiftRuleset(**json.loads(rule.rules_json))
    rows = json.loads(batch.preview_json) if batch.preview_json else []
    mapping_data = json.loads(batch.mapping_json) if batch.mapping_json else []
    mapping = {m["name"]: m["maps_to"] for m in mapping_data}

    workers = []
    for row in rows:
        mapped = {}
        for col, value in row.items():
            target = mapping.get(col, "ignore")
            if target != "ignore":
                mapped[target] = value
        if mapped.get("worker_name"):
            workers.append(mapped)

    start_date = date.today()
    if ruleset.effective_from:
        try:
            start_date = date.fromisoformat(ruleset.effective_from)
        except ValueError:
            pass

    schedule_entries = generate_schedules(workers, ruleset, project, start_date, req.schedule_days)

    return {
        "import_id": batch.id,
        "project_id": req.project_id,
        "shift_rule_id": req.shift_rule_id,
        "workers_count": len(workers),
        "schedule_days": req.schedule_days,
        "total_entries": len(schedule_entries),
        "schedule_preview": schedule_entries[:50],
        "schedule_full": schedule_entries,
    }

@router.post("/confirm-import")
async def confirm_import(
    req: ImportConfirmRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(HRDImportBatch).where(
            HRDImportBatch.id == req.import_id,
            HRDImportBatch.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not batch:
        raise HTTPException(404, "Import batch not found")
    if batch.status not in (HRDImportStatus.PREVIEWING, HRDImportStatus.UPLOADED):
        raise HTTPException(400, f"Import batch status is {batch.status.value}, expected PREVIEWING")

    ruleset = None
    if req.shift_rule_id:
        rule = db.scalar(
            select(ShiftRule).where(
                ShiftRule.id == req.shift_rule_id,
                ShiftRule.tenant_id == ctx.membership.tenant_id,
            )
        )
        if rule:
            ruleset = ShiftRuleset(**json.loads(rule.rules_json))

    project = db.scalar(
        select(Project).where(
            Project.id == req.project_id,
            Project.tenant_id == ctx.membership.tenant_id,
        )
    )
    if not project:
        raise HTTPException(404, "Project not found")

    rows = json.loads(batch.preview_json) if batch.preview_json else []
    mapping_data = json.loads(batch.mapping_json) if batch.mapping_json else []
    mapping = {m["name"]: m["maps_to"] for m in mapping_data}

    batch.status = HRDImportStatus.IMPORTING
    imported = 0
    errors = []

    for i, row in enumerate(rows):
        try:
            mapped = {}
            for col, value in row.items():
                target = mapping.get(col, "ignore")
                if target != "ignore":
                    mapped[target] = value
            if not mapped.get("worker_name"):
                errors.append(f"Row {i+1}: Missing worker name")
                continue

            worker_code = mapped.get("worker_code", f"IMP-{imported+1:04d}")
            worker = db.scalar(
                select(Worker).where(
                    Worker.tenant_id == ctx.membership.tenant_id,
                    Worker.code == worker_code,
                )
            )
            if not worker:
                worker = Worker(
                    id=str(uuid.uuid4()),
                    tenant_id=ctx.membership.tenant_id,
                    code=worker_code,
                    name=mapped["worker_name"],
                    phone=mapped.get("phone"),
                    is_active=True,
                )
                db.add(worker)
                db.flush()

            assignment = Assignment(
                id=str(uuid.uuid4()),
                tenant_id=ctx.membership.tenant_id,
                worker_id=worker.id,
                project_id=req.project_id,
                work_date=date.today(),
            )
            db.add(assignment)

            if ruleset and req.generate_schedule:
                schedule_entries = generate_schedules(
                    [mapped], ruleset, project, date.today(), req.schedule_days
                )
                for entry in schedule_entries:
                    ws = WorkSchedule(
                        id=str(uuid.uuid4()),
                        tenant_id=ctx.membership.tenant_id,
                        worker_id=worker.id,
                        work_date=date.fromisoformat(entry["work_date"]),
                        start_time=time.fromisoformat(entry["start_time"]) if entry.get("start_time") else time(8, 0),
                        end_time=time.fromisoformat(entry["end_time"]) if entry.get("end_time") else time(17, 0),
                        is_working_day=entry.get("is_working_day", True),
                    )
                    db.add(ws)

            imported += 1
        except Exception as e:
            errors.append(f"Row {i+1}: {str(e)}")

    batch.status = HRDImportStatus.COMPLETED
    batch.imported_rows = imported
    batch.error_rows = len(errors)
    batch.error_json = json.dumps(errors) if errors else None
    batch.completed_at = datetime.now()
    batch.shift_rule_id = req.shift_rule_id
    db.commit()

    return {
        "import_id": batch.id,
        "status": "COMPLETED",
        "imported": imported,
        "errors": errors,
    }
