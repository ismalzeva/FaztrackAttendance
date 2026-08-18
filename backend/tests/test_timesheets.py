import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import (
    Assignment,
    AttendanceChallenge,
    AttendanceEvent,
    AttendanceStatus,
    AttendanceType,
    DeviceBinding,
    DeviceEnrollment,
    DeviceStatus,
    EnrollmentStatus,
    Project,
    TimesheetPeriod,
    WorkSchedule,
    Worker,
)


def admin_headers(client, tenant):
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": "ahmad", "password": "secret123"},
    )
    return {
        "Authorization": f"Bearer {response.json()['data']['access_token']}",
        "X-Tenant-ID": tenant.id,
    }


def add_worker_day(db, tenant, project, code, name, work_date):
    worker = Worker(tenant_id=tenant.id, code=code, name=name)
    db.add(worker)
    db.flush()
    db.add_all(
        [
            Assignment(
                tenant_id=tenant.id,
                worker_id=worker.id,
                project_id=project.id,
                work_date=work_date,
            ),
            WorkSchedule(
                tenant_id=tenant.id,
                worker_id=worker.id,
                work_date=work_date,
                start_time=time(8),
                end_time=time(17),
                is_working_day=True,
            ),
        ]
    )
    return worker


def add_event(db, tenant, project, worker, event_type, status, server_time):
    enrollment = DeviceEnrollment(
        tenant_id=tenant.id,
        worker_id=worker.id,
        public_key_jwk="{}",
        public_key_thumbprint=f"thumb-{worker.code}",
        device_label="Android lapangan",
        status=EnrollmentStatus.APPROVED,
    )
    db.add(enrollment)
    db.flush()
    binding = DeviceBinding(
        tenant_id=tenant.id,
        worker_id=worker.id,
        enrollment_id=enrollment.id,
        public_key_jwk="{}",
        public_key_thumbprint=f"thumb-{worker.code}",
        device_label="Android lapangan",
        status=DeviceStatus.ACTIVE,
    )
    db.add(binding)
    db.flush()
    challenge = AttendanceChallenge(
        tenant_id=tenant.id,
        worker_id=worker.id,
        project_id=project.id,
        event_type=event_type,
        challenge_hash="a" * 64,
        work_date=server_time.astimezone(ZoneInfo(tenant.timezone)).date(),
        expires_at=server_time + timedelta(minutes=2),
        used_at=server_time,
    )
    db.add(challenge)
    db.flush()
    db.add(
        AttendanceEvent(
            tenant_id=tenant.id,
            worker_id=worker.id,
            project_id=project.id,
            device_binding_id=binding.id,
            challenge_id=challenge.id,
            event_type=event_type,
            status=status,
            work_date=challenge.work_date,
            server_time=server_time,
            captured_at_client=server_time,
            latitude=project.latitude,
            longitude=project.longitude,
            accuracy_m=10,
            distance_m=0,
            signature="test-signature",
        )
    )
    return binding


def add_second_event(db, tenant, project, worker, binding, event_type, status, server_time):
    challenge = AttendanceChallenge(
        tenant_id=tenant.id,
        worker_id=worker.id,
        project_id=project.id,
        event_type=event_type,
        challenge_hash="b" * 64,
        work_date=server_time.astimezone(ZoneInfo(tenant.timezone)).date(),
        expires_at=server_time + timedelta(minutes=2),
        used_at=server_time,
    )
    db.add(challenge)
    db.flush()
    db.add(
        AttendanceEvent(
            tenant_id=tenant.id,
            worker_id=worker.id,
            project_id=project.id,
            device_binding_id=binding.id,
            challenge_id=challenge.id,
            event_type=event_type,
            status=status,
            work_date=challenge.work_date,
            server_time=server_time,
            captured_at_client=server_time,
            latitude=project.latitude,
            longitude=project.longitude,
            accuracy_m=10,
            distance_m=0,
            signature="test-signature",
        )
    )


