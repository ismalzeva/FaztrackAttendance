# M3 Attendance Proof Acceptance

- [x] Only a scheduled worker can request an attendance challenge.
- [x] The assigned project is resolved by the server, not chosen by the worker.
- [x] An active verified device is mandatory.
- [x] Attendance challenges expire after two minutes and are single-use.
- [x] Check-in and check-out payloads are signed by the active device key.
- [x] Canonical numeric formatting is stable across browser and backend.
- [x] Server time is authoritative and stored with client capture time.
- [x] GPS coordinates, accuracy, project radius, and computed distance are stored.
- [x] Accurate positions inside the geofence return `VALID`.
- [x] Boundary positions affected by GPS uncertainty return `REVIEW`.
- [x] Poor accuracy or positions outside the geofence return `REJECTED`.
- [x] Check-out requires an accepted check-in.
- [x] Duplicate accepted check-in/check-out events are blocked by API and database.
- [x] Rejected attempts remain auditable and can be retried.
- [x] Supervisor review is restricted to the supervisor's worker/project scope.
- [x] Review decisions require a reason and create audit events.
- [x] Fresh migration reaches `0004_m3_attendance_proof`.
- [x] Backend tests and frontend production build pass.

## Deliberately excluded from M3

- Selfie, biometric, and liveness verification.
- Offline attendance synchronization.
- Timesheet closing and payroll calculation.
- Native Android integrity checks.
