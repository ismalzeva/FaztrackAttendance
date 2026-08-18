# M2 Verified Device Enrollment Acceptance

- [x] Worker authenticates with tenant code, worker ID, and PIN.
- [x] Invalid PIN is rejected without revealing which field is wrong.
- [x] PWA creates an EC P-256 device key and stores the private `CryptoKey` locally.
- [x] Only the public key is submitted to Faztrack.
- [x] Server challenge expires after five minutes and can be used only once.
- [x] Server verifies proof of private-key possession before creating a request.
- [x] Invalid device signatures are rejected.
- [x] Enrollment remains `PENDING` until an approver decides.
- [x] Owner, admin, and scoped supervisor can review requests.
- [x] Rejection requires a reason.
- [x] Approving a replacement revokes the previous active device.
- [x] Database enforces at most one active device per worker.
- [x] Request, approval, rejection, login, and replacement are auditable.
- [x] Fresh migration reaches `0003_m2_device_enrollment`.
- [x] Backend tests and frontend production build pass.

## Deliberately excluded from M2

- Biometric and selfie/liveness verification.
- IMEI collection.
- GPS, geofence, and attendance events.
- Payroll calculation.