def setup_report(db, seeded, incomplete=False):
    tenant, _, _, _ = seeded
    zone = ZoneInfo(tenant.timezone)
    work_date = datetime.now(zone).date() - timedelta(days=2)
    project = Project(
        tenant_id=tenant.id,
        code="PRJ-M4",
        name="Lumin Park Residence",
        latitude=-6.2,
        longitude=106.8,
        geofence_radius_m=150,
        work_start=time(8),
        work_end=time(17),
    )
    db.add(project)
    db.flush()
    present = add_worker_day(db, tenant, project, "EMP-001", "Budi Santoso", work_date)
    absent = add_worker_day(db, tenant, project, "EMP-002", "Dedi Irawan", work_date)
    checkin = datetime.combine(work_date, time(8, 15), tzinfo=zone).astimezone(timezone.utc)
    binding = add_event(db, tenant, project, present, AttendanceType.CHECK_IN, AttendanceStatus.VALID, checkin)
    if not incomplete:
        checkout = datetime.combine(work_date, time(16, 30), tzinfo=zone).astimezone(timezone.utc)
        add_second_event(db, tenant, project, present, binding, AttendanceType.CHECK_OUT, AttendanceStatus.VALID, checkout)
    db.commit()
    return tenant, project, work_date, present, absent


def test_timesheet_quantifies_presence_absence_late_and_early_leave(client, seeded, db):
    tenant, project, work_date, _, _ = setup_report(db, seeded)
    response = client.get(
        "/api/v1/timesheets",
        headers=admin_headers(client, tenant),
        params={"date_from": work_date.isoformat(), "date_to": work_date.isoformat(), "project_id": project.id},
    )
    assert response.status_code == 200
    report = response.json()["data"]
    assert report["summary"] == {
        "scheduled_days": 2,
        "present_days": 1,
        "absent_days": 1,
        "incomplete_days": 0,
        "exception_days": 0,
        "pending_days": 0,
        "late_days": 1,
        "early_leave_days": 1,
        "attendance_factor": 0.5,
    }
    present_row = next(row for row in report["rows"] if row["state"] == "PRESENT")
    assert present_row["late_minutes"] == 15
    assert present_row["early_leave_minutes"] == 30


def test_policy_grace_changes_late_and_early_leave_quantification(client, seeded, db):
    tenant, project, work_date, _, _ = setup_report(db, seeded)
    headers = admin_headers(client, tenant)
    update = client.put(
        "/api/v1/attendance-policy",
        headers=headers,
        json={"late_grace_minutes": 15, "early_leave_grace_minutes": 30},
    )
    assert update.status_code == 200
    report = client.get(
        "/api/v1/timesheets",
        headers=headers,
        params={"date_from": work_date.isoformat(), "date_to": work_date.isoformat(), "project_id": project.id},
    ).json()["data"]
    assert report["summary"]["late_days"] == 0
    assert report["summary"]["early_leave_days"] == 0


def test_incomplete_day_blocks_period_close(client, seeded, db):
    tenant, project, work_date, _, _ = setup_report(db, seeded, incomplete=True)
    response = client.post(
        "/api/v1/timesheet-periods/close",
        headers=admin_headers(client, tenant),
        json={"date_from": work_date.isoformat(), "date_to": work_date.isoformat(), "project_id": project.id},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TIMESHEET_HAS_OPEN_EXCEPTIONS"


def test_completed_period_closes_with_immutable_idempotent_snapshot(client, seeded, db):
    tenant, project, work_date, _, _ = setup_report(db, seeded)
    headers = admin_headers(client, tenant)
    payload = {"date_from": work_date.isoformat(), "date_to": work_date.isoformat(), "project_id": project.id}
    first = client.post("/api/v1/timesheet-periods/close", headers=headers, json=payload)
    second = client.post("/api/v1/timesheet-periods/close", headers=headers, json=payload)
    assert first.status_code == 200
    assert first.json()["data"]["idempotent"] is False
    assert len(first.json()["data"]["snapshot_hash"]) == 64
    assert second.status_code == 200
    assert second.json()["data"]["idempotent"] is True
    period = db.query(TimesheetPeriod).one()
    assert json.loads(period.snapshot_json)["summary"]["attendance_factor"] == 0.5
